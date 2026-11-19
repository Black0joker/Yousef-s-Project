"""Appointments, tasks, and routines: the "what do I need to do today?" store.

Why this exists
---------------
Personal memory holds durable facts, but it is the wrong shape for scheduled
things. An appointment has a start and an end, a place, and a status that
changes; a routine has a cadence. Storing those as free-text facts loses the
structure needed to answer "what is on today?" and to drive a reminder.

Design
------
* All times are stored as ISO-8601 UTC strings (``...Z``). The browser renders
  them in local time. Nothing here assumes the server's timezone.
* Every row is scoped by ``user_id``; the tool handlers bind it from the
  authenticated request, so the model never receives or supplies a user id.
* Appointments are soft-cancelled (``status='cancelled'``) so a user can see
  what was removed; hard deletion is available through the API.
* SQL goes through :func:`_execute`, which logs statement text with ``?``
  placeholders only. Titles, places, and notes are personal data and are never
  logged.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import memory

logger = logging.getLogger(__name__)

MAX_TITLE = 200
MAX_PLACE = 200
MAX_NOTES = 2000
MAX_CADENCE = 100
MAX_WINDOW = 100

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

STATUSES = ("pending", "done", "cancelled")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS appointments (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    starts_at  TEXT NOT NULL,
    ends_at    TEXT,
    place      TEXT,
    notes      TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_appointments_user_start
ON appointments(user_id, starts_at);

CREATE TABLE IF NOT EXISTS routines (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    label      TEXT NOT NULL,
    cadence    TEXT NOT NULL,
    window     TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routines_user
ON routines(user_id);
"""


# --- helpers --------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id() -> str:
    return memory._new_id()


def _execute(
    db: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] = (),
    *,
    operation: str,
) -> sqlite3.Cursor:
    """Execute SQL through the shared logger (statement text only)."""
    return memory._execute(db, sql, params, operation=operation)


def _user_id(user_id: Any) -> int:
    return memory._user_id(user_id)


def _timestamp(value: Any, field: str) -> str:
    """Required ISO-8601 timestamp, normalized to UTC."""
    parsed = memory._timestamp(value, field)
    if parsed is None:
        raise memory.MemoryError(f"{field} is required.")
    return parsed


def _optional_timestamp(value: Any, field: str) -> Optional[str]:
    return memory._timestamp(value, field)


def _limit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, number))


def _status(value: Any) -> str:
    if value is None:
        return "pending"
    if not isinstance(value, str) or value.strip().lower() not in STATUSES:
        raise memory.MemoryError(
            "status must be one of: " + ", ".join(STATUSES) + "."
        )
    return value.strip().lower()


def _appointment_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "place": row["place"],
        "notes": row["notes"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


def _routine_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "label": row["label"],
        "cadence": row["cadence"],
        "window": row["window"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


# --- schema ---------------------------------------------------------------


def init_db(connection: Optional[sqlite3.Connection] = None) -> None:
    """Create the schedule tables if they do not exist yet."""
    with memory._db(connection) as db:
        db.executescript(SCHEMA_SQL)
        db.commit()


# --- appointments ---------------------------------------------------------


def save_appointment(
    user_id: Any,
    title: Any,
    starts_at: Any,
    ends_at: Any = None,
    place: Any = None,
    notes: Any = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Create an appointment for the authenticated user."""
    key = _user_id(user_id)
    clean_title = memory._text(title, "title", MAX_TITLE)
    start = _timestamp(starts_at, "starts_at")
    end = _optional_timestamp(ends_at, "ends_at")
    if end is not None and end < start:
        raise memory.MemoryError("ends_at must not be before starts_at.")

    clean_place = memory._optional_text(place, "place", MAX_PLACE)
    clean_notes = memory._optional_text(notes, "notes", MAX_NOTES)
    appointment_id = _new_id()
    created_at = _now()

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            _execute(
                db,
                """
                INSERT INTO appointments
                    (id, user_id, title, starts_at, ends_at, place, notes, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (appointment_id, key, clean_title, start, end, clean_place,
                 clean_notes, created_at),
                operation="appointment.insert",
            )
            db.commit()
            row = _execute(
                db,
                "SELECT * FROM appointments WHERE id = ? AND user_id = ?",
                (appointment_id, key),
                operation="appointment.get",
            ).fetchone()
    finally:
        memory._current_user.reset(token)

    logger.info(
        "schedule.appointment.saved",
        extra={
            "event": "schedule.appointment.saved",
            "user_id": key,
            "has_place": bool(clean_place),
            "has_end": end is not None,
            "outcome": "ok",
        },
    )
    return _appointment_dict(row)


def list_appointments(
    user_id: Any,
    start: Any = None,
    end: Any = None,
    include_done: Any = False,
    include_cancelled: Any = False,
    limit: Any = DEFAULT_LIMIT,
    connection: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Appointments overlapping the ``[start, end)`` window, soonest first.

    ``start``/``end`` are ISO-8601; omit both to list everything upcoming from
    now. An appointment overlaps when it begins before ``end`` and ends after
    ``start``, so a long event that started yesterday still shows today.
    """
    key = _user_id(user_id)
    max_results = _limit(limit)
    lower = _optional_timestamp(start, "start")
    upper = _optional_timestamp(end, "end")
    done = memory._flag(include_done, "include_done")
    cancelled = memory._flag(include_cancelled, "include_cancelled")

    clauses = ["user_id = ?"]
    params: List[Any] = [key]
    if lower is not None:
        clauses.append("(ends_at IS NULL OR ends_at >= ?)")
        params.append(lower)
    else:
        clauses.append("starts_at >= ?")
        params.append(_now())
    if upper is not None:
        clauses.append("starts_at < ?")
        params.append(upper)
    if not done:
        clauses.append("status != 'done'")
    if not cancelled:
        clauses.append("status != 'cancelled'")
    params.append(max_results)

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = _execute(
                db,
                f"""
                SELECT * FROM appointments
                WHERE {' AND '.join(clauses)}
                ORDER BY starts_at ASC
                LIMIT ?
                """,
                params,
                operation="appointment.list",
            ).fetchall()
    finally:
        memory._current_user.reset(token)

    return [_appointment_dict(row) for row in rows]


def day_window(offset_minutes: Any = 0) -> Tuple[str, str]:
    """UTC bounds of "today" for a client whose local time is ``offset``.

    ``offset_minutes`` is minutes east of UTC (e.g. ``120`` for UTC+2, ``-300``
    for UTC-5), which is what ``Date.getTimezoneOffset()`` negated gives.
    """
    try:
        offset = int(offset_minutes)
    except (TypeError, ValueError):
        offset = 0
    offset = max(-14 * 60, min(14 * 60, offset))
    local_now = datetime.now(timezone.utc) + timedelta(minutes=offset)
    local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_end = local_start + timedelta(days=1)
    return (
        (local_start - timedelta(minutes=offset)).isoformat(timespec="seconds").replace("+00:00", "Z"),
        (local_end - timedelta(minutes=offset)).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )


def today_appointments(
    user_id: Any,
    offset_minutes: Any = 0,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Everything on the caller's local day, plus the window used."""
    start, end = day_window(offset_minutes)
    items = list_appointments(
        user_id, start=start, end=end, connection=connection
    )
    return {"from": start, "to": end, "appointments": items}


def set_appointment_status(
    user_id: Any,
    appointment_id: Any,
    status: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Change an appointment's status; ``None`` when it is not the caller's."""
    key = _user_id(user_id)
    new_status = _status(status)
    if not isinstance(appointment_id, str) or not appointment_id.strip():
        return None

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            cursor = _execute(
                db,
                "UPDATE appointments SET status = ? WHERE id = ? AND user_id = ?",
                (new_status, appointment_id.strip(), key),
                operation="appointment.status",
            )
            db.commit()
            if cursor.rowcount == 0:
                return None
            row = _execute(
                db,
                "SELECT * FROM appointments WHERE id = ? AND user_id = ?",
                (appointment_id.strip(), key),
                operation="appointment.get",
            ).fetchone()
    finally:
        memory._current_user.reset(token)

    logger.info(
        "schedule.appointment.status",
        extra={
            "event": "schedule.appointment.status",
            "user_id": key,
            "status": new_status,
            "outcome": "ok",
        },
    )
    return _appointment_dict(row) if row else None


def complete_appointment(
    user_id: Any,
    appointment_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    return set_appointment_status(user_id, appointment_id, "done", connection=connection)


def delete_appointment(
    user_id: Any,
    appointment_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> bool:
    """Hard-delete one of the caller's appointments."""
    key = _user_id(user_id)
    if not isinstance(appointment_id, str) or not appointment_id.strip():
        return False
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            cursor = _execute(
                db,
                "DELETE FROM appointments WHERE id = ? AND user_id = ?",
                (appointment_id.strip(), key),
                operation="appointment.delete",
            )
            db.commit()
            return cursor.rowcount > 0
    finally:
        memory._current_user.reset(token)


# --- routines -------------------------------------------------------------


def save_routine(
    user_id: Any,
    label: Any,
    cadence: Any,
    window: Any = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Record a recurring routine, e.g. "morning walk", daily, 08:00-09:00."""
    key = _user_id(user_id)
    clean_label = memory._text(label, "label", MAX_TITLE)
    clean_cadence = memory._text(cadence, "cadence", MAX_CADENCE)
    clean_window = memory._optional_text(window, "window", MAX_WINDOW)
    routine_id = _new_id()
    created_at = _now()

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            _execute(
                db,
                """
                INSERT INTO routines (id, user_id, label, cadence, window, active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (routine_id, key, clean_label, clean_cadence, clean_window, created_at),
                operation="routine.insert",
            )
            db.commit()
            row = _execute(
                db,
                "SELECT * FROM routines WHERE id = ? AND user_id = ?",
                (routine_id, key),
                operation="routine.get",
            ).fetchone()
    finally:
        memory._current_user.reset(token)

    logger.info(
        "schedule.routine.saved",
        extra={"event": "schedule.routine.saved", "user_id": key, "outcome": "ok"},
    )
    return _routine_dict(row)


def list_routines(
    user_id: Any,
    include_inactive: Any = False,
    connection: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    key = _user_id(user_id)
    clauses = ["user_id = ?"]
    if not memory._flag(include_inactive, "include_inactive"):
        clauses.append("active = 1")

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = _execute(
                db,
                f"SELECT * FROM routines WHERE {' AND '.join(clauses)} ORDER BY created_at ASC",
                (key,),
                operation="routine.list",
            ).fetchall()
    finally:
        memory._current_user.reset(token)

    return [_routine_dict(row) for row in rows]


def delete_routine(
    user_id: Any, routine_id: Any, connection: Optional[sqlite3.Connection] = None
) -> bool:
    key = _user_id(user_id)
    if not isinstance(routine_id, str) or not routine_id.strip():
        return False
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            cursor = _execute(
                db,
                "DELETE FROM routines WHERE id = ? AND user_id = ?",
                (routine_id.strip(), key),
                operation="routine.delete",
            )
            db.commit()
            return cursor.rowcount > 0
    finally:
        memory._current_user.reset(token)


# --- agent tool definitions ----------------------------------------------


SAVE_APPOINTMENT_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "save_appointment",
        "description": (
            "Save an appointment, task, or event the user explicitly mentioned, with a "
            "start time. Use this for anything scheduled ('dentist on Tuesday at 4') "
            "instead of save_memory, so it can be listed and completed. Times must be "
            "ISO-8601; include the user's timezone offset when you know it. "
            "The server stores UTC and the UI renders local time."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": f"Short description, e.g. Dentist appointment (max {MAX_TITLE}).",
                },
                "starts_at": {
                    "type": "string",
                    "description": "ISO-8601 start time, e.g. 2026-05-04T16:00:00+02:00.",
                },
                "ends_at": {
                    "type": "string",
                    "description": "ISO-8601 end time; omit when the duration is unknown.",
                },
                "place": {
                    "type": "string",
                    "description": f"Where it happens (max {MAX_PLACE}).",
                },
                "notes": {
                    "type": "string",
                    "description": f"Anything else worth remembering (max {MAX_NOTES}).",
                },
            },
            "required": ["title", "starts_at"],
        },
    },
}


LIST_APPOINTMENTS_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_appointments",
        "description": (
            "List the signed-in user's upcoming appointments. Call this before answering "
            "'what do I have today/this week?'. Omit the dates for everything from now "
            "on. Returns soonest first, and never includes completed or cancelled items "
            "unless asked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start": {
                    "type": "string",
                    "description": "ISO-8601 lower bound; omit to start from now.",
                },
                "end": {
                    "type": "string",
                    "description": "ISO-8601 upper bound; omit for no upper limit.",
                },
                "include_done": {
                    "type": "boolean",
                    "description": "Also return completed appointments. Default false.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "description": f"Maximum rows (default {DEFAULT_LIMIT}).",
                },
            },
        },
    },
}


COMPLETE_APPOINTMENT_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "complete_appointment",
        "description": (
            "Mark one of the user's appointments as done. Use the id returned by "
            "list_appointments or save_appointment. Call list_appointments first if you "
            "do not already have the id."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "The appointment id to mark done.",
                },
            },
            "required": ["appointment_id"],
        },
    },
}


TOOL_SCHEMAS: Sequence[Dict[str, Any]] = (
    SAVE_APPOINTMENT_SCHEMA,
    LIST_APPOINTMENTS_SCHEMA,
    COMPLETE_APPOINTMENT_SCHEMA,
)


def prompt_brief() -> str:
    """Compact schedule reference appended to every system prompt."""
    return f"""### Schedule data model (reference)

Two stores, both scoped to the signed-in user:

- `appointments` — a scheduled item with `title` (max {MAX_TITLE}), `starts_at`, optional `ends_at`, `place` (max {MAX_PLACE}), and `notes` (max {MAX_NOTES}). Status is `pending`, `done`, or `cancelled`.
- `routines` — a repeating habit with `label`, `cadence` (e.g. daily, weekdays), and an optional `window` (e.g. 08:00-09:00).

Anything with a date or time belongs in `save_appointment`, not `save_memory`. Use `save_memory` for durable facts such as a birthday or a preference, and `save_appointment` for a specific event or task.

`list_appointments` accepts `start`, `end`, `include_done`, and `limit` (1–{MAX_LIMIT}, default {DEFAULT_LIMIT}). `complete_appointment` takes `appointment_id`. Ids, the user id, status, and timestamps are server-managed: never invent or send them, and never send a `user_id`.

Times are stored in UTC and shown to the user in their local time. When the user gives a local time, include its offset in `starts_at`; never guess a timezone."""


def _tool_args(kwargs: Mapping[str, Any], allowed: Sequence[str]) -> Dict[str, Any]:
    """Keep only the documented tool parameters.

    A model that invents ``user_id`` (or any other key) must not be able to
    reach the handler with it; identity is bound by the caller, never supplied.
    """
    return {name: kwargs[name] for name in allowed if name in kwargs}


def handlers(
    user_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Build the schedule tool handlers bound to one authenticated user."""

    def save_appointment_tool(**kwargs: Any) -> Dict[str, Any]:
        return save_appointment(
            user_id,
            connection=connection,
            **_tool_args(kwargs, ("title", "starts_at", "ends_at", "place", "notes")),
        )

    def list_appointments_tool(**kwargs: Any) -> Dict[str, Any]:
        items = list_appointments(
            user_id,
            connection=connection,
            **_tool_args(
                kwargs, ("start", "end", "include_done", "include_cancelled", "limit")
            ),
        )
        return {"appointments": items, "count": len(items)}

    def complete_appointment_tool(**kwargs: Any) -> Dict[str, Any]:
        appointment_id = kwargs.get("appointment_id")
        result = complete_appointment(user_id, appointment_id, connection=connection)
        if result is None:
            raise memory.MemoryError("No such appointment for this account.")
        return result

    return {
        "save_appointment": save_appointment_tool,
        "list_appointments": list_appointments_tool,
        "complete_appointment": complete_appointment_tool,
    }


def toolset(
    user_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(schemas, handlers)`` for the schedule tools."""
    _user_id(user_id)  # fail fast on a missing/invalid request identity
    return [dict(schema) for schema in TOOL_SCHEMAS], handlers(
        user_id, connection=connection
    )
