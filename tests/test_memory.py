"""Tests for the personal-memory store and its agent tools.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="th_memory_test_")
os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"

import auth  # noqa: E402
import memory  # noqa: E402


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
        auth.init_db()
        memory.init_db()
        self.conn = auth.connect()
        for table in ("memory_facts", "relationships", "entities", "users"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        self.user_a = self._create_user("Youssef", "Smith", "youssef@example.com")
        self.user_b = self._create_user("Mona", "Ali", "mona@example.com")

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

    def _save_example(self, user_id=None):
        return memory.save_memory(
            user_id or self.user_a,
            {
                "entities": [{"name": "Sara", "entity_type": "Person"}],
                "relationships": [
                    {
                        "source_entity": "Current User",
                        "target_entity": "Sara",
                        "relationship_type": "Sister",
                    }
                ],
                "facts": [
                    {
                        "entity": "Sara",
                        "predicate": "age",
                        "value": 20,
                        "confidence": 0.95,
                        "source": "user message",
                    }
                ],
            },
            connection=self.conn,
        )

    def test_schema_creation_is_idempotent(self):
        memory.init_db(connection=self.conn)
        memory.init_db(connection=self.conn)
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
            "('entities', 'relationships', 'memory_facts')"
        ).fetchall()
        self.assertEqual({row["name"] for row in rows},
                         {"entities", "relationships", "memory_facts"})

    def test_saves_entities_relationships_and_facts(self):
        result = self._save_example()

        self.assertEqual(result["saved"]["facts"], 1)
        self.assertEqual(result["entities"][0]["name"], "Sara")
        self.assertEqual(result["facts"][0]["value"], 20)

        found = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)
        self.assertEqual(len(found["facts"]), 1)
        self.assertEqual(found["facts"][0]["predicate"], "age")
        self.assertEqual(found["facts"][0]["value"], 20)
        self.assertEqual(found["facts"][0]["confidence"], 0.95)
        self.assertEqual(len(found["relationships"]), 1)
        self.assertEqual(found["relationships"][0]["source"], "Current User")
        self.assertEqual(found["relationships"][0]["target"], "Sara")
        self.assertEqual(found["relationships"][0]["relationship_type"], "Sister")

    def test_query_results_include_dates(self):
        self._save_example()
        found = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)

        for collection in ("entities", "relationships", "facts"):
            self.assertTrue(found[collection], collection)
            for row in found[collection]:
                self.assertIn("created_at", row)
                parsed = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                self.assertIsNotNone(parsed.tzinfo)

        fact = found["facts"][0]
        self.assertIn("observed_at", fact)
        self.assertIn("valid_until", fact)
        self.assertIsNone(fact["valid_until"])
        self.assertIsNotNone(
            datetime.fromisoformat(fact["observed_at"].replace("Z", "+00:00")).tzinfo
        )

    def test_memory_is_scoped_to_one_user(self):
        self._save_example()

        other = memory.query_memory(self.user_b, connection=self.conn)
        self.assertEqual(other, {"entities": [], "relationships": [], "facts": []})

        other_self = memory.ensure_self_entity(self.user_b, connection=self.conn)
        self.assertEqual(other_self["name"], "Current User")
        self.assertNotEqual(other_self["id"], memory.ensure_self_entity(
            self.user_a, connection=self.conn
        )["id"])

    def test_same_entity_names_are_isolated_between_users(self):
        memory.save_memory(
            self.user_a,
            {"facts": [{"entity": "Sara", "predicate": "age", "value": 20}]},
            connection=self.conn,
        )
        memory.save_memory(
            self.user_b,
            {"facts": [{"entity": "Sara", "predicate": "age", "value": 30}]},
            connection=self.conn,
        )

        a = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)
        b = memory.query_memory(self.user_b, entity="Sara", connection=self.conn)
        self.assertEqual([fact["value"] for fact in a["facts"]], [20])
        self.assertEqual([fact["value"] for fact in b["facts"]], [30])
        self.assertNotEqual(a["entities"][0]["id"], b["entities"][0]["id"])

        searched = memory.query_memory(self.user_a, query="Sara", connection=self.conn)
        self.assertEqual([fact["value"] for fact in searched["facts"]], [20])

    def test_write_payload_cannot_override_the_authenticated_user(self):
        memory.save_memory(
            self.user_a,
            {
                "user_id": self.user_b,
                "facts": [{"entity": "Sara", "predicate": "age", "value": 20}],
            },
            connection=self.conn,
        )
        self.assertEqual(
            memory.query_memory(self.user_b, connection=self.conn)["facts"], []
        )
        self.assertEqual(
            memory.query_memory(self.user_a, connection=self.conn)["facts"][0]["value"], 20
        )

    def test_tool_handlers_cannot_be_retargeted_by_an_argument(self):
        _, handlers = memory.toolset(self.user_a, connection=self.conn)
        with self.assertRaises(TypeError):
            handlers["recall_memory"](user_id=self.user_b)

    def test_current_user_alias_resolves_to_the_self_entity(self):
        memory.save_memory(
            self.user_a,
            {
                "relationships": [
                    {"source_entity": "me", "target_entity": "Omar", "relationship_type": "Friend"}
                ],
                "facts": [{"entity": "I", "predicate": "likes", "value": "tea"}],
            },
            connection=self.conn,
        )

        facts = memory.query_memory(self.user_a, entity="Current User", connection=self.conn)
        self.assertEqual(facts["facts"][0]["predicate"], "likes")
        self.assertEqual(facts["relationships"][0]["source"], "Current User")
        self.assertEqual(facts["relationships"][0]["target"], "Omar")

    def test_fact_reuses_an_existing_entity_of_another_type(self):
        memory.save_memory(
            self.user_a,
            {"entities": [{"name": "Sara", "entity_type": "Pet"}]},
            connection=self.conn,
        )
        memory.save_memory(
            self.user_a,
            {"facts": [{"entity": "Sara", "predicate": "age", "value": 3}]},
            connection=self.conn,
        )
        rows = self.conn.execute(
            "SELECT name, entity_type FROM entities WHERE user_id = ?", (self.user_a,)
        ).fetchall()
        self.assertEqual([(row["name"], row["entity_type"]) for row in rows], [("Sara", "Pet")])
        found = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)
        self.assertEqual(found["facts"][0]["entity_type"], "Pet")

    def test_entity_names_are_case_insensitive(self):
        memory.save_memory(
            self.user_a,
            {
                "entities": [
                    {"name": "Sara", "entity_type": "Person"},
                    {"name": "sara", "entity_type": "person"},
                ]
            },
            connection=self.conn,
        )
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE user_id = ?", (self.user_a,)
        ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_duplicate_relationship_is_not_inserted_twice(self):
        self._save_example()
        self._save_example()
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM relationships WHERE user_id = ?", (self.user_a,)
        ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_replace_invalidates_the_previous_fact(self):
        self._save_example()
        memory.save_memory(
            self.user_a,
            {
                "facts": [
                    {"entity": "Sara", "predicate": "age", "value": 21, "replace": True}
                ]
            },
            connection=self.conn,
        )

        current = memory.query_memory(self.user_a, entity="Sara", predicate="age",
                                       connection=self.conn)
        self.assertEqual([fact["value"] for fact in current["facts"]], [21])

        all_facts = memory.query_memory(
            self.user_a, entity="Sara", predicate="age", include_expired=True,
            connection=self.conn,
        )
        self.assertEqual(sorted(fact["value"] for fact in all_facts["facts"]), [20, 21])
        old = [fact for fact in all_facts["facts"] if fact["value"] == 20][0]
        self.assertIsNotNone(old["valid_until"])

    def test_expired_facts_are_hidden_by_default(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        memory.save_memory(
            self.user_a,
            {
                "facts": [
                    {"entity": "Sara", "predicate": "old", "value": "gone", "valid_until": past},
                    {"entity": "Sara", "predicate": "current", "value": "here", "valid_until": future},
                ]
            },
            connection=self.conn,
        )

        current = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)
        self.assertEqual([fact["predicate"] for fact in current["facts"]], ["current"])
        everything = memory.query_memory(
            self.user_a, entity="Sara", include_expired=True, connection=self.conn
        )
        self.assertEqual(len(everything["facts"]), 2)

    def test_json_values_round_trip(self):
        values = {
            "text": "Cairo",
            "number": 42,
            "flag": True,
            "list": ["tea", "coffee"],
            "object": {"city": "Cairo", "country": "Egypt"},
        }
        memory.save_memory(
            self.user_a,
            {
                "facts": [
                    {"entity": "Sara", "predicate": key, "value": value}
                    for key, value in values.items()
                ]
            },
            connection=self.conn,
        )

        found = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)
        by_predicate = {fact["predicate"]: fact["value"] for fact in found["facts"]}
        self.assertEqual(by_predicate, values)

        raw = self.conn.execute(
            "SELECT value FROM memory_facts WHERE predicate = ?", ("object",)
        ).fetchone()["value"]
        self.assertEqual(json.loads(raw), values["object"])

    def test_query_filters_and_limit(self):
        memory.save_memory(
            self.user_a,
            {
                "entities": [
                    {"name": "Sara", "entity_type": "Person"},
                    {"name": "Omar", "entity_type": "Person"},
                ],
                "facts": [
                    {"entity": "Sara", "predicate": "age", "value": 20},
                    {"entity": "Sara", "predicate": "drink", "value": "tea"},
                    {"entity": "Omar", "predicate": "age", "value": 30},
                ],
            },
            connection=self.conn,
        )

        sara = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)
        self.assertEqual(len(sara["facts"]), 2)
        ages = memory.query_memory(self.user_a, predicate="age", connection=self.conn)
        self.assertEqual({fact["entity"] for fact in ages["facts"]}, {"Sara", "Omar"})
        tea = memory.query_memory(self.user_a, query="tea", connection=self.conn)
        self.assertEqual([fact["predicate"] for fact in tea["facts"]], ["drink"])
        limited = memory.query_memory(self.user_a, limit=1, connection=self.conn)
        self.assertEqual(len(limited["facts"]), 1)

    def test_validation_errors_leave_the_database_unchanged(self):
        cases = [
            {},
            {"facts": []},
            {"entities": "not a list"},
            {"facts": [{"entity": "Sara", "predicate": "age", "value": 20, "confidence": 2}]},
            {"facts": [{"entity": "Sara", "predicate": "age"}]},
            {"facts": [{"entity": "Sara", "predicate": "age", "value": 20,
                        "valid_until": "not-a-date"}]},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(memory.MemoryError):
                    memory.save_memory(self.user_a, payload, connection=self.conn)

        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM memory_facts WHERE user_id = ?", (self.user_a,)
        ).fetchone()["n"]
        self.assertEqual(count, 0)

    def test_memory_logs_are_structured_and_redacted(self):
        with self.assertLogs("memory", level="INFO") as captured:
            memory.save_memory(
                self.user_a,
                {
                    "facts": [
                        {
                            "entity": "TOP_SECRET_ENTITY",
                            "predicate": "SECRET_PREDICATE",
                            "value": "TOP_SECRET_VALUE",
                        }
                    ]
                },
                connection=self.conn,
            )
            memory.query_memory(
                self.user_a,
                entity="TOP_SECRET_ENTITY",
                query="TOP_SECRET_QUERY",
                connection=self.conn,
            )

        records = captured.records
        events = [getattr(record, "event", None) for record in records]
        self.assertIn("memory.save", events)
        self.assertIn("memory.query", events)
        self.assertIn("memory.sql", events)
        self.assertTrue(all(getattr(record, "user_id", None) == self.user_a for record in records))

        sql_records = [record for record in records if record.event == "memory.sql"]
        self.assertTrue(any(record.operation == "fact.query" for record in sql_records))
        self.assertTrue(all("?" in record.sql for record in sql_records))

        rendered = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in records)
        for secret in (
            "TOP_SECRET_ENTITY",
            "SECRET_PREDICATE",
            "TOP_SECRET_VALUE",
            "TOP_SECRET_QUERY",
        ):
            self.assertNotIn(secret, rendered)

    def test_invalid_memory_operations_log_warnings(self):
        with self.assertLogs("memory", level="WARNING") as captured:
            with self.assertRaises(memory.MemoryError):
                memory.save_memory(self.user_a, {}, connection=self.conn)
            with self.assertRaises(memory.MemoryError):
                memory.query_memory(self.user_a, limit=True, connection=self.conn)

        events = [getattr(record, "event", None) for record in captured.records]
        self.assertIn("memory.save.invalid", events)
        self.assertIn("memory.query.invalid", events)
        self.assertTrue(all(getattr(record, "reason", None) for record in captured.records))

    def test_toolset_exposes_both_tools_and_binds_the_user(self):
        schemas, handlers = memory.toolset(self.user_a, connection=self.conn)
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(names, ["save_memory", "recall_memory"])
        self.assertEqual(set(handlers), {"save_memory", "recall_memory"})

        handlers["save_memory"](
            facts=[{"entity": "Sara", "predicate": "age", "value": 20}]
        )
        result = handlers["recall_memory"](entity="Sara")
        self.assertEqual(result["facts"][0]["value"], 20)
        self.assertEqual(memory.query_memory(self.user_b, connection=self.conn)["facts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)