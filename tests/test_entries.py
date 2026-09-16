"""
Entry tests — covers CRUD, role-based permissions, and duplicate
detection including a real concurrency test for the race condition fixed
this session (check-then-insert -> atomic DB-level uniqueness).
"""
from concurrent.futures import ThreadPoolExecutor, as_completed


def _entry_body(**overrides):
    body = {"date": "2026-08-20", "activity": "Test activity", "category": "Other", "hours": 2.0, "force": False}
    body.update(overrides)
    return body


# ── Basic CRUD ───────────────────────────────────────────────────────────

def test_create_entry(client, intern_headers):
    r = client.post("/entries", headers=intern_headers, json=_entry_body())
    assert r.status_code == 201
    body = r.json()
    assert body["activity"] == "Test activity"
    assert body["status"] == "pending"


def test_intern_sees_only_own_entries(client, intern_headers, manager_headers):
    client.post("/entries", headers=intern_headers, json=_entry_body(activity="Intern's entry"))
    # Create a second intern and have them log an entry too
    client.post("/users", headers=manager_headers, json={"username": "other_intern", "password": "otherpass123", "role": "intern"})
    r = client.post("/auth/login", json={"username": "other_intern", "password": "otherpass123"})
    other_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.post("/entries", headers=other_headers, json=_entry_body(activity="Other intern's entry"))

    r = client.get("/entries", headers=intern_headers)
    activities = {e["activity"] for e in r.json()}
    assert "Intern's entry" in activities
    assert "Other intern's entry" not in activities


def test_manager_sees_all_entries(client, intern_headers, manager_headers):
    client.post("/entries", headers=intern_headers, json=_entry_body(activity="Visible to manager"))
    r = client.get("/entries", headers=manager_headers)
    activities = {e["activity"] for e in r.json()}
    assert "Visible to manager" in activities


def test_update_own_entry(client, intern_headers):
    r = client.post("/entries", headers=intern_headers, json=_entry_body())
    entry_id = r.json()["id"]
    r = client.patch(f"/entries/{entry_id}", headers=intern_headers, json={"hours": 4.5})
    assert r.status_code == 200
    assert r.json()["hours"] == 4.5


def test_cannot_update_someone_elses_entry(client, intern_headers, manager_headers):
    client.post("/users", headers=manager_headers, json={"username": "other_intern2", "password": "otherpass123", "role": "intern"})
    r = client.post("/auth/login", json={"username": "other_intern2", "password": "otherpass123"})
    other_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.post("/entries", headers=other_headers, json=_entry_body())
    entry_id = r.json()["id"]

    r = client.patch(f"/entries/{entry_id}", headers=intern_headers, json={"hours": 1.0})
    assert r.status_code == 403


def test_delete_own_entry(client, intern_headers):
    r = client.post("/entries", headers=intern_headers, json=_entry_body())
    entry_id = r.json()["id"]
    r = client.delete(f"/entries/{entry_id}", headers=intern_headers)
    assert r.status_code == 204
    r = client.get("/entries", headers=intern_headers)
    assert entry_id not in [e["id"] for e in r.json()]


def test_manager_approve_entry(client, intern_headers, manager_headers):
    r = client.post("/entries", headers=intern_headers, json=_entry_body())
    entry_id = r.json()["id"]
    r = client.post(f"/entries/{entry_id}/approve", headers=manager_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


def test_intern_cannot_approve(client, intern_headers):
    r = client.post("/entries", headers=intern_headers, json=_entry_body())
    entry_id = r.json()["id"]
    r = client.post(f"/entries/{entry_id}/approve", headers=intern_headers)
    assert r.status_code == 403


def test_manager_reject_requires_reason(client, intern_headers, manager_headers):
    r = client.post("/entries", headers=intern_headers, json=_entry_body())
    entry_id = r.json()["id"]
    r = client.post(f"/entries/{entry_id}/reject", headers=manager_headers, json={"reason": "Needs more detail"})
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["rejection_reason"] == "Needs more detail"


# ── Duplicate detection ──────────────────────────────────────────────────

def test_sequential_duplicate_rejected(client, intern_headers):
    r1 = client.post("/entries", headers=intern_headers, json=_entry_body(activity="Dup test"))
    assert r1.status_code == 201
    r2 = client.post("/entries", headers=intern_headers, json=_entry_body(activity="Dup test"))
    assert r2.status_code == 409


def test_force_bypasses_duplicate_check(client, intern_headers):
    r1 = client.post("/entries", headers=intern_headers, json=_entry_body(activity="Forced dup"))
    assert r1.status_code == 201
    r2 = client.post("/entries", headers=intern_headers, json=_entry_body(activity="Forced dup", force=True))
    assert r2.status_code == 201, "force=true must allow an intentional duplicate through"


def test_different_activity_same_day_not_a_duplicate(client, intern_headers):
    r1 = client.post("/entries", headers=intern_headers, json=_entry_body(activity="Morning standup"))
    r2 = client.post("/entries", headers=intern_headers, json=_entry_body(activity="Afternoon coding"))
    assert r1.status_code == 201
    assert r2.status_code == 201


def test_concurrent_duplicate_submissions_race(client, intern_headers):
    """Regression test for the exact race fixed this session: fires 10
    genuinely simultaneous identical submissions and confirms only one
    succeeds — proving the fix is a real DB-level guarantee, not just a
    check that happens to usually win in practice."""
    body = _entry_body(activity="RACE TEST", date="2026-08-21")

    def submit():
        return client.post("/entries", headers=intern_headers, json=body).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = [f.result() for f in as_completed([pool.submit(submit) for _ in range(10)])]

    assert results.count(201) == 1, f"expected exactly 1 success, got {results.count(201)}"
    assert results.count(409) == 9, f"expected exactly 9 conflicts, got {results.count(409)}"


# ── Overtime flag ─────────────────────────────────────────────────────────

def test_overtime_flag_based_on_clocked_hours_not_entry_hours(client, intern_headers):
    """Entries.hours is informational only — overtime is driven purely by
    actual clocked time, per the app's own documented design."""
    r = client.post("/entries", headers=intern_headers, json=_entry_body(hours=20.0))
    assert r.status_code == 201
    # No clock session exists for this date, so this must NOT be flagged
    # as overtime even though the logged hours (20) exceed 8.
    assert r.json()["overtime"] is False

# ── Status filter ──────────────────────────────────────────────────────────

def test_entries_status_filter(client, intern_headers, manager_headers):
    r1 = client.post("/entries", headers=intern_headers, json=_entry_body(activity="Stays pending"))
    r2 = client.post("/entries", headers=intern_headers, json=_entry_body(activity="Gets approved", date="2026-08-21"))
    client.post(f"/entries/{r2.json()['id']}/approve", headers=manager_headers)

    r = client.get("/entries?status=pending", headers=manager_headers)
    activities = {e["activity"] for e in r.json()}
    assert "Stays pending" in activities
    assert "Gets approved" not in activities

    r = client.get("/entries?status=approved", headers=manager_headers)
    activities = {e["activity"] for e in r.json()}
    assert "Gets approved" in activities
    assert "Stays pending" not in activities