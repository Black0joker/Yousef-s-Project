"""Trusted contacts: let a user share their activity with people they choose.

Why this exists
---------------
A user can designate someone (by email) who is allowed to see their last-seen
time and, optionally, their last location. That is the only way a supporter can
notice "they have not been active for a day".

Design
------
* A trusted contact is a row on the *owner* linking an email to a viewer. The
  invite is keyed on the email, not a user id, so it works before the other
  person has registered.
* Nothing is visible until **three gates** pass: the invite is accepted, it is
  not revoked, and the owner grants the ``trusted_sharing`` consent.
* This module is the only place in the codebase that deliberately reads one
  user's row for another. Every read re-evaluates the gates; nothing is cached
  and nothing is trusted from the client.
* An owner learns nothing about whether an email has an account: creating an
  invite answers the same way either way, so it cannot be used as an oracle.
* An unknown or inactive owner is reported as "no activity yet", never as a
  warning. The requirements forbid inferring danger from limited signals.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import memory

logger = logging.getLogger(__name__)

MAX_EMAIL = 254
MAX_LABEL = 200

STATUSES = ("pending", "accepted", "revoked")

#: How long without any activity before a viewer sees a warning.
INACTIVITY_WARNING_HOURS = 24

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trusted_contacts (
    id                TEXT PRIMARY KEY,
    owner_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    email             TEXT NOT NULL,
    can_see_location  INTEGER NOT NULL DEFAULT 1,
    can_see_last_seen INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL,
    accepted_at       TEXT,
    revoked_at        TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_trusted_owner_email
ON trusted_contacts(owner_id, email);

CREATE INDEX IF NOT EXISTS idx_trusted_email
ON trusted_contacts(email);
"""


# --- helpers --------------------------------------------------------------


def _new_id() -> str:
    return memory._new_id()


def _now() -> str:
    return memory._now()


def _user_id(user_id: Any) -> int:
    return memory._user_id(user_id)


def _email(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise memory.MemoryError("An email address is required.")
    cleaned = value.strip().lower()
    if len(cleaned) > MAX_EMAIL or "@" not in cleaned:
        raise memory.MemoryError("Enter a valid email address.")
    return cleaned


def _flag(value: Any, field: str, default: bool = True) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise memory.MemoryError(f"{field} must be true or false.")
    return value


def _contact_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "can_see_location": bool(row["can_see_location"]),
        "can_see_last_seen": bool(row["can_see_last_seen"]),
        "status": row["status"],
        "created_at": row["created_at"],
        "accepted_at": row["accepted_at"],
        "revoked_at": row["revoked_at"],
    }


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --- schema ---------------------------------------------------------------


def init_db(connection: Optional[sqlite3.Connection] = None) -> None:
    """Create the trusted-contacts table if it does not exist yet."""
    with memory._db(connection) as db:
        db.executescript(SCHEMA_SQL)
        db.commit()


# --- owner side -----------------------------------------------------------


def invite(
    user_id: Any,
    email: Any,
    can_see_location: Any = True,
    can_see_last_seen: Any = True,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Invite someone by email. Returns the row whether or not they exist.

    Deliberately does not reveal whether the address has an account: an owner
    must not be able to use this endpoint to enumerate users.
    """
    key = _user_id(user_id)
    address = _email(email)
    see_location = _flag(can_see_location, "can_see_location")
    see_last_seen = _flag(can_see_last_seen, "can_see_last_seen")

    # inviting yourself is meaningless and would expose your own row twice
    owner_email = _owner_email(key, connection)
    if owner_email and owner_email == address:
        raise memory.MemoryError("You cannot add your own email as a trusted contact.")

    contact_id = _new_id()
    created_at = _now()
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            memory._execute(
                db,
                """
                INSERT INTO trusted_contacts
                    (id, owner_id, email, can_see_location, can_see_last_seen,
                     status, created_at)
                VALUES (?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    contact_id,
                    key,
                    address,
                    int(see_location),
                    int(see_last_seen),
                    created_at,
                ),
                operation="trusted.invite",
            )
            db.commit()
            row = memory._execute(
                db,
                "SELECT * FROM trusted_contacts WHERE id = ? AND owner_id = ?",
                (contact_id, key),
                operation="trusted.get",
            ).fetchone()
    finally:
        memory._current_user.reset(token)

    logger.info(
        "trusted.invited",
        extra={
            "event": "trusted.invited",
            "user_id": key,
            "can_see_location": see_location,
            "outcome": "ok",
        },
    )
    return _contact_dict(row)


def _owner_email(user_id: int, connection: Optional[sqlite3.Connection]) -> Optional[str]:
    with memory._db(connection) as db:
        row = memory._execute(
            db,
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
            operation="trusted.owner_email",
        ).fetchone()
    return row["email"].strip().lower() if row else None


def list_trusted(
    user_id: Any, connection: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """People this user has invited (any status)."""
    key = _user_id(user_id)
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = memory._execute(
                db,
                """
                SELECT * FROM trusted_contacts
                WHERE owner_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (key, DEFAULT_LIMIT),
                operation="trusted.list",
            ).fetchall()
    finally:
        memory._current_user.reset(token)

    return [_contact_dict(row) for row in rows]


def revoke(
    user_id: Any, contact_id: Any, connection: Optional[sqlite3.Connection] = None
) -> bool:
    """Owner-side revocation."""
    return _set_status(user_id, contact_id, "revoked", owner=True, connection=connection)


def _set_status(
    user_id: Any,
    contact_id: Any,
    status: str,
    *,
    owner: bool,
    connection: Optional[sqlite3.Connection],
) -> bool:
    key = _user_id(user_id)
    if status not in STATUSES:
        raise memory.MemoryError("Unknown status.")
    if not isinstance(contact_id, str) or not contact_id.strip():
        return False

    column = "owner_id" if owner else "id"
    stamp = _now()
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            if status == "accepted":
                cursor = memory._execute(
                    db,
                    """
                    UPDATE trusted_contacts
                       SET status = 'accepted', accepted_at = ?, revoked_at = NULL
                     WHERE id = ? AND status = 'pending'
                    """,
                    (stamp, contact_id.strip()),
                    operation="trusted.accept",
                )
            else:
                cursor = memory._execute(
                    db,
                    f"""
                    UPDATE trusted_contacts
                       SET status = 'revoked', revoked_at = ?
                     WHERE id = ? AND {column} = ?
                    """,
                    (stamp, contact_id.strip(), key),
                    operation="trusted.revoke",
                )
            db.commit()
            return cursor.rowcount > 0
    finally:
        memory._current_user.reset(token)


# --- viewer side ----------------------------------------------------------


def invitations(
    user_id: Any, connection: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """Pending invitations addressed to this user's email."""
    key = _user_id(user_id)
    address = _owner_email(key, connection)
    if not address:
        return []

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = memory._execute(
                db,
                """
                SELECT * FROM trusted_contacts
                WHERE email = ? AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (address, DEFAULT_LIMIT),
                operation="trusted.invitations",
            ).fetchall()
    finally:
        memory._current_user.reset(token)

    return [_contact_dict(row) for row in rows]


def _owned_by_email(
    user_id: int, contact_id: str, connection: Optional[sqlite3.Connection]
) -> Optional[sqlite3.Row]:
    """The invitation row, only when it is addressed to ``user_id``'s email."""
    address = _owner_email(user_id, connection)
    if not address:
        return None
    with memory._db(connection) as db:
        return memory._execute(
            db,
            "SELECT * FROM trusted_contacts WHERE id = ? AND email = ?",
            (contact_id, address),
            operation="trusted.by_email",
        ).fetchone()


def accept(
    user_id: Any, contact_id: Any, connection: Optional[sqlite3.Connection] = None
) -> bool:
    """Accept an invitation addressed to this user's email."""
    key = _user_id(user_id)
    if not isinstance(contact_id, str) or not contact_id.strip():
        return False
    row = _owned_by_email(key, contact_id.strip(), connection)
    if row is None or row["status"] != "pending":
        return False
    return _set_status(key, contact_id, "accepted", owner=False, connection=connection)


def decline(
    user_id: Any, contact_id: Any, connection: Optional[sqlite3.Connection] = None
) -> bool:
    """Decline (revoke) an invitation addressed to this user's email."""
    key = _user_id(user_id)
    if not isinstance(contact_id, str) or not contact_id.strip():
        return False
    row = _owned_by_email(key, contact_id.strip(), connection)
    if row is None or row["status"] != "pending":
        return False

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            cursor = memory._execute(
                db,
                """
                UPDATE trusted_contacts
                   SET status = 'revoked', revoked_at = ?
                 WHERE id = ? AND email = ? AND status = 'pending'
                """,
                (_now(), contact_id.strip(), row["email"]),
                operation="trusted.decline",
            )
            db.commit()
            return cursor.rowcount > 0
    finally:
        memory._current_user.reset(token)


def watching(
    user_id: Any, connection: Optional[sqlite3.Connection] = None
) -> List[Dict[str, Any]]:
    """Owners who have accepted this user as a trusted contact.

    Only returns rows whose owner still grants ``trusted_sharing``; a revoked
    consent hides the relationship entirely rather than showing it greyed out.
    """
    key = _user_id(user_id)
    address = _owner_email(key, connection)
    if not address:
        return []

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = memory._execute(
                db,
                """
                SELECT c.*, u.first_name, u.last_name
                FROM trusted_contacts c
                JOIN users u ON u.id = c.owner_id
                WHERE c.email = ? AND c.status = 'accepted'
                ORDER BY c.accepted_at DESC
                LIMIT ?
                """,
                (address, DEFAULT_LIMIT),
                operation="trusted.watching",
            ).fetchall()
    finally:
        memory._current_user.reset(token)

    result: List[Dict[str, Any]] = []
    for row in rows:
        if not memory.get_consent(row["owner_id"], "trusted_sharing", connection=connection):
            continue
        result.append(
            {
                "id": row["id"],
                "owner_id": row["owner_id"],
                "name": f"{row['first_name']} {row['last_name']}".strip(),
                "can_see_location": bool(row["can_see_location"]),
                "can_see_last_seen": bool(row["can_see_last_seen"]),
                "accepted_at": row["accepted_at"],
            }
        )
    return result


# --- the cross-user read --------------------------------------------------


def _last_seen(row: sqlite3.Row) -> Optional[str]:
    """The newer of the two activity timestamps, or ``None``."""
    candidates = [_parse(row["last_message_at"]), _parse(row["last_location_at"])]
    present = [item for item in candidates if item is not None]
    if not present:
        return None
    return _iso(max(present))


def peek(
    user_id: Any,
    contact_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """What a viewer may see about one owner, or ``None`` when not permitted.

    Re-checks all three gates on every call: accepted, not revoked, and the
    owner still grants ``trusted_sharing``. This is the only place one user's
    row is read for another, so the gate must never be skipped or cached.
    """
    key = _user_id(user_id)
    if not isinstance(contact_id, str) or not contact_id.strip():
        return None

    address = _owner_email(key, connection)
    if not address:
        return None

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            row = memory._execute(
                db,
                """
                SELECT c.*, u.first_name, u.last_name,
                       u.last_message_at, u.last_location_at,
                       u.last_latitude, u.last_longitude
                FROM trusted_contacts c
                JOIN users u ON u.id = c.owner_id
                WHERE c.id = ? AND c.email = ? AND c.status = 'accepted'
                """,
                (contact_id.strip(), address),
                operation="trusted.peek",
            ).fetchone()
    finally:
        memory._current_user.reset(token)

    if row is None:
        return None
    if not memory.get_consent(row["owner_id"], "trusted_sharing", connection=connection):
        return None

    last_seen = _last_seen(row)
    last_seen_at = _parse(last_seen)
    now = datetime.now(timezone.utc)
    stale = last_seen_at is not None and (now - last_seen_at) > timedelta(
        hours=INACTIVITY_WARNING_HOURS
    )

    location = None
    if row["can_see_location"] and row["last_latitude"] is not None:
        location = {
            "latitude": row["last_latitude"],
            "longitude": row["last_longitude"],
            "at": row["last_location_at"],
        }

    return {
        "contact_id": row["id"],
        "owner": {
            "id": row["owner_id"],
            "name": f"{row['first_name']} {row['last_name']}".strip(),
        },
        "last_seen": last_seen if row["can_see_last_seen"] else None,
        "location": location,
        "warning": stale,
        "warning_hours": INACTIVITY_WARNING_HOURS,
        # A user who has never been active is "unknown", not "in danger".
        "status": "unknown" if last_seen_at is None else ("stale" if stale else "active"),
    }
