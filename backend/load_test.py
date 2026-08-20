"""
Load test for POST /entries under concurrent WRITES from many different
users — this is a different (and more revealing) test than hammering a
read endpoint: SQLite's WAL mode lets concurrent reads proceed freely, so
a read-only load test mostly measures nothing. Writes are different —
SQLite allows only one writer at a time; every other writer queues behind
it and waits (up to busy_timeout=5000ms, set in database.py) for the lock.
This script measures exactly that queueing, which is the real thing that
would slow down 50 people all clocking in or logging time around the same
moment — not the same thing the earlier meetings-read test measured.

Two separate, manually-triggered steps by design — nothing here chains
automatically, so you can inspect state in between:

  1. `python load_test.py run`
     Creates N temporary intern accounts (clearly tagged, see MARKER
     below), then fires one POST /entries per account, all at once, from
     an already-running server. Prints timing stats and saves the
     temporary user IDs to loadtest_state.json — nothing is deleted yet.

  2. `python load_test.py cleanup`
     Reads loadtest_state.json and deletes exactly those temporary users
     via DELETE /users/{id} — which already cascades to delete their
     entries and clock sessions too (see delete_user in main.py), so one
     delete per user is enough to remove everything this script created.
     Then deletes the state file. Anything that existed before `run`, or
     that you created yourself in between, is never touched.

Requirements:
    This script does NOT start your server — start it yourself first,
    in its own terminal, the normal way (e.g. `uvicorn main:app`).
    Needs the `requests` library: pip install requests

Usage:
    # terminal 1 (your existing workflow, unchanged)
    uvicorn main:app

    # terminal 2
    python load_test.py run
    # ...inspect the output, check the DB/UI if you want...
    python load_test.py cleanup
"""
import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import requests

BASE_URL = "http://localhost:8000"
STATE_FILE = Path(__file__).parent / "loadtest_state.json"

# Every temp user/entry this script creates is tagged with this, so it's
# unmistakable in the UI/DB if you want to eyeball it before cleanup.
MARKER = "loadtest"

NUM_SIMULATED_USERS = 50  # one entry per user, all fired at the same instant
TEMP_USER_PASSWORD = "loadtest123"

# Credentials for an EXISTING manager account — needed because creating
# users and deleting users are both manager-only endpoints. This is not
# one of the temporary accounts; it's the real manager account you already
# have. Override with --username/--password if you've changed it from the
# seeded default.
DEFAULT_USERNAME = "manager"
DEFAULT_PASSWORD = "manager123"


def login(username: str, password: str) -> str:
    r = requests.post(f"{BASE_URL}/auth/login", json={"username": username, "password": password})
    if not r.ok:
        sys.exit(
            f"Login failed ({r.status_code}): {r.text}\n"
            f"Is the server running at {BASE_URL}? Start it yourself first, in its own terminal."
        )
    return r.json()["access_token"]


def create_temp_users(manager_token: str, count: int) -> list:
    """Creates N temporary intern accounts. Returns a list of
    {"id": ..., "username": ..., "token": ...} — the token is fetched here
    (sequentially, during setup) so the timed section below only measures
    the concurrent entry-writing itself, not login overhead."""
    headers = {"Authorization": f"Bearer {manager_token}"}
    users = []
    print(f"Creating {count} temporary intern accounts...")
    for i in range(count):
        username = f"{MARKER}_user_{i}"
        r = requests.post(f"{BASE_URL}/users", headers=headers, json={
            "username": username,
            "password": TEMP_USER_PASSWORD,
            "role": "intern",
        })
        if not r.ok:
            sys.exit(f"Failed to create temp user {username}: {r.status_code} {r.text}")
        user_id = r.json()["id"]

        login_r = requests.post(f"{BASE_URL}/auth/login", json={"username": username, "password": TEMP_USER_PASSWORD})
        if not login_r.ok:
            sys.exit(f"Failed to log in as freshly-created user {username}: {login_r.status_code} {login_r.text}")

        users.append({"id": user_id, "username": username, "token": login_r.json()["access_token"]})
    print(f"Created {len(users)} temporary users and logged each one in.")
    return users


def fire_concurrent_writes(users: list) -> list:
    """One POST /entries per user, all submitted to the thread pool at
    once. Because SQLite allows only one writer at a time, these will
    serialize on the server regardless of how concurrently we fire them —
    what we're measuring is exactly that queueing/wait time, via
    busy_timeout in database.py (each connection retries for up to 5s
    before giving up), not whether the requests were sent concurrently."""
    today = date.today().isoformat()
    url = f"{BASE_URL}/entries"

    def one_write(user: dict):
        headers = {"Authorization": f"Bearer {user['token']}"}
        start = time.perf_counter()
        r = requests.post(url, headers=headers, json={
            "date": today,
            "activity": f"{MARKER} entry from {user['username']}",
            "category": "Other",
            "hours": 1.0,
            "force": False,
        })
        elapsed = time.perf_counter() - start
        return elapsed, r.status_code

    print(f"Firing {len(users)} concurrent POST /entries writes (one per user)...")
    durations = []
    errors = 0
    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(users)) as pool:
        futures = [pool.submit(one_write, u) for u in users]
        for fut in as_completed(futures):
            elapsed, status = fut.result()
            if status != 201:
                errors += 1
            durations.append(elapsed)
    wall_elapsed = time.perf_counter() - wall_start

    durations.sort()
    print("\n── Results ──────────────────────────────")
    print(f"  Writes:          {len(users)} ({errors} failed)")
    print(f"  Wall clock time: {wall_elapsed:.3f}s  (all {len(users)} writes, fired concurrently)")
    print(f"  Per-write min:   {min(durations):.3f}s")
    print(f"  Per-write avg:   {statistics.mean(durations):.3f}s")
    print(f"  Per-write p95:   {durations[int(len(durations) * 0.95)]:.3f}s")
    print(f"  Per-write max:   {max(durations):.3f}s")
    print("──────────────────────────────────────────")
    print("  A rising gap between min and max here is expected — it's SQLite's")
    print("  single-writer lock making later writers queue behind earlier ones.")
    print("  A failure (non-201) would mean a writer waited past the 5s")
    print("  busy_timeout in database.py and gave up — that's the actual ceiling.\n")
    return durations


def cmd_run(args):
    if STATE_FILE.exists():
        sys.exit(
            f"{STATE_FILE.name} already exists from a previous run that wasn't cleaned up.\n"
            f"Run `python load_test.py cleanup` first, then try again."
        )
    manager_token = login(args.username, args.password)
    users = create_temp_users(manager_token, args.user_count)
    fire_concurrent_writes(users)
    STATE_FILE.write_text(json.dumps({"user_ids": [u["id"] for u in users]}, indent=2))
    print(f"Saved {len(users)} temporary user IDs to {STATE_FILE.name}.")
    print("Nothing has been deleted. Inspect the app/DB now if you want, then run:")
    print("  python load_test.py cleanup")


def cmd_cleanup(args):
    if not STATE_FILE.exists():
        sys.exit(f"No {STATE_FILE.name} found — nothing to clean up (or `run` hasn't been used yet).")
    state = json.loads(STATE_FILE.read_text())
    user_ids = state.get("user_ids", [])

    token = login(args.username, args.password)
    headers = {"Authorization": f"Bearer {token}"}

    print(f"Deleting {len(user_ids)} temporary users (this also removes their entries)...")
    deleted, failed = 0, 0
    for uid in user_ids:
        r = requests.delete(f"{BASE_URL}/users/{uid}", headers=headers)
        if r.status_code in (204, 404):  # 404 = already gone, fine
            deleted += 1
        else:
            failed += 1
            print(f"  ⚠️  Failed to delete user {uid}: {r.status_code} {r.text}")

    print(f"Deleted {deleted}/{len(user_ids)} temporary users.")
    if failed:
        print(f"{failed} could not be deleted — {STATE_FILE.name} is kept so you can retry.")
        sys.exit(1)

    STATE_FILE.unlink()
    print(f"Removed {STATE_FILE.name}. Everything that existed before `run` is untouched.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Existing manager account (not a temp user)")
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Create temp users and fire the concurrent write test")
    p_run.add_argument("--user-count", type=int, default=NUM_SIMULATED_USERS)
    p_run.set_defaults(func=cmd_run)

    p_cleanup = sub.add_parser("cleanup", help="Delete exactly the temp users the last `run` created")
    p_cleanup.set_defaults(func=cmd_cleanup)

    args = parser.parse_args()
    args.func(args)