"""Familiar places and location context.

Why this exists
---------------
Location pings were write-only: the server stored a coordinate and nothing ever
read it. This module turns a position into something useful — "you are at Mom's
house; last time you were here you noted the spare key is under the pot".

Design
------
* A *place* is a named circle: latitude, longitude, and a radius in metres.
  The user creates one from their current position in the UI, so the browser
  supplies the coordinates rather than the model (a model has no position and
  would have to invent one).
* Matching runs **on the server** with the haversine distance, so the browser
  never receives a list of every place's coordinates. The context payload
  deliberately omits latitude/longitude entirely.
* Memories are attached by name: a place called "Mom's house" picks up the
  facts of the entity with the same name. That reuses the memory store instead
  of duplicating it.
* **Never infer danger.** Being at an unknown place produces no context at all;
  it is not a signal of confusion or risk. There is no "unsafe" state here.
* Coordinates are personal data: they are never logged.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from typing import Any, Dict, List, Optional

import memory
import schedule

logger = logging.getLogger(__name__)

MAX_NAME = 200
MAX_NOTES = 2000

DEFAULT_RADIUS_M = 150
MIN_RADIUS_M = 10
MAX_RADIUS_M = 50_000

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

EARTH_RADIUS_M = 6_371_000.0

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS places (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    latitude   REAL NOT NULL,
    longitude  REAL NOT NULL,
    radius_m   REAL NOT NULL DEFAULT 150,
    notes      TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_places_user
ON places(user_id);
"""


# --- helpers --------------------------------------------------------------


def _new_id() -> str:
    return memory._new_id()


def _now() -> str:
    return memory._now()


def _user_id(user_id: Any) -> int:
    return memory._user_id(user_id)


def _radius(value: Any) -> float:
    if value is None:
        return float(DEFAULT_RADIUS_M)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise memory.MemoryError("radius_m must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise memory.MemoryError("radius_m must be a finite number.")
    if not MIN_RADIUS_M <= number <= MAX_RADIUS_M:
        raise memory.MemoryError(
            f"radius_m must be between {MIN_RADIUS_M} and {MAX_RADIUS_M}."
        )
    return number


def _coordinate(value: Any, limit: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise memory.MemoryError(f"{field} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise memory.MemoryError(f"{field} must be a finite number.")
    if not -limit <= number <= limit:
        raise memory.MemoryError(f"{field} must be between -{limit:g} and {limit:g}.")
    return number


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in metres."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a)))


def _place_dict(row: sqlite3.Row, include_coordinates: bool = True) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "id": row["id"],
        "name": row["name"],
        "radius_m": row["radius_m"],
        "notes": row["notes"],
        "created_at": row["created_at"],
    }
    if include_coordinates:
        item["latitude"] = row["latitude"]
        item["longitude"] = row["longitude"]
    return item


# --- schema ---------------------------------------------------------------


def init_db(connection: Optional[sqlite3.Connection] = None) -> None:
    """Create the places table if it does not exist yet."""
    with memory._db(connection) as db:
        db.executescript(SCHEMA_SQL)
        db.commit()


# --- CRUD -----------------------------------------------------------------


def save_place(
    user_id: Any,
    name: Any,
    latitude: Any,
    longitude: Any,
    radius_m: Any = None,
    notes: Any = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Save a named place for the authenticated user."""
    key = _user_id(user_id)
    clean_name = memory._text(name, "name", MAX_NAME)
    lat = _coordinate(latitude, 90.0, "latitude")
    lng = _coordinate(longitude, 180.0, "longitude")
    radius = _radius(radius_m)
    clean_notes = memory._optional_text(notes, "notes", MAX_NOTES)
    place_id = _new_id()
    created_at = _now()

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            memory._execute(
                db,
                """
                INSERT INTO places
                    (id, user_id, name, latitude, longitude, radius_m, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (place_id, key, clean_name, lat, lng, radius, clean_notes, created_at),
                operation="place.insert",
            )
            db.commit()
            row = memory._execute(
                db,
                "SELECT * FROM places WHERE id = ? AND user_id = ?",
                (place_id, key),
                operation="place.get",
            ).fetchone()
    finally:
        memory._current_user.reset(token)

    logger.info(
        "place.saved",
        extra={
            "event": "place.saved",
            "user_id": key,
            "has_notes": bool(clean_notes),
            "outcome": "ok",
        },
    )
    return _place_dict(row)


def list_places(
    user_id: Any,
    limit: Any = DEFAULT_LIMIT,
    connection: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    key = _user_id(user_id)
    try:
        max_results = max(1, min(MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        max_results = DEFAULT_LIMIT

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = memory._execute(
                db,
                """
                SELECT * FROM places
                WHERE user_id = ?
                ORDER BY name COLLATE NOCASE ASC
                LIMIT ?
                """,
                (key, max_results),
                operation="place.list",
            ).fetchall()
    finally:
        memory._current_user.reset(token)

    return [_place_dict(row) for row in rows]


def delete_place(
    user_id: Any, place_id: Any, connection: Optional[sqlite3.Connection] = None
) -> bool:
    key = _user_id(user_id)
    if not isinstance(place_id, str) or not place_id.strip():
        return False
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            cursor = memory._execute(
                db,
                "DELETE FROM places WHERE id = ? AND user_id = ?",
                (place_id.strip(), key),
                operation="place.delete",
            )
            db.commit()
            return cursor.rowcount > 0
    finally:
        memory._current_user.reset(token)


# --- matching -------------------------------------------------------------


def find_place(
    user_id: Any,
    latitude: Any,
    longitude: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """The nearest saved place whose radius contains the point, if any.

    Returns ``None`` for an unfamiliar location — that is a neutral outcome, not
    an error and not a signal of danger.
    """
    key = _user_id(user_id)
    lat = _coordinate(latitude, 90.0, "latitude")
    lng = _coordinate(longitude, 180.0, "longitude")

    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = memory._execute(
                db,
                "SELECT * FROM places WHERE user_id = ?",
                (key,),
                operation="place.match",
            ).fetchall()
    finally:
        memory._current_user.reset(token)

    best: Optional[Dict[str, Any]] = None
    best_distance = None
    for row in rows:
        metres = distance_m(lat, lng, row["latitude"], row["longitude"])
        if metres > row["radius_m"]:
            continue
        if best_distance is None or metres < best_distance:
            best_distance = metres
            best = _place_dict(row, include_coordinates=False)

    if best is not None:
        best["distance_m"] = round(best_distance, 1) if best_distance is not None else None
    return best


def _memories_for_place(
    user_id: Any, place_name: str, connection: Optional[sqlite3.Connection]
) -> List[Dict[str, Any]]:
    """Facts of an entity that shares the place's name (best effort)."""
    try:
        found = memory.query_memory(
            user_id, entity=place_name, limit=10, connection=connection
        )
    except memory.MemoryError:
        return []
    return found.get("facts", [])


def context_at(
    user_id: Any,
    latitude: Any,
    longitude: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Context to surface when the user is at a familiar place.

    ``None`` when the position matches no saved place. The payload never
    contains coordinates: the browser already knows where it is, and returning
    the place's position would leak the user's own saved data needlessly.
    """
    place = find_place(user_id, latitude, longitude, connection=connection)
    if place is None:
        return None

    memories = _memories_for_place(user_id, place["name"], connection)
    upcoming: List[Dict[str, Any]] = []
    try:
        upcoming = schedule.list_appointments(user_id, limit=1, connection=connection)
    except memory.MemoryError:
        upcoming = []

    return {
        "place": place,
        "memories": memories,
        "appointment": upcoming[0] if upcoming else None,
    }
