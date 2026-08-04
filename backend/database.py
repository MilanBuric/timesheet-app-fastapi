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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clock_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id),
                clocked_in_at   TEXT    NOT NULL,
                clocked_out_at  TEXT,
                is_active       INTEGER NOT NULL DEFAULT 1
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
