"""
Shared fixtures for the whole test suite.

Key design decisions, worth knowing before adding more test files:

1. Required env vars (JWT_SECRET_KEY, TOKEN_ENCRYPTION_KEY) are set at
   MODULE level, before anything else is imported — auth.py and
   crypto_utils.py both raise RuntimeError at import time if these are
   missing, so they must exist before `main` (or anything that imports
   `main`) is ever imported anywhere in the test suite. pytest guarantees
   conftest.py in a directory loads before test files in that directory,
   so this ordering is safe as long as no test file sets these itself.

2. Each test gets its own temp SQLite file (via the `client` fixture,
   through `temp_db`) — full isolation, no shared state between tests,
   nothing to clean up afterward (pytest's tmp_path fixture removes it
   automatically).

3. `client` deliberately does NOT use `TestClient(app)` as a context
   manager. Using `with TestClient(app) as c:` triggers main.py's FastAPI
   startup event, which calls reminders.start_scheduler() and
   clock_auto_close.start_scheduler() — those acquire an OS-level
   singleton lock (process_lock.py) that, once taken, is held for the
   rest of THIS PYTHON PROCESS (not just one test), since nothing
   explicitly releases it between tests. Every test after the first would
   then silently skip starting its own scheduler. That's not a bug — it's
   process_lock.py correctly doing its job — but it's irrelevant to what
   these tests check and would just be noise. Calling database.init_db()
   directly in the temp_db fixture covers the one thing from startup()
   that tests actually need, without ever touching the scheduler machinery
   at all.
"""
import os

os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-not-for-production-use")
os.environ.setdefault("LOGIN_RATE_LIMIT_ENABLED", "false")  # most tests shouldn't have to fight this; test_auth.py enables it explicitly where needed

from cryptography.fernet import Fernet
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())

import sys
from pathlib import Path
# Project layout is: <root>/backend/main.py (etc.) and <root>/tests/conftest.py
# as SIBLING folders — not tests/ nested inside backend/. So the path to
# add is parent.parent/"backend" (root, then into backend), not just
# parent.parent (which would land on <root> itself, where main.py/
# database.py don't directly live).
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from fastapi.testclient import TestClient

import database


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Points database.DB_PATH at a fresh temp file for this test only,
    then builds a clean schema in it. get_connection() looks up DB_PATH as
    a module global at call time (not captured at import time), so this
    monkeypatch correctly redirects every part of the app — main.py,
    auth.py, rate_limit.py, everything — to this test's isolated file."""
    db_path = tmp_path / "test_timesheet.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    database.init_db()
    yield db_path


@pytest.fixture()
def client(temp_db):
    """A TestClient against a fresh, isolated database. See module
    docstring for why this is NOT used as a context manager."""
    import main
    return TestClient(main.app)


@pytest.fixture()
def manager_headers(client):
    r = client.post("/auth/login", json={"username": "manager", "password": "manager123"})
    assert r.status_code == 200, f"manager login fixture failed: {r.text}"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def intern_headers(client):
    r = client.post("/auth/login", json={"username": "intern", "password": "intern123"})
    assert r.status_code == 200, f"intern login fixture failed: {r.text}"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}