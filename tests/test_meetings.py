"""
Meeting scheduling tests — covers recurring series generation, the room
hard-block vs attendee soft-conflict distinction the app is built around,
RSVP (both in-app and the no-login email-link flow), and cancel/reschedule
permissions.
"""
import sqlite3


def _online_meeting(**overrides):
    body = {
        "title": "Sync", "date": "2026-09-01", "start_time": "10:00", "end_time": "10:30",
        "location_type": "online", "attendee_ids": [],
    }
    body.update(overrides)
    return body


def _in_person_meeting(room, **overrides):
    body = {
        "title": "Planning", "date": "2026-09-01", "start_time": "10:00", "end_time": "11:00",
        "location_type": "in_person", "room": room, "attendee_ids": [],
    }
    body.update(overrides)
    return body


def _create_second_intern(client, manager_headers, username="intern2"):
    client.post("/users", headers=manager_headers, json={"username": username, "password": "somepass123", "role": "intern"})
    r = client.post("/auth/login", json={"username": username, "password": "somepass123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── Creation basics ──────────────────────────────────────────────────────

def test_create_online_meeting(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting())
    assert r.status_code == 201
    assert r.json()["location_type"] == "online"


def test_end_before_start_rejected(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(start_time="11:00", end_time="10:00"))
    assert r.status_code == 400


def test_in_person_requires_room(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(location_type="in_person"))
    assert r.status_code == 400


def test_recurring_without_until_rejected(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(recurrence="weekly"))
    assert r.status_code == 400


def test_meeting_invites_attendee(client, manager_headers, intern_headers):
    intern_id = client.get("/auth/me", headers=intern_headers).json()["id"]
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(attendee_ids=[intern_id]))
    assert r.status_code == 201
    attendees = r.json()["attendees"]
    assert any(a["id"] == intern_id and a["status"] == "pending" for a in attendees)


# ── Room conflicts: hard block ──────────────────────────────────────────

def test_room_double_booking_rejected(client, manager_headers):
    client.post("/rooms", headers=manager_headers, json={"name": "Alpha"})
    r1 = client.post("/meetings", headers=manager_headers, json=_in_person_meeting("Alpha"))
    assert r1.status_code == 201
    r2 = client.post("/meetings", headers=manager_headers, json=_in_person_meeting(
        "Alpha", start_time="10:30", end_time="11:30"))  # overlaps r1's 10:00-11:00
    assert r2.status_code == 409


def test_non_overlapping_room_booking_allowed(client, manager_headers):
    client.post("/rooms", headers=manager_headers, json={"name": "Beta"})
    r1 = client.post("/meetings", headers=manager_headers, json=_in_person_meeting("Beta"))
    assert r1.status_code == 201
    r2 = client.post("/meetings", headers=manager_headers, json=_in_person_meeting(
        "Beta", start_time="11:00", end_time="12:00"))  # starts exactly when r1 ends
    assert r2.status_code == 201


def test_room_under_renovation_rejected(client, manager_headers):
    client.post("/rooms", headers=manager_headers, json={"name": "Gamma", "status": "renovation"})
    r = client.post("/meetings", headers=manager_headers, json=_in_person_meeting("Gamma"))
    assert r.status_code == 400


# ── Attendee conflicts: soft warning, never blocking ─────────────────────

def test_attendee_double_booking_warns_but_does_not_block(client, manager_headers, intern_headers):
    intern_id = client.get("/auth/me", headers=intern_headers).json()["id"]
    r1 = client.post("/meetings", headers=manager_headers, json=_online_meeting(attendee_ids=[intern_id]))
    assert r1.status_code == 201
    r2 = client.post("/meetings", headers=manager_headers, json=_online_meeting(
        title="Overlapping", attendee_ids=[intern_id]))  # same date/time as r1
    assert r2.status_code == 201, "attendee conflicts must never block creation, only warn"
    assert r2.json()["attendee_conflicts"], "the overlap should still be surfaced as a warning"


def test_declined_invite_does_not_count_as_conflict(client, manager_headers, intern_headers):
    intern_id = client.get("/auth/me", headers=intern_headers).json()["id"]
    r1 = client.post("/meetings", headers=manager_headers, json=_online_meeting(attendee_ids=[intern_id]))
    meeting_id = r1.json()["id"]
    client.post(f"/meetings/{meeting_id}/rsvp", headers=intern_headers, json={"status": "declined"})

    r2 = client.post("/meetings", headers=manager_headers, json=_online_meeting(
        title="No conflict now", attendee_ids=[intern_id]))
    assert r2.json()["attendee_conflicts"] is None


# ── Recurrence ────────────────────────────────────────────────────────────

def test_weekly_recurrence_creates_expected_occurrences(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(
        recurrence="weekly", recurrence_until="2026-09-22"))  # 09-01, 08, 15, 22
    assert r.status_code == 201
    assert r.json()["series_count"] == 4


def test_recurrence_cap_rejected(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(
        recurrence="daily", recurrence_until="2027-06-01"))  # far more than the 52-occurrence cap
    assert r.status_code == 400


def test_recurring_series_skips_conflicting_occurrence_but_creates_rest(client, manager_headers):
    client.post("/rooms", headers=manager_headers, json={"name": "Delta"})
    # Pre-book just the occurrence that would fall on 2026-09-15
    client.post("/meetings", headers=manager_headers, json=_in_person_meeting("Delta", date="2026-09-15"))
    r = client.post("/meetings", headers=manager_headers, json=_in_person_meeting(
        "Delta", recurrence="weekly", recurrence_until="2026-09-22"))
    assert r.status_code == 201
    assert "2026-09-15" in r.json()["skipped_dates"]
    assert r.json()["series_count"] == 3  # 09-01, 09-08, 09-22 (09-15 skipped)


# ── Visibility ────────────────────────────────────────────────────────────

def test_manager_sees_all_meetings_intern_sees_only_own(client, manager_headers, intern_headers):
    other_headers = _create_second_intern(client, manager_headers)
    client.post("/meetings", headers=other_headers, json=_online_meeting(title="Not mine"))
    client.post("/meetings", headers=intern_headers, json=_online_meeting(title="Mine"))

    r = client.get("/meetings", headers=intern_headers)
    titles = {m["title"] for m in r.json()}
    assert "Mine" in titles
    assert "Not mine" not in titles

    r = client.get("/meetings", headers=manager_headers)
    titles = {m["title"] for m in r.json()}
    assert {"Mine", "Not mine"}.issubset(titles)


def test_meeting_search_ignores_date_filter(client, manager_headers):
    client.post("/meetings", headers=manager_headers, json=_online_meeting(title="Findable Sync", date="2026-12-25"))
    r = client.get("/meetings?search=Findable&date_from=2026-01-01&date_to=2026-01-31", headers=manager_headers)
    assert any(m["title"] == "Findable Sync" for m in r.json())


# ── RSVP ──────────────────────────────────────────────────────────────────

def test_invitee_can_accept(client, manager_headers, intern_headers):
    intern_id = client.get("/auth/me", headers=intern_headers).json()["id"]
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(attendee_ids=[intern_id]))
    meeting_id = r.json()["id"]
    r = client.post(f"/meetings/{meeting_id}/rsvp", headers=intern_headers, json={"status": "accepted"})
    assert r.status_code == 200
    me = next(a for a in r.json()["attendees"] if a["id"] == intern_id)
    assert me["status"] == "accepted"


def test_decline_reason_stored_only_on_decline(client, manager_headers, intern_headers):
    intern_id = client.get("/auth/me", headers=intern_headers).json()["id"]
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(attendee_ids=[intern_id]))
    meeting_id = r.json()["id"]
    r = client.post(f"/meetings/{meeting_id}/rsvp", headers=intern_headers,
                     json={"status": "declined", "reason": "Conflict"})
    me = next(a for a in r.json()["attendees"] if a["id"] == intern_id)
    assert me["status"] == "declined"
    assert me["decline_reason"] == "Conflict"


def test_non_invitee_cannot_rsvp(client, manager_headers, intern_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting())
    meeting_id = r.json()["id"]
    r = client.post(f"/meetings/{meeting_id}/rsvp", headers=intern_headers, json={"status": "accepted"})
    assert r.status_code == 404


def test_rsvp_via_email_link(client, manager_headers, intern_headers, temp_db):
    intern_id = client.get("/auth/me", headers=intern_headers).json()["id"]
    client.post("/meetings", headers=manager_headers, json=_online_meeting(attendee_ids=[intern_id]))
    conn = sqlite3.connect(temp_db)
    token = conn.execute("SELECT rsvp_token FROM meeting_attendees WHERE user_id = ?", (intern_id,)).fetchone()[0]
    conn.close()

    r = client.get(f"/meetings/rsvp?token={token}&action=accept")
    assert r.status_code == 200
    assert "you're in" in r.text.lower()

    conn = sqlite3.connect(temp_db)
    status = conn.execute("SELECT status FROM meeting_attendees WHERE user_id = ?", (intern_id,)).fetchone()[0]
    conn.close()
    assert status == "accepted"


def test_rsvp_link_invalid_token_returns_404(client):
    r = client.get("/meetings/rsvp?token=not-a-real-token&action=accept")
    assert r.status_code == 404


def test_rsvp_link_invalid_action_returns_400(client):
    r = client.get("/meetings/rsvp?token=whatever&action=maybe")
    assert r.status_code == 400


def test_rsvp_series_token_applies_to_every_occurrence(client, manager_headers, intern_headers, temp_db):
    intern_id = client.get("/auth/me", headers=intern_headers).json()["id"]
    client.post("/meetings", headers=manager_headers, json=_online_meeting(
        recurrence="weekly", recurrence_until="2026-09-22", attendee_ids=[intern_id]))
    conn = sqlite3.connect(temp_db)
    token = conn.execute(
        "SELECT rsvp_token FROM meeting_attendees WHERE user_id = ? LIMIT 1", (intern_id,)
    ).fetchone()[0]
    conn.close()

    client.get(f"/meetings/rsvp?token={token}&action=decline")

    conn = sqlite3.connect(temp_db)
    statuses = [row[0] for row in conn.execute(
        "SELECT status FROM meeting_attendees WHERE user_id = ?", (intern_id,)
    ).fetchall()]
    conn.close()
    assert statuses and all(s == "declined" for s in statuses), \
        "one click on a series token should apply to every occurrence"


# ── Cancel / reschedule permissions ───────────────────────────────────────

def test_organizer_can_cancel_own_meeting(client, intern_headers):
    r = client.post("/meetings", headers=intern_headers, json=_online_meeting())
    meeting_id = r.json()["id"]
    r = client.delete(f"/meetings/{meeting_id}", headers=intern_headers)
    assert r.status_code == 204


def test_non_organizer_cannot_cancel(client, manager_headers, intern_headers):
    other_headers = _create_second_intern(client, manager_headers)
    r = client.post("/meetings", headers=other_headers, json=_online_meeting())
    meeting_id = r.json()["id"]
    r = client.delete(f"/meetings/{meeting_id}", headers=intern_headers)
    assert r.status_code == 403


def test_manager_can_cancel_anyones_meeting(client, manager_headers, intern_headers):
    r = client.post("/meetings", headers=intern_headers, json=_online_meeting())
    meeting_id = r.json()["id"]
    r = client.delete(f"/meetings/{meeting_id}", headers=manager_headers)
    assert r.status_code == 204


def test_cancel_series_removes_every_occurrence(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(
        recurrence="weekly", recurrence_until="2026-09-22"))
    group_id = r.json()["recurrence_group_id"]
    r = client.delete(f"/meetings/series/{group_id}", headers=manager_headers)
    assert r.status_code == 204
    remaining = client.get("/meetings?date_from=2026-09-01&date_to=2026-09-30", headers=manager_headers).json()
    assert not any(m.get("recurrence_group_id") == group_id for m in remaining)


def test_reschedule_resets_rsvp_to_pending_and_bumps_sequence(client, manager_headers, intern_headers):
    intern_id = client.get("/auth/me", headers=intern_headers).json()["id"]
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting(attendee_ids=[intern_id]))
    meeting_id = r.json()["id"]
    client.post(f"/meetings/{meeting_id}/rsvp", headers=intern_headers, json={"status": "accepted"})

    r = client.patch(f"/meetings/{meeting_id}/reschedule", headers=manager_headers, json={
        "date": "2026-09-05", "start_time": "14:00", "end_time": "14:30"
    })
    assert r.status_code == 200
    body = r.json()
    assert body["ics_sequence"] == 1
    me = next(a for a in body["attendees"] if a["id"] == intern_id)
    assert me["status"] == "pending"


def test_reschedule_end_before_start_rejected(client, manager_headers):
    r = client.post("/meetings", headers=manager_headers, json=_online_meeting())
    meeting_id = r.json()["id"]
    r = client.patch(f"/meetings/{meeting_id}/reschedule", headers=manager_headers, json={
        "date": "2026-09-05", "start_time": "14:00", "end_time": "13:00"
    })
    assert r.status_code == 400


def test_reschedule_into_room_conflict_rejected(client, manager_headers):
    client.post("/rooms", headers=manager_headers, json={"name": "Echo"})
    client.post("/meetings", headers=manager_headers, json=_in_person_meeting("Echo", date="2026-09-10"))
    r = client.post("/meetings", headers=manager_headers, json=_in_person_meeting("Echo", date="2026-09-11"))
    meeting_id = r.json()["id"]
    r = client.patch(f"/meetings/{meeting_id}/reschedule", headers=manager_headers, json={
        "date": "2026-09-10", "start_time": "10:00", "end_time": "11:00"
    })
    assert r.status_code == 409


def test_nonexistent_meeting_reschedule_404(client, manager_headers):
    r = client.patch("/meetings/999999/reschedule", headers=manager_headers, json={
        "date": "2026-09-05", "start_time": "10:00", "end_time": "11:00"
    })
    assert r.status_code == 404