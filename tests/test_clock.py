"""
Clock in/out and clock-session correction tests. This is the sole basis
for pay and overtime (see get_daily_total in main.py) — entries.hours is
informational only — so accuracy here matters more than in test_entries.py.
"""
import sqlite3


def _get_active_session_id(client, headers):
    return client.get("/clock/active", headers=headers).json()["id"]


# ── Clock in/out ─────────────────────────────────────────────────────────

def test_clock_in_creates_active_session(client, intern_headers):
    r = client.post("/clock/in", headers=intern_headers)
    assert r.status_code == 201
    assert r.json()["is_active"] == 1


def test_clock_in_twice_rejected(client, intern_headers):
    client.post("/clock/in", headers=intern_headers)
    r = client.post("/clock/in", headers=intern_headers)
    assert r.status_code == 400


def test_clock_out_without_clocking_in_rejected(client, intern_headers):
    r = client.post("/clock/out", headers=intern_headers)
    assert r.status_code == 400


def test_clock_out_returns_elapsed_hours(client, intern_headers):
    client.post("/clock/in", headers=intern_headers)
    r = client.post("/clock/out", headers=intern_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["clocked_out_at"]
    assert body["hours"] > 0


def test_active_session_reflects_state(client, intern_headers):
    r = client.get("/clock/active", headers=intern_headers)
    assert r.json() is None
    client.post("/clock/in", headers=intern_headers)
    r = client.get("/clock/active", headers=intern_headers)
    assert r.json() is not None
    assert r.json()["is_active"] == 1


# ── Manager review + correction ───────────────────────────────────────────

def test_intern_cannot_list_clock_sessions(client, intern_headers):
    r = client.get("/clock-sessions", headers=intern_headers)
    assert r.status_code == 403


def test_manager_can_list_and_filter_clock_sessions(client, intern_headers, manager_headers):
    client.post("/clock/in", headers=intern_headers)
    client.post("/clock/out", headers=intern_headers)
    r = client.get("/clock-sessions", headers=manager_headers)
    assert r.status_code == 200
    assert len(r.json()) >= 1

    me = client.get("/auth/me", headers=intern_headers).json()
    r = client.get(f"/clock-sessions?user_id={me['id']}", headers=manager_headers)
    assert all(s["user_id"] == me["id"] for s in r.json())


def test_manager_correction_updates_hours_and_clears_active_flag(client, intern_headers, manager_headers):
    client.post("/clock/in", headers=intern_headers)
    session_id = _get_active_session_id(client, intern_headers)

    r = client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={
        "clocked_in_at": "2026-08-20T09:00:00", "clocked_out_at": "2026-08-20T13:00:00"
    })
    assert r.status_code == 200
    body = r.json()
    assert body["hours"] == 4.0
    assert body["is_active"] is False
    assert body["auto_closed"] is False


def test_manager_correction_rejects_clockout_before_clockin(client, intern_headers, manager_headers):
    client.post("/clock/in", headers=intern_headers)
    session_id = _get_active_session_id(client, intern_headers)
    r = client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={
        "clocked_in_at": "2026-08-20T13:00:00", "clocked_out_at": "2026-08-20T09:00:00"
    })
    assert r.status_code == 400


def test_manager_correction_rejects_bad_datetime_format(client, intern_headers, manager_headers):
    client.post("/clock/in", headers=intern_headers)
    session_id = _get_active_session_id(client, intern_headers)
    r = client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={
        "clocked_in_at": "not-a-date"
    })
    assert r.status_code == 400


def test_manager_correction_nonexistent_session_404(client, manager_headers):
    r = client.patch("/clock-sessions/999999", headers=manager_headers, json={
        "clocked_out_at": "2026-08-20T13:00:00"
    })
    assert r.status_code == 404


def test_intern_cannot_correct_clock_session(client, intern_headers):
    client.post("/clock/in", headers=intern_headers)
    session_id = _get_active_session_id(client, intern_headers)
    r = client.patch(f"/clock-sessions/{session_id}", headers=intern_headers, json={
        "clocked_out_at": "2026-08-20T13:00:00"
    })
    assert r.status_code == 403


def test_correction_moving_clock_in_to_new_day_updates_date_column(client, intern_headers, manager_headers, temp_db):
    """Regression check: stats/reports filter on the `date` column, not by
    parsing clocked_in_at — a corrected clock-in must keep `date` in sync
    or the session would silently vanish from reports for its real day."""
    client.post("/clock/in", headers=intern_headers)
    session_id = _get_active_session_id(client, intern_headers)
    client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={
        "clocked_in_at": "2026-08-15T09:00:00", "clocked_out_at": "2026-08-15T13:00:00"
    })
    conn = sqlite3.connect(temp_db)
    row = conn.execute("SELECT date FROM clock_sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    assert row[0] == "2026-08-15"


def test_overtime_flag_true_when_clocked_hours_exceed_eight(client, intern_headers, manager_headers):
    """The case test_entries.py's overtime test deliberately doesn't cover:
    overtime driven by real clocked time, not by entry hours."""
    client.post("/clock/in", headers=intern_headers)
    session_id = _get_active_session_id(client, intern_headers)
    client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={
        "clocked_in_at": "2026-08-20T08:00:00", "clocked_out_at": "2026-08-20T18:00:00"  # 10h
    })
    r = client.post("/entries", headers=intern_headers, json={
        "date": "2026-08-20", "activity": "Some task", "category": "Other", "hours": 1.0, "force": False
    })
    assert r.status_code == 201
    assert r.json()["overtime"] is True