"""Core Token Harbor client.

A small, dependency-light wrapper around Token Harbor's OpenAI-compatible
gateway (``https://tokenharbor.ai/v1``). It handles:

* passing messages (single call, or a growing conversation),
* streaming token deltas over Server-Sent Events,
* conversation history management,
* retries with backoff and descriptive errors for every HTTP status code
  the gateway (or an upstream provider) can return -- including ``409``.

Docs: https://tokenharbor.ai/docs  (Chat APIs: /docs/api/curl)
"""

from __future__ import annotations

import inspect
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    TypedDict,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

import requests

try:  # optional: auto-load a local .env so TH_API_KEY is picked up
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional
    pass


__all__ = [
    "TokenHarbor",
    "TokenHarborError",
    "Message",
    "ToolCall",
    "tool",
    "function_tool",
    "parse_tool_calls",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "STATUS_HINTS",
]


DEFAULT_BASE_URL = "https://tokenharbor.ai/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"

#: Environment variables checked (in order) for the universal key.
API_KEY_ENV_VARS = ("TH_API_KEY", "TOKENHARBOR_API_KEY", "TOKENHARBOR_KEY")


class Message(TypedDict, total=False):
    """An OpenAI-shaped chat message."""

    role: str
    content: Any
    name: str
    tool_calls: Any
    tool_call_id: str


# --- tool calls -----------------------------------------------------------


@dataclass
class ToolCall:
    """A tool/function call requested by the model, with parsed arguments."""

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""
    type: str = "function"
    parse_error: Optional[str] = None

    def to_raw(self) -> Dict[str, Any]:
        """Rebuild the OpenAI-shaped dict (used when echoing back to the API)."""
        return {
            "id": self.id,
            "type": self.type,
            "function": {"name": self.name, "arguments": self.raw_arguments},
        }

    def __repr__(self) -> str:  # keep error output readable
        return f"ToolCall(id={self.id!r}, name={self.name!r}, arguments={self.arguments!r})"


def _parse_arguments(raw: Any) -> tuple:
    """Return ``(dict, error)`` for a tool-call ``arguments`` value."""
    if isinstance(raw, dict):
        return dict(raw), None
    if raw is None or raw == "":
        return {}, None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        return {}, str(exc)
    if isinstance(parsed, dict):
        return parsed, None
    return {"value": parsed}, None


def parse_tool_calls(raw_calls: Optional[Iterable[Mapping[str, Any]]]) -> List[ToolCall]:
    """Convert OpenAI-shaped ``tool_calls`` into :class:`ToolCall` objects."""
    result: List[ToolCall] = []
    for raw in raw_calls or []:
        function = raw.get("function") or {}
        raw_arguments = function.get("arguments")
        arguments, error = _parse_arguments(raw_arguments)
        result.append(
            ToolCall(
                id=raw.get("id") or "",
                name=function.get("name") or "",
                arguments=arguments,
                raw_arguments=(
                    raw_arguments
                    if isinstance(raw_arguments, str)
                    else json.dumps(raw_arguments or {})
                ),
                type=raw.get("type") or "function",
                parse_error=error,
            )
        )
    return result


def _extract_message(data: Dict[str, Any]) -> Dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        return {"role": "assistant", "content": ""}
    return choices[0].get("message") or {"role": "assistant", "content": ""}


# --- status-code knowledge ------------------------------------------------

#: (code, human hint) for each status the gateway is known to emit.
STATUS_HINTS: Dict[int, tuple] = {
    400: ("invalid_request", "Malformed request body or an unsupported parameter."),
    401: (
        "invalid_api_key",
        "Missing, malformed, or revoked key. Paste the full `thk_live_...` value with no spaces.",
    ),
    402: ("insufficient_credit", "Balance or free allowance exhausted. Top up, or use a `:free` model id."),
    403: ("forbidden", "The key is valid but not permitted to reach this model or endpoint."),
    404: ("not_found", "Unknown model id or path. Fetch valid ids from GET /v1/models."),
    405: ("method_not_allowed", "Wrong HTTP method for this endpoint."),
    408: ("request_timeout", "The gateway timed out waiting on the upstream provider."),
    409: (
        "conflict",
        "Request conflicts with server state (duplicate/in-flight request, or the model is being "
        "rerouted/migrated between providers). Safe to retry after a short backoff.",
    ),
    413: ("payload_too_large", "Request body exceeds the gateway's size limit."),
    415: ("unsupported_media_type", "Content-Type must be application/json."),
    422: ("unprocessable_entity", "Well-formed JSON the gateway still could not process."),
    425: ("too_early", "The gateway is still warming up this request. Retry shortly."),
    429: ("rate_limited", "Too many requests. Honour the Retry-After header and back off."),
    500: ("internal_error", "Unexpected gateway error. Safe to retry."),
    502: ("bad_gateway", "Upstream provider returned an invalid response. Safe to retry."),
    503: ("service_unavailable", "Upstream provider is overloaded or down. Retry with backoff."),
    504: ("gateway_timeout", "Upstream provider did not respond in time. Safe to retry."),
}

#: Statuses worth retrying automatically.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


def _describe(status_code: int) -> tuple:
    """Return ``(code, hint)`` for any status code, known or not."""
    if status_code in STATUS_HINTS:
        return STATUS_HINTS[status_code]
    if 400 <= status_code < 500:
        return ("client_error", "The gateway rejected this request.")
    if status_code >= 500:
        return ("server_error", "Gateway or upstream provider failure. Safe to retry.")
    return ("error", "Unexpected response from the gateway.")


class TokenHarborError(Exception):
    """Raised for any non-2xx response, or when the gateway is unreachable."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        code: Optional[str] = None,
        body: Any = None,
        response: Optional[requests.Response] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.body = body
        self.response = response

    @property
    def retryable(self) -> bool:
        return self.status_code in RETRYABLE_STATUS


# --- helpers --------------------------------------------------------------


def _extract_text(data: Dict[str, Any]) -> str:
    """Pull assistant text out of an OpenAI-shaped completion body."""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):  # multimodal content parts
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""


def _normalize_messages(messages: Any) -> List[Message]:
    if messages is None:
        return []
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if isinstance(messages, dict):
        return [dict(messages)]
    return [dict(m) for m in messages]


# --- tool schema helpers --------------------------------------------------

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}


def _json_type(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "string"
    origin = get_origin(annotation)
    if origin is Union:
        inner = [a for a in get_args(annotation) if a is not type(None)]
        return _json_type(inner[0]) if len(inner) == 1 else "string"
    if origin in (list, List):
        return "array"
    if origin in (dict, Dict):
        return "object"
    return _JSON_TYPES.get(annotation, "string")


def tool(func=None, *, name: Optional[str] = None, description: Optional[str] = None):
    """Decorator turning a Python function into an OpenAI tool definition.

    >>> @tool
    ... def get_weather(city: str) -> dict:
    ...     \"\"\"Get the current weather for a city.\"\"\"
    ...     return {"city": city, "temp_c": 21}
    >>> get_weather.tool_schema["function"]["parameters"]["required"]
    ['city']
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        try:
            hints = get_type_hints(fn)
        except Exception:  # unresolvable annotations -> fall back to defaults
            hints = {}
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for param_name, param in inspect.signature(fn).parameters.items():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            properties[param_name] = {"type": _json_type(hints.get(param_name, param.annotation))}
            if param.default is inspect.Parameter.empty:
                required.append(param_name)
        schema = {
            "type": "function",
            "function": {
                "name": name or fn.__name__,
                "description": description or (inspect.getdoc(fn) or "").strip(),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
        fn.tool_schema = schema  # type: ignore[attr-defined]
        fn.tool_name = schema["function"]["name"]  # type: ignore[attr-defined]
        return fn

    if func is None:
        return decorate
    return decorate(func)


function_tool = tool


def _prepare_tools(
    tools: Optional[Sequence[Any]], handlers: Optional[Mapping[str, Callable[..., Any]]]
) -> tuple:
    """Split ``tools`` into API schemas and a ``name -> callable`` registry."""
    schemas: List[Dict[str, Any]] = []
    registry: Dict[str, Callable[..., Any]] = dict(handlers or {})
    for item in tools or []:
        if isinstance(item, Mapping):
            schemas.append(dict(item))
        elif callable(item):
            schema = getattr(item, "tool_schema", None)
            if schema is None:
                raise TokenHarborError(
                    f"{getattr(item, '__name__', item)!r} has no tool schema; "
                    "decorate it with @tool or pass a schema dict.",
                    code="invalid_tool",
                )
            schemas.append(schema)
            registry.setdefault(getattr(item, "tool_name", item.__name__), item)
        else:
            raise TokenHarborError(f"Cannot use {item!r} as a tool.", code="invalid_tool")
    return schemas, registry


# --- client ---------------------------------------------------------------


class TokenHarbor:
    """Conversation client for the Token Harbor gateway.

    >>> client = TokenHarbor(model="deepseek-v4.1-flash")
    >>> client.ask("Say hi in five words.")          # non-streaming
    >>> for piece in client.stream_ask("Tell me a story"):
    ...     print(piece, end="", flush=True)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 8.0,
        extra_headers: Optional[Dict[str, str]] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.api_key = api_key or self._key_from_env()
        if not self.api_key:
            raise TokenHarborError(
                "No API key found. Pass api_key=... or set one of "
                f"{', '.join(API_KEY_ENV_VARS)} (format: thk_live_...).",
                code="missing_api_key",
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.extra_headers = dict(extra_headers or {})
        self._session = session or requests.Session()

        #: Live conversation history. Mutated by .user/.assistant/.ask/etc.
        self.messages: List[Message] = []

    # -- key handling ------------------------------------------------------

    @staticmethod
    def _key_from_env() -> Optional[str]:
        for name in API_KEY_ENV_VARS:
            value = os.environ.get(name)
            if value:
                return value.strip()
        return None

    # -- history -----------------------------------------------------------

    def add(self, role: str, content: Any = None, **extra: Any) -> "TokenHarbor":
        """Append a raw message to the history."""
        entry: Message = {"role": role, "content": content}
        entry.update(extra)  # e.g. name=..., tool_call_id=...
        self.messages.append(entry)
        return self

    def system(self, content: Any) -> "TokenHarbor":
        return self.add("system", content)

    def user(self, content: Any) -> "TokenHarbor":
        return self.add("user", content)

    def assistant(self, content: Any) -> "TokenHarbor":
        return self.add("assistant", content)

    def tool(self, content: Any, *, tool_call_id: str, name: Optional[str] = None) -> "TokenHarbor":
        extra = {"tool_call_id": tool_call_id}
        if name:
            extra["name"] = name
        return self.add("tool", content, **extra)

    def reset(self) -> "TokenHarbor":
        """Clear the conversation history."""
        self.messages = []
        return self

    def load(self, messages: Iterable[Message]) -> "TokenHarbor":
        """Replace the history with ``messages``."""
        self.messages = _normalize_messages(messages)
        return self

    @property
    def history(self) -> List[Message]:
        """A copy of the current conversation."""
        return [dict(m) for m in self.messages]

    # -- payload / headers -------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _build_payload(
        self,
        messages: Any,
        model: Optional[str],
        *,
        stream: bool,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": _normalize_messages(messages if messages is not None else self.messages),
        }
        if stream:
            payload["stream"] = True
        for key, value in params.items():
            if value is not None:
                payload[key] = value
        return payload

    # -- retry plumbing ----------------------------------------------------

    def _sleep_backoff(self, attempt: int, retry_after: Optional[float] = None) -> None:
        if retry_after is not None:
            delay = max(0.0, retry_after)
        else:
            delay = min(self.backoff_base * (2**attempt), self.backoff_max)
            delay += random.uniform(0, 0.25 * delay)
        if delay > 0:
            time.sleep(delay)

    @staticmethod
    def _retry_after(response: Optional[requests.Response]) -> Optional[float]:
        if response is None:
            return None
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            return None  # HTTP-date form; fall back to exponential backoff

    def _error_from_response(self, response: requests.Response) -> TokenHarborError:
        raw_text = ""
        body: Any = None
        try:
            raw_text = response.text
            body = response.json()
        except ValueError:
            body = None

        code: Optional[str] = None
        detail: Optional[str] = None
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                code = error.get("code") or error.get("type")
                detail = error.get("message")
            elif isinstance(error, str):
                detail = error
            code = code or body.get("code") or body.get("type")
            detail = detail or body.get("message")

        hint_code, hint = _describe(response.status_code)
        code = code or hint_code
        message = detail or hint
        full = f"[{response.status_code} {code}] {message}"
        if detail and detail != hint:
            full = f"{full} ({hint})"
        return TokenHarborError(
            full,
            status_code=response.status_code,
            code=code,
            body=body if body is not None else raw_text,
            response=response,
        )

    # -- transport ---------------------------------------------------------

    def _request_json(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = self._url(path)
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._session.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise TokenHarborError(
                    f"Could not reach Token Harbor at {url}: {exc}", code="connection_error"
                ) from exc

            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise TokenHarborError(
                        f"[{response.status_code}] Response was not valid JSON.",
                        status_code=response.status_code,
                        code="invalid_json",
                        body=response.text,
                        response=response,
                    ) from exc

            error = self._error_from_response(response)
            retry_after = self._retry_after(response)
            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                last_error = error
                self._sleep_backoff(attempt, retry_after)
                continue
            raise error

        # unreachable, but keeps type checkers happy
        raise TokenHarborError(f"Request failed after retries: {last_error}", code="request_failed")

    def _open_stream(self, payload: Dict[str, Any]) -> requests.Response:
        url = self._url("/chat/completions")
        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._session.post(
                    url,
                    headers=self._headers(),
                    json=payload,
                    stream=True,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise TokenHarborError(
                    f"Could not reach Token Harbor at {url}: {exc}", code="connection_error"
                ) from exc

            if response.status_code < 400:
                response.encoding = "utf-8"
                return response

            error = self._error_from_response(response)
            retry_after = self._retry_after(response)
            response.close()
            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                last_error = error
                self._sleep_backoff(attempt, retry_after)
                continue
            raise error

        raise TokenHarborError(f"Request failed after retries: {last_error}", code="request_failed")

    # -- non-streaming API -------------------------------------------------

    def chat_raw(self, messages: Any = None, *, model: Optional[str] = None, **params: Any) -> Dict[str, Any]:
        """Send a completion request and return the full JSON body."""
        payload = self._build_payload(messages, model, stream=False, params=params)
        return self._request_json("POST", "/chat/completions", payload)

    def chat(self, messages: Any = None, *, model: Optional[str] = None, **params: Any) -> str:
        """Send a completion request and return just the assistant text."""
        return _extract_text(self.chat_raw(messages, model=model, **params))

    def ask(self, content: Any, *, model: Optional[str] = None, **params: Any) -> str:
        """Append a user turn, get the reply, append it, and return the text.

        The history grows automatically, so follow-up ``ask`` calls keep context.
        """
        self.user(content)
        data = self.chat_raw(self.messages, model=model, **params)
        text = _extract_text(data)
        self.assistant(text)
        return text

    def chat_message(self, messages: Any = None, *, model: Optional[str] = None, **params: Any) -> Dict[str, Any]:
        """Return the assistant ``message`` dict (includes raw ``tool_calls``)."""
        return _extract_message(self.chat_raw(messages, model=model, **params))

    # -- streaming API -----------------------------------------------------

    def stream_events(
        self,
        messages: Any = None,
        *,
        model: Optional[str] = None,
        include_usage: bool = False,
        **params: Any,
    ) -> Iterator[Dict[str, Any]]:
        """Yield typed SSE events: ``delta``, ``reasoning`` and a final ``done``.

        Each event is a dict with a ``type`` key. ``delta``/``reasoning`` carry
        ``content``; ``done`` carries ``finish_reason``, (optional) ``usage`` and
        a ``tool_calls`` list of :class:`ToolCall` objects (empty if none).
        """
        payload = self._build_payload(messages, model, stream=True, params=params)
        if include_usage:
            payload.setdefault("stream_options", {"include_usage": True})

        response = self._open_stream(payload)
        finish_reason: Optional[str] = None
        usage: Optional[Dict[str, Any]] = None
        tool_slots: Dict[int, Dict[str, str]] = {}
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line.strip()
                if not line or line.startswith(":"):  # blank keep-alive / comment
                    continue
                if not line.startswith("data:"):
                    continue  # ignore event:/id:/retry: framing
                data_str = line[len("data:") :].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if not isinstance(chunk, dict):
                    continue

                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]

                choices = chunk.get("choices") or []
                if not choices:
                    if chunk.get("error"):
                        err = chunk["error"]
                        detail = err.get("message") if isinstance(err, dict) else str(err)
                        raise TokenHarborError(f"Stream error: {detail}", code="stream_error", body=chunk)
                    continue

                choice = choices[0] or {}
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}

                # Tool calls stream as fragments: id/name first, then argument
                # JSON split across many chunks. Accumulate by index.
                for fragment in delta.get("tool_calls") or []:
                    if not isinstance(fragment, dict):
                        continue
                    index = fragment.get("index", len(tool_slots))
                    slot = tool_slots.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if fragment.get("id"):
                        slot["id"] = fragment["id"]
                    function = fragment.get("function") or {}
                    if function.get("name"):
                        slot["name"] = function["name"]
                    if function.get("arguments"):
                        slot["arguments"] += function["arguments"]

                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                content = delta.get("content")
                if content:
                    yield {"type": "delta", "content": content, "reasoning": reasoning}
                elif reasoning:
                    yield {"type": "reasoning", "content": reasoning}

            raw_calls = [
                {
                    "id": slot["id"],
                    "type": "function",
                    "function": {"name": slot["name"], "arguments": slot["arguments"]},
                }
                for _, slot in sorted(tool_slots.items())
            ]
            yield {
                "type": "done",
                "content": "",
                "finish_reason": finish_reason,
                "usage": usage,
                "tool_calls": parse_tool_calls(raw_calls),
            }
        finally:
            response.close()

    def stream(self, messages: Any = None, *, model: Optional[str] = None, **params: Any) -> Iterator[str]:
        """Yield assistant text deltas as they are generated."""
        for event in self.stream_events(messages, model=model, **params):
            if event["type"] == "delta" and event.get("content"):
                yield event["content"]

    def stream_ask(self, content: Any, *, model: Optional[str] = None, **params: Any) -> Iterator[str]:
        """Append a user turn, stream the reply, and record it once complete."""
        self.user(content)
        parts: List[str] = []
        for piece in self.stream(self.messages, model=model, **params):
            parts.append(piece)
            yield piece
        self.assistant("".join(parts))

    # -- tool calling ------------------------------------------------------

    @staticmethod
    def _invoke_tool(call: ToolCall, registry: Mapping[str, Callable[..., Any]]) -> str:
        """Run one tool call and return the string content for the ``tool`` message."""
        if call.parse_error:
            return json.dumps({"error": f"Invalid JSON arguments: {call.parse_error}"})
        handler = registry.get(call.name)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {call.name}"})
        try:
            result = handler(**call.arguments)
        except Exception as exc:  # surface the failure to the model, don't crash
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"})
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, default=str)
        except TypeError:
            return str(result)

    @staticmethod
    def _working_messages(messages: Any, content: Any, history: List[Message]) -> List[Message]:
        if messages is None:
            working = history
        elif isinstance(messages, list):
            working = messages
        else:
            working = _normalize_messages(messages)
        if content is not None:
            working.append({"role": "user", "content": content})
        return working

    def run_tools(
        self,
        content: Any = None,
        *,
        tools: Sequence[Any],
        handlers: Optional[Mapping[str, Callable[..., Any]]] = None,
        model: Optional[str] = None,
        max_rounds: int = 8,
        messages: Any = None,
        **params: Any,
    ) -> str:
        """Run the tool-calling loop until the model answers without a tool.

        ``tools`` accepts ``@tool``-decorated functions and/or raw OpenAI tool
        schema dicts. Handlers can also be supplied explicitly via ``handlers``.
        Tool results are fed back as ``tool`` messages and the final assistant
        text is returned. History is mutated when ``messages`` is omitted.
        """
        working = self._working_messages(messages, content, self.messages)
        schemas, registry = _prepare_tools(tools, handlers)
        rounds = 0
        while True:
            data = self.chat_raw(working, model=model, tools=schemas, **params)
            message = _extract_message(data)
            raw_calls = message.get("tool_calls") or []
            assistant_message: Dict[str, Any] = {
                "role": "assistant",
                "content": message.get("content") or "",
            }
            if raw_calls:
                assistant_message["tool_calls"] = raw_calls
            working.append(assistant_message)  # type: ignore[arg-type]

            if not raw_calls:
                return message.get("content") or ""

            if rounds >= max_rounds:
                raise TokenHarborError(
                    f"Tool loop exceeded max_rounds={max_rounds}; the model kept calling tools.",
                    code="max_tool_rounds",
                )
            rounds += 1
            for call in parse_tool_calls(raw_calls):
                working.append(  # type: ignore[arg-type]
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": self._invoke_tool(call, registry),
                    }
                )

    def run_tools_stream(
        self,
        content: Any = None,
        *,
        tools: Sequence[Any],
        handlers: Optional[Mapping[str, Callable[..., Any]]] = None,
        model: Optional[str] = None,
        max_rounds: int = 8,
        messages: Any = None,
        **params: Any,
    ) -> Iterator[str]:
        """Streaming variant of :meth:`run_tools`; yields assistant text deltas."""
        working = self._working_messages(messages, content, self.messages)
        schemas, registry = _prepare_tools(tools, handlers)
        rounds = 0
        while True:
            parts: List[str] = []
            tool_calls: List[ToolCall] = []
            for event in self.stream_events(working, model=model, tools=schemas, **params):
                if event["type"] == "delta":
                    parts.append(event["content"])
                    yield event["content"]
                elif event["type"] == "done":
                    tool_calls = event.get("tool_calls") or []

            text = "".join(parts)
            assistant_message: Dict[str, Any] = {"role": "assistant", "content": text}
            if tool_calls:
                assistant_message["tool_calls"] = [call.to_raw() for call in tool_calls]
            working.append(assistant_message)  # type: ignore[arg-type]

            if not tool_calls:
                return

            if rounds >= max_rounds:
                raise TokenHarborError(
                    f"Tool loop exceeded max_rounds={max_rounds}; the model kept calling tools.",
                    code="max_tool_rounds",
                )
            rounds += 1
            for call in tool_calls:
                working.append(  # type: ignore[arg-type]
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": self._invoke_tool(call, registry),
                    }
                )

    # -- misc --------------------------------------------------------------

    def list_models(self) -> List[Dict[str, Any]]:
        """Return the model catalogue from ``GET /v1/models``."""
        data = self._request_json("GET", "/models")
        return data.get("data", data) if isinstance(data, dict) else data

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "TokenHarbor":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --- demo -----------------------------------------------------------------

def _demo() -> None:  # pragma: no cover
    client = TokenHarbor()
    client.system("You are a concise assistant.")

    print("--- non-streaming ---")
    print(client.ask("Say hi in five words."))

    print("\n--- streaming ---")
    for piece in client.stream_ask("Write a haiku about the sea."):
        print(piece, end="", flush=True)
    print("\n\n--- history ---")
    print(json.dumps(client.history, indent=2)[:400])


if __name__ == "__main__":  # pragma: no cover
    _demo()
