"""
Login rate limiting — locks a username out for a cooldown period after too
many failed password attempts, so brute-forcing a password isn't just slow,
it's actively blocked.

OFF by default, controlled by a single setting: LOGIN_RATE_LIMIT_ENABLED
in .env (any value other than unset/"false"/"0" turns it on). When off,
every function here is a fast no-op — check_not_locked_out() always
passes, record_failed_attempt() and record_successful_login() do nothing.
This is deliberately NOT commented-out code: it's real, working, and
covered by the same test suite as everything else, so turning it on later
is a one-line .env change with no risk of the logic having quietly drifted
out of sync with the rest of the app in the meantime.

Defaults: 3 failed attempts locks the username out for 30 minutes. A
successful login, or the lockout window simply expiring, both clear it.
"""
import os
from datetime import datetime, timedelta

from fastapi import HTTPException

MAX_ATTEMPTS = 3
LOCKOUT_MINUTES = 30


def _enabled() -> bool:
    return os.environ.get("LOGIN_RATE_LIMIT_ENABLED", "false").lower() not in ("false", "0", "")


def check_not_locked_out(conn, username: str) -> None:
    """Raises HTTPException(429) if this username is currently locked out.
    Call this BEFORE checking the password, so a locked-out account can't
    be used to keep guessing passwords during its own lockout window."""
    if not _enabled():
        return
    row = conn.execute(
        "SELECT locked_until FROM login_attempts WHERE username = ?", (username,)
    ).fetchone()
    if not row or not row["locked_until"]:
        return
    locked_until = datetime.fromisoformat(row["locked_until"])
    if datetime.utcnow() < locked_until:
        remaining_minutes = max(1, int((locked_until - datetime.utcnow()).total_seconds() / 60))
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed login attempts. Try again in about {remaining_minutes} minute(s)."
        )
    # Lockout window has passed on its own — clear it so the next check is cheap.
    conn.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
    conn.commit()


def record_failed_attempt(conn, username: str) -> "int | None":
    """Call this after a failed password check. Locks the username out
    once MAX_ATTEMPTS is reached within the tracked window.

    Returns the number of attempts remaining before a lockout would kick
    in (0 means this failure just triggered the lockout), or None if rate
    limiting is disabled entirely — callers should distinguish "no limit
    in effect" (None) from "zero attempts left" (0), since those need
    different messages shown to the user."""
    if not _enabled():
        return None
    row = conn.execute(
        "SELECT attempt_count FROM login_attempts WHERE username = ?", (username,)
    ).fetchone()
    new_count = (row["attempt_count"] if row else 0) + 1
    locked_until = None
    if new_count >= MAX_ATTEMPTS:
        locked_until = (datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
        new_count = 0  # reset the counter now that a lockout has been applied
    conn.execute(
        """INSERT INTO login_attempts (username, attempt_count, locked_until) VALUES (?, ?, ?)
           ON CONFLICT(username) DO UPDATE SET attempt_count = ?, locked_until = ?""",
        (username, new_count, locked_until, new_count, locked_until)
    )
    conn.commit()
    return 0 if locked_until else MAX_ATTEMPTS - new_count



def record_successful_login(conn, username: str) -> None:
    """Call this after a successful login — clears any tracked failed
    attempts, so a correct password always resets the count to zero."""
    if not _enabled():
        return
    conn.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
    conn.commit()


# ── Forgot-password rate limiting ────────────────────────────────────────
#
# Closes the asymmetry with login: /auth/login was rate-limited, but
# nothing stopped repeated /auth/forgot-password requests for the same
# username — cheap to abuse for spamming a real user's inbox with reset
# emails, or for hammering the SMTP server.
#
# This is deliberately a DIFFERENT shape from the login lockout above,
# not a reuse of it, for one important reason: every /auth/forgot-password
# request already gets the exact same generic response whether or not the
# account exists, specifically so the endpoint can't be used to enumerate
# valid usernames. If a lockout here ever changed that response (e.g. a
# distinct "too many requests" message, or an HTTP 429), a real username
# would start looking different from a fake one under repeated requests —
# reintroducing the exact leak the generic response exists to prevent. So
# a lockout here must stay completely silent: it only skips the "create a
# token and send an email" side effect, never the response text or status
# code, and it's tracked by the raw username string (same as login),
# never by whether a user row actually exists.
#
# ON by default (unlike login, which defaults off): the login lockout was
# left off by default specifically because getting locked out of your own
# account is a real cost worth being deliberate about turning on. Getting
# temporarily throttled on *requesting a reset link* has no such
# downside — nothing about signing in or using the app is affected, so
# there's no real reason to ship this switched off. Override with
# FORGOT_PASSWORD_RATE_LIMIT_ENABLED=false in .env if you ever want it
# off.
FORGOT_PASSWORD_MAX_REQUESTS = 3
FORGOT_PASSWORD_LOCKOUT_MINUTES = 30


def _forgot_password_enabled() -> bool:
    return os.environ.get("FORGOT_PASSWORD_RATE_LIMIT_ENABLED", "true").lower() not in ("false", "0", "")


def is_forgot_password_locked_out(conn, username: str) -> bool:
    """Returns True if this username has made too many reset requests
    recently and this one should be silently throttled — no token
    created, no email sent, but the caller must still return its normal
    generic response either way (see module note above)."""
    if not _forgot_password_enabled():
        return False
    row = conn.execute(
        "SELECT locked_until FROM forgot_password_attempts WHERE username = ?", (username,)
    ).fetchone()
    if not row or not row["locked_until"]:
        return False
    locked_until = datetime.fromisoformat(row["locked_until"])
    if datetime.utcnow() < locked_until:
        return True
    # Lockout window has passed on its own — clear it so the next check is cheap.
    conn.execute("DELETE FROM forgot_password_attempts WHERE username = ?", (username,))
    conn.commit()
    return False


def record_forgot_password_request(conn, username: str) -> None:
    """Call this for EVERY /auth/forgot-password request, for every
    username, whether or not the account exists — same principle as
    record_failed_attempt for login, so the enumeration protection holds
    up under rate limiting too, not just under the generic response
    alone."""
    if not _forgot_password_enabled():
        return
    row = conn.execute(
        "SELECT attempt_count FROM forgot_password_attempts WHERE username = ?", (username,)
    ).fetchone()
    new_count = (row["attempt_count"] if row else 0) + 1
    locked_until = None
    if new_count >= FORGOT_PASSWORD_MAX_REQUESTS:
        locked_until = (datetime.utcnow() + timedelta(minutes=FORGOT_PASSWORD_LOCKOUT_MINUTES)).isoformat()
        new_count = 0  # reset the counter now that a lockout has been applied
    conn.execute(
        """INSERT INTO forgot_password_attempts (username, attempt_count, locked_until) VALUES (?, ?, ?)
           ON CONFLICT(username) DO UPDATE SET attempt_count = ?, locked_until = ?""",
        (username, new_count, locked_until, new_count, locked_until)
    )
    conn.commit()