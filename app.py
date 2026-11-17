"""Flask chat app on top of the ``core`` Token Harbor client.

Routes
------
``GET  /``                 redirects to the login page
``GET  /chat``             chat page: greeting + message input
``POST /api/chat/stream``  streamed reply over SSE, including tool lifecycle
``POST /api/chat``         non-streaming JSON fallback

The browser never sees the API key: all calls go through this server, which
prepends the system prompt as the message at index 0. Memory tool handlers are
bound to the authenticated user, so the model never receives a user id.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, redirect, render_template, request, stream_with_context

import auth
import logging_config
import memory
from core import Message, TokenHarbor, TokenHarborError

logging_config.configure_logging()
logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "You are a friendly, concise assistant in a web chat app. "
    "Answer clearly in plain language, and say so if you don't know something."
)

#: Default model for the app. A ``:free`` id so it works without a balance;
#: override with TH_MODEL.
DEFAULT_WEB_MODEL = "deepseek-v4.1-flash:free"

#: Roles accepted from the browser (the system role is added by the server).
ALLOWED_ROLES = ("user", "assistant")
MAX_MESSAGES = 50
MAX_CONTENT_CHARS = 8000

# --- prompts --------------------------------------------------------------

#: Where the SAGE persona documents live (next to this module).
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

#: Prompt file per supported language. ``language`` indexes this fixed map, so a
#: client-supplied value can never reach the filesystem directly.
LANGUAGE_FILES = {"en": "english.md", "ar": "arabic.md"}
DEFAULT_LANGUAGE = "en"

#: Accepted spellings for each language (lowercased).
LANGUAGE_ALIASES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "ar": "ar",
    "ara": "ar",
    "arabic": "ar",
}

#: Appended to the persona so the language toggle actually forces the reply
#: language, even when the user writes in the other language.
LANGUAGE_DIRECTIVES = {
    "en": "Always write your entire response in English.",
    "ar": "يجب أن تكون جميع ردودك باللغة العربية.",
}

app = Flask(__name__)

# Request logging is registered before auth so rejected requests are logged too.
logging_config.init_request_logging(app)

# register the users/memory tables, the teardown handler and auth middleware
auth.init_db()
memory.init_db()
auth.init_app(app)


# --- config ---------------------------------------------------------------


def model_name() -> str:
    """Model id, overridable with ``TH_MODEL``."""
    return os.environ.get("TH_MODEL", "").strip() or DEFAULT_WEB_MODEL


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def normalize_language(value: Any) -> str:
    """Map a client-supplied language to a supported code, else the default."""
    if isinstance(value, str):
        code = LANGUAGE_ALIASES.get(value.strip().lower())
        if code:
            return code
    return DEFAULT_LANGUAGE


def read_prompt(language: str) -> str:
    """SAGE prompt file for ``language``, read fresh on every call.

    Reading per request means editing a ``prompts/*.md`` file takes effect
    without a restart. A missing or empty file falls back to the built-in text.
    """
    filename = LANGUAGE_FILES.get(language)
    if not filename:
        return DEFAULT_SYSTEM_PROMPT
    try:
        with open(os.path.join(PROMPTS_DIR, filename), encoding="utf-8") as handle:
            content = handle.read().strip()
    except OSError:
        return DEFAULT_SYSTEM_PROMPT
    return content or DEFAULT_SYSTEM_PROMPT


def system_prompt(language: str = DEFAULT_LANGUAGE) -> str:
    """System prompt for ``language``.

    ``TH_SYSTEM_PROMPT`` overrides the persona for every language; otherwise the
    matching ``prompts/*.md`` document is used. A language directive is appended
    so the model replies in the selected language.
    """
    code = normalize_language(language)
    override = os.environ.get("TH_SYSTEM_PROMPT", "").strip()
    base = override or read_prompt(code)
    directive = LANGUAGE_DIRECTIVES[code]
    return f"{base}\n\n{directive}"


_client: Optional[TokenHarbor] = None
_client_error: Optional[TokenHarborError] = None


def get_client() -> TokenHarbor:
    """Return a shared Token Harbor client, created lazily on first use."""
    global _client, _client_error
    if _client is None and _client_error is None:
        try:
            _client = TokenHarbor(model=model_name())
        except TokenHarborError as exc:  # usually a missing key
            _client_error = exc
    if _client_error is not None:
        raise _client_error
    assert _client is not None
    return _client


def log_tool_handlers(handlers: Dict[str, Any], user_id: Any = None) -> Dict[str, Any]:
    """Wrap tool handlers with start/end/error logs, never logging arguments."""
    wrapped: Dict[str, Any] = {}

    for name, handler in handlers.items():

        def invoke(_name: str = name, _handler: Any = handler, **kwargs: Any) -> Any:
            started = time.perf_counter()
            logger.info(
                "tool.start",
                extra={"event": "tool.start", "tool": _name, "user_id": user_id},
            )
            try:
                result = _handler(**kwargs)
            except Exception as exc:
                logger.warning(
                    "tool.error",
                    extra={
                        "event": "tool.error",
                        "tool": _name,
                        "user_id": user_id,
                        "ok": False,
                        "outcome": "error",
                        "error_type": type(exc).__name__,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )
                raise
            logger.info(
                "tool.end",
                extra={
                    "event": "tool.end",
                    "tool": _name,
                    "user_id": user_id,
                    "ok": True,
                    "outcome": "ok",
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return result

        wrapped[name] = invoke

    return wrapped


def memory_toolset():
    """Memory tool schemas/handlers bound to the authenticated user."""
    claims = auth.current_user() or {}
    user_id = claims.get("id")
    if user_id is None:
        raise auth.AuthError("unauthorized", "Authentication required.", 401)
    tools, handlers = memory.toolset(user_id, connection=auth.get_db())
    return tools, log_tool_handlers(handlers, user_id=user_id)


# --- message handling -----------------------------------------------------


def normalize_messages(raw: Any) -> List[Message]:
    """Validate and clean the conversation sent by the browser."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("`messages` must be a non-empty list.")
    if len(raw) > MAX_MESSAGES:
        raise ValueError(f"Too many messages (max {MAX_MESSAGES}).")

    cleaned: List[Message] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each message must be an object with role and content.")
        role = item.get("role")
        content = item.get("content")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"Unsupported role {role!r}; use 'user' or 'assistant'.")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Message content must be a non-empty string.")
        if len(content) > MAX_CONTENT_CHARS:
            raise ValueError(f"Message content is too long (max {MAX_CONTENT_CHARS} characters).")
        cleaned.append({"role": role, "content": content})
    return cleaned


def build_payload(raw: Any, language: str = DEFAULT_LANGUAGE) -> List[Message]:
    """Conversation with the system prompt for ``language`` inserted at index 0."""
    return [{"role": "system", "content": system_prompt(language)}] + normalize_messages(raw)


def error_body(exc: TokenHarborError) -> Dict[str, Any]:
    return {
        "error": {
            "code": exc.code or "error",
            "message": str(exc),
            "status": exc.status_code,
        }
    }


def error_status(exc: TokenHarborError) -> int:
    return exc.status_code if exc.status_code and 400 <= exc.status_code < 600 else 502


def sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# --- pages ----------------------------------------------------------------


@app.get("/")
def index():
    """Send visitors to the login page; it forwards to /chat when signed in."""
    return redirect("/login")


@app.get("/login")
def login_page():
    return render_template("login.html")


@app.get("/register")
def register_page():
    return render_template("register.html")


@app.get("/chat")
def chat():
    """The chat shell. It holds no data; the browser fetches it with a bearer token."""
    return render_template("chat.html")


# --- authentication API ---------------------------------------------------


@app.post("/api/auth/register")
def api_register():
    data = request.get_json(silent=True) or {}
    try:
        user = auth.register_user(
            data.get("first_name"),
            data.get("last_name"),
            data.get("email"),
            data.get("password"),
            data.get("confirm_password"),
        )
    except auth.AuthError as exc:
        return jsonify(exc.to_dict()), exc.status
    return jsonify(auth.auth_payload(user)), 201


@app.post("/api/auth/login")
def api_login():
    data = request.get_json(silent=True) or {}
    try:
        user = auth.authenticate(data.get("email"), data.get("password"))
    except auth.AuthError as exc:
        return jsonify(exc.to_dict()), exc.status
    return jsonify(auth.auth_payload(user))


@app.get("/api/auth/me")
def api_me():
    claims = auth.current_user() or {}
    user = auth.get_user_by_id(claims.get("id"))
    if user is None:
        return jsonify({"error": {"code": "unauthorized", "message": "Account no longer exists."}}), 401
    return jsonify({"user": auth.public_user(user)})


@app.post("/api/auth/logout")
def api_logout():
    # tokens are stateless; the client discards its copy
    return "", 204


# --- API ------------------------------------------------------------------


@app.post("/api/chat")
def api_chat():
    """Non-streaming completion: returns ``{"reply": "..."}``."""
    data = request.get_json(silent=True) or {}
    language = normalize_language(data.get("language"))
    try:
        payload = build_payload(data.get("messages"), language)
    except ValueError as exc:
        return jsonify({"error": {"code": "invalid_request", "message": str(exc)}}), 400

    try:
        client = get_client()
    except TokenHarborError as exc:
        return jsonify(error_body(exc)), 500

    started = time.perf_counter()
    logger.info(
        "chat.request",
        extra={
            "event": "chat.request",
            "model": model_name(),
            "language": language,
            "streaming": False,
            "message_count": max(0, len(payload) - 1),
        },
    )
    tools, handlers = memory_toolset()
    try:
        reply = client.run_tools(messages=payload, tools=tools, handlers=handlers)
    except TokenHarborError as exc:
        logger.error(
            "chat.error",
            extra={
                "event": "chat.error",
                "streaming": False,
                "error_code": exc.code,
                "error_type": type(exc).__name__,
                "status": error_status(exc),
                "retryable": exc.retryable,
                "duration_ms": elapsed_ms(started),
                "outcome": "error",
            },
        )
        return jsonify(error_body(exc)), error_status(exc)

    logger.info(
        "chat.done",
        extra={
            "event": "chat.done",
            "streaming": False,
            "duration_ms": elapsed_ms(started),
            "outcome": "ok",
        },
    )
    return jsonify({"reply": reply})


@app.post("/api/chat/stream")
def api_chat_stream():
    """Streamed completion over Server-Sent Events."""
    data = request.get_json(silent=True) or {}
    language = normalize_language(data.get("language"))
    try:
        payload = build_payload(data.get("messages"), language)
    except ValueError as exc:
        return jsonify({"error": {"code": "invalid_request", "message": str(exc)}}), 400

    try:
        client = get_client()
    except TokenHarborError as exc:
        return jsonify(error_body(exc)), 500

    logger.info(
        "chat.request",
        extra={
            "event": "chat.request",
            "model": model_name(),
            "language": language,
            "streaming": True,
            "message_count": max(0, len(payload) - 1),
        },
    )
    tools, handlers = memory_toolset()

    def generate():
        started = time.perf_counter()
        outcome = "ok"
        error_code: Optional[str] = None
        finish_reason: Optional[str] = None
        try:
            for event in client.run_tools_events(messages=payload, tools=tools, handlers=handlers):
                if event["type"] == "delta" and event.get("content"):
                    yield sse({"delta": event["content"]})
                elif event["type"] == "reasoning" and event.get("content"):
                    yield sse({"reasoning": event["content"]})
                elif event["type"] == "tool_start":
                    yield sse({"tool": {"name": event.get("name"), "status": "start"}})
                elif event["type"] == "tool_end":
                    yield sse(
                        {
                            "tool": {
                                "name": event.get("name"),
                                "status": "end",
                                "ok": bool(event.get("ok", True)),
                            }
                        }
                    )
                elif event["type"] == "done":
                    finish_reason = event.get("finish_reason")
                    yield sse(
                        {
                            "done": True,
                            "finish_reason": finish_reason,
                            "usage": event.get("usage"),
                        }
                    )
        except TokenHarborError as exc:
            outcome = "error"
            error_code = exc.code
            logger.error(
                "chat.stream.error",
                extra={
                    "event": "chat.stream.error",
                    "streaming": True,
                    "error_code": exc.code,
                    "error_type": type(exc).__name__,
                    "status": error_status(exc),
                    "retryable": exc.retryable,
                    "duration_ms": elapsed_ms(started),
                    "outcome": "error",
                },
            )
            yield sse(error_body(exc))
        except Exception as exc:  # never leak a traceback into the stream
            outcome = "error"
            error_code = "server_error"
            logger.exception(
                "chat.stream.error",
                extra={
                    "event": "chat.stream.error",
                    "streaming": True,
                    "error_code": error_code,
                    "error_type": type(exc).__name__,
                    "duration_ms": elapsed_ms(started),
                    "outcome": "error",
                },
            )
            yield sse({"error": {"code": error_code, "message": str(exc)}})
        finally:
            logger.info(
                "chat.stream.done",
                extra={
                    "event": "chat.stream.done",
                    "streaming": True,
                    "finish_reason": finish_reason,
                    "duration_ms": elapsed_ms(started),
                    "outcome": outcome,
                    "error_code": error_code,
                },
            )
        yield "data: [DONE]\n\n"

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # don't buffer behind a reverse proxy
        "Connection": "keep-alive",
    }
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=debug)
