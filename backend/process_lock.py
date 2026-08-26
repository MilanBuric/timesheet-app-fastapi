"""
Ensures only ONE process runs a given background job (reminders,
clock-auto-close), even if this app is ever started with multiple worker
processes (e.g. `uvicorn main:app --workers 4`).

Why this matters: reminders.py and clock_auto_close.py each start their
own in-process APScheduler on startup. That's fine with a single process —
which is how this app runs today — but if it's ever scaled to multiple
workers, every worker would start its own independent scheduler, all
polling the same database at once. reminders.py already guards against
the resulting double-send race with an atomic claim (see that file), but
the cleaner fix is to not run N redundant schedulers in the first place —
one worker should own each job, the rest should skip starting it.

Uses an OS-level advisory file lock (fcntl.flock) rather than a database
row or heartbeat table, specifically because of what happens when a
process dies: a flock is automatically released by the operating system
the moment its owning process exits, crashes, or gets killed — no stale
lock to detect, no timeout to tune, no cleanup code to write. Whichever
worker restarts next simply acquires the now-free lock and takes over.
A DB-based lock would need extra machinery (a heartbeat, a staleness
threshold) to handle that same case correctly.

Linux/Unix only (fcntl is not available on Windows) — the deployment
target is a Linux VM, and local Windows development never starts
multiple workers anyway, so a real lock is unnecessary there. Rather
than crash at import time on Windows (which used to happen here — an
unconditional `import fcntl` blows up with ModuleNotFoundError the
moment reminders.py/clock_auto_close.py call into this module), we
degrade to a no-op that always reports "you own the lock". That's the
correct behavior for the single-process case this always runs as
locally, and simply means multi-worker singleton-locking isn't
enforced on Windows — which was never a real scenario there anyway.
"""
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False  # e.g. Windows — see module docstring

# Keeps file handles alive for the lifetime of the process — the lock is
# released as soon as the handle is garbage-collected/closed, so this
# reference is what keeps a successfully-acquired lock actually held.
_held_locks = {}


def acquire_singleton_lock(name: str, lock_dir: str = "/tmp") -> bool:
    """Attempts to acquire an exclusive, non-blocking lock named `name`.
    Returns True if THIS process now owns it and should run the associated
    job. Returns False if another process already holds it — the caller
    should skip starting that job entirely in this process.

    On platforms without fcntl (Windows), always returns True — see
    module docstring."""
    if not _HAS_FCNTL:
        return True
    lock_path = f"{lock_dir}/timesheet_{name}.lock"
    fh = open(lock_path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return False
    _held_locks[name] = fh  # prevent GC from closing (and releasing) it
    return True