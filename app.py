"""Flask chat app on top of the ``core`` Token Harbor client.

Routes
------
``GET  /``                 redirects to the login page
``GET  /chat``             chat page: greeting + message input
``GET  /call``             voice-call page: microphone + spoken replies
``POST /api/chat/stream``  streamed reply over SSE, including tool lifecycle
``POST /api/chat``         non-streaming JSON fallback
``POST /api/voice/speak``  ElevenLabs text-to-speech for one sentence
``GET  /api/voice/status`` voice token availability and expiry

The browser never sees the Token Harbor or ElevenLabs API tokens: all calls go
through this server, which prepends the system prompt as the message at index 0.
Memory tool handlers are bound to the authenticated user, so the model never
receives a user id.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, Response, jsonify, redirect, render_template, request, stream_with_context

import auth
import conversations
import logging_config
import memory
import voice
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

#: Spoken call mode overrides the language directive: Arabic calls must use
#: Egyptian colloquial, not Modern Standard or Gulf Arabic.
CALL_LANGUAGE_DIRECTIVES = {
    "en": "Always write your entire response in English.",
    "ar": "اكتب كل ردودك بالعامية المصرية الطبيعية، مش بالفصحى ومش باللهجة السعودية.",
}

#: Prompt document per conversation mode. ``call`` makes SAGE speak as though
#: it placed a phone call to the user; the call prompt files fall back to the
#: chat persona when missing.
PROMPT_MODES = {
    "chat": LANGUAGE_FILES,
    "call": {"en": "english-call.md", "ar": "arabic-call.md"},
}
DEFAULT_MODE = "chat"

#: Synthetic first user turn for a call so the assistant can greet the user.
GREETING_TURNS = {
    "en": "[The call has connected. Greet me warmly and briefly.]",
    "ar": "[تم الاتصال. ابدأ بالتحية بمودة وباختصار.]",
}

#: Spoken turns should stay short; the provider bills by the character.
CALL_MAX_TOKENS = 220

app = Flask(__name__)

# Request logging is registered before auth so rejected requests are logged too.
logging_config.init_request_logging(app)

# register the users/memory tables, the teardown handler and auth middleware
auth.init_db()
memory.init_db()
conversations.init_db()
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


def normalize_mode(value: Any) -> str:
    """Map a client-supplied mode to a supported mode, else the default."""
    if isinstance(value, str) and value.strip().lower() in PROMPT_MODES:
        return value.strip().lower()
    return DEFAULT_MODE


def read_prompt(language: str, mode: str = DEFAULT_MODE) -> str:
    """SAGE prompt file for ``language`` and ``mode``, read fresh every call.

    Reading per request means editing a ``prompts/*.md`` file takes effect
    without a restart. A missing or empty call prompt falls back to the chat
    persona, then to the built-in text.
    """
    files = PROMPT_MODES.get(mode) or PROMPT_MODES[DEFAULT_MODE]
    filename = files.get(language)
    if not filename:
        return DEFAULT_SYSTEM_PROMPT
    try:
        with open(os.path.join(PROMPTS_DIR, filename), encoding="utf-8") as handle:
            content = handle.read().strip()
    except OSError:
        if mode != DEFAULT_MODE:
            return read_prompt(language, DEFAULT_MODE)
        return DEFAULT_SYSTEM_PROMPT
    if content:
        return content
    if mode != DEFAULT_MODE:
        return read_prompt(language, DEFAULT_MODE)
    return DEFAULT_SYSTEM_PROMPT


def system_prompt(language: str = DEFAULT_LANGUAGE, mode: str = DEFAULT_MODE) -> str:
    """System prompt for ``language`` and ``mode``.

    ``TH_SYSTEM_PROMPT`` overrides the persona for every language and mode;
    otherwise the matching ``prompts/*.md`` document is used. The generated
    memory schema brief is always appended, so the model keeps the data model
    even when the persona is overridden. A language directive is appended so the
    model replies in the selected language.
    """
    code = normalize_language(language)
    conversation = normalize_mode(mode)
    override = os.environ.get("TH_SYSTEM_PROMPT", "").strip()
    base = override or read_prompt(code, conversation)
    directives = CALL_LANGUAGE_DIRECTIVES if conversation == "call" else LANGUAGE_DIRECTIVES
    directive = directives[code]
    parts = [base, memory.prompt_brief()]
    if conversation == "call":
        parts.append(voice.prompt_brief())
    parts.append(directive)
    return "\n\n".join(parts)


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

    # get_name answers from the request's JWT claims; the database is only a
    # fallback for tokens issued before the claim existed.
    display_name = (claims.get("full_name") or "").strip()
    if not display_name:
        display_name = (auth.get_user_by_id(user_id) or {}).get("full_name") or ""

    tools, handlers = memory.toolset(
        user_id,
        connection=auth.get_db(),
        display_name=display_name,
    )
    return tools, log_tool_handlers(handlers, user_id=user_id)


# --- active generations ---------------------------------------------------

#: Streaming responses currently connected to a provider, keyed by stream id.
#: Entries are scoped to one JWT user id and removed in ``generate()``'s finally.
_ACTIVE_STREAMS: Dict[str, Dict[str, Any]] = {}
_ACTIVE_LOCK = threading.Lock()


def register_stream(user_id: Any) -> Tuple[str, threading.Event, Dict[str, Any]]:
    """Register a generation and return ``(stream_id, cancel_event, entry)``."""
    stream_id = uuid.uuid4().hex
    cancel = threading.Event()
    entry: Dict[str, Any] = {"user_id": user_id, "cancel": cancel, "close": None}
    with _ACTIVE_LOCK:
        _ACTIVE_STREAMS[stream_id] = entry
    return stream_id, cancel, entry


def unregister_stream(stream_id: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_STREAMS.pop(stream_id, None)


def stop_stream(user_id: Any, stream_id: str) -> bool:
    """Cancel one of ``user_id``'s generations; never touches another account's."""
    with _ACTIVE_LOCK:
        entry = _ACTIVE_STREAMS.get(stream_id)
        if entry is None or entry.get("user_id") != user_id:
            return False
        cancel = entry["cancel"]
        closer = entry.get("close")
    cancel.set()
    if closer is not None:
        try:
            closer()  # closes the provider response, unblocking a silent stream
        except Exception:  # already closed / disconnected
            pass
    return True


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


def build_payload(
    raw: Any,
    language: str = DEFAULT_LANGUAGE,
    mode: str = DEFAULT_MODE,
    greeting: bool = False,
) -> List[Message]:
    """Conversation with the system prompt for ``language``/``mode`` at index 0.

    In call mode ``greeting=True`` with no messages inserts a synthetic user
    turn so the assistant can open the call without the client inventing text.
    """
    code = normalize_language(language)
    conversation = normalize_mode(mode)

    if raw:
        messages = normalize_messages(raw)
    else:
        messages = []

    if greeting and conversation == "call" and not messages:
        messages = [{"role": "user", "content": GREETING_TURNS[code]}]

    if not messages:
        raise ValueError("`messages` must be a non-empty list.")

    system = system_prompt(code, conversation)
    return [{"role": "system", "content": system}] + messages


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


# --- location -------------------------------------------------------------


def clean_coordinate(value: Any, limit: float) -> float:
    """Validate one latitude/longitude value from the client.

    ``bool`` is rejected because it subclasses ``int``, and ``NaN``/``inf`` are
    rejected because they pass naive range comparisons.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Latitude and longitude must be numbers.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Latitude and longitude must be finite numbers.")
    if not -limit <= number <= limit:
        raise ValueError(f"Coordinate must be between -{limit:g} and {limit:g}.")
    return number


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


@app.get("/call")
def call_page():
    """Voice-call shell. It holds no data; the browser uses its bearer token."""
    return render_template("call.html")


@app.get("/settings")
def settings_page():
    """Account settings shell; the browser fetches and saves data with its token."""
    return render_template("settings.html")


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


@app.patch("/api/auth/profile")
def api_update_profile():
    """Update names/email and return a token carrying the new claims."""
    claims = auth.current_user() or {}
    data = request.get_json(silent=True) or {}
    try:
        user = auth.update_profile(
            claims.get("id"),
            data.get("first_name"),
            data.get("last_name"),
            data.get("email"),
            data.get("current_password"),
        )
    except auth.AuthError as exc:
        return jsonify(exc.to_dict()), exc.status
    return jsonify(auth.auth_payload(user))


@app.post("/api/auth/password")
def api_change_password():
    """Replace the password after verifying the current one."""
    claims = auth.current_user() or {}
    data = request.get_json(silent=True) or {}
    try:
        auth.change_password(
            claims.get("id"),
            data.get("current_password"),
            data.get("new_password"),
            data.get("confirm_password"),
        )
    except auth.AuthError as exc:
        return jsonify(exc.to_dict()), exc.status
    return "", 204


# --- API ------------------------------------------------------------------


@app.post("/api/chat")
def api_chat():
    """Non-streaming completion: returns ``{"reply": "..."}``."""
    data = request.get_json(silent=True) or {}
    language = normalize_language(data.get("language"))
    mode = normalize_mode(data.get("mode"))
    greeting = bool(data.get("greeting"))
    try:
        payload = build_payload(data.get("messages"), language, mode, greeting)
    except ValueError as exc:
        return jsonify({"error": {"code": "invalid_request", "message": str(exc)}}), 400

    auth.touch_last_message((auth.current_user() or {}).get("id"))

    try:
        client = get_client()
    except TokenHarborError as exc:
        return jsonify(error_body(exc)), 500

    # Persist both sides of the turn. Best effort: a logging failure must not
    # break the reply the user is waiting for.
    user_id = (auth.current_user() or {}).get("id")
    conversation_id = conversations.resolve_conversation(
        user_id, data.get("conversation_id"), mode, language
    )
    conversations.record_user_turn(
        user_id, conversation_id, conversations.last_user_message(payload), model=model_name()
    )

    started = time.perf_counter()
    logger.info(
        "chat.request",
        extra={
            "event": "chat.request",
            "model": model_name(),
            "language": language,
            "mode": mode,
            "streaming": False,
            "message_count": max(0, len(payload) - 1),
        },
    )
    tools, handlers = memory_toolset()
    call_params = {"max_tokens": CALL_MAX_TOKENS} if mode == "call" else {}
    try:
        reply = client.run_tools(
            messages=payload, tools=tools, handlers=handlers, **call_params
        )
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
    conversations.record_assistant_turn(
        user_id,
        conversation_id,
        reply,
        model=model_name(),
        latency_ms=elapsed_ms(started),
        outcome="ok",
    )
    return jsonify({"reply": reply, "conversation_id": conversation_id})


@app.post("/api/chat/stream")
def api_chat_stream():
    """Streamed completion over Server-Sent Events."""
    data = request.get_json(silent=True) or {}
    language = normalize_language(data.get("language"))
    mode = normalize_mode(data.get("mode"))
    greeting = bool(data.get("greeting"))
    try:
        payload = build_payload(data.get("messages"), language, mode, greeting)
    except ValueError as exc:
        return jsonify({"error": {"code": "invalid_request", "message": str(exc)}}), 400

    auth.touch_last_message((auth.current_user() or {}).get("id"))

    try:
        client = get_client()
    except TokenHarborError as exc:
        return jsonify(error_body(exc)), 500

    user_id = (auth.current_user() or {}).get("id")
    conversation_id = conversations.resolve_conversation(
        user_id, data.get("conversation_id"), mode, language
    )
    conversations.record_user_turn(
        user_id, conversation_id, conversations.last_user_message(payload), model=model_name()
    )

    logger.info(
        "chat.request",
        extra={
            "event": "chat.request",
            "model": model_name(),
            "language": language,
            "mode": mode,
            "streaming": True,
            "message_count": max(0, len(payload) - 1),
        },
    )
    tools, handlers = memory_toolset()
    stream_id, cancel, entry = register_stream(user_id)

    def on_open(response: Any) -> None:
        """Expose the live provider response so /api/chat/stop can close it."""
        with _ACTIVE_LOCK:
            entry["close"] = response.close
        if cancel.is_set():  # stopped before the provider connected
            response.close()

    def generate():
        started = time.perf_counter()
        outcome = "ok"
        reason = "finished"
        error_code: Optional[str] = None
        finish_reason: Optional[str] = None
        stopped = False
        answer: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        call_params = {"max_tokens": CALL_MAX_TOKENS} if mode == "call" else {}
        events = client.run_tools_events(
            messages=payload,
            tools=tools,
            handlers=handlers,
            cancel=cancel,
            on_open=on_open,
            **call_params,
        )
        try:
            for event in events:
                if cancel.is_set():
                    stopped = True
                    break
                if event["type"] == "delta" and event.get("content"):
                    answer.append(event["content"])
                    yield sse({"delta": event["content"]})
                elif event["type"] == "reasoning" and event.get("content"):
                    yield sse({"reasoning": event["content"]})
                elif event["type"] == "tool_start":
                    tool_calls.append({"name": event.get("name"), "ok": None})
                    yield sse({"tool": {"name": event.get("name"), "status": "start"}})
                elif event["type"] == "tool_end":
                    if tool_calls:
                        tool_calls[-1]["ok"] = bool(event.get("ok", True))
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
            if cancel.is_set():
                stopped = True

            if stopped:
                outcome = "stopped"
                reason = "stopped"
                yield sse({"stopped": True})
        except GeneratorExit:
            # the browser went away; the finally block closes the provider
            reason = "disconnected"
            outcome = "stopped"
            raise
        except (BrokenPipeError, ConnectionResetError):
            reason = "disconnected"
            outcome = "stopped"
            return
        except TokenHarborError as exc:
            if cancel.is_set():
                outcome = "stopped"
                reason = "stopped"
                yield sse({"stopped": True})
            else:
                outcome = "error"
                reason = "error"
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
            if cancel.is_set():
                outcome = "stopped"
                reason = "stopped"
                yield sse({"stopped": True})
            else:
                outcome = "error"
                reason = "error"
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
            try:
                events.close()  # runs the provider response.close() in core
            except Exception:
                pass
            unregister_stream(stream_id)
            # Record the reply even when the turn stopped, errored, or the
            # browser disconnected: a missing turn is worse than a flagged one.
            conversations.record_assistant_turn(
                user_id,
                conversation_id,
                "".join(answer),
                finish_reason=finish_reason,
                model=model_name(),
                latency_ms=elapsed_ms(started),
                outcome=outcome if outcome in conversations.VALID_OUTCOMES else "error",
                tool_calls=tool_calls or None,
            )
            logger.info(
                "chat.stream.done",
                extra={
                    "event": "chat.stream.done",
                    "streaming": True,
                    "finish_reason": finish_reason,
                    "reason": reason,
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
        "X-Stream-ID": stream_id,
    }
    return Response(stream_with_context(generate()), mimetype="text/event-stream", headers=headers)


@app.post("/api/chat/stop")
def api_chat_stop():
    """Stop one of the caller's active generations, closing the provider link."""
    claims = auth.current_user() or {}
    data = request.get_json(silent=True) or {}
    stream_id = data.get("id")
    if not isinstance(stream_id, str) or not stream_id.strip():
        return (
            jsonify({"error": {"code": "invalid_request", "message": "`id` is required."}}),
            400,
        )
    return jsonify({"stopped": stop_stream(claims.get("id"), stream_id.strip())})


# --- conversation API -----------------------------------------------------


@app.get("/api/conversations")
def api_conversations():
    """The caller's saved threads, newest first."""
    claims = auth.current_user() or {}
    items = conversations.list_conversations(claims.get("id"), request.args.get("limit"))
    return jsonify({"conversations": items})


@app.get("/api/conversations/<conversation_id>")
def api_conversation_detail(conversation_id: str):
    """One thread and its turns; only the owner can read it."""
    claims = auth.current_user() or {}
    detail = conversations.get_conversation_detail(claims.get("id"), conversation_id)
    if detail is None:
        return (
            jsonify({"error": {"code": "not_found", "message": "Conversation not found."}}),
            404,
        )
    return jsonify(detail)


# --- voice API ------------------------------------------------------------


@app.get("/api/voice/status")
def api_voice_status():
    """Whether voice generation is configured, plus the token expiry."""
    return jsonify(voice.status())


@app.post("/api/voice/speak")
def api_voice_speak():
    """Generate spoken audio for one assistant sentence."""
    claims = auth.current_user() or {}
    data = request.get_json(silent=True) or {}
    language = normalize_language(data.get("language"))
    raw_text = data.get("text")
    text_chars = len(raw_text) if isinstance(raw_text, str) else 0
    started = time.perf_counter()

    logger.info(
        "voice.request",
        extra={
            "event": "voice.request",
            "user_id": claims.get("id"),
            "language": language,
            "text_chars": text_chars,
        },
    )
    try:
        audio = voice.synthesize(raw_text, language=language, voice_name=data.get("voice"))
    except voice.VoiceError as exc:
        logger.warning(
            "voice.error",
            extra={
                "event": "voice.error",
                "user_id": claims.get("id"),
                "language": language,
                "text_chars": text_chars,
                "error_code": exc.code,
                "error_type": type(exc).__name__,
                "duration_ms": elapsed_ms(started),
                "outcome": "error",
            },
        )
        return jsonify({"error": {"code": exc.code, "message": exc.message}}), exc.status
    except Exception as exc:
        logger.exception(
            "voice.error",
            extra={
                "event": "voice.error",
                "user_id": claims.get("id"),
                "language": language,
                "text_chars": text_chars,
                "error_code": "voice_upstream_error",
                "error_type": type(exc).__name__,
                "duration_ms": elapsed_ms(started),
                "outcome": "error",
            },
        )
        return (
            jsonify(
                {
                    "error": {
                        "code": "voice_upstream_error",
                        "message": "Voice generation failed.",
                    }
                }
            ),
            502,
        )

    logger.info(
        "voice.done",
        extra={
            "event": "voice.done",
            "user_id": claims.get("id"),
            "language": language,
            "text_chars": text_chars,
            "bytes": len(audio),
            "duration_ms": elapsed_ms(started),
            "outcome": "ok",
        },
    )
    return Response(audio, mimetype="audio/mpeg", headers={"Cache-Control": "no-store"})


# --- location API ---------------------------------------------------------


@app.post("/api/location")
def api_location():
    """Store the caller's latest position; overwritten on every ping.

    Coordinates are personal data: they are written to the caller's own row and
    never logged, returned, or stored in chat history.
    """
    claims = auth.current_user() or {}
    data = request.get_json(silent=True) or {}
    started = time.perf_counter()
    try:
        latitude = clean_coordinate(data.get("latitude"), 90.0)
        longitude = clean_coordinate(data.get("longitude"), 180.0)
    except ValueError as exc:
        logger.warning(
            "location.invalid",
            extra={
                "event": "location.invalid",
                "user_id": claims.get("id"),
                "outcome": "error",
            },
        )
        return jsonify({"error": {"code": "invalid_request", "message": str(exc)}}), 400

    try:
        auth.record_location(claims.get("id"), latitude, longitude)
    except (TypeError, ValueError):
        return (
            jsonify({"error": {"code": "unauthorized", "message": "Authentication required."}}),
            401,
        )

    logger.info(
        "location.update",
        extra={
            "event": "location.update",
            "user_id": claims.get("id"),
            "duration_ms": elapsed_ms(started),
            "outcome": "ok",
        },
    )
    return "", 204


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=debug)
