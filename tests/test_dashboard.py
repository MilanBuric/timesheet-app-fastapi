"""
Manager dashboard tests — GET /dashboard/team-summary. Three behaviors
this endpoint exists to get right, each with its own test:
  - "behind on logging" means clocked time with no entry for that date,
    not just silence for N days
  - "near overtime" only fires for a session that's still active right
    now, never a session that already ended
  - team-wide totals (hours, pending approvals, overtime days) aggregate
    correctly across multiple interns
"""


def _clock_exact_hours(client, headers, manager_headers, date_str, start_h, end_h):
    client.post("/clock/in", headers=headers)
    session_id = client.get("/clock/active", headers=headers).json()["id"]
    client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={
        "clocked_in_at": f"{date_str}T{start_h:02d}:00:00",
        "clocked_out_at": f"{date_str}T{end_h:02d}:00:00",
    })


def _create_second_intern(client, manager_headers, username="intern2"):
    client.post("/users", headers=manager_headers, json={"username": username, "password": "somepass123", "role": "intern"})
    r = client.post("/auth/login", json={"username": username, "password": "somepass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_intern_cannot_view_team_summary(client, intern_headers):
    r = client.get("/dashboard/team-summary", headers=intern_headers)
    assert r.status_code == 403


def test_pending_approvals_count(client, intern_headers, manager_headers):
    client.post("/entries", headers=intern_headers, json={
        "date": "2026-08-20", "activity": "Pending one", "category": "Other", "hours": 1.0, "force": False
    })
    r = client.post("/entries", headers=intern_headers, json={
        "date": "2026-08-20", "activity": "Pending two", "category": "Other", "hours": 1.0, "force": False
    })
    client.post(f"/entries/{r.json()['id']}/approve", headers=manager_headers)

    body = client.get("/dashboard/team-summary?client_date=2026-08-20", headers=manager_headers).json()
    assert body["pending_approvals_count"] == 1


def test_behind_on_logging_when_clocked_but_no_entry(client, intern_headers, manager_headers):
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-17", 9, 13)  # Monday, 4h, no entry
    body = client.get("/dashboard/team-summary?client_date=2026-08-17", headers=manager_headers).json()
    me = next(i for i in body["interns"] if i["username"] == "intern")
    assert "2026-08-17" in me["behind_on_logging_dates"]


def test_not_behind_on_logging_when_entry_exists(client, intern_headers, manager_headers):
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-17", 9, 13)
    client.post("/entries", headers=intern_headers, json={
        "date": "2026-08-17", "activity": "Logged it", "category": "Other", "hours": 4.0, "force": False
    })
    body = client.get("/dashboard/team-summary?client_date=2026-08-17", headers=manager_headers).json()
    me = next(i for i in body["interns"] if i["username"] == "intern")
    assert "2026-08-17" not in me["behind_on_logging_dates"]


def test_not_behind_on_logging_when_no_clocked_time(client, intern_headers, manager_headers):
    """No clocked time at all on a day is just an ordinary day off, not a
    logging gap — nothing to flag."""
    body = client.get("/dashboard/team-summary?client_date=2026-08-17", headers=manager_headers).json()
    me = next(i for i in body["interns"] if i["username"] == "intern")
    assert me["behind_on_logging_dates"] == []


def test_near_overtime_true_for_active_long_session(client, intern_headers, manager_headers):
    client.post("/clock/in", headers=intern_headers)
    session_id = client.get("/clock/active", headers=intern_headers).json()["id"]
    # Still active (no clocked_out_at) but started 7.5h ago
    from datetime import datetime, timedelta
    started = (datetime.utcnow() - timedelta(hours=7, minutes=30)).isoformat()
    client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={"clocked_in_at": started})
    body = client.get("/dashboard/team-summary", headers=manager_headers).json()
    me = next(i for i in body["interns"] if i["username"] == "intern")
    assert me["near_overtime"] is True


def test_near_overtime_false_for_completed_session(client, intern_headers, manager_headers):
    """A session that already ended isn't "near" anything anymore —
    it's simply over 8h (overtime) or it isn't."""
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-17", 8, 16)  # 8h, ended
    body = client.get("/dashboard/team-summary", headers=manager_headers).json()
    me = next(i for i in body["interns"] if i["username"] == "intern")
    assert me["near_overtime"] is False


def test_near_overtime_false_for_short_active_session(client, intern_headers, manager_headers):
    client.post("/clock/in", headers=intern_headers)
    body = client.get("/dashboard/team-summary", headers=manager_headers).json()
    me = next(i for i in body["interns"] if i["username"] == "intern")
    assert me["near_overtime"] is False


def test_overtime_days_and_total_hours_aggregate_across_interns(client, intern_headers, manager_headers):
    other_headers = _create_second_intern(client, manager_headers)
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-17", 8, 18)   # 10h -> overtime day
    _clock_exact_hours(client, other_headers, manager_headers, "2026-08-18", 9, 12)    # 3h -> not overtime

    body = client.get("/dashboard/team-summary?client_date=2026-08-18", headers=manager_headers).json()
    assert body["overtime_days_this_week"] == 1
    assert body["total_hours_week"] == 13.0