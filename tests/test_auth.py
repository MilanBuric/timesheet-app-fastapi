"""
Auth tests — covers login, rate limiting, and the forgot/reset password
flow. Rate limiting tests exist specifically to turn this session's manual
verification (10 concurrent writes, the 3-attempt lockout, the login
message bug) into something that stays proven automatically, not just
proven once in a chat transcript.
"""
from datetime import datetime, timedelta


# ── Basic login ──────────────────────────────────────────────────────────

def test_login_success_manager(client):
    r = client.post("/auth/login", json={"username": "manager", "password": "manager123"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "manager"
    assert body["username"] == "manager"
    assert body["access_token"]


def test_login_success_intern(client):
    r = client.post("/auth/login", json={"username": "intern", "password": "intern123"})
    assert r.status_code == 200
    assert r.json()["role"] == "intern"


def test_login_wrong_password(client):
    r = client.post("/auth/login", json={"username": "intern", "password": "wrongpassword"})
    assert r.status_code == 401


def test_login_unknown_username(client):
    r = client.post("/auth/login", json={"username": "nobody", "password": "whatever123"})
    assert r.status_code == 401


def test_token_actually_authenticates(client, intern_headers):
    r = client.get("/auth/me", headers=intern_headers)
    assert r.status_code == 200
    assert r.json()["username"] == "intern"


def test_no_token_rejected(client):
    r = client.get("/auth/me")
    assert r.status_code == 401


# ── Rate limiting: OFF by default ───────────────────────────────────────
# LOGIN_RATE_LIMIT_ENABLED is unset in these tests (conftest.py defaults
# it to "false"), so behavior here must be byte-for-byte the same as
# before rate limiting existed at all.

def test_rate_limiting_off_never_locks_out(client):
    for _ in range(10):
        r = client.post("/auth/login", json={"username": "intern", "password": "wrongpassword"})
        assert r.status_code == 401, "should always be a plain 401 with rate limiting off, never 429"
    # correct password still works fine after 10 failures
    r = client.post("/auth/login", json={"username": "intern", "password": "intern123"})
    assert r.status_code == 200


def test_rate_limiting_off_message_unchanged(client):
    r = client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    assert r.json()["detail"] == "Invalid username or password"


# ── Rate limiting: ON ────────────────────────────────────────────────────

def test_lockout_after_three_failures(client, monkeypatch):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "true")
    for _ in range(3):
        r = client.post("/auth/login", json={"username": "intern", "password": "wrong"})
        assert r.status_code == 401
    r = client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    assert r.status_code == 429
    assert "minute" in r.json()["detail"]


def test_lockout_blocks_even_correct_password(client, monkeypatch):
    """The property that matters most: knowing the real password doesn't
    let you bypass an active lockout."""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "true")
    for _ in range(3):
        client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    r = client.post("/auth/login", json={"username": "intern", "password": "intern123"})
    assert r.status_code == 429, "correct password must still be blocked during an active lockout"


def test_lockout_is_per_username(client, monkeypatch):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "true")
    for _ in range(3):
        client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    # manager account must be completely unaffected by intern's lockout
    r = client.post("/auth/login", json={"username": "manager", "password": "manager123"})
    assert r.status_code == 200


def test_attempts_remaining_messages(client, monkeypatch):
    """Regression test for the exact bug caught during manual testing this
    session: the message must actually say how many attempts are left,
    not a generic string."""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "true")
    r1 = client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    assert "2 attempt(s) remaining" in r1.json()["detail"]
    r2 = client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    assert "1 attempt(s) remaining" in r2.json()["detail"]
    r3 = client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    assert "locked" in r3.json()["detail"].lower()


def test_successful_login_clears_failed_attempts(client, monkeypatch):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "true")
    client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    r = client.post("/auth/login", json={"username": "intern", "password": "intern123"})
    assert r.status_code == 200  # 2 failures shouldn't have locked anything yet
    # counter should be back to zero now — 2 MORE failures shouldn't lock either
    client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    r = client.post("/auth/login", json={"username": "intern", "password": "wrong"})
    assert r.status_code == 401, "should still be a normal 401, not locked — the earlier success should have reset the counter"


def test_lockout_expires_and_clears_itself(client, monkeypatch, temp_db):
    """A lockout that's already in the past should be treated as expired
    and cleared on the next attempt, without needing to actually wait 30
    real minutes for the test to run."""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ENABLED", "true")
    import sqlite3
    conn = sqlite3.connect(temp_db)
    past = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    conn.execute(
        "INSERT INTO login_attempts (username, attempt_count, locked_until) VALUES (?, 0, ?)",
        ("intern", past)
    )
    conn.commit()
    conn.close()

    r = client.post("/auth/login", json={"username": "intern", "password": "intern123"})
    assert r.status_code == 200, "an expired lockout should not block a login attempt"


# ── Forgot / reset password ─────────────────────────────────────────────

def test_forgot_password_generic_response_for_unknown_user(client):
    """Must not reveal whether a username exists."""
    r = client.post("/auth/forgot-password", json={"username": "totally-fake-user"})
    assert r.status_code == 200
    assert "sent" in r.json()["message"].lower()


def test_forgot_password_same_response_for_real_user_without_email(client):
    r = client.post("/auth/forgot-password", json={"username": "intern"})
    assert r.status_code == 200
    # response text must be identical either way — this IS the enumeration protection
    r2 = client.post("/auth/forgot-password", json={"username": "nonexistent-user"})
    assert r.json() == r2.json()


def test_forgot_password_issues_usable_token(client, manager_headers, temp_db):
    client.patch("/auth/me/email", headers=manager_headers, json={"email": "manager@example.com"})
    client.post("/auth/forgot-password", json={"username": "manager"})

    import sqlite3
    conn = sqlite3.connect(temp_db)
    row = conn.execute("SELECT token FROM password_reset_tokens WHERE used = 0").fetchone()
    conn.close()
    assert row is not None, "a real token should have been issued for a user with an email on file"

    token = row[0]
    r = client.post("/auth/reset-password", json={"token": token, "new_password": "brandnewpass123"})
    assert r.status_code == 200

    # old password should no longer work, new one should
    r = client.post("/auth/login", json={"username": "manager", "password": "manager123"})
    assert r.status_code == 401
    r = client.post("/auth/login", json={"username": "manager", "password": "brandnewpass123"})
    assert r.status_code == 200


def test_reset_password_rejects_invalid_token(client):
    r = client.post("/auth/reset-password", json={"token": "not-a-real-token", "new_password": "somepassword123"})
    assert r.status_code == 400


def test_reset_password_rejects_reused_token(client, manager_headers, temp_db):
    client.patch("/auth/me/email", headers=manager_headers, json={"email": "manager@example.com"})
    client.post("/auth/forgot-password", json={"username": "manager"})
    import sqlite3
    conn = sqlite3.connect(temp_db)
    token = conn.execute("SELECT token FROM password_reset_tokens WHERE used = 0").fetchone()[0]
    conn.close()

    r1 = client.post("/auth/reset-password", json={"token": token, "new_password": "firstnewpass123"})
    assert r1.status_code == 200
    r2 = client.post("/auth/reset-password", json={"token": token, "new_password": "secondnewpass123"})
    assert r2.status_code == 400, "a used token must not work a second time"


def test_forgot_password_sweeps_dead_tokens(client, manager_headers, temp_db):
    """Regression test for this session's cleanup fix — expired/used
    tokens should be swept as a side effect of the next request, not
    accumulate forever."""
    import sqlite3
    conn = sqlite3.connect(temp_db)
    past = (datetime.utcnow() - timedelta(hours=5)).isoformat()
    conn.execute(
        "INSERT INTO password_reset_tokens (token, user_id, expires_at, used) VALUES ('dead-expired', 1, ?, 0)",
        (past,)
    )
    future = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    conn.execute(
        "INSERT INTO password_reset_tokens (token, user_id, expires_at, used) VALUES ('dead-used', 1, ?, 1)",
        (future,)
    )
    conn.commit()
    conn.close()

    client.patch("/auth/me/email", headers=manager_headers, json={"email": "manager@example.com"})
    client.post("/auth/forgot-password", json={"username": "manager"})

    conn = sqlite3.connect(temp_db)
    remaining_tokens = {row[0] for row in conn.execute("SELECT token FROM password_reset_tokens").fetchall()}
    conn.close()
    assert "dead-expired" not in remaining_tokens
    assert "dead-used" not in remaining_tokens


# ── Forgot-password rate limiting ───────────────────────────────────────
# Closes the exact asymmetry noted in the handoff: /auth/login was
# rate-limited, /auth/forgot-password wasn't. ON by default (see
# rate_limit.py), so no monkeypatch is needed to enable it here — only to
# disable it, for the one test that checks the off-switch.

def test_forgot_password_rate_limited_after_threshold(client, manager_headers, temp_db):
    client.patch("/auth/me/email", headers=manager_headers, json={"email": "manager@example.com"})

    responses = [client.post("/auth/forgot-password", json={"username": "manager"}).json() for _ in range(4)]
    # The response must be byte-identical whether or not this request got
    # throttled — a different message would leak the rate-limit state.
    assert len({r["message"] for r in responses}) == 1

    import sqlite3
    conn = sqlite3.connect(temp_db)
    token_count = conn.execute("SELECT COUNT(*) FROM password_reset_tokens WHERE used = 0").fetchone()[0]
    conn.close()
    assert token_count == 3, "the 4th request should have been silently throttled, issuing no new token"


def test_forgot_password_lockout_is_per_username(client, manager_headers, temp_db):
    client.patch("/auth/me/email", headers=manager_headers, json={"email": "manager@example.com"})
    for _ in range(4):
        client.post("/auth/forgot-password", json={"username": "manager"})

    # A completely different username must be unaffected by manager's lockout.
    r = client.post("/auth/forgot-password", json={"username": "intern"})
    assert r.status_code == 200
    assert r.json()["message"] == "If that account has an email on file, a reset link has been sent."


def test_forgot_password_lockout_applies_to_unknown_usernames_too(client):
    """The counter must key on the raw username string, not on whether an
    account actually exists — otherwise a real username would eventually
    behave differently under repeated requests than a fake one, leaking
    exactly what the generic response is designed to hide."""
    responses = [client.post("/auth/forgot-password", json={"username": "totally-fake-user"}) for _ in range(4)]
    assert all(r.status_code == 200 for r in responses)
    assert len({r.json()["message"] for r in responses}) == 1


def test_forgot_password_lockout_expires_and_clears_itself(client, manager_headers, temp_db):
    """An already-expired lockout should be cleared on the next request,
    without needing to wait 30 real minutes for the test to run."""
    import sqlite3
    conn = sqlite3.connect(temp_db)
    past = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
    conn.execute(
        "INSERT INTO forgot_password_attempts (username, attempt_count, locked_until) VALUES (?, 0, ?)",
        ("manager", past)
    )
    conn.commit()
    conn.close()

    client.patch("/auth/me/email", headers=manager_headers, json={"email": "manager@example.com"})
    r = client.post("/auth/forgot-password", json={"username": "manager"})
    assert r.status_code == 200

    conn = sqlite3.connect(temp_db)
    token_count = conn.execute("SELECT COUNT(*) FROM password_reset_tokens WHERE used = 0").fetchone()[0]
    conn.close()
    assert token_count == 1, "an expired lockout should not block a fresh reset request"


def test_forgot_password_rate_limiting_can_be_disabled(client, manager_headers, monkeypatch, temp_db):
    monkeypatch.setenv("FORGOT_PASSWORD_RATE_LIMIT_ENABLED", "false")
    client.patch("/auth/me/email", headers=manager_headers, json={"email": "manager@example.com"})
    for _ in range(6):
        client.post("/auth/forgot-password", json={"username": "manager"})

    import sqlite3
    conn = sqlite3.connect(temp_db)
    token_count = conn.execute("SELECT COUNT(*) FROM password_reset_tokens WHERE used = 0").fetchone()[0]
    conn.close()
    assert token_count == 6, "with rate limiting disabled, every request should issue a token as before"


def test_password_minimum_length_enforced(client, manager_headers):
    r = client.post("/users", headers=manager_headers, json={
        "username": "shortpwtest", "password": "abc1234", "role": "intern"  # 7 chars
    })
    assert r.status_code == 422
    r = client.post("/users", headers=manager_headers, json={
        "username": "okpwtest", "password": "abcd1234", "role": "intern"  # 8 chars
    })
    assert r.status_code == 201