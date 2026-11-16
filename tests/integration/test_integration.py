"""Integration tests against the real Token Harbor gateway.

These make live API calls, so they are kept out of the default unit run.
They need a key in ``TH_API_KEY`` (or ``TOKENHARBOR_API_KEY`` / a local ``.env``).

Run them with::

    python -m unittest discover -s tests/integration -v

Override the model with ``TH_TEST_MODEL`` (default ``deepseek-v4.1-flash:free``,
which uses the free tier instead of your balance).
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import TokenHarbor, TokenHarborError, tool  # noqa: E402

MODEL = os.environ.get("TH_TEST_MODEL", "deepseek-v4.1-flash:free")

# Keep token spend tiny for every live call.
TINY = dict(max_tokens=32, temperature=0)


def _has_key() -> bool:
    return bool(TokenHarbor._key_from_env())


@unittest.skipUnless(_has_key(), "No Token Harbor key found in TH_API_KEY / .env")
class TestLiveGateway(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TokenHarbor(model=MODEL, timeout=90, max_retries=2, backoff_base=1.0)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    # -- catalogue ---------------------------------------------------------

    def test_01_models_are_listed(self):
        models = self.client.list_models()
        self.assertIsInstance(models, list)
        self.assertGreater(len(models), 0)
        self.assertTrue(all(isinstance(m, dict) and m.get("id") for m in models))
        self.assertIn("id", models[0])

    # -- non-streaming -----------------------------------------------------

    def test_02_chat_returns_text(self):
        text = self.client.chat("Reply with the single word: pong", **TINY)
        self.assertIsInstance(text, str)
        self.assertTrue(text.strip(), "expected a non-empty assistant reply")

    def test_03_chat_raw_has_usage(self):
        data = self.client.chat_raw("Say ok", **TINY)
        self.assertIn("choices", data)
        self.assertTrue(data["choices"])
        self.assertIn("message", data["choices"][0])
        usage = data.get("usage")
        self.assertIsInstance(usage, dict)
        self.assertIn("total_tokens", usage)
        self.assertGreater(usage["total_tokens"], 0)

    # -- streaming ---------------------------------------------------------

    def test_04_stream_yields_deltas(self):
        pieces = list(self.client.stream("Count: 1 2 3", **TINY))
        self.assertGreater(len(pieces), 0)
        joined = "".join(pieces)
        self.assertTrue(joined.strip(), "expected streamed text")
        self.assertTrue(all(isinstance(p, str) for p in pieces))

    def test_05_stream_events_end_with_done(self):
        events = list(self.client.stream_events("Say ok", include_usage=True, **TINY))
        self.assertGreater(len(events), 0)
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(any(e["type"] == "delta" for e in events))
        self.assertIn("finish_reason", events[-1])
        usage = events[-1].get("usage")
        self.assertTrue(usage is None or "total_tokens" in usage)

    # -- history -----------------------------------------------------------

    def test_06_ask_grows_history(self):
        client = TokenHarbor(model=MODEL, timeout=90, max_retries=2, backoff_base=1.0)
        self.addCleanup(client.close)
        first = client.ask("Reply with one word: alpha", **TINY)
        self.assertTrue(first.strip())
        self.assertEqual(len(client.messages), 2)
        self.assertEqual([m["role"] for m in client.messages], ["user", "assistant"])
        second = client.ask("Reply with one word: beta", **TINY)
        self.assertTrue(second.strip())
        self.assertEqual(len(client.messages), 4)
        self.assertEqual(
            [m["role"] for m in client.messages],
            ["user", "assistant", "user", "assistant"],
        )

    def test_07_stream_ask_records_reply(self):
        client = TokenHarbor(model=MODEL, timeout=90, max_retries=2, backoff_base=1.0)
        self.addCleanup(client.close)
        streamed = "".join(client.stream_ask("Say ok", **TINY))
        self.assertTrue(streamed.strip())
        self.assertEqual([m["role"] for m in client.messages], ["user", "assistant"])
        self.assertEqual(client.messages[1]["content"], streamed)

    # -- live error paths --------------------------------------------------

    def test_08_invalid_key_is_401(self):
        bad = TokenHarbor(api_key="thk_live_integration_invalid", base_url=self.client.base_url, max_retries=0)
        self.addCleanup(bad.close)
        with self.assertRaises(TokenHarborError) as ctx:
            bad.chat("hi", **TINY)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertFalse(ctx.exception.retryable)
        self.assertTrue(ctx.exception.code)

    def test_09_unknown_model_is_404(self):
        with self.assertRaises(TokenHarborError) as ctx:
            self.client.chat("hi", model="definitely-not-a-model-xyz", max_tokens=4)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(ctx.exception.code, "model_not_found")
        self.assertFalse(ctx.exception.retryable)

    def test_10_connection_failure_is_wrapped(self):
        # Nothing listens on port 1 -> the client must wrap it, not leak requests errors.
        dead = TokenHarbor(
            api_key="thk_live_x",
            base_url="http://127.0.0.1:1/v1",
            timeout=2,
            max_retries=0,
        )
        self.addCleanup(dead.close)
        with self.assertRaises(TokenHarborError) as ctx:
            dead.chat("hi")
        self.assertEqual(ctx.exception.code, "connection_error")
        self.assertIsNone(ctx.exception.status_code)

    # -- tool calling ------------------------------------------------------

    def test_11_tool_calling_non_streaming(self):
        calls = []

        @tool
        def lookup_order_status(order_id: str) -> dict:
            """Look up the shipping status of an order by its id."""
            calls.append(order_id)
            return {"order_id": order_id, "status": "shipped", "eta": "2026-11-20"}

        client = TokenHarbor(model=MODEL, timeout=90, max_retries=2, backoff_base=1.0)
        self.addCleanup(client.close)
        text = client.run_tools(
            "What is the shipping status of order A-1234? "
            "You must use the lookup_order_status tool.",
            tools=[lookup_order_status],
            max_tokens=200,
            temperature=0,
        )

        self.assertTrue(text.strip())
        self.assertEqual(calls, ["A-1234"])
        self.assertIn("shipped", text.lower())
        roles = [m["role"] for m in client.messages]
        self.assertIn("tool", roles)
        tool_message = next(m for m in client.messages if m["role"] == "tool")
        self.assertIn("shipped", tool_message["content"])
        self.assertEqual(tool_message["tool_call_id"], client.messages[1]["tool_calls"][0]["id"])

    def test_12_tool_calling_streaming(self):
        calls = []

        @tool
        def lookup_order_status(order_id: str) -> dict:
            """Look up the shipping status of an order by its id."""
            calls.append(order_id)
            return {"order_id": order_id, "status": "delivered"}

        client = TokenHarbor(model=MODEL, timeout=90, max_retries=2, backoff_base=1.0)
        self.addCleanup(client.close)
        text = "".join(
            client.run_tools_stream(
                "What is the shipping status of order B-9? "
                "You must use the lookup_order_status tool.",
                tools=[lookup_order_status],
                max_tokens=200,
                temperature=0,
            )
        )

        self.assertTrue(text.strip())
        self.assertEqual(calls, ["B-9"])
        self.assertIn("delivered", text.lower())
        self.assertEqual(
            [m["role"] for m in client.messages],
            ["user", "assistant", "tool", "assistant"],
        )

    def test_13_live_streamed_tool_calls_are_parsed(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ]
        events = list(
            self.client.stream_events(
                "What is the weather in Paris? You must call the get_weather tool.",
                tools=tools,
                max_tokens=150,
                temperature=0,
            )
        )
        done = events[-1]
        self.assertEqual(done["finish_reason"], "tool_calls")
        self.assertEqual(len(done["tool_calls"]), 1)
        call = done["tool_calls"][0]
        self.assertEqual(call.name, "get_weather")
        self.assertIsInstance(call.arguments, dict)
        self.assertIn("city", call.arguments)

    # -- streaming hardening ----------------------------------------------

    def test_14_stream_truncation_reports_length(self):
        events = list(self.client.stream_events("Write a long essay about the ocean.", max_tokens=2, temperature=0))
        self.assertEqual(events[-1]["finish_reason"], "length")

    def test_15_multibyte_text_over_sse(self):
        text = "".join(
            self.client.stream(
                "Repeat this text exactly, with no translation: naïve café 日本語",
                max_tokens=64,
                temperature=0,
            )
        )
        self.assertTrue(text.strip())
        self.assertNotIn("\ufffd", text, "streaming produced replacement characters")


if __name__ == "__main__":
    unittest.main(verbosity=2)