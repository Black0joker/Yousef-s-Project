"""Tests for the ElevenLabs voice client.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import voice  # noqa: E402


def fake_jwt(payload: dict) -> str:
    def encode(value: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii")
        return raw.rstrip("=")

    return f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode(payload)}.signature"


class FakeResponse:
    def __init__(self, status_code=200, chunks=(b"fake-mp3",), headers=None, json_body=None):
        self.status_code = status_code
        self.chunks = list(chunks)
        self.headers = headers or {}
        self.json_body = json_body
        self.closed = False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            yield chunk

    def close(self):
        self.closed = True

    def json(self):
        if self.json_body is None:
            raise ValueError("no JSON body")
        return self.json_body

    def text(self):
        return ""


class VoiceTestCase(unittest.TestCase):
    def setUp(self):
        # Never let the developer's real .env or cached token leak into a test.
        self._env_dir = tempfile.mkdtemp(prefix="th_voice_env_")
        self._env_path = os.path.join(self._env_dir, ".env")
        env_patcher = patch.object(voice, "ENV_PATH", self._env_path)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)

        self._saved_env = {}
        for name in (
            "ELEVENLABS_TOKEN",
            "ELEVENLABS_AUTHORIZATION",
            "ELEVENLABS_REFRESH_TOKEN",
            "ELEVENLABS_UID",
            "FIREBASE_API_KEY",
            "ELEVENLABS_VOICE_ID",
            "ELEVENLABS_VOICE_ID_AR",
            "ELEVENLABS_BASE_URL",
        ):
            self._saved_env[name] = os.environ.get(name)
            os.environ.pop(name, None)

        voice._cached_access_token = None
        voice._cached_access_expiry = 0.0
        voice._token_source = "missing"
        voice._quota_exhausted = False
        self.addCleanup(self._restore_globals)

    def _restore_globals(self):
        for name, value in self._saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        voice._cached_access_token = None
        voice._cached_access_expiry = 0.0
        voice._token_source = "missing"
        voice._quota_exhausted = False

    def set_env(self, **values):
        old = {key: os.environ.get(key) for key in values}
        os.environ.update({key: value for key, value in values.items() if value is not None})
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)

        def restore():
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.addCleanup(restore)


class TokenTests(VoiceTestCase):
    def test_missing_token_status(self):
        self.set_env(ELEVENLABS_TOKEN="")
        state = voice.status()
        self.assertFalse(state["configured"])
        self.assertFalse(state["available"])
        self.assertEqual(state["reason"], "missing")

    def test_expired_token_status_and_error(self):
        expired = fake_jwt({"exp": int(time.time()) - 60})
        self.set_env(ELEVENLABS_TOKEN=expired)
        state = voice.status()
        self.assertTrue(state["configured"])
        self.assertTrue(state["expired"])
        self.assertFalse(state["available"])
        self.assertTrue(state["expires_at"].endswith("Z"))

        with self.assertRaises(voice.VoiceError) as ctx:
            voice.synthesize("Hello.")
        self.assertEqual(ctx.exception.code, "voice_token_expired")
        self.assertEqual(ctx.exception.status, 503)

    def test_valid_token_is_reported_available(self):
        token = fake_jwt({"exp": int(time.time()) + 3600})
        self.set_env(ELEVENLABS_TOKEN=token)
        state = voice.status()
        self.assertTrue(state["available"])
        self.assertFalse(state["expired"])
        self.assertIn("owl", state["voices"])


class RefreshTests(VoiceTestCase):
    def _firebase_body(self, token: str, refresh: str = "refresh-token") -> dict:
        return {
            "access_token": token,
            "id_token": token,
            "expires_in": "3600",
            "token_type": "Bearer",
            "refresh_token": refresh,
            "user_id": "uid-1",
            "project_id": "265222077342",
        }

    def test_status_refreshes_an_expired_access_token(self):
        expired = fake_jwt({"exp": int(time.time()) - 60})
        fresh = fake_jwt({"exp": int(time.time()) + 3600})
        self.set_env(
            ELEVENLABS_TOKEN=expired,
            ELEVENLABS_REFRESH_TOKEN="refresh-1",
            FIREBASE_API_KEY="public-key",
        )
        firebase = FakeResponse(json_body=self._firebase_body(fresh, refresh="refresh-1"))
        with patch.object(voice.requests, "post", return_value=firebase) as post:
            state = voice.status()

        self.assertTrue(state["available"])
        self.assertTrue(state["refresh_configured"])
        self.assertEqual(state["reason"], "refresh")
        self.assertTrue(post.call_args.args[0].startswith("https://securetoken.googleapis.com/v1/token"))
        self.assertEqual(post.call_args.kwargs["data"]["refresh_token"], "refresh-1")
        self.assertEqual(post.call_args.kwargs["params"]["key"], "public-key")

    def test_valid_manual_token_skips_refresh(self):
        manual = fake_jwt({"exp": int(time.time()) + 3600})
        self.set_env(ELEVENLABS_TOKEN=manual, ELEVENLABS_REFRESH_TOKEN="refresh-1")
        with patch.object(voice.requests, "post", side_effect=AssertionError("must not refresh")):
            self.assertEqual(voice._access_token(), manual)

    def test_valid_cached_token_is_reused_until_it_expires(self):
        cached = fake_jwt({"exp": int(time.time()) + 3600})
        voice._cached_access_token = cached
        voice._cached_access_expiry = time.time() + 3600
        self.set_env(ELEVENLABS_REFRESH_TOKEN="refresh-1")
        with patch.object(voice.requests, "post", side_effect=AssertionError("must not refresh")):
            self.assertEqual(voice._access_token(), cached)

    def test_synthesis_uses_the_refreshed_token(self):
        expired = fake_jwt({"exp": int(time.time()) - 60})
        fresh = fake_jwt({"exp": int(time.time()) + 3600})
        self.set_env(
            ELEVENLABS_TOKEN=expired,
            ELEVENLABS_REFRESH_TOKEN="refresh-1",
            ELEVENLABS_BASE_URL="https://example.test",
        )
        firebase = FakeResponse(json_body=self._firebase_body(fresh, refresh="refresh-2"))
        eleven = FakeResponse(chunks=[b"mp3-data"])
        with patch.object(voice.requests, "post", side_effect=[firebase, eleven]) as post:
            audio = voice.synthesize("Hello.")

        self.assertEqual(audio, b"mp3-data")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["authorization"], f"Bearer {fresh}")
        # The rotated refresh token is written back to the (temporary) .env.
        with open(self._env_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("ELEVENLABS_REFRESH_TOKEN=refresh-2", text)

    def test_rejected_refresh_token_is_reported(self):
        self.set_env(ELEVENLABS_REFRESH_TOKEN="bad-refresh")
        firebase = FakeResponse(status_code=400, json_body={"error": {"message": "INVALID_REFRESH_TOKEN"}})
        with patch.object(voice.requests, "post", return_value=firebase) as post:
            with self.assertRaises(voice.VoiceError) as ctx:
                voice.synthesize("Hello.")

        self.assertEqual(ctx.exception.code, "voice_token_expired")
        self.assertEqual(post.call_count, 1)

    def test_status_reports_which_token_is_in_play(self):
        """A manual token wins over a working refresh token; say so out loud."""
        manual = fake_jwt({"exp": int(time.time()) + 3600})
        self.set_env(ELEVENLABS_TOKEN=manual, ELEVENLABS_REFRESH_TOKEN="refresh-1")
        state = voice.status()

        self.assertTrue(state["available"])
        self.assertEqual(state["token_source"], "manual")
        self.assertTrue(state["manual_token"])
        self.assertTrue(state["refresh_configured"])

    def test_status_reports_a_refreshed_token(self):
        fresh = fake_jwt({"exp": int(time.time()) + 3600})
        self.set_env(ELEVENLABS_REFRESH_TOKEN="refresh-1", FIREBASE_API_KEY="public-key")
        firebase = FakeResponse(json_body=self._firebase_body(fresh, refresh="refresh-1"))
        with patch.object(voice.requests, "post", return_value=firebase):
            state = voice.status()

        self.assertEqual(state["token_source"], "refresh")
        self.assertFalse(state["manual_token"])

    def test_a_rejected_manual_token_is_replaced_by_a_refresh(self):
        """Regression: a stale ELEVENLABS_TOKEN must not survive the retry.

        The manual token looks valid locally (so _access_token() keeps returning
        it) while ElevenLabs rejects it. The retry has to mint a token from the
        refresh token instead of asking _access_token() again.
        """
        manual = fake_jwt({"exp": int(time.time()) + 3600})
        fresh = fake_jwt({"exp": int(time.time()) + 3600, "kid": "fresh"})
        self.set_env(
            ELEVENLABS_TOKEN=manual,
            ELEVENLABS_REFRESH_TOKEN="refresh-1",
            FIREBASE_API_KEY="public-key",
            ELEVENLABS_BASE_URL="https://example.test",
        )

        attempts = []
        accepted = FakeResponse(chunks=[b"mp3-data"])

        def fake_post(url, headers, payload):
            attempts.append(headers.get("authorization"))
            if len(attempts) == 1:
                raise voice.VoiceError(
                    "voice_token_expired",
                    "The ElevenLabs web token was rejected or has expired.",
                    503,
                )
            return accepted

        firebase = FakeResponse(json_body=self._firebase_body(fresh, refresh="refresh-1"))
        with patch.object(voice, "_post", side_effect=fake_post), patch.object(
            voice.requests, "post", return_value=firebase
        ) as firebase_call:
            audio = voice.synthesize("Hello.")

        self.assertEqual(audio, b"mp3-data")
        self.assertEqual(firebase_call.call_count, 1)
        self.assertEqual(attempts[0], f"Bearer {manual}")
        self.assertEqual(attempts[1], f"Bearer {fresh}")

    def test_a_rejected_token_logs_a_sanitized_provider_detail(self):
        self.set_env(ELEVENLABS_TOKEN="opaque-token")
        rejected = FakeResponse(
            status_code=401,
            json_body={"detail": {"status": "invalid_token", "message": "user a@b.com was logged out"}},
        )
        with patch.object(voice.requests, "post", return_value=rejected):
            with self.assertLogs("voice", level="WARNING") as captured:
                with self.assertRaises(voice.VoiceError):
                    voice.synthesize("Hello.")

        record = next(
            item
            for item in captured.records
            if getattr(item, "event", None) == "voice.upstream.rejected"
        )
        self.assertEqual(getattr(record, "status", None), 401)
        self.assertEqual(getattr(record, "token_source", None), "manual")
        detail = getattr(record, "detail", "")
        self.assertIn("invalid_token", detail)
        self.assertNotIn("a@b.com", detail)
        self.assertIn("<email>", detail)

    def test_quota_exceeded_is_not_reported_as_an_expired_token(self):
        """ElevenLabs answers 401 when the account is out of credits.

        The token is fine, so blaming it sends people to the wrong problem.
        """
        self.set_env(ELEVENLABS_REFRESH_TOKEN="refresh-1", FIREBASE_API_KEY="public-key")
        fresh = fake_jwt({"exp": int(time.time()) + 3600})
        firebase = FakeResponse(json_body=self._firebase_body(fresh, refresh="refresh-1"))
        rejected = FakeResponse(
            status_code=401,
            json_body={
                "detail": {
                    "type": "invalid_request",
                    "code": "quota_exceeded",
                    "message": "This request exceeds your quota of 10000. "
                    "You have 7 credits remaining, while 16 credits are required.",
                    "status": "quota_exceeded",
                }
            },
        )
        with patch.object(voice.requests, "post", side_effect=[firebase, rejected, rejected, rejected]):
            with self.assertRaises(voice.VoiceError) as ctx:
                voice.synthesize("Hello.")

        self.assertEqual(ctx.exception.code, "voice_quota_exceeded")
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn("credits are used up", ctx.exception.message)

        # and the status endpoint reports the real reason, not "expired"
        state = voice.status()
        self.assertFalse(state["available"])
        self.assertFalse(state["expired"])
        self.assertEqual(state["reason"], "voice_quota_exceeded")

    def test_a_successful_request_clears_the_quota_flag(self):
        voice._quota_exhausted = True
        self.addCleanup(setattr, voice, "_quota_exhausted", False)
        self.set_env(ELEVENLABS_TOKEN=fake_jwt({"exp": int(time.time()) + 3600}))
        with patch.object(voice.requests, "post", return_value=FakeResponse(chunks=[b"mp3"])):
            self.assertEqual(voice.synthesize("Hi."), b"mp3")

        self.assertFalse(voice.status()["reason"] == "voice_quota_exceeded")

    def test_missing_credentials_report_missing(self):
        with self.assertRaises(voice.VoiceError) as ctx:
            voice.synthesize("Hello.")
        self.assertEqual(ctx.exception.code, "voice_token_missing")

    def test_persist_refresh_token_updates_env_file(self):
        with open(self._env_path, "w", encoding="utf-8") as handle:
            handle.write("TH_API_KEY=keep-me\nELEVENLABS_REFRESH_TOKEN=old-token\n")

        voice._persist_refresh_token("new-token")

        with open(self._env_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("TH_API_KEY=keep-me", text)
        self.assertIn("ELEVENLABS_REFRESH_TOKEN=new-token", text)
        self.assertNotIn("old-token", text)


class VoiceSelectionTests(VoiceTestCase):
    def test_language_picks_the_right_default_voice(self):
        self.assertEqual(voice.resolve_voice_id(None, "en"), "r1KmysJdVYZjJCm4mL3b")
        self.assertEqual(voice.resolve_voice_id(None, "ar"), "L10lEremDiJfPicq5CPh")

    def test_arabic_env_override_still_wins(self):
        self.set_env(ELEVENLABS_VOICE_ID_AR="custom-arabic-voice")
        self.assertEqual(voice.resolve_voice_id(None, "ar"), "custom-arabic-voice")


class SynthesisTests(VoiceTestCase):
    def setUp(self):
        super().setUp()
        self.token = fake_jwt({"exp": int(time.time()) + 3600})
        self.set_env(ELEVENLABS_TOKEN=self.token, ELEVENLABS_BASE_URL="https://example.test")

    def test_builds_the_web_request(self):
        response = FakeResponse(headers={"request-id": "req-1"})
        with patch.object(voice.requests, "post", return_value=response) as post:
            audio = voice.synthesize("Hello there.", language="en")

        self.assertEqual(audio, b"fake-mp3")
        args, kwargs = post.call_args
        self.assertTrue(args[0].endswith("/v1/text-to-dialogue/stream"))
        self.assertEqual(kwargs["headers"]["authorization"], f"Bearer {self.token}")
        payload = kwargs["json"]
        self.assertEqual(payload["inputs"][0]["text"], "Hello there.")
        self.assertEqual(payload["inputs"][0]["voice_id"], voice.DEFAULT_VOICES["owl"])
        self.assertEqual(payload["model_id"], voice.MODEL)
        self.assertTrue(payload["collect_preference"])
        self.assertIn("stability", payload["settings"])

    def test_arabic_uses_the_configured_voice(self):
        self.set_env(ELEVENLABS_VOICE_ID_AR="arabic-voice")
        response = FakeResponse()
        with patch.object(voice.requests, "post", return_value=response) as post:
            voice.synthesize("مرحبا.", language="ar")
        self.assertEqual(post.call_args.kwargs["json"]["inputs"][0]["voice_id"], "arabic-voice")

    def test_unknown_voice_is_rejected(self):
        with self.assertRaises(voice.VoiceError) as ctx:
            voice.synthesize("Hello.", voice_name="not-a-voice")
        self.assertEqual(ctx.exception.code, "voice_invalid_request")

    def test_rejected_token_maps_to_expired(self):
        response = FakeResponse(status_code=401)
        with patch.object(voice.requests, "post", return_value=response):
            with self.assertRaises(voice.VoiceError) as ctx:
                voice.synthesize("Hello.")
        self.assertEqual(ctx.exception.code, "voice_token_expired")
        self.assertTrue(response.closed)

    def test_restricted_account_is_reported_distinctly(self):
        response = FakeResponse(
            status_code=401,
            json_body={
                "detail": {
                    "status": "detected_unusual_activity",
                    "message": "Free Tier access has been disabled.",
                }
            },
        )
        with patch.object(voice.requests, "post", return_value=response):
            with self.assertRaises(voice.VoiceError) as ctx:
                voice.synthesize("Hello.")
        self.assertEqual(ctx.exception.code, "voice_account_restricted")

    def test_long_text_is_split_with_continuity(self):
        long_text = "Sentence. " * 260  # > 2000 characters
        responses = [
            FakeResponse(chunks=[b"part-1"], headers={"request-id": "req-1"}),
            FakeResponse(chunks=[b"part-2"], headers={"request-id": "req-2"}),
        ]
        with patch.object(voice.requests, "post", side_effect=responses) as post:
            audio = voice.synthesize(long_text)

        self.assertEqual(audio, b"part-1part-2")
        self.assertEqual(post.call_count, 2)
        second_payload = post.call_args_list[1].kwargs["json"]
        self.assertEqual(second_payload["previous_request_ids"], ["req-1"])
        self.assertIn("previous_text", second_payload)

    def test_text_too_long_is_rejected(self):
        with self.assertRaises(voice.VoiceError) as ctx:
            voice.synthesize("x" * (voice.MAX_TEXT_CHARS + 1))
        self.assertEqual(ctx.exception.code, "voice_text_too_long")

    def test_logs_counts_but_not_text(self):
        response = FakeResponse()
        with patch.object(voice.requests, "post", return_value=response):
            with self.assertLogs("voice", level="INFO") as captured:
                voice.synthesize("TOP_SECRET_TEXT")
        records = captured.records
        self.assertEqual(records[-1].event, "voice.speak")
        self.assertEqual(records[-1].text_chars, len("TOP_SECRET_TEXT"))
        rendered = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in records)
        self.assertNotIn("TOP_SECRET_TEXT", rendered)
        self.assertNotIn(self.token, rendered)


class PromptTests(unittest.TestCase):
    def test_prompt_brief_teaches_delivery_tags(self):
        brief = voice.prompt_brief()
        self.assertIn("[laughs]", brief)
        self.assertIn("[sighs]", brief)
        self.assertIn("SSML", brief)
        self.assertIn("markdown", brief)
        self.assertIn("Never read a tag aloud", brief)


class ChunkingTests(unittest.TestCase):
    def test_split_text_keeps_chunks_under_the_limit(self):
        pieces = voice.split_text("A" * 2500, limit=1000)
        self.assertEqual(len(pieces), 3)
        self.assertTrue(all(len(piece) <= 1000 for piece in pieces))

    def test_chunk_inputs_groups_items(self):
        items = [{"text": "a" * 600, "voice_id": "v"}, {"text": "b" * 600, "voice_id": "v"}]
        chunks = voice.chunk_inputs(items, limit=1000)
        self.assertEqual(len(chunks), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)