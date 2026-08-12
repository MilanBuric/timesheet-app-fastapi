from fastapi import FastAPI, HTTPException, Depends, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pathlib import Path
import secrets
import uuid
from datetime import datetime, date, timedelta
from database import get_connection, init_db
from models import (EntryCreate, EntryUpdate, EntryResponse, StatsResponse,
                    LoginRequest, TokenResponse, UserResponse, UpdateRateRequest,
                    CreateUserRequest, WeeklyReport, WeeklyReportDay, RejectRequest,
                    BasicUser, MeetingCreate, MeetingResponse, UpdateEmailRequest,
                    RSVPRequest, MeetingReschedule, RoomCreate, RoomUpdate, RoomResponse,
                    RoomOccupancySlot, ForgotPasswordRequest, ResetPasswordRequest)
from auth import verify_password, create_token, get_current_user, require_manager
import email_utils
import google_meet
import reminders

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
    reminders.start_scheduler()


@app.on_event("shutdown")
def shutdown():
    reminders.stop_scheduler()


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


@app.post("/auth/forgot-password")
def forgot_password(body: ForgotPasswordRequest):
    # Always return the same generic response whether or not the account/email
    # exists, so this endpoint can't be used to enumerate valid usernames.
    generic_response = {"message": "If that account has an email on file, a reset link has been sent."}
    with get_connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (body.username,)).fetchone()
        if not user or not user["email"]:
            return generic_response
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        conn.execute(
            "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user["id"], expires_at)
        )
        conn.commit()
    reset_url = f"{email_utils.BASE_URL}/?reset_token={token}"
    try:
        email_utils.send_password_reset(user["email"], user["username"], reset_url)
    except Exception as exc:
        print(f"❌ Failed to send password reset email: {exc}")
    return generic_response


@app.post("/auth/reset-password")
def reset_password(body: ResetPasswordRequest):
    from auth import hash_password
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM password_reset_tokens WHERE token = ?", (body.token,)
        ).fetchone()
        if not row or row["used"]:
            raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used")
        if datetime.utcnow() > datetime.fromisoformat(row["expires_at"]):
            raise HTTPException(status_code=400, detail="This reset link has expired — request a new one")
        conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(body.new_password), row["user_id"]))
        conn.execute("UPDATE password_reset_tokens SET used = 1 WHERE token = ?", (body.token,))
        conn.commit()
    return {"message": "Password updated — you can now log in with your new password."}


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

@app.get("/rooms", response_model=list[RoomResponse])
def get_rooms(current_user=Depends(get_current_user)):
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM rooms ORDER BY name").fetchall()
        return [dict(r) for r in rows]


@app.post("/rooms", response_model=RoomResponse, status_code=201)
def create_room(body: RoomCreate, current_user=Depends(require_manager)):
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM rooms WHERE name = ?", (body.name,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail=f"A room named \"{body.name}\" already exists")
        cursor = conn.execute(
            "INSERT INTO rooms (name, capacity, equipment, status) VALUES (?, ?, ?, ?)",
            (body.name, body.capacity, body.equipment, body.status)
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM rooms WHERE id = ?", (cursor.lastrowid,)).fetchone())


@app.patch("/rooms/{room_id}", response_model=RoomResponse)
def update_room(room_id: int, body: RoomUpdate, current_user=Depends(require_manager)):
    with get_connection() as conn:
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        name = body.name if body.name is not None else room["name"]
        capacity = body.capacity if body.capacity is not None else room["capacity"]
        equipment = body.equipment if body.equipment is not None else room["equipment"]
        status = body.status if body.status is not None else room["status"]
        conn.execute(
            "UPDATE rooms SET name = ?, capacity = ?, equipment = ?, status = ? WHERE id = ?",
            (name, capacity, equipment, status, room_id)
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone())


@app.delete("/rooms/{room_id}", status_code=204)
def delete_room(room_id: int, current_user=Depends(require_manager)):
    with get_connection() as conn:
        conn.execute("DELETE FROM rooms WHERE id = ?", (room_id,))
        conn.commit()


@app.get("/rooms/{room_id}/occupancy", response_model=list[RoomOccupancySlot])
def get_room_occupancy(room_id: int, date_from: str = None, date_to: str = None,
                        current_user=Depends(get_current_user)):
    """The from/to time period each existing booking occupies this room for,
    so a manager can see at a glance when a room is actually free. Defaults
    to today through 30 days out when no range is given."""
    with get_connection() as conn:
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        if not date_from:
            date_from = date.today().isoformat()
        if not date_to:
            date_to = (date.today() + timedelta(days=30)).isoformat()
        rows = conn.execute(
            """SELECT m.id as meeting_id, m.title, m.date, m.start_time, m.end_time, u.username as organizer_username
               FROM meetings m JOIN users u ON m.organizer_id = u.id
               WHERE m.location_type = 'in_person' AND m.room = ? AND m.date >= ? AND m.date <= ?
               ORDER BY m.date, m.start_time""",
            (room["name"], date_from, date_to)
        ).fetchall()
        return [dict(r) for r in rows]


def _meeting_with_attendees(conn, meeting_id: int):
    m = conn.execute(
        """SELECT m.*, u.username as organizer_username FROM meetings m
           JOIN users u ON m.organizer_id = u.id WHERE m.id = ?""",
        (meeting_id,)
    ).fetchone()
    if not m:
        return None
    attendees = conn.execute(
        """SELECT u.id, u.username, ma.status, ma.decline_reason FROM meeting_attendees ma
           JOIN users u ON ma.user_id = u.id WHERE ma.meeting_id = ?
           ORDER BY u.username""",
        (meeting_id,)
    ).fetchall()
    result = dict(m)
    result["attendees"] = [dict(a) for a in attendees]
    return result


@app.get("/meetings", response_model=list[MeetingResponse])
def get_meetings(date_from: str = None, date_to: str = None, search: str = None,
                  current_user=Depends(get_current_user)):
    # Managers see every meeting; everyone else sees meetings they organize or are invited to
    query = """SELECT DISTINCT m.id, m.date, m.start_time FROM meetings m
               LEFT JOIN meeting_attendees ma ON ma.meeting_id = m.id
               LEFT JOIN users au ON ma.user_id = au.id
               JOIN users ou ON m.organizer_id = ou.id
               WHERE 1=1"""
    params = []
    if current_user["role"] != "manager":
        query += " AND (m.organizer_id = ? OR ma.user_id = ?)"
        params.extend([current_user["id"], current_user["id"]])
    # A search term looks across the whole visible history rather than being
    # boxed in by the currently-displayed calendar month.
    if search:
        query += """ AND (m.title LIKE ? OR m.description LIKE ? OR m.room LIKE ?
                          OR ou.username LIKE ? OR au.username LIKE ?)"""
        like = f"%{search}%"
        params.extend([like, like, like, like, like])
    else:
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


def _rooms_overlap(conn, date: str, start_time: str, end_time: str, room: str, exclude_meeting_id: int = None) -> bool:
    query = """SELECT id FROM meetings
               WHERE date = ? AND location_type = 'in_person' AND room = ?
                 AND NOT (end_time <= ? OR start_time >= ?)"""
    params = [date, room, start_time, end_time]
    if exclude_meeting_id is not None:
        query += " AND id != ?"
        params.append(exclude_meeting_id)
    return conn.execute(query, params).fetchone() is not None


RECURRENCE_STEP_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14}
MAX_RECURRENCE_OCCURRENCES = 52  # safety cap so a bad end date can't spawn hundreds of rows


def _generate_recurrence_dates(start_date: str, rule: str, until: str) -> list:
    """Returns the list of ISO date strings for a recurring series, start_date included."""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(until, "%Y-%m-%d").date()
    dates = []
    if rule == "monthly":
        cursor = start
        while cursor <= end and len(dates) < MAX_RECURRENCE_OCCURRENCES:
            dates.append(cursor.isoformat())
            # add one calendar month, clamping the day if the next month is shorter
            month = cursor.month + 1
            year = cursor.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            day = min(cursor.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                                    31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
            cursor = date(year, month, day)
    else:
        step = RECURRENCE_STEP_DAYS.get(rule)
        if not step:
            return [start_date]
        cursor = start
        while cursor <= end and len(dates) < MAX_RECURRENCE_OCCURRENCES:
            dates.append(cursor.isoformat())
            cursor = cursor + timedelta(days=step)
    return dates


def _create_one_meeting(conn, organizer, body: MeetingCreate, occurrence_date: str,
                         room: str, meeting_link: str, recurrence_group_id: str):
    """Inserts a single meeting row + its attendee invites. Returns (meeting_id, invitees) or
    (None, None) if skipped due to a room conflict."""
    if body.location_type == "in_person" and _rooms_overlap(conn, occurrence_date, body.start_time, body.end_time, room):
        return None, None

    cursor = conn.execute(
        """INSERT INTO meetings (organizer_id, title, description, date, start_time, end_time,
                                  location_type, room, meeting_link, recurrence_rule,
                                  recurrence_until, recurrence_group_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (organizer["id"], body.title, body.description, occurrence_date, body.start_time, body.end_time,
         body.location_type, room, meeting_link, body.recurrence, body.recurrence_until, recurrence_group_id)
    )
    meeting_id = cursor.lastrowid
    invitees = []
    for uid in set(body.attendee_ids) - {organizer["id"]}:
        attendee = conn.execute("SELECT id, username, email FROM users WHERE id = ?", (uid,)).fetchone()
        if attendee:
            token = secrets.token_urlsafe(24)
            conn.execute(
                "INSERT OR IGNORE INTO meeting_attendees (meeting_id, user_id, status, rsvp_token) VALUES (?, ?, 'pending', ?)",
                (meeting_id, uid, token)
            )
            invitees.append(dict(attendee) | {"rsvp_token": token})
    return meeting_id, invitees


def _email_invites_for_meeting(organizer, body: MeetingCreate, occurrence_date: str,
                                room: str, meeting_link: str, invitees: list, meeting_id: int) -> None:
    all_guest_names = [inv["username"] for inv in invitees]
    all_guest_emails = [inv["email"] for inv in invitees if inv.get("email")]
    for invitee in invitees:
        if invitee.get("email"):
            try:
                email_utils.send_meeting_invite(
                    to_email=invitee["email"],
                    attendee_name=invitee["username"],
                    organizer_name=organizer["username"],
                    title=body.title,
                    date=occurrence_date,
                    start_time=body.start_time,
                    end_time=body.end_time,
                    description=body.description,
                    rsvp_token=invitee["rsvp_token"],
                    location_type=body.location_type,
                    room=room,
                    meeting_link=meeting_link,
                    guests=all_guest_names,
                    meeting_id=meeting_id,
                    sequence=0,
                    organizer_email=organizer.get("email"),
                    attendee_emails=all_guest_emails
                )
            except Exception as exc:
                print(f"❌ Failed to email {invitee['email']}: {exc}")

    if organizer.get("email"):
        try:
            email_utils.send_organizer_confirmation(
                to_email=organizer["email"],
                organizer_name=organizer["username"],
                title=body.title,
                date=occurrence_date,
                start_time=body.start_time,
                end_time=body.end_time,
                description=body.description,
                location_type=body.location_type,
                room=room,
                meeting_link=meeting_link,
                guests=all_guest_names
            )
        except Exception as exc:
            print(f"❌ Failed to email organizer {organizer['email']}: {exc}")


@app.post("/meetings", response_model=MeetingResponse, status_code=201)
def create_meeting(body: MeetingCreate, background_tasks: BackgroundTasks, current_user=Depends(get_current_user)):
    if body.end_time <= body.start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time")
    if body.recurrence != "none" and not body.recurrence_until:
        raise HTTPException(status_code=400, detail="Please choose an end date for the recurring series")

    room = body.room.strip() if body.room else None
    meeting_link = body.meeting_link.strip() if body.meeting_link else None

    if body.location_type == "in_person":
        if not room:
            raise HTTPException(status_code=400, detail="Room is required for in-person meetings")
        meeting_link = None
    else:
        room = None
        if body.use_google_meet:
            try:
                # One Meet link is generated and reused across every occurrence
                # of a recurring series, matching how Google Calendar itself
                # handles recurring meetings.
                meeting_link = google_meet.create_meet_link(
                    user_id=current_user["id"], login_hint=current_user.get("email")
                )
            except google_meet.GoogleMeetError as exc:
                raise HTTPException(status_code=502, detail=str(exc))

    if body.recurrence == "none":
        occurrence_dates = [body.date]
        recurrence_group_id = None
    else:
        occurrence_dates = _generate_recurrence_dates(body.date, body.recurrence, body.recurrence_until)
        if len(occurrence_dates) >= MAX_RECURRENCE_OCCURRENCES:
            raise HTTPException(
                status_code=400,
                detail=f"That recurring range produces too many meetings (cap is {MAX_RECURRENCE_OCCURRENCES}) — pick a shorter end date."
            )
        recurrence_group_id = uuid.uuid4().hex

    created_ids = []
    skipped_dates = []
    with get_connection() as conn:
        if room:
            room_row = conn.execute("SELECT status FROM rooms WHERE name = ?", (room,)).fetchone()
            if room_row and room_row["status"] == "renovation":
                raise HTTPException(
                    status_code=400,
                    detail=f"Room \"{room}\" is currently under renovation and can't be booked"
                )
        for occurrence_date in occurrence_dates:
            meeting_id, invitees = _create_one_meeting(
                conn, current_user, body, occurrence_date, room, meeting_link, recurrence_group_id
            )
            if meeting_id is None:
                skipped_dates.append(occurrence_date)
                continue
            created_ids.append((meeting_id, occurrence_date, invitees))
        conn.commit()

        if not created_ids:
            raise HTTPException(
                status_code=409,
                detail=f"Room \"{room}\" is already booked for every occurrence in this series"
            )

        result = _meeting_with_attendees(conn, created_ids[0][0])

    # Emails (including SMTP round-trips and, for recurring series, one send
    # per occurrence) are queued to run *after* the response goes out — this
    # is what makes creating a recurring meeting with several attendees feel
    # instant instead of blocking the request for several seconds, which was
    # making the very next fetch (the calendar refresh) race a still-busy
    # server and occasionally fail with "Failed to fetch".
    for meeting_id, occurrence_date, invitees in created_ids:
        background_tasks.add_task(
            _email_invites_for_meeting, current_user, body, occurrence_date, room, meeting_link, invitees, meeting_id
        )

    result["series_count"] = len(created_ids) if recurrence_group_id else None
    result["skipped_dates"] = skipped_dates or None
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


@app.delete("/meetings/series/{group_id}", status_code=204)
def delete_meeting_series(group_id: str, current_user=Depends(get_current_user)):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, organizer_id FROM meetings WHERE recurrence_group_id = ?", (group_id,)
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Meeting series not found")
        if current_user["role"] != "manager" and any(r["organizer_id"] != current_user["id"] for r in rows):
            raise HTTPException(status_code=403, detail="Only the organizer or a manager can cancel this series")
        ids = [r["id"] for r in rows]
        conn.executemany("DELETE FROM meeting_attendees WHERE meeting_id = ?", [(i,) for i in ids])
        conn.executemany("DELETE FROM meetings WHERE id = ?", [(i,) for i in ids])
        conn.commit()


def _email_reschedule_notices(m, organizer, body: MeetingReschedule, old_date, old_start, old_end,
                               new_sequence: int, meeting_id: int, invitees: list) -> None:
    all_guest_names = [inv["username"] for inv in invitees]
    all_guest_emails = [inv["email"] for inv in invitees if inv.get("email")]
    for invitee in invitees:
        if invitee.get("email"):
            try:
                email_utils.send_meeting_reschedule_notice(
                    to_email=invitee["email"],
                    attendee_name=invitee["username"],
                    organizer_name=organizer["username"] if organizer else "",
                    title=m["title"],
                    date=body.date,
                    start_time=body.start_time,
                    end_time=body.end_time,
                    description=m["description"],
                    rsvp_token=invitee["rsvp_token"],
                    old_date=old_date, old_start_time=old_start, old_end_time=old_end,
                    location_type=m["location_type"],
                    room=m["room"],
                    meeting_link=m["meeting_link"],
                    guests=all_guest_names,
                    meeting_id=meeting_id,
                    sequence=new_sequence,
                    organizer_email=(organizer["email"] if organizer else None),
                    attendee_emails=all_guest_emails
                )
            except Exception as exc:
                print(f"❌ Failed to email {invitee['email']}: {exc}")

    if organizer and organizer["email"]:
        try:
            email_utils.send_organizer_confirmation(
                to_email=organizer["email"],
                organizer_name=organizer["username"],
                title=m["title"],
                date=body.date,
                start_time=body.start_time,
                end_time=body.end_time,
                description=m["description"],
                location_type=m["location_type"],
                room=m["room"],
                meeting_link=m["meeting_link"],
                guests=all_guest_names
            )
        except Exception as exc:
            print(f"❌ Failed to email organizer {organizer['email']}: {exc}")


@app.patch("/meetings/{meeting_id}/reschedule", response_model=MeetingResponse)
def reschedule_meeting(meeting_id: int, body: MeetingReschedule, background_tasks: BackgroundTasks, current_user=Depends(get_current_user)):
    if body.end_time <= body.start_time:
        raise HTTPException(status_code=400, detail="End time must be after start time")

    with get_connection() as conn:
        m = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if not m:
            raise HTTPException(status_code=404, detail="Meeting not found")
        if current_user["role"] != "manager" and m["organizer_id"] != current_user["id"]:
            raise HTTPException(status_code=403, detail="Only the organizer or a manager can reschedule this meeting")

        if m["location_type"] == "in_person" and _rooms_overlap(
            conn, body.date, body.start_time, body.end_time, m["room"], exclude_meeting_id=meeting_id
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Room \"{m['room']}\" is already booked for an overlapping time on {body.date}"
            )

        old_date, old_start, old_end = m["date"], m["start_time"], m["end_time"]
        new_sequence = (m["ics_sequence"] or 0) + 1

        # Reset every attendee back to pending with a fresh RSVP token — the old
        # accept/decline links no longer apply to the new time.
        attendee_rows = conn.execute(
            "SELECT user_id FROM meeting_attendees WHERE meeting_id = ?", (meeting_id,)
        ).fetchall()
        for a in attendee_rows:
            new_token = secrets.token_urlsafe(24)
            conn.execute(
                "UPDATE meeting_attendees SET status = 'pending', rsvp_token = ?, decline_reason = NULL WHERE meeting_id = ? AND user_id = ?",
                (new_token, meeting_id, a["user_id"])
            )

        conn.execute(
            "UPDATE meetings SET date = ?, start_time = ?, end_time = ?, reminder_sent = 0, ics_sequence = ? WHERE id = ?",
            (body.date, body.start_time, body.end_time, new_sequence, meeting_id)
        )
        conn.commit()
        organizer = conn.execute("SELECT id, username, email FROM users WHERE id = ?", (m["organizer_id"],)).fetchone()
        result = _meeting_with_attendees(conn, meeting_id)
        invitees = [dict(r) for r in conn.execute(
            """SELECT u.id, u.username, u.email, ma.rsvp_token FROM meeting_attendees ma
               JOIN users u ON ma.user_id = u.id WHERE ma.meeting_id = ?""",
            (meeting_id,)
        ).fetchall()]

    # Emails are queued to run after the response is sent — see the comment
    # in create_meeting for why this matters for the frontend's UX.
    background_tasks.add_task(
        _email_reschedule_notices, m, organizer, body, old_date, old_start, old_end,
        new_sequence, meeting_id, invitees
    )

    return result


@app.post("/meetings/{meeting_id}/rsvp", response_model=MeetingResponse)
def rsvp_meeting(meeting_id: int, body: RSVPRequest, current_user=Depends(get_current_user)):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM meeting_attendees WHERE meeting_id = ? AND user_id = ?",
            (meeting_id, current_user["id"])
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="You are not invited to this meeting")
        reason = body.reason.strip() if (body.status == "declined" and body.reason) else None
        conn.execute(
            "UPDATE meeting_attendees SET status = ?, decline_reason = ? WHERE meeting_id = ? AND user_id = ?",
            (body.status, reason, meeting_id, current_user["id"])
        )
        conn.commit()
        return _meeting_with_attendees(conn, meeting_id)


def _rsvp_confirmation_page(title: str, message: str, decline_note_token: str = None) -> str:
    note_form = ""
    if decline_note_token:
        note_form = f"""
        <form method="POST" action="/meetings/rsvp/note" style="margin-top:18px; text-align:left;">
          <input type="hidden" name="token" value="{decline_note_token}" />
          <label style="font-size:12px; color:#777; display:block; margin-bottom:6px;">
            Optional — let the organizer know why:
          </label>
          <textarea name="reason" rows="3" maxlength="500"
            style="width:100%; box-sizing:border-box; font-family:inherit; font-size:13px;
                   border:1px solid #ddd; border-radius:6px; padding:8px; resize:vertical;"
            placeholder="e.g. Conflicts with another meeting"></textarea>
          <button type="submit" style="margin-top:10px; background:#1a73e8; color:#fff; border:none;
                   padding:8px 16px; border-radius:6px; font-size:13px; cursor:pointer;">
            Send note
          </button>
        </form>
        """
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
    <body><div class="box"><h1>{title}</h1><p>{message}</p>{note_form}</div></body></html>
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
    # Only declines get the optional note form — no need to explain an acceptance.
    return HTMLResponse(_rsvp_confirmation_page(title, message, decline_note_token=token if status == "declined" else None))


@app.post("/meetings/rsvp/note", response_class=HTMLResponse)
def rsvp_add_decline_note(token: str = Form(...), reason: str = Form("")):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM meeting_attendees WHERE rsvp_token = ?", (token,)).fetchone()
        if not row:
            return HTMLResponse(
                _rsvp_confirmation_page("Link expired", "This RSVP link is invalid or has already been used."),
                status_code=404
            )
        conn.execute(
            "UPDATE meeting_attendees SET decline_reason = ? WHERE meeting_id = ? AND user_id = ?",
            (reason.strip()[:500] or None, row["meeting_id"], row["user_id"])
        )
        conn.commit()
    return HTMLResponse(_rsvp_confirmation_page("Thanks!", "Your note has been sent to the organizer."))