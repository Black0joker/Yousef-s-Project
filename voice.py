"""ElevenLabs voice generation for the SAGE call experience.

This ports the request shape used by the ElevenApi project
(https://github.com/Black0joker/ElevenApi): it replays the elevenlabs.io web
request against ``POST /v1/text-to-dialogue/stream`` using a Firebase session
token rather than an official ``xi-api-key``.

The web app authenticates with Firebase, so a long-lived refresh token
(``ELEVENLABS_REFRESH_TOKEN``) can mint a fresh one-hour access token through
``securetoken.googleapis.com``. Refresh tokens may rotate; the new value is
written back to ``.env``. ``ELEVENLABS_TOKEN`` remains as a manual fallback.
Never log the access token, the refresh token, or the text.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
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

#: Firebase public Web API key from the elevenlabs.io web app. It is public by
#: design; override with FIREBASE_API_KEY if the app changes it.
DEFAULT_FIREBASE_API_KEY = "AIzaSyBSsRE_1Os04-bxpd5JTLIniy3UK4OqKys"
FIREBASE_TOKEN_URL = "https://securetoken.googleapis.com/v1/token"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

#: Reuse a cached access token until it actually expires; a request that lands
#: after expiry is retried once with a freshly refreshed token.
REFRESH_SKEW_SECONDS = 0.0
_FIREBASE_TIMEOUT = 30.0

_TOKEN_LOCK = threading.Lock()
_cached_access_token: Optional[str] = None
_cached_access_expiry: float = 0.0

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
    """A manually supplied access token, if any."""
    raw = (
        os.environ.get("ELEVENLABS_TOKEN")
        or os.environ.get("ELEVENLABS_AUTHORIZATION")
        or ""
    ).strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw


def _refresh_token() -> str:
    return os.environ.get("ELEVENLABS_REFRESH_TOKEN", "").strip()


def _firebase_api_key() -> str:
    return os.environ.get("FIREBASE_API_KEY", "").strip() or DEFAULT_FIREBASE_API_KEY


def _firebase_uid() -> str:
    return os.environ.get("ELEVENLABS_UID", "").strip()


def _token_expiry(token: str) -> Optional[float]:
    exp = _jwt_claims(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def _token_expired(token: str) -> bool:
    if not token:
        return True
    expiry = _token_expiry(token)
    return bool(expiry and expiry <= time.time())


def _persist_refresh_token(value: str) -> None:
    """Write a rotated refresh token back to ``.env``; never log the value."""
    if not value:
        return
    os.environ["ELEVENLABS_REFRESH_TOKEN"] = value
    try:
        lines: List[str] = []
        if os.path.isfile(ENV_PATH):
            with open(ENV_PATH, encoding="utf-8-sig") as handle:
                lines = handle.read().splitlines()

        output: List[str] = []
        replaced = False
        for line in lines:
            if line.strip().startswith("ELEVENLABS_REFRESH_TOKEN="):
                output.append(f"ELEVENLABS_REFRESH_TOKEN={value}")
                replaced = True
            else:
                output.append(line)
        if not replaced:
            output.append(f"ELEVENLABS_REFRESH_TOKEN={value}")

        text = "\n".join(output).rstrip("\n") + "\n"
        temp_path = f"{ENV_PATH}.tmp"
        with open(temp_path, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temp_path, ENV_PATH)
    except OSError as exc:
        logger.warning(
            "voice.refresh.persist_failed",
            extra={
                "event": "voice.refresh.persist_failed",
                "error_type": type(exc).__name__,
            },
        )


def _refresh_access_token() -> str:
    """Exchange the Firebase refresh token for a fresh one-hour access token."""
    refresh = _refresh_token()
    if not refresh:
        raise VoiceError(
            "voice_token_missing",
            "Voice is not configured. Set ELEVENLABS_REFRESH_TOKEN or ELEVENLABS_TOKEN.",
            503,
        )

    try:
        response = requests.post(
            FIREBASE_TOKEN_URL,
            params={"key": _firebase_api_key()},
            headers={
                # The Web API key is referrer-restricted to the site, so mirror
                # the browser's origin or Google answers 403.
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://elevenlabs.io",
                "Referer": "https://elevenlabs.io/",
            },
            data={"grant_type": "refresh_token", "refresh_token": refresh},
            timeout=_FIREBASE_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise VoiceError(
            "voice_upstream_error",
            f"Token refresh failed ({type(exc).__name__}).",
            502,
        ) from None

    if response.status_code != 200:
        raise VoiceError(
            "voice_token_expired",
            "The ElevenLabs refresh token was rejected. Sign in again to refresh it.",
            503,
        )

    try:
        body = response.json()
    except ValueError:
        raise VoiceError("voice_upstream_error", "Token refresh returned invalid JSON.", 502)

    token = ""
    if isinstance(body, dict):
        token = body.get("access_token") or body.get("id_token") or ""
    token = token.strip() if isinstance(token, str) else ""
    if not token:
        raise VoiceError("voice_token_expired", "Token refresh returned no access token.", 503)

    rotated = body.get("refresh_token") if isinstance(body, dict) else None
    if isinstance(rotated, str) and rotated.strip() and rotated.strip() != refresh:
        _persist_refresh_token(rotated.strip())

    try:
        seconds = float(body.get("expires_in") or 3600)
    except (TypeError, ValueError):
        seconds = 3600.0

    global _cached_access_token, _cached_access_expiry
    _cached_access_token = token
    _cached_access_expiry = time.time() + max(60.0, seconds)
    return token


def _access_token() -> str:
    """Return a valid access token, refreshing it through Firebase if needed."""
    manual = _token()
    if manual:
        if not _token_expired(manual):
            return manual
        if not _refresh_token():
            raise VoiceError(
                "voice_token_expired",
                "The ElevenLabs access token has expired. Set ELEVENLABS_REFRESH_TOKEN.",
                503,
            )

    with _TOKEN_LOCK:
        if _cached_access_token and _cached_access_expiry > time.time() + REFRESH_SKEW_SECONDS:
            return _cached_access_token

    return _refresh_access_token()


def _forget_cached_token() -> None:
    global _cached_access_token, _cached_access_expiry
    with _TOKEN_LOCK:
        _cached_access_token = None
        _cached_access_expiry = 0.0


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
    """Describe the configured credentials without ever returning a token."""
    refresh = _refresh_token()
    if refresh:
        return {
            "configured": True,
            "available": True,
            "expired": False,
            "expires_at": _iso(_cached_access_expiry) if _cached_access_expiry else None,
            "reason": "refresh",
        }

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
    refresh_configured = bool(_refresh_token())
    if refresh_configured:
        try:
            _access_token()
            state = token_state()
        except VoiceError as exc:
            state = {
                "configured": True,
                "available": False,
                "expired": True,
                "expires_at": state.get("expires_at"),
                "reason": exc.code,
            }

    return {
        "configured": state["configured"],
        "available": state["available"],
        "expired": state["expired"],
        "expires_at": state["expires_at"],
        "reason": state["reason"],
        "refresh_configured": refresh_configured,
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


def _response_detail(response: requests.Response) -> str:
    """Best-effort provider error text, truncated; never a token or the input."""
    try:
        body = response.json()
    except ValueError:
        try:
            body = response.text
        except Exception:
            body = ""
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, dict):
            body = " ".join(str(value) for value in detail.values())
        elif detail is not None:
            body = str(detail)
        else:
            body = " ".join(str(value) for value in body.values())
    text = str(body).strip()
    return text[:300]


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
        if status_code in (401, 403):
            detail = _response_detail(response)
            response.close()
            lowered = detail.lower()
            if "unusual activity" in lowered or "free tier" in lowered:
                raise VoiceError(
                    "voice_account_restricted",
                    "ElevenLabs has restricted free-tier access for this account. "
                    "Upgrade the plan or use a different account.",
                    503,
                )
            raise VoiceError(
                "voice_token_expired",
                "The ElevenLabs web token was rejected or has expired.",
                503,
            )

        response.close()
        if status_code in (429, 500, 502, 503, 504) and attempt < ATTEMPTS:
            time.sleep(2.0 ** attempt)
            continue
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

    voice_id = resolve_voice_id(voice_name, language)
    chunks = chunk_inputs([{"text": cleaned, "voice_id": voice_id}])
    base = _base_url()
    url = f"{base}{ENDPOINT}"
    headers = dict(WEB_HEADERS)
    headers["authorization"] = f"Bearer {_access_token()}"
    refreshed = False

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
            try:
                response = _post(url, headers, payload)
            except VoiceError as exc:
                # The cached access token can expire between refreshes; mint a
                # new one once and retry the same chunk.
                if (
                    exc.code == "voice_token_expired"
                    and _refresh_token()
                    and not refreshed
                ):
                    refreshed = True
                    _forget_cached_token()
                    headers["authorization"] = f"Bearer {_access_token()}"
                    response = _post(url, headers, payload)
                else:
                    raise
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