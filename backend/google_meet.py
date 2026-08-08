"""
Auto-generates real Google Meet links by creating a private Google Calendar
event (on the organizer's own calendar) with a conference request attached.

Setup required (one-time, per the walkthrough Claude gave you):
    1. Google Cloud project with the Calendar API enabled
    2. OAuth 2.0 "Desktop app" credentials, downloaded as
       backend/google_credentials.json
    3. The very first time create_meet_link() runs, a browser window opens
       asking you to log into Google and grant access. After that, a
       token.json is saved locally so it won't ask again.

Both google_credentials.json and token.json are gitignored — never commit
them.
"""
import os
import datetime
import uuid
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BACKEND_DIR = Path(__file__).parent
CREDENTIALS_PATH = BACKEND_DIR / "google_credentials.json"
TOKEN_PATH = BACKEND_DIR / "token.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TIMEZONE = os.environ.get("GOOGLE_CALENDAR_TIMEZONE", "Europe/Belgrade")


class GoogleMeetError(Exception):
    """Raised whenever a Meet link can't be generated, with a message safe to show the user."""
    pass


def _get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not CREDENTIALS_PATH.exists():
        raise GoogleMeetError(
            "google_credentials.json not found in the backend folder. "
            "Follow the Google Cloud setup steps to download it first."
        )

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # First-time use: this opens a browser window for you to log in
            # and grant access. Only happens once per machine.
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())

    return creds


def create_meet_link(title: str, description: str, date: str, start_time: str, end_time: str) -> str:
    """
    Creates a private Google Calendar event on the organizer's own calendar
    (no attendees added — the Timesheet App sends its own invite emails
    separately) purely to obtain a real meet.google.com link.

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
        creds = _get_credentials()
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
