"""Personal memory: entities, relationships, facts, and agent tools.

Storage is SQLite (the same database as :mod:`auth`). The PostgreSQL-style
schema from the product spec maps to SQLite types as follows:

* UUIDs are stored as canonical UUID strings in ``TEXT`` columns.
* ``JSONB`` values are stored as JSON text and decoded on read.
* ``TIMESTAMPTZ`` values are ISO-8601 UTC strings.

Every row is scoped by ``user_id``. Tool handlers bind that id from the
authenticated request, so the model can never read or write another user's
memory.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import auth

logger = logging.getLogger(__name__)
_current_user: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "memory_current_user", default=None
)

# --- limits ---------------------------------------------------------------

MAX_ENTITIES_PER_CALL = 25
MAX_RELATIONSHIPS_PER_CALL = 50
MAX_FACTS_PER_CALL = 100

MAX_NAME = 200
MAX_ENTITY_TYPE = 50
MAX_RELATIONSHIP_TYPE = 100
MAX_PREDICATE = 100
MAX_SOURCE = 500
MAX_QUERY = 200
MAX_VALUE_BYTES = 16_000

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

SELF_ENTITY_NAME = "Current User"
SELF_ENTITY_TYPE = "Person"
SELF_ALIASES = frozenset({"current user", "me", "i", "myself", "self", "أنا", "المستخدم الحالي"})


class MemoryError(ValueError):
    """Validation or lookup failure that is safe to report to the model."""


# --- schema ---------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entities (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_user_name
ON entities(user_id, name COLLATE NOCASE, entity_type COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_entities_user
ON entities(user_id);

CREATE TABLE IF NOT EXISTS relationships (
    id                 TEXT PRIMARY KEY,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    relationship_type  TEXT NOT NULL,
    created_at         TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_relationships_unique
ON relationships(user_id, source_entity_id, target_entity_id, relationship_type COLLATE NOCASE);

CREATE INDEX IF NOT EXISTS idx_relationships_user
ON relationships(user_id);

CREATE TABLE IF NOT EXISTS memory_facts (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate   TEXT NOT NULL,
    value       TEXT NOT NULL,
    confidence  REAL,
    source      TEXT,
    created_at  TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    valid_until TEXT
);

CREATE INDEX IF NOT EXISTS idx_memory_facts_user_entity
ON memory_facts(user_id, entity_id);

CREATE INDEX IF NOT EXISTS idx_memory_facts_predicate
ON memory_facts(predicate COLLATE NOCASE);
"""


# --- connection helpers ---------------------------------------------------


@contextmanager
def _db(connection: Optional[sqlite3.Connection] = None) -> Iterator[sqlite3.Connection]:
    """Use the request connection when possible; close connections we own."""
    if connection is not None:
        yield connection
        return

    try:
        from flask import has_app_context

        in_request = has_app_context()
    except Exception:  # Flask is required by the app, but keep this module testable
        in_request = False

    if in_request:
        yield auth.get_db()
        return

    owned = auth.connect()
    try:
        yield owned
    finally:
        owned.close()


def init_db(connection: Optional[sqlite3.Connection] = None) -> None:
    """Create the memory tables/indexes if they do not exist yet."""
    with _db(connection) as db:
        db.executescript(SCHEMA_SQL)
        db.commit()


# --- value / time helpers -------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


_SQL_OFF = frozenset({"0", "false", "no", "off"})


def _sql_logging_enabled() -> bool:
    return os.environ.get("LOG_SQL", "1").strip().lower() not in _SQL_OFF


def _normalize_sql(sql: str) -> str:
    return " ".join(sql.split())


def _execute(
    db: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] = (),
    *,
    operation: str,
) -> sqlite3.Cursor:
    """Execute SQL and log the statement text (placeholders only, no values)."""
    normalized = _normalize_sql(sql)
    started = time.perf_counter()
    try:
        cursor = db.execute(sql, params)
    except Exception:
        logger.exception(
            "memory.sql.error",
            extra={
                "event": "memory.sql.error",
                "user_id": _current_user.get(),
                "operation": operation,
                "sql": normalized,
                "duration_ms": _elapsed(started),
            },
        )
        raise
    if _sql_logging_enabled():
        logger.info(
            "memory.sql",
            extra={
                "event": "memory.sql",
                "user_id": _current_user.get(),
                "operation": operation,
                "sql": normalized,
                "duration_ms": _elapsed(started),
                "outcome": "ok",
            },
        )
    return cursor


def _text(value: Any, field: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryError(f"{field} must be a non-empty string.")
    cleaned = " ".join(value.split())
    if len(cleaned) > max_length:
        raise MemoryError(f"{field} is too long (max {max_length} characters).")
    return cleaned


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_text(value: Any, field: str, max_length: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MemoryError(f"{field} must be a string.")
    if not value.strip():
        return None
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise MemoryError(f"{field} is too long (max {max_length} characters).")
    return cleaned


def _user_id(user_id: Any) -> int:
    try:
        key = int(user_id)
    except (TypeError, ValueError) as exc:
        raise MemoryError("A valid authenticated user is required.") from exc
    if key <= 0:
        raise MemoryError("A valid authenticated user is required.")
    return key


def _timestamp(value: Any, field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MemoryError(f"{field} must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryError(f"{field} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_value(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise MemoryError("Fact value must be JSON-serializable.") from exc
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise MemoryError(f"Fact value is too large (max {MAX_VALUE_BYTES} bytes).")
    return encoded


def _confidence(value: Any) -> Optional[float]:
    if value is None:
        return 1.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MemoryError("confidence must be a number between 0 and 1.")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise MemoryError("confidence must be a number between 0 and 1.")
    return number


def _limit(value: Any) -> int:
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int):
        raise MemoryError("limit must be an integer.")
    return max(1, min(MAX_LIMIT, value))


def _flag(value: Any, field: str) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        raise MemoryError(f"{field} must be true or false.")
    return value


def _is_self(name: str) -> bool:
    return name.strip().lower() in SELF_ALIASES


def _entity_type(value: Any) -> str:
    if value is None:
        return "Person"
    return _text(value, "entity_type", MAX_ENTITY_TYPE)


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# --- row helpers ----------------------------------------------------------


def _entity_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "entity_type": row["entity_type"],
        "created_at": row["created_at"],
    }


def _relationship_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source_name"],
        "target": row["target_name"],
        "relationship_type": row["relationship_type"],
        "created_at": row["created_at"],
    }


def _fact_dict(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        value = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        value = row["value"]
    return {
        "id": row["id"],
        "entity": row["entity_name"],
        "entity_type": row["entity_type"],
        "predicate": row["predicate"],
        "value": value,
        "confidence": row["confidence"],
        "source": row["source"],
        "created_at": row["created_at"],
        "observed_at": row["observed_at"],
        "valid_until": row["valid_until"],
    }


def _find_entity(
    db: sqlite3.Connection, user_id: int, name: str, entity_type: str
) -> Optional[sqlite3.Row]:
    return _execute(
        db,
        """
        SELECT * FROM entities
        WHERE user_id = ? AND name = ? COLLATE NOCASE AND entity_type = ? COLLATE NOCASE
        """,
        (user_id, name, entity_type),
        operation="entity.find",
    ).fetchone()


def _get_or_create_entity(
    db: sqlite3.Connection, user_id: int, name: str, entity_type: str
) -> Tuple[sqlite3.Row, bool]:
    existing = _find_entity(db, user_id, name, entity_type)
    if existing is not None:
        return existing, False

    entity_id = _new_id()
    try:
        _execute(
            db,
            "INSERT INTO entities (id, user_id, name, entity_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (entity_id, user_id, name, entity_type, _now()),
            operation="entity.insert",
        )
    except sqlite3.IntegrityError:
        # A concurrent request may have created the same entity; reuse it.
        existing = _find_entity(db, user_id, name, entity_type)
        if existing is None:
            raise
        return existing, False

    row = _execute(
        db,
        "SELECT * FROM entities WHERE id = ? AND user_id = ?",
        (entity_id, user_id),
        operation="entity.get",
    ).fetchone()
    assert row is not None
    return row, True


def _resolve_entity(
    db: sqlite3.Connection, user_id: int, name: Any, entity_type: Any = None
) -> Tuple[sqlite3.Row, bool]:
    cleaned = _text(name, "entity name", MAX_NAME)
    if _is_self(cleaned):
        return _get_or_create_entity(db, user_id, SELF_ENTITY_NAME, SELF_ENTITY_TYPE)

    kind = _entity_type(entity_type)
    if entity_type is None:
        # A relationship/fact refers to an entity by name only. Prefer any
        # existing entity of that name instead of creating a duplicate Person.
        existing = _execute(
            db,
            """
            SELECT * FROM entities
            WHERE user_id = ? AND name = ? COLLATE NOCASE
            ORDER BY created_at ASC LIMIT 1
            """,
            (user_id, cleaned),
            operation="entity.resolve",
        ).fetchone()
        if existing is not None:
            return existing, False
    return _get_or_create_entity(db, user_id, cleaned, kind)


def ensure_self_entity(
    user_id: Any, connection: Optional[sqlite3.Connection] = None
) -> Dict[str, Any]:
    """Return the per-user ``Current User`` entity, creating it if needed."""
    key = _user_id(user_id)
    token = _current_user.set(key)
    try:
        with _db(connection) as db:
            row, _ = _get_or_create_entity(db, key, SELF_ENTITY_NAME, SELF_ENTITY_TYPE)
            db.commit()
            return _entity_dict(row)
    finally:
        _current_user.reset(token)


# --- write path -----------------------------------------------------------


def _list(value: Any, field: str, limit: int) -> List[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise MemoryError(f"{field} must be a list.")
    if len(value) > limit:
        raise MemoryError(f"Too many {field} (max {limit}).")
    return value


def _save_memory(
    key: int,
    payload: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Transactional write path; the public wrapper adds identity and logging."""
    if not isinstance(payload, Mapping):
        raise MemoryError("save_memory expects an object.")

    entities = _list(payload.get("entities"), "entities", MAX_ENTITIES_PER_CALL)
    relationships = _list(payload.get("relationships"), "relationships", MAX_RELATIONSHIPS_PER_CALL)
    facts = _list(payload.get("facts"), "facts", MAX_FACTS_PER_CALL)

    if not entities and not relationships and not facts:
        raise MemoryError("Provide at least one entity, relationship, or fact to save.")

    now = _now()
    touched_entities: Dict[str, sqlite3.Row] = {}
    created_entities = 0
    created_relationships = 0
    saved_facts: List[Dict[str, Any]] = []

    with _db(connection) as db:
        try:
            for item in entities:
                if not isinstance(item, Mapping):
                    raise MemoryError("Each entity must be an object.")
                row, created = _get_or_create_entity(
                    db,
                    key,
                    _text(item.get("name"), "entity name", MAX_NAME),
                    _entity_type(item.get("entity_type")),
                )
                touched_entities[row["id"]] = row
                created_entities += int(created)

            for item in relationships:
                if not isinstance(item, Mapping):
                    raise MemoryError("Each relationship must be an object.")
                source, source_created = _resolve_entity(db, key, item.get("source_entity"))
                target, target_created = _resolve_entity(db, key, item.get("target_entity"))
                touched_entities[source["id"]] = source
                touched_entities[target["id"]] = target
                created_entities += int(source_created) + int(target_created)

                relationship_type = _text(
                    item.get("relationship_type"), "relationship_type", MAX_RELATIONSHIP_TYPE
                )
                cursor = _execute(
                    db,
                    """
                    INSERT OR IGNORE INTO relationships
                        (id, user_id, source_entity_id, target_entity_id, relationship_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (_new_id(), key, source["id"], target["id"], relationship_type, now),
                    operation="relationship.insert",
                )
                created_relationships += int(cursor.rowcount > 0)

            for item in facts:
                if not isinstance(item, Mapping):
                    raise MemoryError("Each fact must be an object.")
                entity, entity_created = _resolve_entity(db, key, item.get("entity"))
                touched_entities[entity["id"]] = entity
                created_entities += int(entity_created)

                predicate = _text(item.get("predicate"), "predicate", MAX_PREDICATE)
                if "value" not in item:
                    raise MemoryError("Each fact needs a value.")
                value = _json_value(item.get("value"))
                confidence = _confidence(item.get("confidence"))
                source = _optional_text(item.get("source"), "source", MAX_SOURCE) or "user_message"
                observed_at = _timestamp(item.get("observed_at"), "observed_at") or now
                valid_until = _timestamp(item.get("valid_until"), "valid_until")

                if _flag(item.get("replace"), "replace"):
                    _execute(
                        db,
                        """
                        UPDATE memory_facts
                        SET valid_until = ?
                        WHERE user_id = ? AND entity_id = ? AND predicate = ? COLLATE NOCASE
                          AND (valid_until IS NULL OR valid_until > ?)
                        """,
                        (now, key, entity["id"], predicate, now),
                        operation="fact.expire",
                    )

                fact_id = _new_id()
                _execute(
                    db,
                    """
                    INSERT INTO memory_facts
                        (id, user_id, entity_id, predicate, value, confidence, source,
                         created_at, observed_at, valid_until)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (fact_id, key, entity["id"], predicate, value, confidence, source,
                     now, observed_at, valid_until),
                    operation="fact.insert",
                )
                row = _execute(
                    db,
                    """
                    SELECT f.*, e.name AS entity_name, e.entity_type AS entity_type
                    FROM memory_facts f JOIN entities e ON e.id = f.entity_id
                    WHERE f.id = ? AND f.user_id = ? AND e.user_id = ?
                    """,
                    (fact_id, key, key),
                    operation="fact.get",
                ).fetchone()
                assert row is not None
                saved_facts.append(_fact_dict(row))

            db.commit()
        except Exception:
            db.rollback()
            raise

    return {
        "saved": {
            "entities": created_entities,
            "relationships": created_relationships,
            "facts": len(saved_facts),
        },
        "entities": [_entity_dict(row) for row in touched_entities.values()],
        "facts": saved_facts,
    }


def save_memory(
    user_id: Any,
    payload: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Persist entities, relationships, and facts for one authenticated user.

    ``payload`` is the argument object from the ``save_memory`` tool. All
    records are written in one transaction; invalid input raises
    :class:`MemoryError` and leaves the database unchanged.
    """
    started = time.perf_counter()
    key: Optional[int] = None
    token = None
    try:
        key = _user_id(user_id)
        token = _current_user.set(key)
        result = _save_memory(key, payload, connection=connection)
    except MemoryError as exc:
        logger.warning(
            "memory.save.invalid",
            extra={
                "event": "memory.save.invalid",
                "user_id": key,
                "error_type": type(exc).__name__,
                "reason": str(exc),
                "duration_ms": _elapsed(started),
            },
        )
        raise
    except Exception:
        logger.exception(
            "memory.save.error",
            extra={
                "event": "memory.save.error",
                "user_id": key,
                "duration_ms": _elapsed(started),
            },
        )
        raise
    finally:
        if token is not None:
            _current_user.reset(token)

    input_entities = payload.get("entities") if isinstance(payload, Mapping) else None
    input_relationships = payload.get("relationships") if isinstance(payload, Mapping) else None
    input_facts = payload.get("facts") if isinstance(payload, Mapping) else None
    logger.info(
        "memory.save",
        extra={
            "event": "memory.save",
            "user_id": key,
            "input_entities": len(input_entities) if isinstance(input_entities, list) else 0,
            "input_relationships": (
                len(input_relationships) if isinstance(input_relationships, list) else 0
            ),
            "input_facts": len(input_facts) if isinstance(input_facts, list) else 0,
            "created_entities": result["saved"]["entities"],
            "created_relationships": result["saved"]["relationships"],
            "saved_facts": result["saved"]["facts"],
            "duration_ms": _elapsed(started),
            "outcome": "ok",
        },
    )
    return result


# --- read path ------------------------------------------------------------


def _query_memory(
    key: int,
    *,
    entity: Any = None,
    predicate: Any = None,
    query: Any = None,
    include_expired: bool = False,
    limit: Any = DEFAULT_LIMIT,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Scoped read path; the public wrapper adds identity and logging."""
    entity_name = _optional_text(entity, "entity", MAX_NAME)
    if entity_name and _is_self(entity_name):
        entity_name = SELF_ENTITY_NAME
    predicate_name = _optional_text(predicate, "predicate", MAX_PREDICATE)
    query_text = _optional_text(query, "query", MAX_QUERY)
    max_results = _limit(limit)
    include_expired = _flag(include_expired, "include_expired")
    now = _now()

    with _db(connection) as db:
        entity_rows: List[sqlite3.Row]
        if entity_name:
            found = _execute(
                db,
                """
                SELECT * FROM entities
                WHERE user_id = ? AND name = ? COLLATE NOCASE
                ORDER BY created_at ASC
                """,
                (key, entity_name),
                operation="entity.query",
            ).fetchall()
            entity_rows = list(found)
        elif query_text:
            entity_rows = _execute(
                db,
                """
                SELECT * FROM entities
                WHERE user_id = ? AND name LIKE ? ESCAPE '\\'
                ORDER BY created_at ASC LIMIT ?
                """,
                (key, _like(query_text), max_results),
                operation="entity.query",
            ).fetchall()
        else:
            entity_rows = _execute(
                db,
                "SELECT * FROM entities WHERE user_id = ? ORDER BY created_at ASC LIMIT ?",
                (key, max_results),
                operation="entity.query",
            ).fetchall()

        fact_clauses = ["f.user_id = ?", "e.user_id = ?"]
        fact_params: List[Any] = [key, key]
        if entity_name:
            fact_clauses.append("e.name = ? COLLATE NOCASE")
            fact_params.append(entity_name)
        if predicate_name:
            fact_clauses.append("f.predicate = ? COLLATE NOCASE")
            fact_params.append(predicate_name)
        if query_text:
            pattern = _like(query_text)
            fact_clauses.append(
                "(e.name LIKE ? ESCAPE '\\' OR f.predicate LIKE ? ESCAPE '\\' "
                "OR f.value LIKE ? ESCAPE '\\')"
            )
            fact_params.extend((pattern, pattern, pattern))
        if not include_expired:
            fact_clauses.append("(f.valid_until IS NULL OR f.valid_until > ?)")
            fact_params.append(now)
        fact_params.append(max_results)

        fact_rows = _execute(
            db,
            f"""
            SELECT f.*, e.name AS entity_name, e.entity_type AS entity_type
            FROM memory_facts f JOIN entities e ON e.id = f.entity_id
            WHERE {' AND '.join(fact_clauses)}
            ORDER BY f.observed_at DESC, f.created_at DESC
            LIMIT ?
            """,
            fact_params,
            operation="fact.query",
        ).fetchall()

        relationship_clauses = ["r.user_id = ?", "s.user_id = ?", "t.user_id = ?"]
        relationship_params: List[Any] = [key, key, key]
        if entity_name:
            relationship_clauses.append("(s.name = ? COLLATE NOCASE OR t.name = ? COLLATE NOCASE)")
            relationship_params.extend((entity_name, entity_name))
        if query_text:
            pattern = _like(query_text)
            relationship_clauses.append(
                "(s.name LIKE ? ESCAPE '\\' OR t.name LIKE ? ESCAPE '\\' "
                "OR r.relationship_type LIKE ? ESCAPE '\\')"
            )
            relationship_params.extend((pattern, pattern, pattern))
        relationship_params.append(max_results)

        relationship_rows = _execute(
            db,
            f"""
            SELECT r.*, s.name AS source_name, t.name AS target_name
            FROM relationships r
            JOIN entities s ON s.id = r.source_entity_id
            JOIN entities t ON t.id = r.target_entity_id
            WHERE {' AND '.join(relationship_clauses)}
            ORDER BY r.created_at DESC
            LIMIT ?
            """,
            relationship_params,
            operation="relationship.query",
        ).fetchall()

    return {
        "entities": [_entity_dict(row) for row in entity_rows],
        "relationships": [_relationship_dict(row) for row in relationship_rows],
        "facts": [_fact_dict(row) for row in fact_rows],
    }


def query_memory(
    user_id: Any,
    *,
    entity: Any = None,
    predicate: Any = None,
    query: Any = None,
    include_expired: bool = False,
    limit: Any = DEFAULT_LIMIT,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Search one user's memory and return entities, relationships, and facts."""
    started = time.perf_counter()
    key: Optional[int] = None
    token = None
    try:
        key = _user_id(user_id)
        token = _current_user.set(key)
        result = _query_memory(
            key,
            entity=entity,
            predicate=predicate,
            query=query,
            include_expired=include_expired,
            limit=limit,
            connection=connection,
        )
    except MemoryError as exc:
        logger.warning(
            "memory.query.invalid",
            extra={
                "event": "memory.query.invalid",
                "user_id": key,
                "error_type": type(exc).__name__,
                "reason": str(exc),
                "duration_ms": _elapsed(started),
            },
        )
        raise
    except Exception:
        logger.exception(
            "memory.query.error",
            extra={
                "event": "memory.query.error",
                "user_id": key,
                "duration_ms": _elapsed(started),
            },
        )
        raise
    finally:
        if token is not None:
            _current_user.reset(token)

    logger.info(
        "memory.query",
        extra={
            "event": "memory.query",
            "user_id": key,
            "has_entity": _has_text(entity),
            "has_predicate": _has_text(predicate),
            "has_query": _has_text(query),
            "query_chars": len(query.strip()) if isinstance(query, str) else 0,
            "include_expired": bool(include_expired),
            "limit": limit,
            "entity_count": len(result["entities"]),
            "relationship_count": len(result["relationships"]),
            "fact_count": len(result["facts"]),
            "duration_ms": _elapsed(started),
            "outcome": "ok",
        },
    )
    return result


# --- agent tool definitions ----------------------------------------------


SAVE_MEMORY_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "save_memory",
        "description": (
            "Save durable personal facts the signed-in user explicitly stated, such as "
            "relationships, preferences, dates, routines, and attributes. Use the literal "
            "entity name 'Current User' for the speaker. Batch related entities, "
            "relationships, and facts in one call. Do not save guesses, transient chat, "
            "or information the user did not state. Set replace=true when a new fact "
            "supersedes an older value for the same entity and predicate."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "description": "People, places, things, or concepts mentioned by the user.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Canonical display name."},
                            "entity_type": {
                                "type": "string",
                                "description": "Person, Place, Pet, Organization, Event, etc.",
                            },
                        },
                        "required": ["name"],
                    },
                },
                "relationships": {
                    "type": "array",
                    "description": "Directed relationships between entities.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_entity": {
                                "type": "string",
                                "description": "Source name; use 'Current User' for the speaker.",
                            },
                            "target_entity": {"type": "string", "description": "Target entity name."},
                            "relationship_type": {
                                "type": "string",
                                "description": "Relationship label, e.g. Sister, Friend, WorksAt.",
                            },
                        },
                        "required": ["source_entity", "target_entity", "relationship_type"],
                    },
                },
                "facts": {
                    "type": "array",
                    "description": "Predicate/value facts attached to an entity.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "entity": {
                                "type": "string",
                                "description": "Entity name; use 'Current User' for the speaker.",
                            },
                            "predicate": {
                                "type": "string",
                                "description": "Attribute name, e.g. age, birthday, likes.",
                            },
                            "value": {
                                "description": "JSON value: string, number, boolean, array, or object.",
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "boolean"},
                                    {"type": "array", "items": {}},
                                    {"type": "object"},
                                ],
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                                "description": "How certain the statement is; default 1.0.",
                            },
                            "source": {
                                "type": "string",
                                "description": "Where the fact came from, e.g. user message.",
                            },
                            "observed_at": {
                                "type": "string",
                                "description": "ISO-8601 time the fact was observed; defaults to now.",
                            },
                            "valid_until": {
                                "type": "string",
                                "description": "ISO-8601 time the fact stops being valid; omit if open-ended.",
                            },
                            "replace": {
                                "type": "boolean",
                                "description": "Invalidate older values for this entity+predicate.",
                            },
                        },
                        "required": ["entity", "predicate", "value"],
                    },
                },
            },
        },
    },
}


RECALL_MEMORY_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "recall_memory",
        "description": (
            "Search the signed-in user's saved personal memory. Call this before answering "
            "questions about the user's people, relationships, preferences, dates, routines, "
            "or past details. All filters are optional; omit them to browse recent memory. "
            "If the user refers to themselves, use the entity name 'Current User'. "
            "Results include created_at for every record, plus observed_at and valid_until "
            "for facts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Exact entity name to filter by, e.g. Sara.",
                },
                "predicate": {
                    "type": "string",
                    "description": "Exact fact predicate to filter by, e.g. age.",
                },
                "query": {
                    "type": "string",
                    "description": "Free-text search across entity names, predicates, values, and relationship types.",
                },
                "include_expired": {
                    "type": "boolean",
                    "description": "Include facts whose valid_until has passed. Default false.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_LIMIT,
                    "description": f"Maximum rows per collection (default {DEFAULT_LIMIT}).",
                },
            },
        },
    },
}


TOOL_SCHEMAS: Sequence[Dict[str, Any]] = (SAVE_MEMORY_SCHEMA, RECALL_MEMORY_SCHEMA)


def handlers(
    user_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Build tool handlers bound to one authenticated user."""

    def save(**kwargs: Any) -> Dict[str, Any]:
        return save_memory(user_id, kwargs, connection=connection)

    def recall(**kwargs: Any) -> Dict[str, Any]:
        return query_memory(user_id, connection=connection, **kwargs)

    return {"save_memory": save, "recall_memory": recall}


def toolset(
    user_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Return ``(schemas, handlers)`` ready for ``TokenHarbor.run_tools*``."""
    _user_id(user_id)  # fail fast on a missing/invalid request identity
    return [dict(SAVE_MEMORY_SCHEMA), dict(RECALL_MEMORY_SCHEMA)], handlers(
        user_id, connection=connection
    )