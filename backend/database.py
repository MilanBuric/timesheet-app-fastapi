import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "timesheet.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL lets readers proceed while a write is in progress instead of
    # blocking on SQLite's default single-writer lock — this matters once
    # more than one person is using the app at the same time. busy_timeout
    # makes a connection that *does* hit a lock wait and retry for up to 5s
    # instead of immediately raising "database is locked", which covers the
    # brief overlaps that are normal under concurrent use.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT    NOT NULL UNIQUE,
                password     TEXT    NOT NULL,
                role         TEXT    NOT NULL DEFAULT 'intern',
                hourly_rate  REAL    NOT NULL DEFAULT 0.0,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migration: add email to users created before this column existed
        user_cols = [r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "email" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        # Migration: add google_token so each app user has their own Google OAuth
        # credentials for auto-generated Meet links, instead of one shared token
        if "google_token" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN google_token TEXT")
        # Migration: job title (free text) and team assignment (structured,
        # like rooms) — title varies per person so stays free text, team
        # benefits from consistency so it's its own managed entity below
        conn.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        if "title" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN title TEXT")
        if "team_id" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN team_id INTEGER REFERENCES teams(id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id),
                date          TEXT    NOT NULL,
                activity      TEXT    NOT NULL,
                category      TEXT    NOT NULL,
                hours         REAL    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending',
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migration: add rejection_reason to entries created before this column existed
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
        if "rejection_reason" not in cols:
            conn.execute("ALTER TABLE entries ADD COLUMN rejection_reason TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clock_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id),
                clocked_in_at   TEXT    NOT NULL,
                clocked_out_at  TEXT,
                is_active       INTEGER NOT NULL DEFAULT 1,
                auto_closed     INTEGER NOT NULL DEFAULT 0
            )
        """)
        cs_cols = [r["name"] for r in conn.execute("PRAGMA table_info(clock_sessions)").fetchall()]
        if "auto_closed" not in cs_cols:
            conn.execute("ALTER TABLE clock_sessions ADD COLUMN auto_closed INTEGER NOT NULL DEFAULT 0")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                organizer_id           INTEGER NOT NULL REFERENCES users(id),
                title                  TEXT    NOT NULL,
                description            TEXT,
                date                   TEXT    NOT NULL,
                start_time             TEXT    NOT NULL,
                end_time               TEXT    NOT NULL,
                location_type          TEXT    NOT NULL DEFAULT 'online',
                room                   TEXT,
                meeting_link           TEXT,
                recurrence_rule        TEXT    NOT NULL DEFAULT 'none',
                recurrence_until       TEXT,
                recurrence_group_id    TEXT,
                reminder_sent          INTEGER NOT NULL DEFAULT 0,
                ics_sequence           INTEGER NOT NULL DEFAULT 0,
                created_at             TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migration: add location_type/room/meeting_link to meetings created before these columns existed
        meeting_cols = [r["name"] for r in conn.execute("PRAGMA table_info(meetings)").fetchall()]
        if "location_type" not in meeting_cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN location_type TEXT NOT NULL DEFAULT 'online'")
        if "room" not in meeting_cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN room TEXT")
        if "meeting_link" not in meeting_cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN meeting_link TEXT")
        # Migration: recurrence + reminder tracking columns
        if "recurrence_rule" not in meeting_cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN recurrence_rule TEXT NOT NULL DEFAULT 'none'")
        if "recurrence_until" not in meeting_cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN recurrence_until TEXT")
        if "recurrence_group_id" not in meeting_cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN recurrence_group_id TEXT")
        if "reminder_sent" not in meeting_cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0")
        # Migration: bumped on every reschedule so .ics attachments update the
        # same calendar event in the recipient's calendar app instead of
        # creating a duplicate
        if "ics_sequence" not in meeting_cols:
            conn.execute("ALTER TABLE meetings ADD COLUMN ics_sequence INTEGER NOT NULL DEFAULT 0")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meeting_attendees (
                meeting_id  INTEGER NOT NULL REFERENCES meetings(id),
                user_id     INTEGER NOT NULL REFERENCES users(id),
                status      TEXT    NOT NULL DEFAULT 'pending',
                rsvp_token  TEXT,
                PRIMARY KEY (meeting_id, user_id)
            )
        """)
        # Migration: add status/rsvp_token to meeting_attendees created before these columns existed
        ma_cols = [r["name"] for r in conn.execute("PRAGMA table_info(meeting_attendees)").fetchall()]
        if "status" not in ma_cols:
            conn.execute("ALTER TABLE meeting_attendees ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'")
        if "rsvp_token" not in ma_cols:
            conn.execute("ALTER TABLE meeting_attendees ADD COLUMN rsvp_token TEXT")
        if "decline_reason" not in ma_cols:
            conn.execute("ALTER TABLE meeting_attendees ADD COLUMN decline_reason TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE,
                capacity    INTEGER,
                equipment   TEXT,
                status      TEXT    NOT NULL DEFAULT 'operational',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        room_cols = [r["name"] for r in conn.execute("PRAGMA table_info(rooms)").fetchall()]
        if "status" not in room_cols:
            conn.execute("ALTER TABLE rooms ADD COLUMN status TEXT NOT NULL DEFAULT 'operational'")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token       TEXT    PRIMARY KEY,
                user_id     INTEGER NOT NULL REFERENCES users(id),
                expires_at  TEXT    NOT NULL,
                used        INTEGER NOT NULL DEFAULT 0
            )
        """)

        conn.commit()
        _seed_users(conn)


def _seed_users(conn):
    from auth import hash_password
    existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if existing == 0:
        conn.execute(
            "INSERT INTO users (username, password, role, hourly_rate) VALUES (?, ?, ?, ?)",
            ("intern", hash_password("intern123"), "intern", 15.0)
        )
        conn.execute(
            "INSERT INTO users (username, password, role, hourly_rate) VALUES (?, ?, ?, ?)",
            ("manager", hash_password("manager123"), "manager", 0.0)
        )
        conn.commit()
        print("✅ Seeded default users: intern/intern123, manager/manager123")