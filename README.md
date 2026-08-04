# Timesheet App

A clean timesheet app built with FastAPI + SQLite + Vanilla JS.

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
cd backend
uvicorn main:app --reload
```

Then open http://localhost:8000 in your browser.

## Stack
- **Backend:** Python + FastAPI
- **Database:** SQLite (auto-created as `backend/timesheet.db`)
- **Frontend:** Vanilla JS + plain CSS (no frameworks)

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | /entries | List entries (supports filters) |
| POST | /entries | Create entry |
| PATCH | /entries/{id} | Update entry |
| DELETE | /entries/{id} | Delete entry |
| GET | /stats | Today/week hours + total entries |
| POST | /clock/in | Start a clock session |
| POST | /clock/out | End session, returns hours |
| GET | /clock/active | Get active session if any |

## Features (V2)
- Log time entries with date, activity, category, hours
- Clock in / clock out with live timer
- Edit and delete entries
- Filter entries by date range and category
- Export filtered entries to CSV
- Dashboard with today/week stats
