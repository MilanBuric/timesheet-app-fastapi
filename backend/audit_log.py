"""
Records every create/update/delete across the app's business data — who
did it, when, to what, and a short human-readable summary of what
actually changed. This is deliberately NOT a generic "request log": it's
written explicitly at each mutation point in main.py, one call per
meaningful action, so every row reads like a sentence a manager can
actually understand later ("Changed hourly rate for intern from 15.0 to
22.5") instead of a raw before/after diff they'd have to interpret.

Design notes:
- `actor_username` is stored as a snapshot at write time, not just
  `actor_id` — if that user is later deleted, the log entry stays
  readable ("Deleted user "bob"") instead of pointing at a dangling id
  with no name attached.
- `entity_id` is TEXT, not INTEGER — most actions affect a single row's
  integer id, but a cancelled recurring meeting series is identified by
  its recurrence_group_id (a uuid string), so the column has to hold
  either.
- No separate structured "details" column: everything worth recording is
  written straight into `summary` as plain text. A parallel JSON diff
  column would need its own display logic in the frontend for little
  benefit over a well-written sentence.
- This module only ever appends rows — nothing here ever reads back or
  prunes the log; that's main.py's /audit-log endpoint's job.
"""
from datetime import datetime


def record(conn, actor: dict, action: str, entity_type: str, entity_id, summary: str) -> None:
    """Writes one audit log row and commits immediately, so an audit
    entry is durable even if something later in the same request fails.
    `actor` is the current_user dict from get_current_user/require_manager
    — never None in practice, since every mutating endpoint requires
    authentication, but tolerated as None for any future system/
    background-triggered action that has no human actor.
    """
    conn.execute(
        """INSERT INTO audit_log (created_at, actor_id, actor_username, action, entity_type, entity_id, summary)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            datetime.utcnow().isoformat(),
            actor["id"] if actor else None,
            actor["username"] if actor else None,
            action,
            entity_type,
            str(entity_id) if entity_id is not None else None,
            summary,
        )
    )
    conn.commit()