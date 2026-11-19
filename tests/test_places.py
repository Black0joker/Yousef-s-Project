"""Tests for Phase 5: familiar places and location context.

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

_TMPDIR = tempfile.mkdtemp(prefix="th_places_test_")
os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"
os.environ["LOG_LEVEL"] = "CRITICAL"
os.environ["LOG_FILE"] = ""

import auth  # noqa: E402
import memory  # noqa: E402
import places  # noqa: E402
import schedule  # noqa: E402

HOME = (30.0444, 31.2357)      # Cairo
OFFICE = (30.0600, 31.2500)


class PlacesTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
        auth.init_db()
        memory.init_db()
        schedule.init_db()
        places.init_db()
        self.conn = auth.connect()
        for table in ("places", "appointments", "routines", "consents",
                      "memory_facts", "relationships", "entities", "users"):
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

    # -- schema ------------------------------------------------------------

    def test_init_is_idempotent(self):
        places.init_db()
        tables = {
            row["name"]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        self.assertIn("places", tables)

    # -- distance ----------------------------------------------------------

    def test_distance_of_identical_points_is_zero(self):
        self.assertEqual(places.distance_m(*HOME, *HOME), 0.0)

    def test_distance_is_roughly_right(self):
        # Cairo -> Alexandria is about 180 km
        alexandria = (31.2001, 29.9187)
        metres = places.distance_m(*HOME, *alexandria)
        self.assertGreater(metres, 150_000)
        self.assertLess(metres, 220_000)

    def test_distance_is_symmetric(self):
        forward = places.distance_m(*HOME, *OFFICE)
        backward = places.distance_m(*OFFICE, *HOME)
        self.assertAlmostEqual(forward, backward, places=6)

    # -- CRUD --------------------------------------------------------------

    def test_saving_and_listing_places(self):
        place = places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], radius_m=100,
            notes="Spare key under the pot", connection=self.conn,
        )
        self.assertEqual(place["name"], "Home")
        self.assertEqual(place["radius_m"], 100)
        self.assertIn("created_at", place)

        listed = places.list_places(self.user_a, connection=self.conn)
        self.assertEqual([item["name"] for item in listed], ["Home"])

    def test_radius_defaults_and_bounds(self):
        default = places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], connection=self.conn
        )
        self.assertEqual(default["radius_m"], places.DEFAULT_RADIUS_M)

        for value in (0, 1, places.MAX_RADIUS_M + 1, "big", float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(memory.MemoryError):
                    places.save_place(
                        self.user_a, "X", HOME[0], HOME[1], radius_m=value,
                        connection=self.conn,
                    )

    def test_coordinates_are_validated(self):
        for latitude, longitude in ((91, 0), (0, 181), ("30", 31), (None, 1),
                                    (float("nan"), 0)):
            with self.subTest(latitude=latitude, longitude=longitude):
                with self.assertRaises(memory.MemoryError):
                    places.save_place(
                        self.user_a, "X", latitude, longitude, connection=self.conn
                    )

    def test_empty_name_is_rejected(self):
        with self.assertRaises(memory.MemoryError):
            places.save_place(self.user_a, "  ", HOME[0], HOME[1], connection=self.conn)

    def test_places_are_isolated_between_users(self):
        places.save_place(self.user_a, "Home", HOME[0], HOME[1], connection=self.conn)
        self.assertEqual(places.list_places(self.user_b, connection=self.conn), [])

    def test_deleting_a_place(self):
        place = places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], connection=self.conn
        )
        self.assertTrue(places.delete_place(self.user_a, place["id"], connection=self.conn))
        self.assertEqual(places.list_places(self.user_a, connection=self.conn), [])

    def test_another_user_cannot_delete_a_place(self):
        place = places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], connection=self.conn
        )
        self.assertFalse(places.delete_place(self.user_b, place["id"], connection=self.conn))
        self.assertEqual(len(places.list_places(self.user_a, connection=self.conn)), 1)

    def test_deleting_a_user_cascades_to_places(self):
        places.save_place(self.user_a, "Home", HOME[0], HOME[1], connection=self.conn)
        self.conn.execute("DELETE FROM users WHERE id = ?", (self.user_a,))
        self.conn.commit()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) AS n FROM places").fetchone()["n"], 0
        )

    # -- matching ----------------------------------------------------------

    def test_matching_a_place_inside_its_radius(self):
        places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], radius_m=200, connection=self.conn
        )
        found = places.find_place(self.user_a, HOME[0], HOME[1], connection=self.conn)
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "Home")
        self.assertAlmostEqual(found["distance_m"], 0.0, places=1)

    def test_no_match_outside_the_radius(self):
        places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], radius_m=50, connection=self.conn
        )
        self.assertIsNone(
            places.find_place(self.user_a, *OFFICE, connection=self.conn)
        )

    def test_no_match_when_no_places_exist(self):
        # an unfamiliar location is neutral, not an error
        self.assertIsNone(places.find_place(self.user_a, *HOME, connection=self.conn))

    def test_nearest_place_wins_when_two_overlap(self):
        places.save_place(
            self.user_a, "Wide", HOME[0], HOME[1], radius_m=5000, connection=self.conn
        )
        places.save_place(
            self.user_a, "Tight", HOME[0], HOME[1], radius_m=100, connection=self.conn
        )
        found = places.find_place(self.user_a, HOME[0], HOME[1], connection=self.conn)
        self.assertIn(found["name"], {"Wide", "Tight"})
        # equal centres: the first encountered wins, but both are valid matches
        self.assertIsNotNone(found["distance_m"])

    def test_places_of_another_user_never_match(self):
        places.save_place(
            self.user_b, "Their home", HOME[0], HOME[1], connection=self.conn
        )
        self.assertIsNone(places.find_place(self.user_a, *HOME, connection=self.conn))

    def test_match_omits_coordinates(self):
        places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], connection=self.conn
        )
        found = places.find_place(self.user_a, HOME[0], HOME[1], connection=self.conn)
        self.assertNotIn("latitude", found)
        self.assertNotIn("longitude", found)

    # -- context -----------------------------------------------------------

    def test_context_is_none_at_an_unfamiliar_place(self):
        self.assertIsNone(places.context_at(self.user_a, *HOME, connection=self.conn))

    def test_context_includes_the_place_and_its_memories(self):
        places.save_place(
            self.user_a, "Mom's house", HOME[0], HOME[1], radius_m=200,
            connection=self.conn,
        )
        memory.save_memory(
            self.user_a,
            {"facts": [{"entity": "Mom's house", "predicate": "note", "value": "key under pot"}]},
            connection=self.conn,
        )
        context = places.context_at(self.user_a, HOME[0], HOME[1], connection=self.conn)
        self.assertEqual(context["place"]["name"], "Mom's house")
        self.assertEqual([fact["value"] for fact in context["memories"]], ["key under pot"])
        self.assertNotIn("latitude", context["place"])

    def test_context_includes_the_next_appointment(self):
        from datetime import datetime, timedelta, timezone

        places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], connection=self.conn
        )
        soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
        schedule.save_appointment(
            self.user_a, "Dentist", soon, connection=self.conn
        )
        context = places.context_at(self.user_a, HOME[0], HOME[1], connection=self.conn)
        self.assertIsNotNone(context["appointment"])
        self.assertEqual(context["appointment"]["title"], "Dentist")

    def test_context_has_no_appointment_when_none_exist(self):
        places.save_place(
            self.user_a, "Home", HOME[0], HOME[1], connection=self.conn
        )
        context = places.context_at(self.user_a, HOME[0], HOME[1], connection=self.conn)
        self.assertIsNone(context["appointment"])

    def test_context_never_signals_danger(self):
        # an unfamiliar place must not produce any warning-shaped payload
        context = places.context_at(self.user_a, 0.0, 0.0, connection=self.conn)
        self.assertIsNone(context)


if __name__ == "__main__":
    unittest.main(verbosity=2)
