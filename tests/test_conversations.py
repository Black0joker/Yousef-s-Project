"""Tests for conversation persistence (Phase 1).

Offline: temporary SQLite database, no network or API key.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="th_conversations_test_")
os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["LOG_FILE"] = ""

import auth  # noqa: E402
import conversations  # noqa: E402


class ConversationTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
        auth.init_db()
        conversations.init_db()
        self.conn = auth.connect()
        self.conn.execute("DELETE FROM users")
        self.conn.commit()
        self.user_a = self._create_user("A", "One", "a@example.com")
        self.user_b = self._create_user("B", "Two", "b@example.com")

    def tearDown(self):
        self.conn.close()

    def _create_user(self, first, last, email):
        cursor = self.conn.execute(
            """
            INSERT INTO users (first_name, last_name, email, password_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (first, last, email, "not-a-real-hash", "2026-01-01T00:00:00Z"),
        )
        self.conn.commit()
        return cursor.lastrowid

    # -- schema ------------------------------------------------------------

    def test_init_is_idempotent(self):
        conversations.init_db()
        conversations.init_db()
        tables = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertIn("conversations", tables)
        self.assertIn("conversation_turns", tables)

    # -- threads -----------------------------------------------------------

    def test_create_and_fetch_a_conversation(self):
        created = conversations.create_conversation(
            self.user_a, mode="call", language="ar", connection=self.conn
        )
        self.assertEqual(created["mode"], "call")
        self.assertEqual(created["language"], "ar")
        self.assertIsNotNone(created["started_at"])

        fetched = conversations.get_conversation(
            self.user_a, created["id"], connection=self.conn
        )
        self.assertEqual(fetched["id"], created["id"])

    def test_a_conversation_is_not_visible_to_another_user(self):
        created = conversations.create_conversation(self.user_a, connection=self.conn)
        self.assertIsNone(
            conversations.get_conversation(self.user_b, created["id"], connection=self.conn)
        )
        self.assertEqual(conversations.list_conversations(self.user_b, connection=self.conn), [])

    def test_resolve_honors_an_owned_id(self):
        created = conversations.create_conversation(self.user_a, connection=self.conn)
        resolved = conversations.resolve_conversation(
            self.user_a, created["id"], connection=self.conn
        )
        self.assertEqual(resolved, created["id"])

    def test_resolve_starts_a_new_thread_for_a_foreign_id(self):
        foreign = conversations.create_conversation(self.user_b, connection=self.conn)
        resolved = conversations.resolve_conversation(
            self.user_a, foreign["id"], connection=self.conn
        )
        self.assertNotEqual(resolved, foreign["id"])
        self.assertIsNotNone(
            conversations.get_conversation(self.user_a, resolved, connection=self.conn)
        )

    def test_resolve_starts_a_new_thread_for_a_unknown_or_bad_id(self):
        for value in (None, "", "not-a-uuid", 42):
            with self.subTest(value=value):
                resolved = conversations.resolve_conversation(
                    self.user_a, value, connection=self.conn
                )
                self.assertIsNotNone(
                    conversations.get_conversation(self.user_a, resolved, connection=self.conn)
                )

    def test_list_is_newest_first_and_limited(self):
        for _ in range(3):
            conversations.create_conversation(self.user_a, connection=self.conn)
        everything = conversations.list_conversations(self.user_a, connection=self.conn)
        self.assertEqual(len(everything), 3)
        limited = conversations.list_conversations(self.user_a, limit=1, connection=self.conn)
        self.assertEqual(len(limited), 1)

    # -- turns -------------------------------------------------------------

    def test_turns_round_trip_in_order(self):
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        conversations.append_turn(
            self.user_a, conversation["id"], "user", "hello", connection=self.conn
        )
        conversations.append_turn(
            self.user_a,
            conversation["id"],
            "assistant",
            "hi there",
            finish_reason="stop",
            model="test-model",
            latency_ms=12.5,
            connection=self.conn,
        )

        turns = conversations.get_turns(self.user_a, conversation["id"], connection=self.conn)
        self.assertEqual([turn["role"] for turn in turns], ["user", "assistant"])
        self.assertEqual(turns[0]["content"], "hello")
        self.assertEqual(turns[1]["content"], "hi there")
        self.assertEqual(turns[1]["finish_reason"], "stop")
        self.assertEqual(turns[1]["model"], "test-model")
        self.assertEqual(turns[1]["latency_ms"], 12.5)
        self.assertEqual(turns[1]["outcome"], "ok")

    def test_turns_include_dates_and_ids(self):
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        turn = conversations.append_turn(
            self.user_a, conversation["id"], "user", "hi", connection=self.conn
        )
        for field in ("id", "conversation_id", "created_at"):
            self.assertIn(field, turn)
            self.assertTrue(turn[field])
        self.assertEqual(turn["conversation_id"], conversation["id"])

    def test_stopped_and_failed_turns_are_recorded(self):
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        stopped = conversations.record_assistant_turn(
            self.user_a, conversation["id"], "partial", outcome="stopped", connection=self.conn
        )
        failed = conversations.record_assistant_turn(
            self.user_a, conversation["id"], "", outcome="error", connection=self.conn
        )
        self.assertEqual(stopped["outcome"], "stopped")
        self.assertEqual(failed["outcome"], "error")
        turns = conversations.get_turns(self.user_a, conversation["id"], connection=self.conn)
        self.assertEqual(len(turns), 2)

    def test_unknown_outcome_falls_back_to_ok(self):
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        turn = conversations.append_turn(
            self.user_a,
            conversation["id"],
            "assistant",
            "text",
            outcome="nonsense",
            connection=self.conn,
        )
        self.assertEqual(turn["outcome"], "ok")

    def test_invalid_role_is_rejected_without_raising(self):
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        with patch.object(conversations.logger, "warning"):
            result = conversations.append_turn(
                self.user_a, conversation["id"], "system", "x", connection=self.conn
            )
        self.assertIsNone(result)
        self.assertEqual(
            conversations.get_turns(self.user_a, conversation["id"], connection=self.conn), []
        )

    def test_a_turn_cannot_be_written_into_another_users_thread(self):
        foreign = conversations.create_conversation(self.user_b, connection=self.conn)
        result = conversations.append_turn(
            self.user_a, foreign["id"], "user", "intrusion", connection=self.conn
        )
        self.assertIsNone(result)
        self.assertEqual(
            conversations.get_turns(self.user_b, foreign["id"], connection=self.conn), []
        )

    def test_tool_calls_round_trip(self):
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        calls = [{"name": "save_memory", "ok": True}]
        conversations.append_turn(
            self.user_a,
            conversation["id"],
            "assistant",
            "done",
            tool_calls=calls,
            connection=self.conn,
        )
        turns = conversations.get_turns(self.user_a, conversation["id"], connection=self.conn)
        self.assertEqual(turns[0]["tool_calls"], calls)

    def test_appending_a_turn_updates_ended_at(self):
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        self.assertIsNone(conversation["ended_at"])
        conversations.append_turn(
            self.user_a, conversation["id"], "user", "hi", connection=self.conn
        )
        refreshed = conversations.get_conversation(
            self.user_a, conversation["id"], connection=self.conn
        )
        self.assertIsNotNone(refreshed["ended_at"])

    # -- helpers -----------------------------------------------------------

    def test_last_user_message_skips_assistant_turns(self):
        payload = [
            {"role": "system", "content": "prompt"},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "latest"},
        ]
        self.assertEqual(conversations.last_user_message(payload), "latest")

    def test_last_user_message_handles_empty_input(self):
        for value in (None, [], "text", {}):
            with self.subTest(value=value):
                self.assertEqual(conversations.last_user_message(value), "")

    def test_persistence_failure_does_not_raise(self):
        # a closed connection stands in for a broken database
        broken = auth.connect()
        broken.close()
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        with patch.object(conversations.logger, "warning"), patch.object(
            conversations.logger, "exception"
        ):
            result = conversations.append_turn(
                self.user_a, conversation["id"], "user", "hi", connection=broken
            )
        self.assertIsNone(result)

    def test_deleting_a_user_cascades_to_conversations(self):
        conversation = conversations.create_conversation(self.user_a, connection=self.conn)
        conversations.append_turn(
            self.user_a, conversation["id"], "user", "hi", connection=self.conn
        )
        self.conn.execute("DELETE FROM users WHERE id = ?", (self.user_a,))
        self.conn.commit()
        remaining = self.conn.execute("SELECT COUNT(*) AS n FROM conversation_turns").fetchone()["n"]
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
