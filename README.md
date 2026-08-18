# Timesheet App

A timesheet and meeting-scheduling app for small teams, built with FastAPI + SQLite + vanilla JS — no frontend framework, no build step.

Pay is calculated entirely from clocking in/out. Task logs are a separate, informational record reviewed and approved/rejected by a manager, but they never affect pay.

## Setup

```bash
# Install dependencies
pip install -r requirements.txt
```

Create a `.env` file inside `backend/` with at minimum:

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=your-gmail-app-password
BASE_URL=http://localhost:8000
```

`BASE_URL` matters: it's what invite/reset-password email links point to. `localhost` only works if you're testing on the same machine as the server — see the ngrok/Cloudflare notes below for testing with other people.

Google Meet auto-link generation additionally needs a `backend/google_credentials.json` (OAuth client credentials from Google Cloud Console) — the app works fine without this, you just won't be able to auto-generate Meet links.

```bash
# Run the server
cd backend
uvicorn main:app --reload
```

Then open http://localhost:8000 in your browser.

On first run, two accounts are seeded automatically:
- `manager` / `manager123`
- `intern` / `intern123`

Change these before using this anywhere real.

### Testing with people outside your network

`uvicorn main:app --reload` only accepts connections from your own machine. To let someone else reach it:

```bash
python backend/start_with_ngrok.py
```

This starts the server, opens an ngrok tunnel, and points `BASE_URL` at the public URL automatically. If ngrok isn't an option, run `cloudflared tunnel --url http://localhost:8000` in a second terminal instead (no account needed) and set `BASE_URL` in `.env` to the printed URL yourself.

**Don't use `--reload` when other people are actually using the app** — it restarts the server (dropping active connections) on every file change.

## Stack

- **Backend:** Python + FastAPI, JWT auth (python-jose + passlib/bcrypt)
- **Database:** SQLite in WAL mode (auto-created as `backend/timesheet.db`), for better behavior under concurrent use
- **Background jobs:** APScheduler — meeting reminders, auto-closing forgotten clock-in sessions
- **Email:** Gmail SMTP, HTML invites styled like Google Calendar's own, with `.ics` calendar attachments (including proper `RRULE` support for recurring meetings)
- **Google Meet:** per-user OAuth via `google-auth-oauthlib` for auto-generated Meet links
- **Frontend:** Vanilla JS + plain CSS, no framework — mobile-responsive (collapsible nav, scrollable calendar, single-column forms on narrow screens)

## Features

**Time tracking & pay**
- Clock in/out with exact elapsed time (down to the minute)
- Pay is calculated purely from clocked time — task logs never affect it
- Forgotten clock-outs auto-close after 12 hours (flagged, not silently guessed at); a manager can review and correct any session
- Task log entries: date, category, activity, an informational time estimate; approved or rejected by a manager for accountability, independent of pay
- Weekly/monthly report: category breakdown, clocked hours, pay, overtime flagging, PDF export
- Entries list is paginated and filterable by date range/category; CSV export

**Meetings**
- Online or in-person, with room-conflict detection (hard block) and invitee double-booking warnings (soft warning, checked live as you build the invite)
- Recurring meetings (daily/weekly/biweekly/monthly) — one consolidated invite per series, not one email per occurrence
- Reschedule (resets RSVPs) or cancel a single occurrence or an entire series
- RSVP in-app or via one-click emailed links, no login required; optional note when declining
- Room management: capacity, equipment, operational/under-renovation status (enforced, not cosmetic), and an occupancy schedule visible to everyone
- Search across meetings by title, room, or attendee
- Select an entire team as attendees at once, or add people individually

**People**
- Manager-only account creation, with password confirmation
- Job title (free text) and team assignment (structured, manager-managed), shown next to names when picking attendees
- Manager/intern roles throughout, enforced on the API, not just hidden in the UI

**Other**
- Password reset via email
- Dark mode
- Google Meet auto-link generation

## Known limitations

- No persistent deployment yet — the app (and everything time-sensitive: reminders, auto-close, RSVP links) only works while the server is actively running on some machine. Most free hosts wipe SQLite on restart, so this hasn't been solved yet.
- No automated test suite — changes are currently verified manually/ad hoc.
- No rate limiting on login.
- Reminder and auto-close timing use the server's local clock — if the server and users are in different timezones, "15 minutes before" is 15 minutes before the server's clock, not each person's.

## API reference

The API is self-documenting — with the server running, visit **http://localhost:8000/docs** for an interactive, always-up-to-date reference (Swagger UI) covering every endpoint, request/response shape, and auth requirement.
