"""Tests for trusted contacts and the 24-hour inactivity warning.

Offline: temporary SQLite database, no network or API key.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="th_trusted_test_")
os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["LOG_FILE"] = ""

import auth  # noqa: E402
import memory  # noqa: E402
import trusted  # noqa: E402


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class TrustedTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
        auth.init_db()
        memory.init_db()
        trusted.init_db()
        self.conn = auth.connect()
        for table in ("trusted_contacts", "consents", "memory_facts",
                      "relationships", "entities", "users"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        self.owner = self._create_user("Owner", "One", "owner@example.com")
        self.viewer = self._create_user("Viewer", "Two", "viewer@example.com")
        self.stranger = self._create_user("Stranger", "Three", "stranger@example.com")

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

    def share(self):
        """Owner grants the sharing consent and accepts a viewer."""
        memory.set_consent(self.owner, "trusted_sharing", True, connection=self.conn)
        contact = trusted.invite(
            self.owner, "viewer@example.com", connection=self.conn
        )
        self.assertTrue(
            trusted.accept(self.viewer, contact["id"], connection=self.conn)
        )
        return contact

    def set_activity(self, user_id, message_at=None, location_at=None):
        self.conn.execute(
            "UPDATE users SET last_message_at = ?, last_location_at = ? WHERE id = ?",
            (
                iso(message_at) if message_at else None,
                iso(location_at) if location_at else None,
                user_id,
            ),
        )
        self.conn.commit()

    # -- schema ------------------------------------------------------------

    def test_init_is_idempotent(self):
        trusted.init_db()
        tables = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertIn("trusted_contacts", tables)

    # -- inviting ----------------------------------------------------------

    def test_invite_starts_pending(self):
        contact = trusted.invite(self.owner, "viewer@example.com", connection=self.conn)
        self.assertEqual(contact["status"], "pending")
        self.assertIsNone(contact["accepted_at"])
        self.assertIn("created_at", contact)

    def test_invite_lowercases_the_email(self):
        contact = trusted.invite(self.owner, "  VIEWER@Example.COM ", connection=self.conn)
        self.assertEqual(contact["email"], "viewer@example.com")

    def test_invite_unknown_email_does_not_reveal_anything(self):
        # no account exists for this address; the call still succeeds
        contact = trusted.invite(self.owner, "nobody@example.com", connection=self.conn)
        self.assertEqual(contact["status"], "pending")

    def test_cannot_invite_your_own_email(self):
        with self.assertRaises(memory.MemoryError):
            trusted.invite(self.owner, "owner@example.com", connection=self.conn)

    def test_duplicate_invite_is_rejected(self):
        trusted.invite(self.owner, "viewer@example.com", connection=self.conn)
        with patch.object(memory.logger, "exception"):
            with self.assertRaises(Exception):
                trusted.invite(self.owner, "viewer@example.com", connection=self.conn)

    def test_invalid_email_is_rejected(self):
        for value in (None, "", "not-an-email", 42):
            with self.subTest(value=value):
                with self.assertRaises(memory.MemoryError):
                    trusted.invite(self.owner, value, connection=self.conn)

    # -- the gates ---------------------------------------------------------

    def test_a_pending_invite_reveals_nothing(self):
        self.set_activity(self.owner, message_at=datetime.now(timezone.utc))
        contact = trusted.invite(self.owner, "viewer@example.com", connection=self.conn)
        self.assertIsNone(trusted.peek(self.viewer, contact["id"], connection=self.conn))

    def test_accepting_reveals_activity(self):
        contact = self.share()
        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertIsNotNone(result)
        self.assertEqual(result["owner"]["id"], self.owner)

    def test_only_the_invited_email_can_accept(self):
        contact = trusted.invite(self.owner, "viewer@example.com", connection=self.conn)
        self.assertFalse(
            trusted.accept(self.stranger, contact["id"], connection=self.conn)
        )
        self.assertIsNone(
            trusted.peek(self.stranger, contact["id"], connection=self.conn)
        )

    def test_only_the_invited_email_can_peek(self):
        contact = self.share()
        self.assertIsNone(trusted.peek(self.stranger, contact["id"], connection=self.conn))

    def test_revoked_consent_hides_everything(self):
        contact = self.share()
        memory.set_consent(self.owner, "trusted_sharing", False, connection=self.conn)
        self.assertIsNone(trusted.peek(self.viewer, contact["id"], connection=self.conn))

    def test_owner_revocation_hides_everything(self):
        contact = self.share()
        self.assertTrue(trusted.revoke(self.owner, contact["id"], connection=self.conn))
        self.assertIsNone(trusted.peek(self.viewer, contact["id"], connection=self.conn))

    def test_declining_an_invitation(self):
        contact = trusted.invite(self.owner, "viewer@example.com", connection=self.conn)
        self.assertTrue(
            trusted.decline(self.viewer, contact["id"], connection=self.conn)
        )
        self.assertIsNone(
            trusted.peek(self.viewer, contact["id"], connection=self.conn)
        )

    def test_unknown_contact_id_is_safe(self):
        self.share()
        for value in ("nope", "", None):
            with self.subTest(value=value):
                self.assertIsNone(trusted.peek(self.viewer, value, connection=self.conn))

    # -- permissions -------------------------------------------------------

    def test_location_is_withheld_when_not_granted(self):
        memory.set_consent(self.owner, "trusted_sharing", True, connection=self.conn)
        contact = trusted.invite(
            self.owner, "viewer@example.com", can_see_location=False, connection=self.conn
        )
        trusted.accept(self.viewer, contact["id"], connection=self.conn)
        self.conn.execute(
            "UPDATE users SET last_latitude = 1.0, last_longitude = 2.0,"
            " last_location_at = ? WHERE id = ?",
            (iso(datetime.now(timezone.utc)), self.owner),
        )
        self.conn.commit()

        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertIsNone(result["location"])
        self.assertIsNotNone(result["last_seen"])

    def test_location_is_shared_when_granted(self):
        memory.set_consent(self.owner, "trusted_sharing", True, connection=self.conn)
        contact = trusted.invite(self.owner, "viewer@example.com", connection=self.conn)
        trusted.accept(self.viewer, contact["id"], connection=self.conn)
        self.conn.execute(
            "UPDATE users SET last_latitude = 1.0, last_longitude = 2.0,"
            " last_location_at = ? WHERE id = ?",
            (iso(datetime.now(timezone.utc)), self.owner),
        )
        self.conn.commit()

        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertAlmostEqual(result["location"]["latitude"], 1.0)
        self.assertAlmostEqual(result["location"]["longitude"], 2.0)

    def test_last_seen_is_withheld_when_not_granted(self):
        memory.set_consent(self.owner, "trusted_sharing", True, connection=self.conn)
        contact = trusted.invite(
            self.owner, "viewer@example.com", can_see_last_seen=False, connection=self.conn
        )
        trusted.accept(self.viewer, contact["id"], connection=self.conn)
        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertIsNone(result["last_seen"])

    # -- last seen ---------------------------------------------------------

    def test_last_seen_uses_the_newer_timestamp(self):
        now = datetime.now(timezone.utc)
        self.set_activity(self.owner, message_at=now - timedelta(hours=5), location_at=now)
        contact = self.share()
        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertEqual(result["last_seen"], iso(now))

    def test_last_seen_prefers_the_message_when_it_is_newer(self):
        now = datetime.now(timezone.utc)
        self.set_activity(self.owner, message_at=now, location_at=now - timedelta(hours=5))
        contact = self.share()
        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertEqual(result["last_seen"], iso(now))

    # -- the 24-hour warning -----------------------------------------------

    def test_warning_fires_after_24_hours(self):
        past = datetime.now(timezone.utc) - timedelta(hours=25)
        self.set_activity(self.owner, message_at=past)
        contact = self.share()
        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertTrue(result["warning"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["warning_hours"], 24)

    def test_no_warning_within_24_hours(self):
        recent = datetime.now(timezone.utc) - timedelta(hours=2)
        self.set_activity(self.owner, message_at=recent)
        contact = self.share()
        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertFalse(result["warning"])
        self.assertEqual(result["status"], "active")

    def test_a_never_active_user_is_unknown_not_warning(self):
        # no activity at all must not read as "something is wrong"
        contact = self.share()
        result = trusted.peek(self.viewer, contact["id"], connection=self.conn)
        self.assertFalse(result["warning"])
        self.assertEqual(result["status"], "unknown")
        self.assertIsNone(result["last_seen"])

    # -- listings ----------------------------------------------------------

    def test_invitations_are_listed_for_the_invited_email(self):
        trusted.invite(self.owner, "viewer@example.com", connection=self.conn)
        mine = trusted.invitations(self.viewer, connection=self.conn)
        self.assertEqual(len(mine), 1)
        self.assertEqual(trusted.invitations(self.stranger, connection=self.conn), [])

    def test_watching_lists_owners_who_accepted(self):
        self.share()
        watching = trusted.watching(self.viewer, connection=self.conn)
        self.assertEqual([row["owner_id"] for row in watching], [self.owner])
        self.assertEqual(watching[0]["name"], "Owner One")

    def test_watching_hides_owners_who_revoked_sharing(self):
        contact = self.share()
        trusted.revoke(self.owner, contact["id"], connection=self.conn)
        self.assertEqual(trusted.watching(self.viewer, connection=self.conn), [])

    def test_list_trusted_shows_every_status(self):
        contact = self.share()
        listing = trusted.list_trusted(self.owner, connection=self.conn)
        self.assertEqual([row["status"] for row in listing], ["accepted"])
        self.assertEqual(listing[0]["id"], contact["id"])

    def test_deleting_a_user_cascades_to_trusted_contacts(self):
        self.share()
        self.conn.execute("DELETE FROM users WHERE id = ?", (self.owner,))
        self.conn.commit()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM trusted_contacts"
            ).fetchone()["n"],
            0,
        )

    def test_trusted_sharing_is_not_granted_by_default(self):
        self.assertFalse(
            memory.get_consent(self.owner, "trusted_sharing", connection=self.conn)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
