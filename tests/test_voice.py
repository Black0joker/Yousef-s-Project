"""Tests for the ElevenLabs voice client.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import json
import os
import sys
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
    def __init__(self, status_code=200, chunks=(b"fake-mp3",), headers=None):
        self.status_code = status_code
        self.chunks = list(chunks)
        self.headers = headers or {}
        self.closed = False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            yield chunk

    def close(self):
        self.closed = True

    def text(self):
        return ""


class VoiceTestCase(unittest.TestCase):
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


class SynthesisTests(VoiceTestCase):
    def setUp(self):
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