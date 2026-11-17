"""Tests for structured logging, request correlation, and redaction.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="th_logging_test_")
os.environ.setdefault("TH_DB_PATH", os.path.join(_TMPDIR, "app.db"))
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")
os.environ["LOG_FILE"] = ""
os.environ.setdefault("LOG_LEVEL", "CRITICAL")

from flask import Flask, g  # noqa: E402

import logging_config  # noqa: E402
from app import app as flask_app  # noqa: E402


class FormatterTests(unittest.TestCase):
    def _record(self):
        record = logging.LogRecord(
            "memory", logging.INFO, __file__, 1, "memory.query", (), None
        )
        record.event = "memory.query"
        record.user_id = 42
        record.duration_ms = 1.25
        record.fact_count = 3
        record.reason = "مرحبا"
        record.operation = "fact.query"
        record.sql = "SELECT * FROM memory_facts WHERE user_id = ?"
        return record

    def test_json_formatter_emits_structured_fields(self):
        payload = json.loads(logging_config.JsonFormatter().format(self._record()))
        self.assertEqual(payload["level"], "INFO")
        self.assertEqual(payload["logger"], "memory")
        self.assertEqual(payload["event"], "memory.query")
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["duration_ms"], 1.25)
        self.assertEqual(payload["fact_count"], 3)
        self.assertEqual(payload["reason"], "مرحبا")
        self.assertEqual(payload["operation"], "fact.query")
        self.assertIn("WHERE user_id = ?", payload["sql"])

    def test_text_formatter_includes_event_and_extras(self):
        line = logging_config.TextFormatter().format(self._record())
        self.assertIn("INFO", line)
        self.assertIn("memory.query", line)
        self.assertIn("user_id=42", line)
        self.assertIn("fact_count=3", line)
        self.assertIn("duration_ms=1.25ms", line)
        self.assertIn("operation=fact.query", line)
        self.assertIn("\n    SQL: SELECT * FROM memory_facts WHERE user_id = ?", line)


class RequestContextTests(unittest.TestCase):
    def test_filter_adds_request_and_user_ids(self):
        test_app = Flask(__name__)
        with test_app.test_request_context("/chat"):
            g.request_id = "req-12345678"
            g.user = {"id": 7}
            record = logging.LogRecord(
                "memory", logging.INFO, __file__, 1, "memory.query", (), None
            )
            logging_config.RequestContextFilter().filter(record)
        self.assertEqual(record.request_id, "req-12345678")
        self.assertEqual(record.user_id, 7)

    def test_request_logs_carry_a_request_id_header(self):
        client = flask_app.test_client()
        with self.assertLogs("app", level="INFO") as captured:
            response = client.get("/login")

        request_id = response.headers.get("X-Request-ID")
        self.assertTrue(request_id)
        self.assertGreaterEqual(len(request_id), 8)
        events = [getattr(record, "event", None) for record in captured.records]
        self.assertIn("http.request", events)
        self.assertIn("http.response", events)
        self.assertTrue(
            any(getattr(record, "request_id", None) == request_id for record in captured.records)
        )


class ConfigurationTests(unittest.TestCase):
    def _set_env(self, **values):
        old = {key: os.environ.get(key) for key in values}
        os.environ.update(values)

        def restore():
            for key, value in old.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            logging_config.configure_logging(force=True)

        self.addCleanup(restore)

    def test_rotating_file_handler_is_configured_once(self):
        log_path = os.path.join(_TMPDIR, "rotating.log")
        self._set_env(
            LOG_FILE=log_path,
            LOG_LEVEL="INFO",
            LOG_FORMAT="json",
            LOG_MAX_BYTES="4096",
            LOG_BACKUP_COUNT="2",
        )
        logging_config.configure_logging(force=True)

        root = logging.getLogger()
        managed = [h for h in root.handlers if getattr(h, "_th_managed", False)]
        self.assertEqual(len(managed), 2)
        file_handlers = [h for h in managed if isinstance(h, RotatingFileHandler)]
        self.assertEqual(len(file_handlers), 1)
        self.assertEqual(file_handlers[0].maxBytes, 4096)
        self.assertEqual(file_handlers[0].backupCount, 2)

        logging.getLogger("app").info("test.event", extra={"event": "test.event"})
        for handler in managed:
            handler.flush()

        with open(log_path, encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line.strip()]
        self.assertTrue(lines)
        self.assertEqual(json.loads(lines[-1])["event"], "test.event")

        logging_config.configure_logging()
        again = [h for h in root.handlers if getattr(h, "_th_managed", False)]
        self.assertEqual(len(again), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)