"""ElevenLabs voice generation for the SAGE call experience.

This ports the request shape used by the ElevenApi project
(https://github.com/Black0joker/ElevenApi): it replays the elevenlabs.io web
request against ``POST /v1/text-to-dialogue/stream`` using a browser session
token rather than an official ``xi-api-key``.

The session token expires after about an hour and must be refreshed manually
from the browser's DevTools network tab. ``status()`` reports when it dies so
the UI can explain the failure. Never log the token or the text.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

logger = logging.getLogger(__name__)

ENDPOINT = "/v1/text-to-dialogue/stream"
MODEL = "eleven_v4_exp"
DEFAULT_BASE_URL = "https://api.us.elevenlabs.io"
DEFAULT_FORMAT = "mp3_44100_128"
CHUNK_SIZE = 64 * 1024
MAX_CHARS = 2_000
MAX_TEXT_CHARS = 4_000
REQUEST_TIMEOUT = 120.0
ATTEMPTS = 3

#: Friendly names used by the call UI. Override the default with
#: ELEVENLABS_VOICE_ID (and ELEVENLABS_VOICE_ID_AR for Arabic).
DEFAULT_VOICES: Dict[str, str] = {
    "owl": "r1KmysJdVYZjJCm4mL3b",
    "george": "JBFqnCBsd6RMkjVDRZzb",
    "host": "Aw4FAjKCGjjNkVhN1Xmq",
}
DEFAULT_VOICE_NAME = "owl"

WEB_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.8",
    "content-type": "application/json",
    "origin": "https://elevenlabs.io",
    "priority": "u=1, i",
    "referer": "https://elevenlabs.io/",
    "sec-ch-ua": '"Not-A.Brand";v="99", "Brave";v="157", "Chromium";v="157"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "sec-gpc": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
    ),
    "x-generation-actor": "User",
    "x-generation-surface": "Speech Synthesis",
}

SENTENCE_RE = re.compile(r"(?<=[.!?\u2026])\s+")


class VoiceError(Exception):
    """A voice failure with a stable code and an HTTP status for the route."""

    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


#: Delivery/tag cheat sheet appended to call-mode system prompts so the model
#: can direct ElevenLabs without reading the tags aloud. Derived from TAGS.md
#: in the ElevenApi project.
VOICE_TAG_BRIEF = """\
Voice delivery tags

You are speaking through a text-to-dialogue voice model. Put delivery tags in
square brackets directly in your reply; the model performs them instead of
reading them aloud. Never read a tag aloud or explain it.

- Laughter: [laughs] [chuckles] [giggling]
- Breath: [sighs] [exhales] [inhales deeply] [clears throat]
- Emotion: [happy] [sad] [excited] [curious] [thoughtful] [surprised] [angry] [annoyed]
- Register: [sympathetic] [reassuring] [professional] [questioning] [whispers]
- Attitude: [playful] [sarcastic] [deadpan] [dismissive] [mischievously]
- Timbre: [low, gravelly voice] [quiet, measured narration]

Punctuation controls pacing: "..." is a hesitation or trailing off, "--" is a
short pause, CAPS adds emphasis, and "!" or "?" changes intonation. There is no
SSML and no markdown: <break time="1.5s" /> and asterisks will not work.

Use tags sparingly and only when they genuinely fit the sentence. A call with
no tags reads better than one where every line has a tag."""


def prompt_brief() -> str:
    """Language-independent delivery-tag guidance for call-mode prompts."""
    return VOICE_TAG_BRIEF


# --- config ---------------------------------------------------------------


def _token() -> str:
    raw = (
        os.environ.get("ELEVENLABS_TOKEN")
        or os.environ.get("ELEVENLABS_AUTHORIZATION")
        or ""
    ).strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw


def _base_url() -> str:
    return (
        os.environ.get("ELEVENLABS_BASE_URL", "").strip() or DEFAULT_BASE_URL
    ).rstrip("/")


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return min(1.0, max(0.0, value))


def _env_voice(name: str, legacy: str) -> str:
    return os.environ.get(name, "").strip() or os.environ.get(legacy, "").strip()


def _jwt_claims(token: str) -> Dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


def _iso(timestamp: float) -> str:
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def token_state() -> Dict[str, Any]:
    """Describe the configured web token without ever returning the token."""
    raw = _token()
    if not raw:
        return {
            "configured": False,
            "available": False,
            "expired": False,
            "expires_at": None,
            "reason": "missing",
        }

    claims = _jwt_claims(raw)
    exp = claims.get("exp")
    if isinstance(exp, (int, float)):
        expired = exp <= time.time()
        return {
            "configured": True,
            "available": not expired,
            "expired": expired,
            "expires_at": _iso(exp),
            "reason": "expired" if expired else "ok",
        }

    return {
        "configured": True,
        "available": True,
        "expired": False,
        "expires_at": None,
        "reason": "opaque",
    }


def voice_names() -> List[str]:
    return sorted(DEFAULT_VOICES)


def default_voice_name(language: str = "en") -> str:
    if language == "ar" and _env_voice("ELEVENLABS_VOICE_ID_AR", "TH_VOICE_ID_AR"):
        return "arabic"
    return DEFAULT_VOICE_NAME


def resolve_voice_id(name: Any = None, language: str = "en") -> str:
    """Resolve a friendly voice name or known id to a voice id."""
    if language == "ar":
        arabic = _env_voice("ELEVENLABS_VOICE_ID_AR", "TH_VOICE_ID_AR")
        if arabic and (name is None or name in ("", "default", "arabic")):
            return arabic

    if name in (None, "", "default"):
        configured = _env_voice("ELEVENLABS_VOICE_ID", "TH_VOICE_ID")
        return configured or DEFAULT_VOICES[DEFAULT_VOICE_NAME]

    if not isinstance(name, str):
        raise VoiceError("voice_invalid_request", "Voice must be a name or id.", 400)
    cleaned = name.strip()
    if cleaned in DEFAULT_VOICES:
        return DEFAULT_VOICES[cleaned]
    if cleaned in DEFAULT_VOICES.values():
        return cleaned
    configured = _env_voice("ELEVENLABS_VOICE_ID", "TH_VOICE_ID")
    if cleaned and cleaned == configured:
        return cleaned
    raise VoiceError("voice_invalid_request", "Unknown voice.", 400)


def status() -> Dict[str, Any]:
    """Public voice status for the UI: availability, expiry, and voice names."""
    state = token_state()
    return {
        "configured": state["configured"],
        "available": state["available"],
        "expired": state["expired"],
        "expires_at": state["expires_at"],
        "reason": state["reason"],
        "voices": voice_names(),
        "default_voice": default_voice_name(),
        "model": MODEL,
    }


# --- text / payload helpers ----------------------------------------------


def split_text(text: str, limit: int = MAX_CHARS) -> List[str]:
    if len(text) <= limit:
        return [text]

    pieces: List[str] = []
    buffer = ""
    for sentence in SENTENCE_RE.split(text):
        candidate = f"{buffer} {sentence}".strip() if buffer else sentence
        if len(candidate) <= limit:
            buffer = candidate
            continue
        if buffer:
            pieces.append(buffer)
            buffer = ""
        while len(sentence) > limit:
            pieces.append(sentence[:limit])
            sentence = sentence[limit:]
        buffer = sentence
    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_inputs(inputs: Sequence[Dict[str, str]], limit: int = MAX_CHARS) -> List[List[Dict[str, str]]]:
    expanded: List[Dict[str, str]] = []
    for item in inputs:
        for piece in split_text(item["text"], limit):
            expanded.append({"text": piece, "voice_id": item["voice_id"]})

    chunks: List[List[Dict[str, str]]] = []
    current: List[Dict[str, str]] = []
    current_chars = 0
    for item in expanded:
        if current and current_chars + len(item["text"]) > limit:
            chunks.append(current)
            current, current_chars = [], 0
        current.append(item)
        current_chars += len(item["text"])
    if current:
        chunks.append(current)
    return chunks


def build_payload(text: str, voice_id: str) -> Dict[str, Any]:
    return {
        "inputs": [{"text": text, "voice_id": voice_id}],
        "model_id": os.environ.get("ELEVENLABS_MODEL", "").strip() or MODEL,
        "settings": {
            "stability": _float_env("ELEVENLABS_STABILITY", 0.5),
            "similarity": _float_env("ELEVENLABS_SIMILARITY", 0.75),
            "text_focus": False,
        },
        "collect_preference": True,
    }


# --- HTTP -----------------------------------------------------------------


def _post(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> requests.Response:
    last_error: Optional[Exception] = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt < ATTEMPTS:
                time.sleep(2.0 ** attempt)
                continue
            raise VoiceError(
                "voice_upstream_error",
                f"ElevenLabs request failed ({type(exc).__name__}).",
                502,
            ) from None

        if response.status_code in (200, 201):
            return response

        status_code = response.status_code
        response.close()
        if status_code in (429, 500, 502, 503, 504) and attempt < ATTEMPTS:
            time.sleep(2.0 ** attempt)
            continue
        if status_code in (401, 403):
            raise VoiceError(
                "voice_token_expired",
                "The ElevenLabs web token was rejected or has expired.",
                503,
            )
        raise VoiceError(
            "voice_upstream_error",
            f"ElevenLabs returned status {status_code}.",
            502,
        )

    raise VoiceError("voice_upstream_error", "ElevenLabs request failed.", 502) from last_error


def synthesize(
    text: Any,
    language: str = "en",
    voice_name: Any = None,
) -> bytes:
    """Generate MP3 audio for ``text``; never logs the text or token."""
    if not isinstance(text, str) or not text.strip():
        raise VoiceError("voice_invalid_request", "Voice text is required.", 400)
    cleaned = text.strip()
    if len(cleaned) > MAX_TEXT_CHARS:
        raise VoiceError(
            "voice_text_too_long",
            f"Voice text is too long (max {MAX_TEXT_CHARS} characters).",
            400,
        )

    state = token_state()
    if not state["configured"]:
        raise VoiceError(
            "voice_token_missing",
            "Voice is not configured. Set ELEVENLABS_TOKEN.",
            503,
        )
    if not state["available"]:
        raise VoiceError(
            "voice_token_expired",
            "The ElevenLabs web token has expired. Refresh ELEVENLABS_TOKEN.",
            503,
        )

    voice_id = resolve_voice_id(voice_name, language)
    chunks = chunk_inputs([{"text": cleaned, "voice_id": voice_id}])
    base = _base_url()
    url = f"{base}{ENDPOINT}"
    headers = dict(WEB_HEADERS)
    headers["authorization"] = f"Bearer {_token()}"

    started = time.perf_counter()
    audio = bytearray()
    previous_request_ids: List[str] = []

    try:
        for index, chunk in enumerate(chunks):
            payload = build_payload(chunk[0]["text"], chunk[0]["voice_id"])
            if len(chunks) > 1:
                if previous_request_ids:
                    payload["previous_request_ids"] = previous_request_ids[-3:]
                if index > 0:
                    previous_text = " ".join(
                        item["text"] for item in chunks[index - 1]
                    )[-100:]
                    payload["previous_text"] = previous_text
            response = _post(url, headers, payload)
            try:
                request_id = (
                    response.headers.get("request-id")
                    or response.headers.get("x-request-id")
                    or ""
                )
                if request_id:
                    previous_request_ids.append(request_id)
                for block in response.iter_content(CHUNK_SIZE):
                    if block:
                        audio.extend(block)
            finally:
                response.close()
    except VoiceError as exc:
        logger.warning(
            "voice.speak.error",
            extra={
                "event": "voice.speak.error",
                "error_code": exc.code,
                "error_type": type(exc).__name__,
                "text_chars": len(cleaned),
                "chunks": len(chunks),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "outcome": "error",
            },
        )
        raise
    except Exception as exc:
        logger.exception(
            "voice.speak.error",
            extra={
                "event": "voice.speak.error",
                "error_code": "voice_upstream_error",
                "error_type": type(exc).__name__,
                "text_chars": len(cleaned),
                "chunks": len(chunks),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "outcome": "error",
            },
        )
        raise VoiceError("voice_upstream_error", "Voice generation failed.", 502) from None

    logger.info(
        "voice.speak",
        extra={
            "event": "voice.speak",
            "text_chars": len(cleaned),
            "chunks": len(chunks),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "outcome": "ok",
        },
    )
    return bytes(audio)