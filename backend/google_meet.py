"""
Auto-generates real Google Meet links by creating a private Google Calendar
event (on the organizer's own calendar) with a conference request attached.

Credentials are stored PER APP USER in the database (users.google_token),
not in a single shared token.json — so each person using the Timesheet App
authorizes their own Google account, and meetings they create use their own
Calendar/Meet identity rather than whoever authorized first.

Setup required (one-time per Google Cloud project, per app user):
    1. Google Cloud project with the Calendar API enabled
    2. OAuth 2.0 "Desktop app" credentials, downloaded as
       backend/google_credentials.json (shared across all app users — this
       identifies the *app*, not the individual person)
    3. The first time a given app user schedules an auto-generated Meet
       link, a browser window opens asking THEM to log into Google and
       grant access. After that, their credentials are saved in the
       database against their account and reused automatically.

Note on identity: the "login_hint" passed to Google only pre-fills/suggests
an account on the sign-in screen — it does not force or verify which
Google account someone actually authorizes with. Nothing stops a person
from signing into a different Google account than their app email if they
choose to on that screen.

google_credentials.json is gitignored — never commit it. The old shared
token.json from earlier versions of this app is no longer used; each app
user just needs to authorize once under this new system.
"""
import os
import json
import uuid
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BACKEND_DIR = Path(__file__).parent
CREDENTIALS_PATH = BACKEND_DIR / "google_credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TIMEZONE = os.environ.get("GOOGLE_CALENDAR_TIMEZONE", "Europe/Belgrade")


class GoogleMeetError(Exception):
    """Raised whenever a Meet link can't be generated, with a message safe to show the user."""
    pass


def _load_credentials(conn, user_id: int):
    from google.oauth2.credentials import Credentials
    row = conn.execute("SELECT google_token FROM users WHERE id = ?", (user_id,)).fetchone()
    if row and row["google_token"]:
        try:
            return Credentials.from_authorized_user_info(json.loads(row["google_token"]), SCOPES)
        except (ValueError, json.JSONDecodeError):
            return None  # corrupted/old-format token; treat as absent
    return None


def _save_credentials(conn, user_id: int, creds) -> None:
    conn.execute("UPDATE users SET google_token = ? WHERE id = ?", (creds.to_json(), user_id))
    conn.commit()


def _get_credentials(user_id: int, login_hint: str = None):
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from database import get_connection

    if not CREDENTIALS_PATH.exists():
        raise GoogleMeetError(
            "google_credentials.json not found in the backend folder. "
            "Follow the Google Cloud setup steps to download it first."
        )

    with get_connection() as conn:
        creds = _load_credentials(conn, user_id)

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_credentials(conn, user_id, creds)
            return creds

        # No usable credentials for this app user yet: this opens a browser
        # window for THEM to log in and grant access. login_hint just
        # pre-fills the suggested account on Google's screen.
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        kwargs = {"login_hint": login_hint} if login_hint else {}
        creds = flow.run_local_server(port=0, **kwargs)
        _save_credentials(conn, user_id, creds)
        return creds


def create_meet_link(title: str, description: str, date: str, start_time: str, end_time: str,
                      user_id: int, login_hint: str = None, attendee_emails: list = None) -> str:
    """
    Creates a private Google Calendar event on the organizer's own calendar
    purely to obtain a real meet.google.com link. attendee_emails are added
    as real guests on the event (not just our own separate invite email) so
    Google Meet can recognize and auto-admit them instead of putting them
    in the "waiting for the host" screen — this only works for guests who
    join while signed into the Google account matching their invited
    email; anonymous or differently-signed-in joins will still need to be
    manually admitted by the organizer, since Meet can't verify identity
    any other way.

    user_id ties the Google credentials to a specific app user, so each
    person authorizes and uses their own Google account.

    Raises GoogleMeetError with a human-readable message on any failure.
    """
    try:
        from googleapiclient.discovery import build
    except ImportError:
        raise GoogleMeetError(
            "Google API client library isn't installed. Run: "
            "pip install -r requirements.txt"
        )

    try:
        creds = _get_credentials(user_id, login_hint)
        service = build("calendar", "v3", credentials=creds)

        start_dt = f"{date}T{start_time}:00"
        end_dt = f"{date}T{end_time}:00"

        event = {
            "summary": title,
            "description": description or "",
            "start": {"dateTime": start_dt, "timeZone": TIMEZONE},
            "end": {"dateTime": end_dt, "timeZone": TIMEZONE},
            "conferenceData": {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"}
                }
            }
        }
        if attendee_emails:
            event["attendees"] = [{"email": e} for e in attendee_emails]
            event["guestsCanSeeOtherGuests"] = True

        created = service.events().insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1,
            sendUpdates="none"  # we send our own invite emails
        ).execute()

        link = created.get("hangoutLink")
        if not link:
            raise GoogleMeetError("Google didn't return a Meet link for this event.")
        return link

    except GoogleMeetError:
        raise
    except Exception as exc:
        raise GoogleMeetError(f"Failed to create Google Meet link: {exc}")
