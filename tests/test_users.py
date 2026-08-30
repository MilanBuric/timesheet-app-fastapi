"""
User, team, and room administration tests — manager-only surfaces for
creating/deleting people, profile/rate updates, and the team + room
directories used when scheduling meetings.
"""
import sqlite3


def _create_intern(client, manager_headers, username="new_intern", **overrides):
    body = {"username": username, "password": "somepass123", "role": "intern"}
    body.update(overrides)
    return client.post("/users", headers=manager_headers, json=body)


# ── Users ─────────────────────────────────────────────────────────────────

def test_manager_can_list_users(client, manager_headers):
    r = client.get("/users", headers=manager_headers)
    assert r.status_code == 200
    usernames = {u["username"] for u in r.json()}
    assert {"intern", "manager"}.issubset(usernames)


def test_intern_cannot_list_users(client, intern_headers):
    r = client.get("/users", headers=intern_headers)
    assert r.status_code == 403


def test_create_user_success(client, manager_headers):
    r = _create_intern(client, manager_headers, username="alice")
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "alice"
    assert body["role"] == "intern"


def test_create_user_duplicate_username_rejected(client, manager_headers):
    _create_intern(client, manager_headers, username="bob")
    r = _create_intern(client, manager_headers, username="bob")
    assert r.status_code == 409


def test_create_user_with_nonexistent_team_rejected(client, manager_headers):
    r = _create_intern(client, manager_headers, username="carol", team_id=999)
    assert r.status_code == 404


def test_intern_cannot_create_user(client, intern_headers):
    r = client.post("/users", headers=intern_headers, json={
        "username": "x", "password": "somepass123", "role": "intern"
    })
    assert r.status_code == 403


def test_delete_user_cascades_entries_and_clock_sessions(client, manager_headers, temp_db):
    r = _create_intern(client, manager_headers, username="doomed")
    user_id = r.json()["id"]
    login_r = client.post("/auth/login", json={"username": "doomed", "password": "somepass123"})
    doomed_headers = {"Authorization": f"Bearer {login_r.json()['access_token']}"}

    client.post("/entries", headers=doomed_headers, json={
        "date": "2026-08-20", "activity": "Doomed entry", "category": "Other", "hours": 1.0, "force": False
    })
    client.post("/clock/in", headers=doomed_headers)

    r = client.delete(f"/users/{user_id}", headers=manager_headers)
    assert r.status_code == 204

    conn = sqlite3.connect(temp_db)
    entry_count = conn.execute("SELECT COUNT(*) FROM entries WHERE user_id = ?", (user_id,)).fetchone()[0]
    session_count = conn.execute("SELECT COUNT(*) FROM clock_sessions WHERE user_id = ?", (user_id,)).fetchone()[0]
    conn.close()
    assert entry_count == 0
    assert session_count == 0


def test_manager_cannot_delete_self(client, manager_headers):
    me = client.get("/auth/me", headers=manager_headers).json()
    r = client.delete(f"/users/{me['id']}", headers=manager_headers)
    assert r.status_code == 400


def test_delete_nonexistent_user_404(client, manager_headers):
    r = client.delete("/users/999999", headers=manager_headers)
    assert r.status_code == 404


def test_intern_cannot_delete_user(client, intern_headers, manager_headers):
    r = _create_intern(client, manager_headers, username="victim")
    user_id = r.json()["id"]
    r = client.delete(f"/users/{user_id}", headers=intern_headers)
    assert r.status_code == 403


def test_basic_users_visible_to_intern(client, intern_headers):
    """The attendee picker needs this list too, not just managers."""
    r = client.get("/users/basic", headers=intern_headers)
    assert r.status_code == 200
    assert any(u["username"] == "manager" for u in r.json())


def test_update_user_profile(client, manager_headers):
    r = _create_intern(client, manager_headers, username="dave")
    user_id = r.json()["id"]
    r = client.patch(f"/users/{user_id}/profile", headers=manager_headers, json={"title": "QA Intern"})
    assert r.status_code == 200
    assert r.json()["title"] == "QA Intern"


def test_update_profile_nonexistent_team_404(client, manager_headers):
    r = _create_intern(client, manager_headers, username="erin")
    user_id = r.json()["id"]
    r = client.patch(f"/users/{user_id}/profile", headers=manager_headers, json={"team_id": 999})
    assert r.status_code == 404


def test_update_profile_nonexistent_user_404(client, manager_headers):
    r = client.patch("/users/999999/profile", headers=manager_headers, json={"title": "Ghost"})
    assert r.status_code == 404


def test_set_hourly_rate(client, manager_headers):
    r = _create_intern(client, manager_headers, username="frank")
    user_id = r.json()["id"]
    r = client.patch(f"/users/{user_id}/rate", headers=manager_headers, json={"hourly_rate": 22.5})
    assert r.status_code == 200
    assert r.json()["hourly_rate"] == 22.5


def test_intern_cannot_set_own_rate(client, intern_headers):
    r = client.patch("/users/1/rate", headers=intern_headers, json={"hourly_rate": 999})
    assert r.status_code == 403


# ── Teams ─────────────────────────────────────────────────────────────────

def test_create_and_list_teams(client, manager_headers):
    r = client.post("/teams", headers=manager_headers, json={"name": "Platform"})
    assert r.status_code == 201
    r = client.get("/teams", headers=manager_headers)
    assert "Platform" in {t["name"] for t in r.json()}


def test_create_duplicate_team_rejected(client, manager_headers):
    client.post("/teams", headers=manager_headers, json={"name": "Growth"})
    r = client.post("/teams", headers=manager_headers, json={"name": "Growth"})
    assert r.status_code == 409


def test_rename_team_conflict_rejected(client, manager_headers):
    client.post("/teams", headers=manager_headers, json={"name": "Design"})
    r2 = client.post("/teams", headers=manager_headers, json={"name": "Data"})
    team_id = r2.json()["id"]
    r = client.patch(f"/teams/{team_id}", headers=manager_headers, json={"name": "Design"})
    assert r.status_code == 409


def test_rename_nonexistent_team_404(client, manager_headers):
    r = client.patch("/teams/999999", headers=manager_headers, json={"name": "Whatever"})
    assert r.status_code == 404


def test_delete_team_unassigns_members_instead_of_blocking(client, manager_headers):
    team_r = client.post("/teams", headers=manager_headers, json={"name": "Temp Team"})
    team_id = team_r.json()["id"]
    user_r = _create_intern(client, manager_headers, username="grace", team_id=team_id)
    user_id = user_r.json()["id"]

    r = client.delete(f"/teams/{team_id}", headers=manager_headers)
    assert r.status_code == 204

    users = client.get("/users", headers=manager_headers).json()
    grace = next(u for u in users if u["id"] == user_id)
    assert grace["team_id"] is None


# ── Rooms ─────────────────────────────────────────────────────────────────

def test_create_and_list_rooms(client, manager_headers):
    r = client.post("/rooms", headers=manager_headers, json={"name": "Room A", "capacity": 6})
    assert r.status_code == 201
    r = client.get("/rooms", headers=manager_headers)
    assert "Room A" in {rm["name"] for rm in r.json()}


def test_create_duplicate_room_rejected(client, manager_headers):
    client.post("/rooms", headers=manager_headers, json={"name": "Room B"})
    r = client.post("/rooms", headers=manager_headers, json={"name": "Room B"})
    assert r.status_code == 409


def test_update_room_partial_keeps_other_fields(client, manager_headers):
    r = client.post("/rooms", headers=manager_headers, json={"name": "Room C", "capacity": 4, "equipment": "TV"})
    room_id = r.json()["id"]
    r = client.patch(f"/rooms/{room_id}", headers=manager_headers, json={"capacity": 8})
    assert r.status_code == 200
    body = r.json()
    assert body["capacity"] == 8
    assert body["equipment"] == "TV"  # untouched field preserved


def test_update_nonexistent_room_404(client, manager_headers):
    r = client.patch("/rooms/999999", headers=manager_headers, json={"capacity": 2})
    assert r.status_code == 404


def test_room_occupancy_shows_bookings_in_range(client, manager_headers):
    client.post("/rooms", headers=manager_headers, json={"name": "Room D"})
    r = client.post("/meetings", headers=manager_headers, json={
        "title": "Standup", "date": "2026-09-01", "start_time": "09:00", "end_time": "09:30",
        "location_type": "in_person", "room": "Room D", "attendee_ids": []
    })
    assert r.status_code == 201
    room_id = next(rm["id"] for rm in client.get("/rooms", headers=manager_headers).json() if rm["name"] == "Room D")
    r = client.get(f"/rooms/{room_id}/occupancy", headers=manager_headers)
    assert r.status_code == 200
    assert any(slot["title"] == "Standup" for slot in r.json())


def test_room_occupancy_nonexistent_room_404(client, manager_headers):
    r = client.get("/rooms/999999/occupancy", headers=manager_headers)
    assert r.status_code == 404