"""Tests for Phase 2: user control over memory and consent records.

Offline: temporary SQLite database, no network or API key.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="th_control_test_")
os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["LOG_FILE"] = ""

import auth  # noqa: E402
import memory  # noqa: E402


class ControlTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
        auth.init_db()
        memory.init_db()
        self.conn = auth.connect()
        for table in ("consents", "memory_facts", "relationships", "entities", "users"):
            self.conn.execute(f"DELETE FROM {table}")
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

    def seed(self, user_id=None):
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
                "facts": [{"entity": "Sara", "predicate": "age", "value": 20}],
            },
            connection=self.conn,
        )

    # -- consent -----------------------------------------------------------

    def test_consent_defaults_to_not_granted(self):
        for kind in sorted(memory.CONSENT_KINDS):
            with self.subTest(kind=kind):
                self.assertFalse(memory.get_consent(self.user_a, kind, connection=self.conn))

    def test_granting_and_revoking_a_consent(self):
        granted = memory.set_consent(self.user_a, "location", True, connection=self.conn)
        self.assertTrue(granted["granted"])
        self.assertTrue(memory.get_consent(self.user_a, "location", connection=self.conn))

        revoked = memory.set_consent(self.user_a, "location", False, connection=self.conn)
        self.assertFalse(revoked["granted"])
        self.assertFalse(memory.get_consent(self.user_a, "location", connection=self.conn))

    def test_a_revoked_row_is_kept_for_the_record(self):
        memory.set_consent(self.user_a, "voice", True, connection=self.conn)
        memory.set_consent(self.user_a, "voice", False, connection=self.conn)
        row = self.conn.execute(
            "SELECT * FROM consents WHERE user_id = ? AND kind = ?", (self.user_a, "voice")
        ).fetchone()
        self.assertIsNotNone(row["granted_at"])
        self.assertIsNotNone(row["revoked_at"])

    def test_consent_is_scoped_per_user(self):
        memory.set_consent(self.user_a, "location", True, connection=self.conn)
        self.assertFalse(memory.get_consent(self.user_b, "location", connection=self.conn))

    def test_list_consents_reports_every_kind(self):
        memory.set_consent(self.user_a, "voice", True, connection=self.conn)
        listing = memory.list_consents(self.user_a, connection=self.conn)
        self.assertEqual(set(listing), set(memory.CONSENT_KINDS))
        self.assertTrue(listing["voice"]["granted"])
        self.assertFalse(listing["location"]["granted"])

    def test_unknown_consent_kind_is_rejected(self):
        with self.assertRaises(memory.MemoryError):
            memory.set_consent(self.user_a, "telepathy", True, connection=self.conn)

    def test_consent_granted_must_be_boolean(self):
        with self.assertRaises(memory.MemoryError):
            memory.set_consent(self.user_a, "location", "yes", connection=self.conn)

    # -- deletion ----------------------------------------------------------

    def test_deleting_a_fact_removes_only_that_fact(self):
        self.seed()
        fact = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)["facts"][0]
        self.assertTrue(memory.delete_fact(self.user_a, fact["id"], connection=self.conn))
        self.assertEqual(memory.query_memory(self.user_a, connection=self.conn)["facts"], [])

    def test_deleting_an_entity_cascades_to_its_facts(self):
        self.seed()
        entities = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)["entities"]
        self.assertTrue(memory.delete_entity(self.user_a, entities[0]["id"], connection=self.conn))
        remaining = memory.query_memory(self.user_a, connection=self.conn)
        self.assertEqual([entity["name"] for entity in remaining["entities"]], ["Current User"])
        self.assertEqual(remaining["facts"], [])

    def test_deleting_a_relationship_keeps_both_entities(self):
        self.seed()
        data = memory.query_memory(self.user_a, connection=self.conn)
        relationship = data["relationships"][0]
        self.assertTrue(
            memory.delete_relationship(self.user_a, relationship["id"], connection=self.conn)
        )
        after = memory.query_memory(self.user_a, connection=self.conn)
        self.assertEqual(after["relationships"], [])
        self.assertEqual({entity["name"] for entity in after["entities"]}, {"Current User", "Sara"})

    def test_deleting_a_foreign_row_is_refused(self):
        self.seed()
        fact = memory.query_memory(self.user_a, entity="Sara", connection=self.conn)["facts"][0]
        self.assertFalse(memory.delete_fact(self.user_b, fact["id"], connection=self.conn))
        # the row is still there for its owner
        self.assertEqual(
            len(memory.query_memory(self.user_a, connection=self.conn)["facts"]), 1
        )

    def test_deleting_an_unknown_or_bad_id_is_false(self):
        for value in ("missing-id", "", None, 42):
            with self.subTest(value=value):
                self.assertFalse(memory.delete_fact(self.user_a, value, connection=self.conn))

    def test_delete_all_clears_every_store(self):
        self.seed()
        counts = memory.delete_all_memory(self.user_a, connection=self.conn)
        self.assertGreater(counts["entities"], 0)
        self.assertGreater(counts["facts"], 0)
        self.assertGreater(counts["relationships"], 0)
        remaining = memory.query_memory(self.user_a, connection=self.conn)
        self.assertEqual(remaining, {"entities": [], "relationships": [], "facts": []})

    def test_delete_all_leaves_other_accounts_alone(self):
        self.seed(self.user_a)
        self.seed(self.user_b)
        memory.delete_all_memory(self.user_a, connection=self.conn)
        other = memory.query_memory(self.user_b, connection=self.conn)
        self.assertEqual(len(other["facts"]), 1)

    def test_deleting_a_fact_does_not_touch_consent(self):
        self.seed()
        memory.set_consent(self.user_a, "location", True, connection=self.conn)
        memory.delete_all_memory(self.user_a, connection=self.conn)
        self.assertTrue(memory.get_consent(self.user_a, "location", connection=self.conn))

    # -- export ------------------------------------------------------------

    def test_export_contains_every_store(self):
        self.seed()
        memory.set_consent(self.user_a, "voice", True, connection=self.conn)
        payload = memory.export_memory(self.user_a, connection=self.conn)
        self.assertIn("exported_at", payload)
        self.assertEqual(len(payload["entities"]), 2)
        self.assertEqual(len(payload["facts"]), 1)
        self.assertEqual(len(payload["relationships"]), 1)
        self.assertTrue(payload["consents"]["voice"]["granted"])

    def test_export_includes_expired_facts(self):
        memory.save_memory(
            self.user_a,
            {"facts": [{"entity": "Sara", "predicate": "age", "value": 20}]},
            connection=self.conn,
        )
        memory.save_memory(
            self.user_a,
            {"facts": [{"entity": "Sara", "predicate": "age", "value": 21}]},
            connection=self.conn,
        )
        payload = memory.export_memory(self.user_a, connection=self.conn)
        self.assertEqual(sorted(fact["value"] for fact in payload["facts"]), [20, 21])

    def test_export_is_scoped_to_one_account(self):
        self.seed(self.user_a)
        payload = memory.export_memory(self.user_b, connection=self.conn)
        self.assertEqual(payload["entities"], [])
        self.assertEqual(payload["facts"], [])

    def test_export_includes_dates(self):
        self.seed()
        payload = memory.export_memory(self.user_a, connection=self.conn)
        for entity in payload["entities"]:
            self.assertIn("created_at", entity)
        fact = payload["facts"][0]
        for field in ("created_at", "observed_at", "valid_until"):
            self.assertIn(field, fact)

    def test_export_after_delete_is_empty(self):
        self.seed()
        memory.delete_all_memory(self.user_a, connection=self.conn)
        payload = memory.export_memory(self.user_a, connection=self.conn)
        self.assertEqual(payload["entities"], [])
        self.assertEqual(payload["facts"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
