import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "timesheet.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
                is_active       INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                organizer_id   INTEGER NOT NULL REFERENCES users(id),
                title          TEXT    NOT NULL,
                description    TEXT,
                date           TEXT    NOT NULL,
                start_time     TEXT    NOT NULL,
                end_time       TEXT    NOT NULL,
                location_type  TEXT    NOT NULL DEFAULT 'online',
                room           TEXT,
                meeting_link   TEXT,
                created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
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
