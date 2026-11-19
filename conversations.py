"""Conversation persistence: durable, per-user transcripts of every chat turn.

Why this exists
---------------
The browser keeps a fast local copy of the thread in ``localStorage``, but that
is not a record: it disappears with the browser profile and the server never
sees it. The clinician dashboard, the multi-model evaluation harness, and the
research ablation all need transcripts that live on the server, so every turn is
also written here.

Design
------
* One ``conversations`` row per thread; one ``conversation_turns`` row per
  message, in order.
* Every row is scoped by ``user_id``. A conversation id supplied by the client
  is only honored when it belongs to the caller; otherwise a new thread starts.
* Persistence is **best effort**. A database failure while logging a turn must
  never break the chat request it is recording, so the public helpers swallow
  and log errors instead of raising.
* A stopped or failed turn is still recorded, flagged with ``outcome``, rather
  than silently dropped.
* SQL goes through :func:`_execute`, which logs statement text with ``?``
  placeholders only. Message text is personal data: it is never logged.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import memory

logger = logging.getLogger(__name__)

#: Stored content is capped defensively. The chat routes already reject longer
#: messages; this only guards direct writes and keeps a runaway row out of the
#: database. Persistence truncates instead of raising.
MAX_STORED_CONTENT = 20_000

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

VALID_ROLES = ("user", "assistant")
VALID_OUTCOMES = ("ok", "stopped", "error")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode       TEXT NOT NULL,
    language   TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_started
ON conversations(user_id, started_at);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id                TEXT PRIMARY KEY,
    conversation_id   TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role              TEXT NOT NULL,
    content           TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    finish_reason     TEXT,
    model             TEXT,
    strategy_id       TEXT,
    latency_ms        REAL,
    outcome           TEXT NOT NULL DEFAULT 'ok',
    tool_calls_json   TEXT
);

CREATE INDEX IF NOT EXISTS idx_turns_conversation_created
ON conversation_turns(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_turns_user_created
ON conversation_turns(user_id, created_at);
"""


# --- helpers --------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id() -> str:
    return str(uuid.uuid4())


def _elapsed(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _execute(
    db: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] = (),
    *,
    operation: str,
) -> sqlite3.Cursor:
    """Execute SQL, logging the normalized statement with ``?`` placeholders.

    Bind values are never logged: they contain message text. ``LOG_SQL=0``
    disables the statement log, matching :mod:`memory`.
    """
    normalized = memory._normalize_sql(sql)
    started = time.perf_counter()
    try:
        cursor = db.execute(sql, params)
    except Exception:
        logger.exception(
            "conversation.sql.error",
            extra={
                "event": "conversation.sql.error",
                "user_id": memory._current_user.get(),
                "operation": operation,
                "sql": normalized,
                "duration_ms": _elapsed(started),
                "outcome": "error",
            },
        )
        raise
    if memory._sql_logging_enabled():
        logger.info(
            "conversation.sql",
            extra={
                "event": "conversation.sql",
                "user_id": memory._current_user.get(),
                "operation": operation,
                "sql": normalized,
                "duration_ms": _elapsed(started),
                "outcome": "ok",
            },
        )
    return cursor


def _user_id(user_id: Any) -> int:
    try:
        key = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("A valid authenticated user is required.") from exc
    if key <= 0:
        raise ValueError("A valid authenticated user is required.")
    return key


def _clamp_limit(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, number))


def _content(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value[:MAX_STORED_CONTENT]


def _optional(value: Any, max_length: int = 200) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


def _tool_calls(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)[:MAX_STORED_CONTENT]
    except (TypeError, ValueError):
        return None


# --- schema ---------------------------------------------------------------


def init_db(connection: Optional[sqlite3.Connection] = None) -> None:
    """Create the conversation tables and indexes if they do not exist yet."""
    with memory._db(connection) as db:
        db.executescript(SCHEMA_SQL)
        db.commit()


# --- serializers ----------------------------------------------------------


def _conversation_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "mode": row["mode"],
        "language": row["language"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
    }


def _turn_dict(row: sqlite3.Row) -> Dict[str, Any]:
    tool_calls = None
    if row["tool_calls_json"]:
        try:
            tool_calls = json.loads(row["tool_calls_json"])
        except (TypeError, ValueError):
            tool_calls = None
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
        "finish_reason": row["finish_reason"],
        "model": row["model"],
        "strategy_id": row["strategy_id"],
        "latency_ms": row["latency_ms"],
        "outcome": row["outcome"],
        "tool_calls": tool_calls,
    }


# --- write path -----------------------------------------------------------


def create_conversation(
    user_id: Any,
    mode: str = "chat",
    language: str = "en",
    connection: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Start a new thread for ``user_id`` and return it."""
    key = _user_id(user_id)
    conversation_id = _new_id()
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            _execute(
                db,
                """
                INSERT INTO conversations (id, user_id, mode, language, started_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, key, mode or "chat", language or "en", _now()),
                operation="conversation.insert",
            )
            db.commit()
            row = _execute(
                db,
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, key),
                operation="conversation.get",
            ).fetchone()
        assert row is not None
        return _conversation_dict(row)
    finally:
        memory._current_user.reset(token)


def get_conversation(
    user_id: Any,
    conversation_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Return one thread owned by ``user_id``, or ``None``."""
    key = _user_id(user_id)
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return None
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            row = _execute(
                db,
                "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id.strip(), key),
                operation="conversation.get",
            ).fetchone()
        return _conversation_dict(row) if row else None
    finally:
        memory._current_user.reset(token)


def resolve_conversation(
    user_id: Any,
    conversation_id: Any = None,
    mode: str = "chat",
    language: str = "en",
    connection: Optional[sqlite3.Connection] = None,
) -> str:
    """Return a usable conversation id for this request.

    A client-supplied id is honored only when it belongs to the caller; a
    foreign or unknown id starts a fresh thread instead of leaking or failing.
    """
    existing = get_conversation(user_id, conversation_id, connection=connection)
    if existing is not None:
        return existing["id"]
    return create_conversation(user_id, mode, language, connection=connection)["id"]


def append_turn(
    user_id: Any,
    conversation_id: str,
    role: str,
    content: str,
    *,
    finish_reason: Optional[str] = None,
    model: Optional[str] = None,
    strategy_id: Optional[str] = None,
    latency_ms: Optional[float] = None,
    outcome: str = "ok",
    tool_calls: Any = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Append one turn. Returns the stored row, or ``None`` on failure.

    Never raises: a logging failure must not break the chat request.
    """
    try:
        key = _user_id(user_id)
        if role not in VALID_ROLES:
            raise ValueError("role must be 'user' or 'assistant'")
        if outcome not in VALID_OUTCOMES:
            outcome = "ok"

        turn_id = _new_id()
        created_at = _now()
        token = memory._current_user.set(key)
        try:
            with memory._db(connection) as db:
                # the conversation must belong to this user before we write
                owned = _execute(
                    db,
                    "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
                    (conversation_id, key),
                    operation="conversation.owner",
                ).fetchone()
                if owned is None:
                    return None
                _execute(
                    db,
                    """
                    INSERT INTO conversation_turns
                        (id, conversation_id, user_id, role, content, created_at,
                         finish_reason, model, strategy_id, latency_ms, outcome,
                         tool_calls_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        turn_id,
                        conversation_id,
                        key,
                        role,
                        _content(content),
                        created_at,
                        _optional(finish_reason),
                        _optional(model),
                        _optional(strategy_id),
                        latency_ms,
                        outcome,
                        _tool_calls(tool_calls),
                    ),
                    operation="turn.insert",
                )
                _execute(
                    db,
                    "UPDATE conversations SET ended_at = ? WHERE id = ? AND user_id = ?",
                    (created_at, conversation_id, key),
                    operation="conversation.touch",
                )
                db.commit()
                row = _execute(
                    db,
                    "SELECT * FROM conversation_turns WHERE id = ? AND user_id = ?",
                    (turn_id, key),
                    operation="turn.get",
                ).fetchone()
            return _turn_dict(row) if row else None
        finally:
            memory._current_user.reset(token)
    except Exception:
        logger.warning(
            "conversation.write.failed",
            extra={
                "event": "conversation.write.failed",
                "user_id": user_id if isinstance(user_id, int) else None,
                "role": role if role in VALID_ROLES else None,
                "outcome": "error",
                "error_type": "persistence",
            },
        )
        return None


# --- read path ------------------------------------------------------------


def list_conversations(
    user_id: Any,
    limit: Any = DEFAULT_LIMIT,
    connection: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Most recent threads for ``user_id``, newest first."""
    key = _user_id(user_id)
    max_results = _clamp_limit(limit)
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = _execute(
                db,
                """
                SELECT * FROM conversations
                WHERE user_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (key, max_results),
                operation="conversation.list",
            ).fetchall()
        return [_conversation_dict(row) for row in rows]
    finally:
        memory._current_user.reset(token)


def get_turns(
    user_id: Any,
    conversation_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> List[Dict[str, Any]]:
    """Ordered turns of one thread owned by ``user_id`` (empty if not owned)."""
    key = _user_id(user_id)
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        return []
    token = memory._current_user.set(key)
    try:
        with memory._db(connection) as db:
            rows = _execute(
                db,
                """
                SELECT * FROM conversation_turns
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (conversation_id.strip(), key),
                operation="turn.list",
            ).fetchall()
        return [_turn_dict(row) for row in rows]
    finally:
        memory._current_user.reset(token)


def get_conversation_detail(
    user_id: Any,
    conversation_id: Any,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """One thread plus its turns, or ``None`` when it is not the caller's."""
    conversation = get_conversation(user_id, conversation_id, connection=connection)
    if conversation is None:
        return None
    return {
        "conversation": conversation,
        "turns": get_turns(user_id, conversation_id, connection=connection),
    }


# --- request-scoped recording --------------------------------------------


def record_user_turn(
    user_id: Any,
    conversation_id: str,
    content: str,
    *,
    model: Optional[str] = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Record the inbound message. Best effort; never raises."""
    return append_turn(
        user_id,
        conversation_id,
        "user",
        content,
        model=model,
        connection=connection,
    )


def record_assistant_turn(
    user_id: Any,
    conversation_id: str,
    content: str,
    *,
    finish_reason: Optional[str] = None,
    model: Optional[str] = None,
    latency_ms: Optional[float] = None,
    outcome: str = "ok",
    tool_calls: Any = None,
    connection: Optional[sqlite3.Connection] = None,
) -> Optional[Dict[str, Any]]:
    """Record the reply, including partial/stopped/failed turns. Never raises."""
    return append_turn(
        user_id,
        conversation_id,
        "assistant",
        content,
        finish_reason=finish_reason,
        model=model,
        latency_ms=latency_ms,
        outcome=outcome,
        tool_calls=tool_calls,
        connection=connection,
    )


def last_user_message(messages: Any) -> str:
    """Content of the newest ``user`` message in a validated payload."""
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        return ""
    for item in reversed(list(messages)):
        if isinstance(item, Mapping) and item.get("role") == "user":
            return _content(item.get("content"))
    return ""


def summarize_turns(turns: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Counts for logging and later analytics; no message text."""
    counts: Dict[str, Any] = {"turns": len(turns), "user": 0, "assistant": 0}
    for turn in turns:
        role = turn.get("role")
        if role in ("user", "assistant"):
            counts[role] += 1
    return counts
