"""
Auto-closes a clock session that's been active for longer than a normal
workday — almost always means someone forgot to clock out, not that they
genuinely worked a 12+ hour continuous shift.

Design note: the session is closed AT the cap boundary (exactly
MAX_SESSION_HOURS after clock-in), not at whatever time this poller happens
to notice it. If it just used "now", someone who forgot to clock out at 5pm
and wasn't caught until 2am would get credited for 9 hours they never
worked. Capping at a fixed offset avoids that — it's still a best-effort
guess (the system has no way to know when someone actually stopped
working), which is exactly why every auto-closed session is flagged
(`auto_closed = 1`) rather than looking identical to a real clock-out, and
a manager can still correct it via PATCH /clock-sessions/{id}.

Same polling architecture as reminders.py, for the same reasons: simple to
reason about, survives restarts with no extra bookkeeping.
"""
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from database import get_connection

MAX_SESSION_HOURS = 12
POLL_INTERVAL_MINUTES = 5

_scheduler = None


def _check_and_close_stale_sessions() -> None:
    now = datetime.now()
    with get_connection() as conn:
        active = conn.execute(
            "SELECT * FROM clock_sessions WHERE is_active = 1"
        ).fetchall()
        for session in active:
            try:
                started = datetime.fromisoformat(session["clocked_in_at"])
            except ValueError:
                continue
            if now - started >= timedelta(hours=MAX_SESSION_HOURS):
                cutoff = started + timedelta(hours=MAX_SESSION_HOURS)
                conn.execute(
                    "UPDATE clock_sessions SET clocked_out_at = ?, is_active = 0, auto_closed = 1 WHERE id = ?",
                    (cutoff.isoformat(), session["id"])
                )
                conn.commit()
                print(f"⏰ Auto-closed clock session {session['id']} (user_id={session['user_id']}) "
                      f"after {MAX_SESSION_HOURS}h — likely a forgotten clock-out")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _check_and_close_stale_sessions, "interval",
        minutes=POLL_INTERVAL_MINUTES, id="clock_auto_close",
        next_run_time=datetime.now()
    )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None