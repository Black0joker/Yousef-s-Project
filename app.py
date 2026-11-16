"""Flask chat app on top of the ``core`` Token Harbor client.

Routes
------
``GET  /``                 welcome page: enter your name (saved in localStorage)
``GET  /chat``             chat page: greeting + message input
``POST /api/chat/stream``  streamed reply over SSE
``POST /api/chat``         non-streaming JSON fallback

The browser never sees the API key: all calls go through this server, which
prepends the system prompt as the message at index 0.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from core import Message, TokenHarbor, TokenHarborError

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

app = Flask(__name__)


# --- config ---------------------------------------------------------------


def model_name() -> str:
    """Model id, overridable with ``TH_MODEL``."""
    return os.environ.get("TH_MODEL", "").strip() or DEFAULT_WEB_MODEL


def system_prompt() -> str:
    """System prompt, overridable with ``TH_SYSTEM_PROMPT``."""
    return os.environ.get("TH_SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT


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


def build_payload(raw: Any) -> List[Message]:
    """Conversation with the system prompt inserted at index 0."""
    return [{"role": "system", "content": system_prompt()}] + normalize_messages(raw)


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
def welcome():
    """Welcome page: the user enters their name."""
    return render_template("welcome.html")


@app.get("/chat")
def chat():
    """Chat page; the name is read from localStorage by the browser."""
    return render_template("chat.html")


# --- API ------------------------------------------------------------------


@app.post("/api/chat")
def api_chat():
    """Non-streaming completion: returns ``{"reply": "..."}``."""
    data = request.get_json(silent=True) or {}
    try:
        payload = build_payload(data.get("messages"))
    except ValueError as exc:
        return jsonify({"error": {"code": "invalid_request", "message": str(exc)}}), 400

    try:
        client = get_client()
    except TokenHarborError as exc:
        return jsonify(error_body(exc)), 500

    try:
        reply = client.chat(payload)
    except TokenHarborError as exc:
        return jsonify(error_body(exc)), error_status(exc)

    return jsonify({"reply": reply})


@app.post("/api/chat/stream")
def api_chat_stream():
    """Streamed completion over Server-Sent Events."""
    data = request.get_json(silent=True) or {}
    try:
        payload = build_payload(data.get("messages"))
    except ValueError as exc:
        return jsonify({"error": {"code": "invalid_request", "message": str(exc)}}), 400

    try:
        client = get_client()
    except TokenHarborError as exc:
        return jsonify(error_body(exc)), 500

    def generate():
        try:
            for event in client.stream_events(payload):
                if event["type"] == "delta" and event.get("content"):
                    yield sse({"delta": event["content"]})
                elif event["type"] == "reasoning" and event.get("content"):
                    yield sse({"reasoning": event["content"]})
                elif event["type"] == "done":
                    yield sse(
                        {
                            "done": True,
                            "finish_reason": event.get("finish_reason"),
                            "usage": event.get("usage"),
                        }
                    )
        except TokenHarborError as exc:
            yield sse(error_body(exc))
        except Exception as exc:  # never leak a traceback into the stream
            yield sse({"error": {"code": "server_error", "message": str(exc)}})
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
