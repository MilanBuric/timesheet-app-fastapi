from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pathlib import Path
import secrets
from datetime import datetime, date, timedelta
from database import get_connection, init_db
from models import (EntryCreate, EntryUpdate, EntryResponse, StatsResponse,
                    LoginRequest, TokenResponse, UserResponse, UpdateRateRequest,
                    CreateUserRequest, WeeklyReport, WeeklyReportDay, RejectRequest,
                    BasicUser, MeetingCreate, MeetingResponse, UpdateEmailRequest,
                    RSVPRequest)
from auth import verify_password, create_token, get_current_user, require_manager
import email_utils

app = FastAPI(title="Timesheet API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_PATH = Path(__file__).parent.parent / "frontend"


@app.on_event("startup")
def startup():
    init_db()


# ── Static + frontend ─────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=FRONTEND_PATH), name="static")

@app.get("/")
def root():
    return FileResponse(FRONTEND_PATH / "index.html")


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest):
    with get_connection() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (body.username,)
        ).fetchone()
    if not user or not verify_password(body.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_token({"sub": str(user["id"]), "role": user["role"]})
    return TokenResponse(
        access_token=token,
        role=user["role"],
        username=user["username"],
        user_id=user["id"]
    )


@app.get("/auth/me")
def me(current_user=Depends(get_current_user)):
    return {
        "id": current_user["id"],
        "username": current_user["username"],
        "role": current_user["role"],
        "hourly_rate": current_user["hourly_rate"],
        "email": current_user["email"]
    }


@app.patch("/auth/me/email")
def update_my_email(body: UpdateEmailRequest, current_user=Depends(get_current_user)):
    with get_connection() as conn:
        conn.execute("UPDATE users SET email = ? WHERE id = ?", (body.email, current_user["id"]))
        conn.commit()
    return {"email": body.email}


# ── Users (manager only) ──────────────────────────────────────────────────────

@app.get("/users", response_model=list[UserResponse])
def get_users(current_user=Depends(require_manager)):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, username, role, hourly_rate FROM users ORDER BY role, username"
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/users", response_model=UserResponse, status_code=201)
def create_user(body: CreateUserRequest, current_user=Depends(require_manager)):
    from auth import hash_password
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ?", (body.username,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Username already taken")
        cursor = conn.execute(
            "INSERT INTO users (username, password, role, hourly_rate) VALUES (?, ?, ?, ?)",
            (body.username, hash_password(body.password), body.role, body.hourly_rate)
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, username, role, hourly_rate FROM users WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


@app.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, current_user=Depends(require_manager)):
    if user_id == current_user["id"]:
        raise HTTPException(status_code=400, detail="You cannot delete yourself")
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")
        # Delete user's entries and clock sessions too
        conn.execute("DELETE FROM entries WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM clock_sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


@app.get("/users/basic", response_model=list[BasicUser])
def get_basic_users(current_user=Depends(get_current_user)):
    """Lightweight user list (id/username/role only) for picking meeting attendees."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, username, role FROM users ORDER BY role, username"
        ).fetchall()
    return [dict(r) for r in rows]


@app.patch("/users/{user_id}/rate", response_model=UserResponse)
def set_hourly_rate(user_id: int, body: UpdateRateRequest, current_user=Depends(require_manager)):
    with get_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        conn.execute(
            "UPDATE users SET hourly_rate = ? WHERE id = ?",
            (body.hourly_rate, user_id)
        )
        conn.commit()
        row = conn.execute(
            "SELECT id, username, role, hourly_rate FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row)


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_daily_total(conn, user_id: int, date_str: str, exclude_id: int = None) -> float:
    query = "SELECT COALESCE(SUM(hours), 0) FROM entries WHERE user_id = ? AND date = ?"
    params = [user_id, date_str]
    if exclude_id:
        query += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(query, params).fetchone()[0]


# ── Entries ───────────────────────────────────────────────────────────────────

@app.get("/entries", response_model=list[EntryResponse])
def get_entries(
    date_from: str = None,
    date_to: str = None,
    category: str = None,
    current_user=Depends(get_current_user)
):
    query = "SELECT e.*, u.username FROM entries e JOIN users u ON e.user_id = u.id WHERE 1=1"
    params = []
    if current_user["role"] == "intern":
        query += " AND e.user_id = ?"
        params.append(current_user["id"])
    if date_from:
        query += " AND e.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND e.date <= ?"
        params.append(date_to)
    if category:
        query += " AND e.category = ?"
        params.append(category)
    query += " ORDER BY e.date DESC, e.created_at DESC"

    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        for row in rows:
            row["overtime"] = get_daily_total(conn, row["user_id"], row["date"]) > 8
    return rows


@app.post("/entries", response_model=EntryResponse, status_code=201)
def create_entry(entry: EntryCreate, current_user=Depends(get_current_user)):
    with get_connection() as conn:
        if not entry.force:
            duplicate = conn.execute(
                "SELECT id FROM entries WHERE user_id = ? AND date = ? AND activity = ?",
                (current_user["id"], entry.date, entry.activity)
            ).fetchone()
            if duplicate:
                raise HTTPException(
                    status_code=409,
                    detail=f"Duplicate entry: '{entry.activity}' already logged on {entry.date}."
                )
        cursor = conn.execute(
            "INSERT INTO entries (user_id, date, activity, category, hours) VALUES (?, ?, ?, ?, ?)",
            (current_user["id"], entry.date, entry.activity, entry.category.value, entry.hours)
        )
        conn.commit()
        row = dict(conn.execute(
            "SELECT e.*, u.username FROM entries e JOIN users u ON e.user_id = u.id WHERE e.id = ?",
            (cursor.lastrowid,)
        ).fetchone())
        row["overtime"] = get_daily_total(conn, current_user["id"], entry.date) > 8
    return row


@app.patch("/entries/{entry_id}", response_model=EntryResponse)
def update_entry(entry_id: int, update: EntryUpdate, current_user=Depends(get_current_user)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Entry not found")
        if current_user["role"] == "intern" and existing["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not your entry")
        fields = {k: v for k, v in update.model_dump().items() if v is not None}
        if fields:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE entries SET {set_clause} WHERE id = ?", list(fields.values()) + [entry_id])
            conn.commit()
        row = dict(conn.execute(
            "SELECT e.*, u.username FROM entries e JOIN users u ON e.user_id = u.id WHERE e.id = ?",
            (entry_id,)
        ).fetchone())
        row["overtime"] = get_daily_total(conn, row["user_id"], row["date"]) > 8
    return row


@app.delete("/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int, current_user=Depends(get_current_user)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Entry not found")
        if current_user["role"] == "intern" and existing["user_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Not your entry")
        conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        conn.commit()


@app.post("/entries/{entry_id}/approve", response_model=EntryResponse)
def approve_entry(entry_id: int, current_user=Depends(require_manager)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Entry not found")
        conn.execute("UPDATE entries SET status = 'approved', rejection_reason = NULL WHERE id = ?", (entry_id,))
        conn.commit()
        row = dict(conn.execute(
            "SELECT e.*, u.username FROM entries e JOIN users u ON e.user_id = u.id WHERE e.id = ?",
            (entry_id,)
        ).fetchone())
        row["overtime"] = get_daily_total(conn, row["user_id"], row["date"]) > 8
    return row


@app.post("/entries/{entry_id}/reject", response_model=EntryResponse)
def reject_entry(entry_id: int, body: RejectRequest, current_user=Depends(require_manager)):
    with get_connection() as conn:
        existing = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Entry not found")
        conn.execute(
            "UPDATE entries SET status = 'rejected', rejection_reason = ? WHERE id = ?",
            (body.reason, entry_id)
        )
        conn.commit()
        row = dict(conn.execute(
            "SELECT e.*, u.username FROM entries e JOIN users u ON e.user_id = u.id WHERE e.id = ?",
            (entry_id,)
        ).fetchone())
        row["overtime"] = get_daily_total(conn, row["user_id"], row["date"]) > 8
    return row


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/stats", response_model=StatsResponse)
def get_stats(client_date: str = None, current_user=Depends(get_current_user)):
    # Use client-provided date to avoid UTC offset issues
    today = client_date if client_date else date.today().isoformat()
    try:
        today_dt = datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        today_dt = date.today()
    week_start = (today_dt - timedelta(days=today_dt.weekday())).isoformat()
    today = today_dt.isoformat()

    with get_connection() as conn:
        if current_user["role"] == "manager":
            # Manager sees totals across all interns
            today_hours = conn.execute(
                "SELECT COALESCE(SUM(hours), 0) FROM entries WHERE date = ?", (today,)
            ).fetchone()[0]
            week_hours = conn.execute(
                "SELECT COALESCE(SUM(hours), 0) FROM entries WHERE date >= ?", (week_start,)
            ).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        else:
            uid = current_user["id"]
            today_hours = conn.execute(
                "SELECT COALESCE(SUM(hours), 0) FROM entries WHERE user_id = ? AND date = ?", (uid, today)
            ).fetchone()[0]
            week_hours = conn.execute(
                "SELECT COALESCE(SUM(hours), 0) FROM entries WHERE user_id = ? AND date >= ?", (uid, week_start)
            ).fetchone()[0]
            total = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE user_id = ?", (uid,)
            ).fetchone()[0]
    return StatsResponse(hours_today=round(today_hours, 2), hours_week=round(week_hours, 2), total_entries=total)


# ── Weekly report ─────────────────────────────────────────────────────────────

@app.get("/reports/weekly", response_model=WeeklyReport)
def weekly_report(
    date_from: str,
    date_to: str,
    user_id: int = None,
    current_user=Depends(get_current_user)
):
    # Every query always fetches u.hourly_rate as user_rate
    # Pay is always calculated per entry: hours * user_rate
    # This works for one intern, many interns, or mixed rates

    with get_connection() as conn:
        if current_user["role"] == "manager" and user_id:
            rows = [dict(r) for r in conn.execute(
                """SELECT e.*, u.username, u.hourly_rate as user_rate FROM entries e
                   JOIN users u ON e.user_id = u.id
                   WHERE e.user_id = ? AND e.date >= ? AND e.date <= ?
                   ORDER BY e.date ASC, e.created_at ASC""",
                (user_id, date_from, date_to)
            ).fetchall()]
        elif current_user["role"] == "manager":
            rows = [dict(r) for r in conn.execute(
                """SELECT e.*, u.username, u.hourly_rate as user_rate FROM entries e
                   JOIN users u ON e.user_id = u.id
                   WHERE u.role = 'intern' AND e.date >= ? AND e.date <= ?
                   ORDER BY e.date ASC, e.created_at ASC""",
                (date_from, date_to)
            ).fetchall()]
        else:
            rows = [dict(r) for r in conn.execute(
                """SELECT e.*, u.username, u.hourly_rate as user_rate FROM entries e
                   JOIN users u ON e.user_id = u.id
                   WHERE e.user_id = ? AND e.date >= ? AND e.date <= ?
                   ORDER BY e.date ASC, e.created_at ASC""",
                (current_user["id"], date_from, date_to)
            ).fetchall()]

        for row in rows:
            row["overtime"] = get_daily_total(conn, row["user_id"], row["date"]) > 8

    # For display: show the rate if all entries share the same rate, else -1 (mixed rates)
    rates = list(set(r["user_rate"] for r in rows)) if rows else [0.0]
    display_rate = rates[0] if len(rates) == 1 else -1.0

    # Get unique users in this report
    user_ids = list(dict.fromkeys(r["user_id"] for r in rows))
    multi_user = len(user_ids) > 1

    # Group by date + user so overtime is per person per day
    days_map = {}  # date -> { user_id -> {username, Self-study, Meeting, Other} }
    for r in rows:
        d = r["date"]
        uid = r["user_id"]
        if d not in days_map:
            days_map[d] = {}
        if uid not in days_map[d]:
            days_map[d][uid] = {"username": r["username"], "Self-study": 0, "Meeting": 0, "Other": 0, "user_rate": r["user_rate"]}
        days_map[d][uid][r["category"]] += r["hours"]

    days = []
    for d in sorted(days_map.keys()):
        user_totals = days_map[d]
        # Day total across all users
        day_total = sum(
            v["Self-study"] + v["Meeting"] + v["Other"]
            for v in user_totals.values()
        )
        # Overtime only if ANY single user exceeds 8h that day
        any_overtime = any(
            v["Self-study"] + v["Meeting"] + v["Other"] > 8
            for v in user_totals.values()
        )
        approved_day_pay = sum(
            r["hours"] * r["user_rate"]
            for r in rows if r["date"] == d and r["status"] == "approved"
        )
        days.append(WeeklyReportDay(
            date=d,
            self_study=round(sum(v["Self-study"] for v in user_totals.values()), 2),
            meeting=round(sum(v["Meeting"] for v in user_totals.values()), 2),
            other=round(sum(v["Other"] for v in user_totals.values()), 2),
            total=round(day_total, 2),
            approved_pay=round(approved_day_pay, 2),
            any_overtime=any_overtime,
            user_breakdown=[
                {
                    "username": v["username"],
                    "self_study": round(v["Self-study"], 2),
                    "meeting": round(v["Meeting"], 2),
                    "other": round(v["Other"], 2),
                    "total": round(v["Self-study"] + v["Meeting"] + v["Other"], 2),
                    "overtime": v["Self-study"] + v["Meeting"] + v["Other"] > 8,
                    "approved_pay": round(sum(
                        r["hours"] * r["user_rate"]
                        for r in rows
                        if r["date"] == d and r["user_id"] == uid and r["status"] == "approved"
                    ), 2)
                }
                for uid, v in user_totals.items()
            ] if multi_user else []
        ))

    total_hours = sum(r["hours"] for r in rows)
    approved_rows = [r for r in rows if r["status"] == "approved"]
    category_totals = {
        "Self-study": round(sum(r["hours"] for r in rows if r["category"] == "Self-study"), 2),
        "Meeting": round(sum(r["hours"] for r in rows if r["category"] == "Meeting"), 2),
        "Other": round(sum(r["hours"] for r in rows if r["category"] == "Other"), 2),
    }
    total_pay = round(sum(r["hours"] * r["user_rate"] for r in approved_rows), 2)

    return WeeklyReport(
        from_date=date_from,
        to_date=date_to,
        days=days,
        category_totals=category_totals,
        total_hours=round(total_hours, 2),
        hourly_rate=display_rate,
        total_pay=total_pay,
        entries=rows
    )


# ── Clock ─────────────────────────────────────────────────────────────────────

@app.get("/clock/active")
def get_active_session(current_user=Depends(get_current_user)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM clock_sessions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
            (current_user["id"],)
        ).fetchone()
    return dict(row) if row else None


@app.post("/clock/in", status_code=201)
def clock_in(current_user=Depends(get_current_user)):
    with get_connection() as conn:
        active = conn.execute(
            "SELECT id FROM clock_sessions WHERE user_id = ? AND is_active = 1",
            (current_user["id"],)
        ).fetchone()
        if active:
            raise HTTPException(status_code=400, detail="Already clocked in")
        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            "INSERT INTO clock_sessions (user_id, clocked_in_at) VALUES (?, ?)",
            (current_user["id"], now)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM clock_sessions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return dict(row)


@app.post("/clock/out")
def clock_out(current_user=Depends(get_current_user)):
    with get_connection() as conn:
        active = conn.execute(
            "SELECT * FROM clock_sessions WHERE user_id = ? AND is_active = 1 ORDER BY id DESC LIMIT 1",
            (current_user["id"],)
        ).fetchone()
        if not active:
            raise HTTPException(status_code=400, detail="Not clocked in")
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE clock_sessions SET clocked_out_at = ?, is_active = 0 WHERE id = ?",
            (now, active["id"])
        )
        conn.commit()
        elapsed = (datetime.utcnow() - datetime.fromisoformat(active["clocked_in_at"])).total_seconds() / 3600
    return {"clocked_in_at": active["clocked_in_at"], "clocked_out_at": now, "hours": max(0.5, round(elapsed * 2) / 2)}


# ── Meetings ──────────────────────────────────────────────────────────────────

def _meeting_with_attendees(conn, meeting_id: int):
    m = conn.execute(
        """SELECT m.*, u.username as organizer_username FROM meetings m
           JOIN users u ON m.organizer_id = u.id WHERE m.id = ?""",
        (meeting_id,)
    ).fetchone()
    if not m:
        return None
    attendees = conn.execute(
        """SELECT u.id, u.username, ma.status FROM meeting_attendees ma
           JOIN users u ON ma.user_id = u.id WHERE ma.meeting_id = ?
           ORDER BY u.username""",
        (meeting_id,)
    ).fetchall()
    result = dict(m)
    result["attendees"] = [dict(a) for a in attendees]
    return result


@app.get("/meetings", response_model=list[MeetingResponse])
def get_meetings(date_from: str = None, date_to: str = None, current_user=Depends(get_current_user)):
    # Managers see every meeting; everyone else sees meetings they organize or are invited to
    query = """SELECT DISTINCT m.id, m.date, m.start_time FROM meetings m
               LEFT JOIN meeting_attendees ma ON ma.meeting_id = m.id
               WHERE 1=1"""
    params = []
    if current_user["role"] != "manager":
        query += " AND (m.organizer_id = ? OR ma.user_id = ?)"
        params.extend([current_user["id"], current_user["id"]])
    if date_from:
        query += " AND m.date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND m.date <= ?"
        params.append(date_to)
    query += " ORDER BY m.date, m.start_time"

    with get_connection() as conn:
        ids = [r["id"] for r in conn.execute(query, params).fetchall()]
        return [_meeting_with_attendees(conn, mid) for mid in ids]


@app.post("/meetings", response_model=MeetingResponse, status_code=201)
def create_meeting(body: MeetingCreate, current_user=Depends(get_current_user)):
    if body.end_time <= body.start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time")
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO meetings (organizer_id, title, description, date, start_time, end_time)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (current_user["id"], body.title, body.description, body.date, body.start_time, body.end_time)
        )
        meeting_id = cursor.lastrowid
        invitees = []
        for uid in set(body.attendee_ids) - {current_user["id"]}:
            attendee = conn.execute("SELECT id, username, email FROM users WHERE id = ?", (uid,)).fetchone()
            if attendee:
                token = secrets.token_urlsafe(24)
                conn.execute(
                    "INSERT OR IGNORE INTO meeting_attendees (meeting_id, user_id, status, rsvp_token) VALUES (?, ?, 'pending', ?)",
                    (meeting_id, uid, token)
                )
                invitees.append(dict(attendee) | {"rsvp_token": token})
        conn.commit()
        result = _meeting_with_attendees(conn, meeting_id)

    # Send invite emails after the transaction commits; never let a failed
    # email prevent the meeting itself from being created.
    for invitee in invitees:
        if invitee.get("email"):
            try:
                email_utils.send_meeting_invite(
                    to_email=invitee["email"],
                    attendee_name=invitee["username"],
                    organizer_name=current_user["username"],
                    title=body.title,
                    date=body.date,
                    start_time=body.start_time,
                    end_time=body.end_time,
                    description=body.description,
                    rsvp_token=invitee["rsvp_token"]
                )
            except Exception as exc:
                print(f"❌ Failed to email {invitee['email']}: {exc}")
    return result


@app.delete("/meetings/{meeting_id}", status_code=204)
def delete_meeting(meeting_id: int, current_user=Depends(get_current_user)):
    with get_connection() as conn:
        m = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Meeting not found")
        if current_user["role"] != "manager" and m["organizer_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Only the organizer or a manager can cancel this meeting")
        conn.execute("DELETE FROM meeting_attendees WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        conn.commit()


@app.post("/meetings/{meeting_id}/rsvp", response_model=MeetingResponse)
def rsvp_meeting(meeting_id: int, body: RSVPRequest, current_user=Depends(get_current_user)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM meeting_attendees WHERE meeting_id = ? AND user_id = ?",
            (meeting_id, current_user["id"])
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="You are not invited to this meeting")
        conn.execute(
            "UPDATE meeting_attendees SET status = ? WHERE meeting_id = ? AND user_id = ?",
            (body.status, meeting_id, current_user["id"])
        )
        conn.commit()
        return _meeting_with_attendees(conn, meeting_id)


def _rsvp_confirmation_page(title: str, message: str) -> str:
    return f"""
    <html><head><title>{title}</title>
    <style>
      body {{ font-family: Arial, sans-serif; display: flex; align-items: center;
              justify-content: center; height: 100vh; margin: 0; background: #f7f7f8; }}
      .box {{ background: #fff; padding: 32px 40px; border-radius: 12px;
              box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; max-width: 360px; }}
      h1 {{ font-size: 18px; margin-bottom: 8px; }}
      p {{ color: #555; font-size: 14px; }}
    </style></head>
    <body><div class="box"><h1>{title}</h1><p>{message}</p></div></body></html>
    """


@app.get("/meetings/rsvp", response_class=HTMLResponse)
def rsvp_via_email_link(token: str, action: str):
    if action not in ("accept", "decline"):
        return HTMLResponse(_rsvp_confirmation_page("Invalid link", "This RSVP link isn't valid."), status_code=400)
    status = "accepted" if action == "accept" else "declined"
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM meeting_attendees WHERE rsvp_token = ?", (token,)
        ).fetchone()
        if not row:
            return HTMLResponse(
                _rsvp_confirmation_page("Link expired", "This RSVP link is invalid or has already been used."),
                status_code=404
            )
        conn.execute(
            "UPDATE meeting_attendees SET status = ? WHERE meeting_id = ? AND user_id = ?",
            (status, row["meeting_id"], row["user_id"])
        )
        conn.commit()
        meeting = conn.execute("SELECT title FROM meetings WHERE id = ?", (row["meeting_id"],)).fetchone()
    title = "You're in! ✅" if status == "accepted" else "Response recorded"
    message = f'You have {status} the invite to "{meeting["title"] if meeting else "the meeting"}".'
    return HTMLResponse(_rsvp_confirmation_page(title, message))
