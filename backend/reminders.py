"""
Background reminder emails, sent 15 minutes before a meeting starts.

Runs on a simple polling loop via APScheduler rather than scheduling one
job per meeting — much simpler to reason about, survives server restarts
without any extra bookkeeping, and the `reminder_sent` flag on the meeting
row prevents duplicate sends across polls.

Precision: since this polls once a minute, a reminder fires the moment a
meeting first falls within the 15-minute window — in practice that means
somewhere between 14 and 15 minutes before start, never later than 15.

Caveat: times are compared using the server's local naive time, same
simplification the rest of this app already makes (see /stats' client_date
handling). If the server and users are in different timezones, reminders
will fire relative to the server's clock, not the user's.
"""
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from database import get_connection
import email_utils

REMINDER_WINDOW_MINUTES = 15
POLL_INTERVAL_MINUTES = 1

_scheduler = None


def _send_reminder_for_meeting(conn, m) -> None:
    organizer = conn.execute(
        "SELECT username, email FROM users WHERE id = ?", (m["organizer_id"],)
    ).fetchone()
    attendees = conn.execute(
        """SELECT u.username, u.email FROM meeting_attendees ma
           JOIN users u ON ma.user_id = u.id
           WHERE ma.meeting_id = ? AND ma.status != 'declined'""",
        (m["id"],)
    ).fetchall()

    recipients = [dict(a) for a in attendees]
    if organizer:
        recipients.append(dict(organizer))

    for r in recipients:
        if not r.get("email"):
            continue
        try:
            email_utils.send_meeting_reminder(
                to_email=r["email"],
                recipient_name=r["username"],
                title=m["title"],
                date=m["date"],
                start_time=m["start_time"],
                end_time=m["end_time"],
                location_type=m["location_type"],
                room=m["room"],
                meeting_link=m["meeting_link"],
            )
        except Exception as exc:
            print(f"❌ Failed to send reminder to {r['email']}: {exc}")


def _check_and_send_reminders() -> None:
    now = datetime.now()
    window_end = now + timedelta(minutes=REMINDER_WINDOW_MINUTES)
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM meetings WHERE reminder_sent = 0").fetchall()
        for m in rows:
            try:
                start_dt = datetime.strptime(f"{m['date']} {m['start_time']}", "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if now <= start_dt <= window_end:
                # Claim the row FIRST, atomically, before sending anything.
                # Only one caller's UPDATE can match reminder_sent = 0 for a
                # given id — a second scheduler (e.g. a second uvicorn
                # worker) checking the same meeting will find 0 rows
                # affected and skip it, instead of both callers reading
                # reminder_sent = 0, both sending, and both marking it sent
                # after the fact. This currently only matters if you ever
                # run more than one server process, but it costs nothing to
                # have right now.
                cursor = conn.execute(
                    "UPDATE meetings SET reminder_sent = 1 WHERE id = ? AND reminder_sent = 0",
                    (m["id"],)
                )
                conn.commit()
                if cursor.rowcount == 0:
                    continue  # another process already claimed this one
                _send_reminder_for_meeting(conn, m)


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _check_and_send_reminders, "interval",
        minutes=POLL_INTERVAL_MINUTES, id="meeting_reminders",
        next_run_time=datetime.now()  # also check once immediately on startup
    )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None