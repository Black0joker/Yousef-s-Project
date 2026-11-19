"""Tests for registration, login, JWTs and the auth middleware.

Offline: uses a temporary SQLite database, no network or API key.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt as pyjwt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMPDIR = tempfile.mkdtemp(prefix="th_auth_test_")
os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"

import app as app_module  # noqa: E402
import auth  # noqa: E402

VALID = {
    "first_name": "Youssef",
    "last_name": "Smith",
    "email": "youssef@example.com",
    "password": "supersecret",
}


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
        os.environ["JWT_SECRET"] = "test-secret-do-not-use-in-production"
        auth.init_db()
        connection = auth.connect()
        connection.execute("DELETE FROM users")
        connection.commit()
        connection.close()

        app_module.app.config["TESTING"] = True
        self.http = app_module.app.test_client()

    # -- helpers -----------------------------------------------------------

    def register(self, **overrides):
        payload = dict(VALID)
        payload.update(overrides)
        return self.http.post("/api/auth/register", json=payload)

    def login(self, email=VALID["email"], password=VALID["password"]):
        return self.http.post("/api/auth/login", json={"email": email, "password": password})

    @staticmethod
    def bearer(token):
        return {"Authorization": "Bearer " + token}

    def signed_in(self):
        """Register a user and return its bearer header."""
        return self.bearer(self.register().get_json()["token"])


class TestRegister(AuthTestCase):
    def test_returns_201_token_and_public_user(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        body = response.get_json()

        self.assertTrue(body["token"])
        self.assertTrue(body["expires_at"].endswith("Z"))
        self.assertEqual(
            sorted(body["user"]),
            ["email", "first_name", "full_name", "id", "last_name"],
        )
        self.assertEqual(body["user"]["first_name"], "Youssef")
        self.assertEqual(body["user"]["last_name"], "Smith")
        self.assertEqual(body["user"]["full_name"], "Youssef Smith")
        self.assertEqual(body["user"]["email"], "youssef@example.com")
        self.assertIsInstance(body["user"]["id"], int)

    def test_never_returns_the_password_hash(self):
        body = self.register().get_json()
        self.assertNotIn("password_hash", body["user"])
        self.assertNotIn("password_hash", str(body))
        self.assertNotIn("supersecret", str(body))

    def test_email_is_lowercased(self):
        body = self.register(email="YOUSSEF@Example.COM").get_json()
        self.assertEqual(body["user"]["email"], "youssef@example.com")

    def test_duplicate_email_is_409(self):
        self.register()
        response = self.register()
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "email_taken")

    def test_duplicate_email_is_case_insensitive(self):
        self.register()
        response = self.register(email="YOUSSEF@EXAMPLE.COM")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "email_taken")

    def test_password_is_hashed_in_the_database(self):
        self.register()
        connection = auth.connect()
        row = connection.execute("SELECT password_hash FROM users").fetchone()
        connection.close()
        self.assertNotEqual(row["password_hash"], "supersecret")
        self.assertNotIn("supersecret", row["password_hash"])

    def test_validation_errors(self):
        cases = [
            ({"first_name": ""}, "invalid_request"),
            ({"first_name": None}, "invalid_request"),
            ({"last_name": "   "}, "invalid_request"),
            ({"email": "not-an-email"}, "invalid_request"),
            ({"email": ""}, "invalid_request"),
            ({"password": "short"}, "invalid_request"),
            ({"password": None}, "invalid_request"),
            ({"first_name": "x" * 61}, "invalid_request"),
        ]
        for overrides, code in cases:
            with self.subTest(overrides=overrides):
                response = self.register(**overrides)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["error"]["code"], code)

    def test_missing_fields_return_400(self):
        response = self.http.post("/api/auth/register", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_request")

    def test_password_confirmation_mismatch(self):
        response = self.register(confirm_password="something-else")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "password_mismatch")

    def test_password_confirmation_match_is_accepted(self):
        response = self.register(confirm_password=VALID["password"])
        self.assertEqual(response.status_code, 201)


class TestLogin(AuthTestCase):
    def test_login_with_correct_credentials(self):
        self.register()
        response = self.login()
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["token"])
        self.assertEqual(body["user"]["email"], VALID["email"])

    def test_login_accepts_different_email_casing(self):
        self.register()
        self.assertEqual(self.login(email="YOUSSEF@EXAMPLE.COM").status_code, 200)

    def test_wrong_password_and_unknown_email_look_identical(self):
        self.register()
        wrong = self.login(password="wrong-password")
        unknown = self.login(email="nobody@example.com")

        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.get_json(), unknown.get_json())
        self.assertEqual(wrong.get_json()["error"]["code"], "invalid_credentials")

    def test_login_requires_strings(self):
        response = self.http.post("/api/auth/login", json={"email": 5, "password": 5})
        self.assertEqual(response.status_code, 401)


class TestTokenClaims(AuthTestCase):
    def test_claims_contain_id_email_full_name_and_expiry(self):
        body = self.register().get_json()
        claims = pyjwt.decode(body["token"], os.environ["JWT_SECRET"], algorithms=["HS256"])

        self.assertEqual(claims["id"], body["user"]["id"])
        self.assertEqual(claims["sub"], str(body["user"]["id"]))
        self.assertEqual(claims["email"], "youssef@example.com")
        self.assertEqual(claims["full_name"], "Youssef Smith")
        self.assertIn("iat", claims)
        self.assertIn("exp", claims)

    def test_token_lifetime_is_seven_days(self):
        body = self.register().get_json()
        claims = pyjwt.decode(body["token"], os.environ["JWT_SECRET"], algorithms=["HS256"])
        self.assertEqual(claims["exp"] - claims["iat"], 7 * 24 * 60 * 60)

    def test_expires_at_matches_the_exp_claim(self):
        body = self.register().get_json()
        claims = pyjwt.decode(body["token"], os.environ["JWT_SECRET"], algorithms=["HS256"])
        self.assertEqual(
            body["expires_at"],
            datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        )

    def _craft(self, secret, *, expired=False):
        now = datetime.now(timezone.utc)
        issued = now - timedelta(days=8) if expired else now
        expires = now - timedelta(days=1) if expired else now + timedelta(days=7)
        return pyjwt.encode(
            {
                "sub": "1",
                "id": 1,
                "email": "crafted@example.com",
                "full_name": "Crafted User",
                "iat": int(issued.timestamp()),
                "exp": int(expires.timestamp()),
            },
            secret,
            algorithm="HS256",
        )

    def test_expired_token_is_rejected(self):
        token = self._craft(os.environ["JWT_SECRET"], expired=True)
        response = self.http.get("/api/auth/me", headers=self.bearer(token))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "token_expired")

    def test_token_signed_with_another_secret_is_rejected(self):
        token = self._craft("a-completely-different-secret-value-32b")
        response = self.http.get("/api/auth/me", headers=self.bearer(token))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "unauthorized")

    def test_garbage_token_is_rejected(self):
        response = self.http.get("/api/auth/me", headers=self.bearer("not.a.jwt"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "unauthorized")


class TestMiddleware(AuthTestCase):
    def test_me_requires_a_token(self):
        response = self.http.get("/api/auth/me")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "unauthorized")

    def test_me_returns_the_current_user(self):
        headers = self.signed_in()
        response = self.http.get("/api/auth/me", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["user"]["full_name"], "Youssef Smith")

    def test_me_returns_401_when_the_account_is_gone(self):
        headers = self.signed_in()
        connection = auth.connect()
        connection.execute("DELETE FROM users")
        connection.commit()
        connection.close()

        response = self.http.get("/api/auth/me", headers=headers)
        self.assertEqual(response.status_code, 401)

    def test_chat_api_requires_a_token(self):
        for path in ("/api/chat", "/api/chat/stream"):
            with self.subTest(path=path):
                response = self.http.post(path, json={"messages": [{"role": "user", "content": "hi"}]})
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.get_json()["error"]["code"], "unauthorized")

    def test_chat_api_rejects_an_expired_token(self):
        response = self.http.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "hi"}]},
            headers=self.bearer(self._expired()),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "token_expired")

    def _expired(self):
        now = datetime.now(timezone.utc)
        return pyjwt.encode(
            {"sub": "1", "id": 1, "email": "a@b.c", "full_name": "A B",
             "iat": int((now - timedelta(days=9)).timestamp()),
             "exp": int((now - timedelta(days=2)).timestamp())},
            os.environ["JWT_SECRET"],
            algorithm="HS256",
        )

    def test_public_paths_need_no_token(self):
        self.assertEqual(self.http.get("/login").status_code, 200)
        self.assertEqual(self.http.get("/register").status_code, 200)
        self.assertEqual(self.http.get("/chat").status_code, 200)
        self.assertEqual(self.http.get("/call").status_code, 200)
        self.assertEqual(self.http.get("/settings").status_code, 200)
        self.assertEqual(self.http.get("/").status_code, 302)

    def test_logout_returns_204(self):
        response = self.http.post("/api/auth/logout", headers=self.signed_in())
        self.assertEqual(response.status_code, 204)


class TestProfileUpdates(AuthTestCase):
    def update_profile(self, token, **payload):
        return self.http.patch("/api/auth/profile", json=payload, headers=self.bearer(token))

    def change_password(self, token, **payload):
        return self.http.post("/api/auth/password", json=payload, headers=self.bearer(token))

    def profile(self, **overrides):
        payload = {"first_name": "Youssef", "last_name": "Smith", "email": VALID["email"]}
        payload.update(overrides)
        return payload

    def test_name_update_reissues_the_token(self):
        token = self.register().get_json()["token"]
        response = self.update_profile(token, **self.profile(first_name="Layla"))
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["user"]["full_name"], "Layla Smith")
        self.assertNotEqual(body["token"], token)
        self.assertEqual(auth.decode_token(body["token"])["full_name"], "Layla Smith")

        me = self.http.get("/api/auth/me", headers=self.bearer(body["token"])).get_json()
        self.assertEqual(me["user"]["first_name"], "Layla")

    def test_email_change_requires_the_current_password(self):
        token = self.register().get_json()["token"]
        response = self.update_profile(token, **self.profile(email="new@example.com"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json()["error"]["code"], "invalid_credentials")
        self.assertEqual(self.login("new@example.com").status_code, 401)
        self.assertEqual(self.login(VALID["email"]).status_code, 200)

    def test_email_change_with_the_current_password(self):
        token = self.register().get_json()["token"]
        response = self.update_profile(
            token,
            **self.profile(email="New@Example.com", current_password=VALID["password"])
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["user"]["email"], "new@example.com")
        self.assertEqual(auth.decode_token(body["token"])["email"], "new@example.com")
        self.assertEqual(self.login("new@example.com").status_code, 200)
        self.assertEqual(self.login(VALID["email"]).status_code, 401)

    def test_email_taken_by_another_account_is_409(self):
        self.register()
        token = self.register(email="other@example.com").get_json()["token"]
        response = self.update_profile(
            token,
            **self.profile(email=VALID["email"], current_password=VALID["password"])
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "email_taken")

    def test_profile_validation_errors(self):
        token = self.register().get_json()["token"]
        cases = [
            self.profile(first_name=""),
            self.profile(email="not-an-email"),
            self.profile(last_name="x" * (auth.MAX_NAME + 1)),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                response = self.update_profile(token, **payload)
                self.assertEqual(response.status_code, 400)

    def test_password_change_replaces_the_old_password(self):
        token = self.register().get_json()["token"]
        response = self.change_password(
            token,
            current_password=VALID["password"],
            new_password="evenbetter1",
            confirm_password="evenbetter1",
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.login(VALID["email"], VALID["password"]).status_code, 401)
        self.assertEqual(self.login(VALID["email"], "evenbetter1").status_code, 200)

    def test_password_change_requires_the_current_password(self):
        token = self.register().get_json()["token"]
        response = self.change_password(
            token,
            current_password="wrong-password",
            new_password="evenbetter1",
            confirm_password="evenbetter1",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.login(VALID["email"], VALID["password"]).status_code, 200)

    def test_password_change_validation(self):
        token = self.register().get_json()["token"]
        mismatch = self.change_password(
            token,
            current_password=VALID["password"],
            new_password="evenbetter1",
            confirm_password="different1",
        )
        self.assertEqual(mismatch.status_code, 400)
        self.assertEqual(mismatch.get_json()["error"]["code"], "password_mismatch")

        short = self.change_password(
            token,
            current_password=VALID["password"],
            new_password="short",
            confirm_password="short",
        )
        self.assertEqual(short.status_code, 400)

    def test_existing_tokens_stay_valid_after_a_password_change(self):
        token = self.register().get_json()["token"]
        self.change_password(
            token,
            current_password=VALID["password"],
            new_password="evenbetter1",
            confirm_password="evenbetter1",
        )
        # stateless JWTs: sessions elsewhere are not revoked (documented behaviour)
        self.assertEqual(self.http.get("/api/auth/me", headers=self.bearer(token)).status_code, 200)

    def test_profile_and_password_api_require_a_token(self):
        profile = self.http.patch("/api/auth/profile", json=self.profile())
        password = self.http.post(
            "/api/auth/password",
            json={"current_password": "x", "new_password": "y", "confirm_password": "y"},
        )
        self.assertEqual(profile.status_code, 401)
        self.assertEqual(password.status_code, 401)


class TestSchemaMigration(unittest.TestCase):
    """init_db() must upgrade a users table created before the new columns."""

    def setUp(self):
        self.path = os.path.join(_TMPDIR, "legacy.db")
        os.environ["TH_DB_PATH"] = self.path
        if os.path.exists(self.path):
            os.remove(self.path)

    def tearDown(self):
        os.environ["TH_DB_PATH"] = os.path.join(_TMPDIR, "app.db")
        if os.path.exists(self.path):
            os.remove(self.path)

    def columns(self):
        connection = auth.connect()
        try:
            return {
                row["name"]
                for row in connection.execute("PRAGMA table_info(users)").fetchall()
            }
        finally:
            connection.close()

    def test_creates_the_full_schema_on_a_fresh_database(self):
        auth.init_db()
        self.assertTrue(
            {"last_message_at", "last_latitude", "last_longitude", "last_location_at"}
            <= self.columns()
        )

    def test_adds_columns_to_an_existing_legacy_table(self):
        # the original schema, exactly as it shipped
        connection = auth.connect()
        connection.execute(
            """
            CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name    TEXT NOT NULL,
                last_name     TEXT NOT NULL,
                email         TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO users (first_name, last_name, email, password_hash, created_at)"
            " VALUES ('A', 'B', 'a@example.com', 'x', '2026-01-01T00:00:00Z')"
        )
        connection.commit()
        connection.close()
        self.assertNotIn("last_message_at", self.columns())

        auth.init_db()

        self.assertTrue(
            {"last_message_at", "last_latitude", "last_longitude", "last_location_at"}
            <= self.columns()
        )
        # the pre-existing row survives and the new columns are null
        connection = auth.connect()
        try:
            row = connection.execute("SELECT * FROM users").fetchone()
            self.assertEqual(row["email"], "a@example.com")
            self.assertIsNone(row["last_message_at"])
        finally:
            connection.close()

    def test_is_idempotent(self):
        auth.init_db()
        auth.init_db()
        auth.init_db()
        self.assertIn("last_message_at", self.columns())


class TestPages(AuthTestCase):
    def test_login_page_renders(self):
        response = self.http.get("/login")
        self.assertIn(b'data-endpoint="/api/auth/login"', response.data)
        self.assertIn(b'data-field="password"', response.data)

    def test_register_page_renders_with_all_fields(self):
        response = self.http.get("/register")
        self.assertIn(b'data-endpoint="/api/auth/register"', response.data)
        for field in (b"first_name", b"last_name", b"email", b"password", b"confirm_password"):
            self.assertIn(b'data-field="' + field + b'"', response.data)

    def test_settings_page_is_a_data_free_shell(self):
        response = self.http.get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"settings.js", response.data)
        self.assertIn(b"session.js", response.data)
        self.assertIn(b'id="profile-form"', response.data)
        self.assertIn(b'id="password-form"', response.data)
        # the shell carries no account data; /api/auth/me fills it in
        self.assertNotIn(b"@example.com", response.data)

    def test_root_redirects_to_login(self):
        response = self.http.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_auth_pages_load_session_and_auth_scripts(self):
        for path in ("/login", "/register"):
            with self.subTest(path=path):
                body = self.http.get(path).data
                self.assertIn(b"session.js", body)
                self.assertIn(b"auth.js", body)
                self.assertIn(b"theme.js", body)
                self.assertIn(b"data-theme-toggle", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
