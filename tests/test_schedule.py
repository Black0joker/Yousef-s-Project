"""Tests for Phase 4: appointments, tasks, and routines.

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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="th_schedule_test_")
os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["LOG_FILE"] = ""

import auth  # noqa: E402
import memory  # noqa: E402
import schedule  # noqa: E402


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class ScheduleTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
        auth.init_db()
        memory.init_db()
        schedule.init_db()
        self.conn = auth.connect()
        for table in ("appointments", "routines", "consents", "memory_facts",
                      "relationships", "entities", "users"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        self.user_a = self._create_user("A", "One", "a@example.com")
        self.user_b = self._create_user("B", "Two", "b@example.com")
        self.now = datetime.now(timezone.utc)

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

    def later(self, **kwargs):
        return iso(self.now + timedelta(**kwargs))

    # -- schema ------------------------------------------------------------

    def test_init_is_idempotent(self):
        schedule.init_db()
        tables = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertIn("appointments", tables)
        self.assertIn("routines", tables)

    # -- appointments ------------------------------------------------------

    def test_saving_an_appointment(self):
        item = schedule.save_appointment(
            self.user_a,
            "Dentist",
            self.later(days=1),
            place="Clinic",
            notes="Bring the card",
            connection=self.conn,
        )
        self.assertEqual(item["title"], "Dentist")
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["place"], "Clinic")
        self.assertIn("created_at", item)
        self.assertIn("id", item)

    def test_times_are_normalized_to_utc(self):
        # a +02:00 wall-clock time is stored as the matching UTC instant
        item = schedule.save_appointment(
            self.user_a, "Call", "2026-05-04T16:00:00+02:00", connection=self.conn
        )
        self.assertEqual(item["starts_at"], "2026-05-04T14:00:00Z")

    def test_end_before_start_is_rejected(self):
        with self.assertRaises(memory.MemoryError):
            schedule.save_appointment(
                self.user_a, "Bad", self.later(days=1), ends_at=self.later(),
                connection=self.conn,
            )

    def test_missing_or_bad_start_is_rejected(self):
        for value in (None, "", "not-a-date", 123):
            with self.subTest(value=value):
                with self.assertRaises(memory.MemoryError):
                    schedule.save_appointment(
                        self.user_a, "X", value, connection=self.conn
                    )

    def test_empty_title_is_rejected(self):
        with self.assertRaises(memory.MemoryError):
            schedule.save_appointment(
                self.user_a, "   ", self.later(days=1), connection=self.conn
            )

    def test_listing_returns_soonest_first(self):
        for days, title in ((3, "third"), (1, "first"), (2, "second")):
            schedule.save_appointment(
                self.user_a, title, self.later(days=days), connection=self.conn
            )
        titles = [
            item["title"]
            for item in schedule.list_appointments(self.user_a, connection=self.conn)
        ]
        self.assertEqual(titles, ["first", "second", "third"])

    def test_past_appointments_are_hidden_by_default(self):
        schedule.save_appointment(
            self.user_a, "Yesterday", self.later(days=-1), connection=self.conn
        )
        schedule.save_appointment(
            self.user_a, "Tomorrow", self.later(days=1), connection=self.conn
        )
        titles = [
            item["title"]
            for item in schedule.list_appointments(self.user_a, connection=self.conn)
        ]
        self.assertEqual(titles, ["Tomorrow"])

    def test_window_includes_an_event_that_started_earlier(self):
        # a two-day event that began yesterday still overlaps today
        schedule.save_appointment(
            self.user_a,
            "Conference",
            self.later(days=-1),
            ends_at=self.later(days=2),
            connection=self.conn,
        )
        items = schedule.list_appointments(
            self.user_a,
            start=self.later(),
            end=self.later(days=1),
            connection=self.conn,
        )
        self.assertEqual([item["title"] for item in items], ["Conference"])

    def test_completed_appointments_are_hidden_unless_asked(self):
        item = schedule.save_appointment(
            self.user_a, "Done", self.later(days=1), connection=self.conn
        )
        schedule.complete_appointment(self.user_a, item["id"], connection=self.conn)
        self.assertEqual(
            schedule.list_appointments(self.user_a, connection=self.conn), []
        )
        with_done = schedule.list_appointments(
            self.user_a, include_done=True, connection=self.conn
        )
        self.assertEqual([row["status"] for row in with_done], ["done"])

    def test_cancelled_appointments_are_hidden(self):
        item = schedule.save_appointment(
            self.user_a, "Cancel me", self.later(days=1), connection=self.conn
        )
        schedule.set_appointment_status(
            self.user_a, item["id"], "cancelled", connection=self.conn
        )
        self.assertEqual(
            schedule.list_appointments(self.user_a, connection=self.conn), []
        )

    def test_completing_an_unknown_appointment_returns_none(self):
        self.assertIsNone(
            schedule.complete_appointment(self.user_a, "missing", connection=self.conn)
        )

    def test_status_must_be_valid(self):
        item = schedule.save_appointment(
            self.user_a, "X", self.later(days=1), connection=self.conn
        )
        with self.assertRaises(memory.MemoryError):
            schedule.set_appointment_status(
                self.user_a, item["id"], "archived", connection=self.conn
            )

    def test_appointments_are_isolated_between_users(self):
        schedule.save_appointment(
            self.user_a, "Mine", self.later(days=1), connection=self.conn
        )
        self.assertEqual(
            schedule.list_appointments(self.user_b, connection=self.conn), []
        )

    def test_another_user_cannot_complete_or_delete(self):
        item = schedule.save_appointment(
            self.user_a, "Mine", self.later(days=1), connection=self.conn
        )
        self.assertIsNone(
            schedule.complete_appointment(self.user_b, item["id"], connection=self.conn)
        )
        self.assertFalse(
            schedule.delete_appointment(self.user_b, item["id"], connection=self.conn)
        )
        self.assertEqual(
            len(schedule.list_appointments(self.user_a, connection=self.conn)), 1
        )

    def test_deleting_an_appointment(self):
        item = schedule.save_appointment(
            self.user_a, "Gone", self.later(days=1), connection=self.conn
        )
        self.assertTrue(
            schedule.delete_appointment(self.user_a, item["id"], connection=self.conn)
        )
        self.assertEqual(
            schedule.list_appointments(self.user_a, connection=self.conn), []
        )

    def test_limit_is_clamped(self):
        for index in range(3):
            schedule.save_appointment(
                self.user_a, f"item-{index}", self.later(days=index + 1),
                connection=self.conn,
            )
        self.assertEqual(
            len(schedule.list_appointments(self.user_a, limit=1, connection=self.conn)), 1
        )

    # -- today -------------------------------------------------------------

    def test_today_uses_the_client_offset(self):
        # an event 10 minutes from now is "today" in every realistic offset
        schedule.save_appointment(
            self.user_a, "Soon", self.later(minutes=10), connection=self.conn
        )
        result = schedule.today_appointments(self.user_a, 0, connection=self.conn)
        self.assertIn("from", result)
        self.assertIn("to", result)
        self.assertEqual([item["title"] for item in result["appointments"]], ["Soon"])

    def test_today_excludes_tomorrow(self):
        schedule.save_appointment(
            self.user_a, "Tomorrow", self.later(days=1), connection=self.conn
        )
        result = schedule.today_appointments(self.user_a, 0, connection=self.conn)
        self.assertNotIn("Tomorrow", [item["title"] for item in result["appointments"]])

    def test_day_window_is_24_hours(self):
        start, end = schedule.day_window(0)
        start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_at = datetime.fromisoformat(end.replace("Z", "+00:00"))
        self.assertEqual((end_at - start_at), timedelta(days=1))

    def test_day_window_respects_the_offset(self):
        start, end = schedule.day_window(120)
        start_at = datetime.fromisoformat(start.replace("Z", "+00:00"))
        # midnight in UTC+2 is 22:00 UTC the previous day
        self.assertEqual(start_at.hour, 22)

    def test_bad_offset_falls_back_to_utc(self):
        self.assertEqual(schedule.day_window("nope"), schedule.day_window(0))

    # -- routines ----------------------------------------------------------

    def test_saving_and_listing_a_routine(self):
        routine = schedule.save_routine(
            self.user_a, "Morning walk", "daily", "08:00-09:00", connection=self.conn
        )
        self.assertTrue(routine["active"])
        self.assertEqual(routine["window"], "08:00-09:00")
        listed = schedule.list_routines(self.user_a, connection=self.conn)
        self.assertEqual([row["label"] for row in listed], ["Morning walk"])

    def test_routines_require_label_and_cadence(self):
        for label, cadence in (("", "daily"), ("Walk", "")):
            with self.subTest(label=label, cadence=cadence):
                with self.assertRaises(memory.MemoryError):
                    schedule.save_routine(
                        self.user_a, label, cadence, connection=self.conn
                    )

    def test_routines_are_isolated_between_users(self):
        schedule.save_routine(self.user_a, "Walk", "daily", connection=self.conn)
        self.assertEqual(schedule.list_routines(self.user_b, connection=self.conn), [])

    def test_deleting_a_routine(self):
        routine = schedule.save_routine(
            self.user_a, "Walk", "daily", connection=self.conn
        )
        self.assertTrue(
            schedule.delete_routine(self.user_a, routine["id"], connection=self.conn)
        )
        self.assertFalse(
            schedule.delete_routine(self.user_b, routine["id"], connection=self.conn)
        )

    # -- tools -------------------------------------------------------------

    def test_toolset_exposes_the_schedule_tools(self):
        schemas, handlers = schedule.toolset(self.user_a, connection=self.conn)
        names = [schema["function"]["name"] for schema in schemas]
        self.assertEqual(
            names, ["save_appointment", "list_appointments", "complete_appointment"]
        )
        self.assertEqual(set(handlers), set(names))

    def test_tool_handlers_are_bound_to_the_user(self):
        _, handlers = schedule.toolset(self.user_a, connection=self.conn)
        saved = handlers["save_appointment"](
            title="Dentist", starts_at=self.later(days=1)
        )
        self.assertEqual(saved["title"], "Dentist")
        # a model-supplied user id is ignored
        result = handlers["list_appointments"](user_id=self.user_b)
        self.assertEqual(result["count"], 1)
        _, other = schedule.toolset(self.user_b, connection=self.conn)
        self.assertEqual(other["list_appointments"]()["count"], 0)

    def test_complete_tool_raises_for_unknown_id(self):
        _, handlers = schedule.toolset(self.user_a, connection=self.conn)
        with self.assertRaises(memory.MemoryError):
            handlers["complete_appointment"](appointment_id="nope")

    def test_tool_schemas_have_no_user_id_parameter(self):
        for schema in schedule.TOOL_SCHEMAS:
            properties = schema["function"]["parameters"].get("properties", {})
            self.assertNotIn("user_id", properties)

    def test_prompt_brief_covers_every_tool_parameter(self):
        brief = schedule.prompt_brief()
        for schema in schedule.TOOL_SCHEMAS:
            for name in schema["function"]["parameters"].get("properties", {}):
                self.assertIn(name, brief)
        for tool in ("save_appointment", "list_appointments", "complete_appointment"):
            self.assertIn(tool, brief)
        self.assertIn(str(schedule.MAX_LIMIT), brief)

    def test_deleting_a_user_cascades_to_schedule_rows(self):
        schedule.save_appointment(
            self.user_a, "X", self.later(days=1), connection=self.conn
        )
        schedule.save_routine(self.user_a, "Walk", "daily", connection=self.conn)
        self.conn.execute("DELETE FROM users WHERE id = ?", (self.user_a,))
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM appointments").fetchone()["n"], 0
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM routines").fetchone()["n"], 0
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
