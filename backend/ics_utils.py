"""
Builds .ics (iCalendar) attachments so invite emails land as a real event
in Outlook/Apple Mail/Google Calendar's "Add to calendar" flow, with no
OAuth needed on the recipient's side (unlike the Google Meet integration,
which only auto-adds the event to the *organizer's* Google Calendar).

Times are written as floating local date-times (no TZID/UTC 'Z' suffix) —
the same simplification the rest of the app makes elsewhere (see
reminders.py), since there's no per-user timezone stored anywhere yet.

UID is stable per meeting (`meeting-{id}@timesheet-app`) and SEQUENCE is
bumped on every reschedule, so calendar apps update the existing event
instead of creating a duplicate when a rescheduled invite arrives.
"""
from datetime import datetime
import re

DOMAIN = "timesheet-app.local"


def _escape(text: str) -> str:
    if not text:
        return ""
    return (text.replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545 requires lines longer than 75 octets to be folded."""
    if len(line) <= 75:
        return line
    out = [line[:75]]
    rest = line[75:]
    while rest:
        out.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(out)


RECURRENCE_FREQ = {"daily": "DAILY", "weekly": "WEEKLY", "biweekly": "WEEKLY", "monthly": "MONTHLY"}


def build_rrule(recurrence: str, until: str) -> str:
    """Maps our recurrence vocabulary to an RFC 5545 RRULE string, e.g.
    FREQ=WEEKLY;INTERVAL=2;UNTIL=20261231T235959Z. Returns None for a
    non-recurring meeting."""
    freq = RECURRENCE_FREQ.get(recurrence)
    if not freq:
        return None
    parts = [f"FREQ={freq}"]
    if recurrence == "biweekly":
        parts.append("INTERVAL=2")
    if until:
        parts.append(f"UNTIL={until.replace('-', '')}T235959Z")
    return ";".join(parts)


def build_ics(meeting_id, sequence: int, title: str, description: str,
              date: str, start_time: str, end_time: str,
              organizer_email: str, organizer_name: str,
              attendee_emails: list, location_text: str,
              method: str = "REQUEST", status: str = "CONFIRMED",
              rrule: str = None) -> str:
    dtstart = datetime.strptime(f"{date} {start_time}", "%Y-%m-%d %H:%M").strftime("%Y%m%dT%H%M%S")
    dtend = datetime.strptime(f"{date} {end_time}", "%Y-%m-%d %H:%M").strftime("%Y%m%dT%H%M%S")
    dtstamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    uid = f"meeting-{meeting_id}@{DOMAIN}"

    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Timesheet App//Meetings//EN",
        "VERSION:2.0",
        f"METHOD:{method}",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"SEQUENCE:{sequence}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{_escape(title)}",
        f"STATUS:{status}",
    ]
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if location_text:
        lines.append(f"LOCATION:{_escape(location_text)}")
    if organizer_email:
        lines.append(f"ORGANIZER;CN={_escape(organizer_name)}:mailto:{organizer_email}")
    for email in attendee_emails or []:
        lines.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{email}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    return "\r\n".join(_fold(l) for l in lines) + "\r\n"