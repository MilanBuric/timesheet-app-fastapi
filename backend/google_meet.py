"""
Auto-generates real Google Meet links, directly through the Meet API.

Earlier versions of this tried two different ways to lock the room down
automatically:
    1. Create the link via Calendar, then patch access through the Meet
       API — doesn't work, since Google's Meet API can't edit a space
       Calendar created (confirmed via Google's own issue tracker:
       https://issuetracker.google.com/379337762).
    2. Create the space directly via the Meet API with accessType set to
       RESTRICTED — also doesn't work: Google's API rejects this for
       personal/consumer Gmail accounts with a
       FEATURE_UNAVAILABLE_TO_USER error. Restricting a meeting space
       programmatically is a Google Workspace-only capability; personal
       accounts are locked to their account's default access setting and
       can't override it via the API at all.

So this version just creates a plain Meet space with no access
restriction — the link works reliably, but locking a specific call down
has to be done manually, live, once the organizer has joined (Meet's
in-call "Quick access" toggle under host controls). That's the only lever
available on personal accounts; it applies per-call, not permanently.

Credentials are stored PER APP USER in the database (users.google_token),
not in a single shared token.json — so each person using the Timesheet App
authorizes their own Google account, and meetings they create use their
own Meet identity rather than whoever authorized first.

Setup required (one-time per Google Cloud project):
    1. Google Cloud project with the Google Meet API enabled
    2. OAuth 2.0 "Desktop app" credentials, downloaded as
       backend/google_credentials.json (shared across all app users — this
       identifies the *app*, not the individual person)
    3. The meetings.space.created scope added under OAuth consent screen
       → Audience → Data Access
    4. The first time a given app user schedules an auto-generated Meet
       link, a browser window opens asking THEM to log into Google and
       grant access. After that, their credentials are saved in the
       database against their account and reused automatically.

Note on identity: the "login_hint" passed to Google only pre-fills/suggests
an account on the sign-in screen — it does not force or verify which
Google account someone actually authorizes with.

google_credentials.json is gitignored — never commit it.
"""
import os
import json
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BACKEND_DIR = Path(__file__).parent
CREDENTIALS_PATH = BACKEND_DIR / "google_credentials.json"
SCOPES = ["https://www.googleapis.com/auth/meetings.space.created"]


class GoogleMeetError(Exception):
    """Raised whenever a Meet link can't be generated, with a message safe to show the user."""
    pass


def _load_credentials(conn, user_id: int):
    from google.oauth2.credentials import Credentials
    row = conn.execute("SELECT google_token FROM users WHERE id = ?", (user_id,)).fetchone()
    if row and row["google_token"]:
        try:
            # Deliberately NOT passing SCOPES as an override here — doing
            # so would force .scopes to always equal whatever the current
            # code expects, regardless of what Google actually granted,
            # which silently breaks the exact-match staleness check below.
            return Credentials.from_authorized_user_info(json.loads(row["google_token"]))
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

        # Require an EXACT scope match, not just "at least these scopes" —
        # a token granted under an older, different scope list (e.g. one
        # that included a scope since removed from the OAuth consent
        # screen) can fail on refresh even if it still technically covers
        # what we need now, since Google validates against the original
        # grant. Any scope-list change should force a fresh consent.
        has_all_scopes = bool(creds) and set(creds.scopes or []) == set(SCOPES)

        if creds and creds.valid and has_all_scopes:
            return creds

        if creds and creds.expired and creds.refresh_token and has_all_scopes:
            creds.refresh(Request())
            _save_credentials(conn, user_id, creds)
            return creds

        # No usable credentials for this app user yet (or their saved token
        # predates a scope this app now needs): this opens a browser
        # window for THEM to log in and grant access. login_hint just
        # pre-fills the suggested account on Google's screen.
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
        kwargs = {"login_hint": login_hint} if login_hint else {}
        creds = flow.run_local_server(port=0, **kwargs)
        _save_credentials(conn, user_id, creds)
        return creds


def create_meet_link(user_id: int, login_hint: str = None) -> str:
    """
    Creates a real Google Meet space using the account's default access
    setting — personal Gmail accounts can't override this via the API
    (see module docstring), so the room is NOT restricted automatically.
    To actually lock a specific call down, the organizer needs to join
    first and turn off "Quick access" live, under Meet's host controls.

    user_id ties the Google credentials to a specific app user, so each
    person authorizes and uses their own Google account, and the space is
    owned by (and only editable by) that same account.

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
        service = build("meet", "v2", credentials=creds)

        space = service.spaces().create(body={}).execute()

        link = space.get("meetingUri")
        if not link:
            raise GoogleMeetError("Google didn't return a Meet link for this space.")
        return link

    except GoogleMeetError:
        raise
    except Exception as exc:
        raise GoogleMeetError(f"Failed to create Google Meet link: {exc}")
