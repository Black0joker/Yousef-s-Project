"""Application logging: structured JSON/text output and request correlation.

Logs go to stdout and, by default, a rotating file at ``instance/app.log``.
Set ``LOG_FILE=""`` to disable the file handler.

Privacy rules for call sites:

* log user ids, tool names, counts, flags, timings, and error codes;
* never log chat text, entity names, predicates, fact values, tool arguments,
  tool results, JWTs, API keys, cookies, or request bodies.

SQL bind values are the one exception, and only when ``LOG_SQL_PARAMS`` is
enabled: those logs contain personal memory data, so keep the flag off outside
local debugging.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

try:
    from flask import Flask, g, has_request_context, request
except Exception:  # pragma: no cover - Flask is required by the app
    Flask = Any  # type: ignore[assignment]
    g = None  # type: ignore[assignment]
    request = None  # type: ignore[assignment]

    def has_request_context() -> bool:  # type: ignore[no-redef]
        return False


DEFAULT_LOG_FILE = os.path.join("instance", "app.log")
DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_BACKUP_COUNT = 5

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

#: Fields added through ``logger.info(..., extra={...})`` that should appear in
#: structured output. Keep this list explicit so arbitrary record attributes
#: never leak into logs.
EXTRA_FIELDS = (
    "event",
    "request_id",
    "user_id",
    "method",
    "path",
    "status",
    "tool",
    "operation",
    "sql",
    "sql_params",
    "ok",
    "outcome",
    "error_type",
    "error_code",
    "retryable",
    "reason",
    "duration_ms",
    "model",
    "language",
    "streaming",
    "message_count",
    "finish_reason",
    "has_entity",
    "has_predicate",
    "has_query",
    "query_chars",
    "include_expired",
    "limit",
    "input_entities",
    "input_relationships",
    "input_facts",
    "created_entities",
    "created_relationships",
    "saved_facts",
    "entity_count",
    "relationship_count",
    "fact_count",
)

app_logger = logging.getLogger("app")


def _utc_timestamp(created: float) -> str:
    return (
        datetime.fromtimestamp(created, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with a stable set of optional fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": _utc_timestamp(record.created),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None),
            "message": record.getMessage(),
        }
        for field in EXTRA_FIELDS:
            if field == "event":
                continue
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):  # never let logging break a request
            return json.dumps(
                {
                    "timestamp": payload["timestamp"],
                    "level": "ERROR",
                    "logger": record.name,
                    "message": "log serialization failed",
                }
            )


class TextFormatter(logging.Formatter):
    """Readable local-development format with key=value extras."""

    @staticmethod
    def _value(field: str, value: Any) -> str:
        if field == "duration_ms" and isinstance(value, (int, float)):
            return f"{value:.2f}ms"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _one_line(value: Any) -> str:
        # Bind values are already repr-escaped; strip anything left so a value
        # can never inject a fake log line.
        return "".join(ch if ch.isprintable() else " " for ch in str(value))

    def format(self, record: logging.LogRecord) -> str:
        timestamp = _utc_timestamp(record.created).replace("T", " ").replace("Z", "")
        event = getattr(record, "event", None) or record.getMessage()
        line = f"{timestamp} {record.levelname:<5} {record.name:<14} {event}"

        fields = []
        for field in EXTRA_FIELDS:
            if field in ("event", "sql", "sql_params"):
                continue
            value = getattr(record, field, None)
            if value is not None:
                fields.append(f"{field}={self._value(field, value)}")
        if fields:
            line += " | " + " ".join(fields)

        sql = getattr(record, "sql", None)
        if sql:
            line += f"\n    SQL: {sql}"
        sql_params = getattr(record, "sql_params", None)
        if sql_params:
            line += f"\n    PARAMS: {self._one_line(sql_params)}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class RequestContextFilter(logging.Filter):
    """Attach the current Flask request id and authenticated user id."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not has_request_context() or g is None:
            return True
        try:
            if not getattr(record, "request_id", None):
                record.request_id = getattr(g, "request_id", None)
            if getattr(record, "user_id", None) is None:
                user = getattr(g, "user", None)
                if isinstance(user, dict):
                    record.user_id = user.get("id")
        except Exception:  # pragma: no cover - defensive only
            pass
        return True


_MANAGED_FLAG = "_th_managed"


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(0, value)


def configure_logging(force: bool = False) -> None:
    """Configure stdlib logging; safe to call more than once.

    ``force=True`` replaces handlers previously installed by this function and
    is intended for tests or explicit reconfiguration.
    """
    root = logging.getLogger()
    managed = [handler for handler in root.handlers if getattr(handler, _MANAGED_FLAG, False)]
    if managed and not force:
        return
    for handler in managed:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    level_name = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    log_format = os.environ.get("LOG_FORMAT", "text").strip().lower()
    formatter: logging.Formatter = TextFormatter() if log_format == "text" else JsonFormatter()

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    log_file = os.environ.get("LOG_FILE", DEFAULT_LOG_FILE).strip()
    if log_file:
        try:
            path = os.path.abspath(log_file)
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    path,
                    maxBytes=_int_env("LOG_MAX_BYTES", DEFAULT_MAX_BYTES),
                    backupCount=_int_env("LOG_BACKUP_COUNT", DEFAULT_BACKUP_COUNT),
                    encoding="utf-8",
                )
            )
        except OSError as exc:
            print(f"logging: cannot open log file {log_file!r}: {exc}", file=sys.stderr)

    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        handler.addFilter(RequestContextFilter())
        setattr(handler, _MANAGED_FLAG, True)
        root.addHandler(handler)


def _request_id() -> str:
    incoming = (request.headers.get("X-Request-ID") or "").strip() if request is not None else ""
    if _REQUEST_ID_RE.match(incoming):
        return incoming
    return uuid.uuid4().hex


def _safe_path(path: str) -> str:
    # request.path never contains the query string, but strip control chars
    # anyway so a malformed path cannot inject line breaks into text logs.
    return "".join(ch for ch in path if ch.isprintable())


def init_request_logging(app: Flask) -> None:
    """Register request id, duration, and status logging on a Flask app.

    Register this before the auth middleware so requests rejected by auth are
    still logged. The logged user id is read from ``g.user`` at emit time, so
    it appears once authentication has succeeded.
    """

    @app.before_request
    def _log_request_start() -> None:
        g.request_id = _request_id()
        g.request_started = time.perf_counter()
        level = logging.DEBUG if request.path.startswith("/static/") else logging.INFO
        app_logger.log(
            level,
            "http.request",
            extra={
                "event": "http.request",
                "method": request.method,
                "path": _safe_path(request.path),
                "request_id": g.request_id,
            },
        )

    @app.after_request
    def _log_request_end(response):
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id

        started = getattr(g, "request_started", None)
        duration_ms = (
            round((time.perf_counter() - started) * 1000, 2) if started is not None else None
        )
        user = getattr(g, "user", None)
        user_id = user.get("id") if isinstance(user, dict) else None
        level = logging.DEBUG if request.path.startswith("/static/") else logging.INFO
        app_logger.log(
            level,
            "http.response",
            extra={
                "event": "http.response",
                "request_id": request_id,
                "method": request.method,
                "path": _safe_path(request.path),
                "status": response.status_code,
                "duration_ms": duration_ms,
                "user_id": user_id,
                "outcome": "ok" if response.status_code < 400 else "error",
            },
        )
        return response