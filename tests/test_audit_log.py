"""
Audit log tests — confirms mutations across entries, users, clock
sessions, and meetings actually produce a readable audit_log row, and
that the manager-only /audit-log endpoint filters/paginates correctly.
This deliberately doesn't re-test every single mutation endpoint in the
app (that's what test_entries.py/test_users.py/test_clock.py/
test_meetings.py are for) — just enough of a representative sample per
entity_type to prove the logging is actually wired up end to end.
"""


def _latest_log(client, manager_headers, **params):
    r = client.get("/audit-log", headers=manager_headers, params=params)
    assert r.status_code == 200
    return r.json()


def test_intern_cannot_view_audit_log(client, intern_headers):
    r = client.get("/audit-log", headers=intern_headers)
    assert r.status_code == 403


def test_entry_create_update_delete_are_logged(client, intern_headers, manager_headers):
    r = client.post("/entries", headers=intern_headers, json={
        "date": "2026-08-20", "activity": "Audit test", "category": "Other", "hours": 1.0, "force": False
    })
    entry_id = r.json()["id"]
    client.patch(f"/entries/{entry_id}", headers=intern_headers, json={"hours": 2.0})
    client.delete(f"/entries/{entry_id}", headers=intern_headers)

    logs = _latest_log(client, manager_headers, entity_type="entry")
    actions = [l["action"] for l in logs if l["entity_id"] == str(entry_id)]
    assert actions == ["entry.delete", "entry.update", "entry.create"]  # newest first
    update_row = next(l for l in logs if l["action"] == "entry.update")
    assert "2.0" in update_row["summary"] and "1.0" in update_row["summary"]


def test_entry_actor_is_recorded(client, intern_headers, manager_headers):
    client.post("/entries", headers=intern_headers, json={
        "date": "2026-08-20", "activity": "Whose entry", "category": "Other", "hours": 1.0, "force": False
    })
    logs = _latest_log(client, manager_headers, entity_type="entry", action="entry.create")
    assert logs[0]["actor_username"] == "intern"


def test_user_create_and_rate_change_are_logged(client, manager_headers):
    r = client.post("/users", headers=manager_headers, json={
        "username": "audituser", "password": "somepass123", "role": "intern"
    })
    user_id = r.json()["id"]
    client.patch(f"/users/{user_id}/rate", headers=manager_headers, json={"hourly_rate": 18.0})

    logs = _latest_log(client, manager_headers, entity_type="user")
    actions = [l["action"] for l in logs if l["entity_id"] == str(user_id)]
    assert "user.create" in actions
    assert "user.update_rate" in actions
    rate_log = next(l for l in logs if l["action"] == "user.update_rate" and l["entity_id"] == str(user_id))
    assert "18.0" in rate_log["summary"]
    assert rate_log["actor_username"] == "manager"


def test_user_delete_logged_with_snapshot_username(client, manager_headers, temp_db):
    r = client.post("/users", headers=manager_headers, json={
        "username": "doomed_audit", "password": "somepass123", "role": "intern"
    })
    user_id = r.json()["id"]
    client.delete(f"/users/{user_id}", headers=manager_headers)

    logs = _latest_log(client, manager_headers, entity_type="user", action="user.delete")
    entry = next(l for l in logs if l["entity_id"] == str(user_id))
    assert "doomed_audit" in entry["summary"]


def test_clock_in_and_out_are_logged(client, intern_headers, manager_headers):
    client.post("/clock/in", headers=intern_headers)
    client.post("/clock/out", headers=intern_headers)
    logs = _latest_log(client, manager_headers, entity_type="clock_session")
    actions = {l["action"] for l in logs}
    assert "clock_session.create" in actions
    assert "clock_session.update" in actions


def test_manager_correction_is_logged(client, intern_headers, manager_headers):
    client.post("/clock/in", headers=intern_headers)
    session_id = client.get("/clock/active", headers=intern_headers).json()["id"]
    client.patch(f"/clock-sessions/{session_id}", headers=manager_headers, json={
        "clocked_in_at": "2026-08-20T09:00:00", "clocked_out_at": "2026-08-20T13:00:00"
    })
    logs = _latest_log(client, manager_headers, action="clock_session.correct")
    assert logs and logs[0]["actor_username"] == "manager"


def test_meeting_create_and_cancel_are_logged(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json={
        "title": "Audit sync", "date": "2026-09-01", "start_time": "10:00", "end_time": "10:30",
        "location_type": "online", "attendee_ids": []
    })
    meeting_id = r.json()["id"]
    client.delete(f"/meetings/{meeting_id}", headers=manager_headers)

    logs = _latest_log(client, manager_headers, entity_type="meeting")
    actions = [l["action"] for l in logs if l["entity_id"] == str(meeting_id)]
    assert "meeting.create" in actions
    assert "meeting.cancel" in actions


def test_recurring_series_logs_one_entry_not_one_per_occurrence(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json={
        "title": "Recurring audit sync", "date": "2026-09-01", "start_time": "10:00", "end_time": "10:30",
        "location_type": "online", "attendee_ids": [], "recurrence": "weekly", "recurrence_until": "2026-09-22"
    })
    group_id = r.json()["recurrence_group_id"]
    logs = _latest_log(client, manager_headers, action="meeting.create")
    matching = [l for l in logs if l["entity_id"] == group_id]
    assert len(matching) == 1
    assert "4 occurrences" in matching[0]["summary"]


def test_filter_by_entity_type_excludes_others(client, intern_headers, manager_headers):
    client.post("/entries", headers=intern_headers, json={
        "date": "2026-08-20", "activity": "Filter test", "category": "Other", "hours": 1.0, "force": False
    })
    client.post("/teams", headers=manager_headers, json={"name": "Filter Team"})

    logs = _latest_log(client, manager_headers, entity_type="team")
    assert all(l["entity_type"] == "team" for l in logs)
    assert any(l["action"] == "team.create" for l in logs)


def test_pagination_and_total_count_header(client, intern_headers, manager_headers):
    for i in range(5):
        client.post("/entries", headers=intern_headers, json={
            "date": "2026-08-20", "activity": f"Page test {i}", "category": "Other", "hours": 1.0, "force": False
        })
    r = client.get("/audit-log?entity_type=entry&limit=2&offset=0", headers=manager_headers)
    assert r.status_code == 200
    assert len(r.json()) == 2
    assert int(r.headers["X-Total-Count"]) == 5