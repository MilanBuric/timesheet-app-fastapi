"""
Reporting tests — /stats and /reports/weekly. The central invariant this
file exists to prove: entries.hours is informational only, and pay,
overtime, and total hours all come exclusively from clock_sessions, never
from logged entries.
"""


def _set_rate(client, manager_headers, user_id, rate):
    client.patch(f"/users/{user_id}/rate", headers=manager_headers, json={"hourly_rate": rate})


def _create_second_intern(client, manager_headers, username="intern2"):
    client.post("/users", headers=manager_headers, json={"username": username, "password": "somepass123", "role": "intern"})
    r = client.post("/auth/login", json={"username": username, "password": "somepass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _clock_exact_hours(client, headers, manager_headers, date_str, start_h, end_h):
    """Clocks in, then has a manager correct the session to an exact
    clocked_in_at/out on a specific date — the only reliable way to get a
    precise, non-flaky duration in a test."""
    client.post("/clock/in", headers=headers)
    session_id = client.get("/clock/active", headers=headers).json()["id"]
    client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={
        "clocked_in_at": f"{date_str}T{start_h:02d}:00:00",
        "clocked_out_at": f"{date_str}T{end_h:02d}:00:00",
    })


# ── Stats ────────────────────────────────────────────────────────────────

def test_intern_stats_reflect_own_clocked_hours(client, intern_headers, manager_headers):
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-20", 9, 13)  # 4h
    r = client.get("/stats?client_date=2026-08-20", headers=intern_headers)
    assert r.status_code == 200
    assert r.json()["hours_today"] == 4.0


def test_manager_stats_aggregate_across_interns(client, manager_headers, intern_headers):
    other_headers = _create_second_intern(client, manager_headers)
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-20", 9, 12)   # 3h
    _clock_exact_hours(client, other_headers, manager_headers, "2026-08-20", 9, 14)    # 5h
    r = client.get("/stats?client_date=2026-08-20", headers=manager_headers)
    assert r.json()["hours_today"] == 8.0


# ── Weekly report ────────────────────────────────────────────────────────

def test_intern_report_uses_clocked_hours_not_entry_hours(client, intern_headers, manager_headers):
    """The core invariant: a 2h entry logged on a day with 6h actually
    clocked must report 6h total and pay based on 6h, not 2h."""
    me = client.get("/auth/me", headers=intern_headers).json()
    _set_rate(client, manager_headers, me["id"], 10.0)
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-20", 9, 15)  # 6h
    client.post("/entries", headers=intern_headers, json={
        "date": "2026-08-20", "activity": "Logged task", "category": "Self-study", "hours": 2.0, "force": False
    })

    r = client.get("/reports/weekly?date_from=2026-08-20&date_to=2026-08-20", headers=intern_headers)
    assert r.status_code == 200
    day = r.json()["days"][0]
    assert day["total"] == 6.0
    assert day["approved_pay"] == 60.0
    assert r.json()["total_hours"] == 6.0
    assert r.json()["total_pay"] == 60.0


def test_day_with_only_clocked_time_and_no_entries_still_appears(client, intern_headers, manager_headers):
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-21", 9, 12)  # 3h, no entries logged
    r = client.get("/reports/weekly?date_from=2026-08-21&date_to=2026-08-21", headers=intern_headers)
    days = r.json()["days"]
    assert len(days) == 1
    assert days[0]["total"] == 3.0
    assert days[0]["self_study"] == 0
    assert days[0]["meeting"] == 0
    assert days[0]["other"] == 0


def test_overtime_flag_on_report_day(client, intern_headers, manager_headers):
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-22", 8, 18)  # 10h
    r = client.get("/reports/weekly?date_from=2026-08-22&date_to=2026-08-22", headers=intern_headers)
    assert r.json()["days"][0]["any_overtime"] is True


def test_intern_cannot_see_others_report(client, intern_headers, manager_headers):
    other_headers = _create_second_intern(client, manager_headers, username="intern3")
    other_id = client.get("/auth/me", headers=other_headers).json()["id"]
    r = client.get(
        f"/reports/weekly?date_from=2026-01-01&date_to=2026-12-31&user_id={other_id}", headers=intern_headers
    )
    # user_id is silently ignored for non-managers — the report always scopes to current_user
    assert r.status_code == 200
    usernames = {e["username"] for e in r.json()["entries"]}
    assert "intern3" not in usernames


def test_manager_report_without_user_id_aggregates_all_interns(client, manager_headers, intern_headers):
    other_headers = _create_second_intern(client, manager_headers, username="intern4")
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-23", 9, 11)   # 2h
    _clock_exact_hours(client, other_headers, manager_headers, "2026-08-23", 9, 13)    # 4h

    r = client.get("/reports/weekly?date_from=2026-08-23&date_to=2026-08-23", headers=manager_headers)
    day = r.json()["days"][0]
    assert day["total"] == 6.0
    assert day["user_breakdown"], "multi-user manager report should include a per-user breakdown"


def test_manager_report_with_user_id_scopes_to_one_intern(client, manager_headers, intern_headers):
    me = client.get("/auth/me", headers=intern_headers).json()
    other_headers = _create_second_intern(client, manager_headers, username="intern5")
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-24", 9, 11)  # 2h
    _clock_exact_hours(client, other_headers, manager_headers, "2026-08-24", 9, 15)   # 6h

    r = client.get(
        f"/reports/weekly?date_from=2026-08-24&date_to=2026-08-24&user_id={me['id']}", headers=manager_headers
    )
    assert r.json()["days"][0]["total"] == 2.0


def test_mixed_hourly_rates_report_hourly_rate_field_is_sentinel(client, manager_headers, intern_headers):
    """hourly_rate on the report is only meaningful for a single flat rate;
    when scoped users have different rates it must come back as -1.0
    rather than silently picking one."""
    me = client.get("/auth/me", headers=intern_headers).json()
    other_headers = _create_second_intern(client, manager_headers, username="intern6")
    other_id = client.get("/auth/me", headers=other_headers).json()["id"]
    _set_rate(client, manager_headers, me["id"], 10.0)
    _set_rate(client, manager_headers, other_id, 20.0)
    _clock_exact_hours(client, intern_headers, manager_headers, "2026-08-25", 9, 10)
    _clock_exact_hours(client, other_headers, manager_headers, "2026-08-25", 9, 10)

    r = client.get("/reports/weekly?date_from=2026-08-25&date_to=2026-08-25", headers=manager_headers)
    assert r.json()["hourly_rate"] == -1.0