"""Authentication: user storage, password hashing, JWTs, and request middleware.

Design
------
* Users live in a small SQLite database (``instance/app.db`` by default, override
  with ``TH_DB_PATH``). Passwords are hashed with Werkzeug's scrypt.
* Tokens are HS256 JWTs carrying ``sub``, ``id``, ``email``, ``full_name``,
  ``iat`` and ``exp``.
* The token travels in the ``Authorization: Bearer <token>`` header. A single
  ``before_request`` middleware (registered by :func:`init_app`) enforces it for
  every route that is not explicitly public.

Note on page routes: a browser *navigation* cannot send an ``Authorization``
header, so the ``/chat`` HTML shell is public and holds no data. Its first action
is ``GET /api/auth/me`` with the bearer token; a 401 bounces the user to
``/login``. Everything that carries data or changes state goes through the API,
and the API is fully guarded by the middleware.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from flask import Flask, g, jsonify, redirect, request
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
DEFAULT_DB_PATH = os.path.join("instance", "app.db")

#: Routes reachable without a token. ``/chat`` is the public HTML shell described
#: in the module docstring; the data endpoints it calls are still guarded.
PUBLIC_PATHS = frozenset(
    {
        "/",
        "/login",
        "/register",
        "/chat",
        "/call",
        "/settings",
        "/favicon.ico",
        "/api/auth/register",
        "/api/auth/login",
    }
)
PUBLIC_PREFIXES = ("/static/",)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MAX_NAME = 60
MAX_EMAIL = 254
MIN_PASSWORD = 8
MAX_PASSWORD = 200

_secret_fallback: Optional[str] = None


# --- errors ---------------------------------------------------------------


class AuthError(Exception):
    """An authentication/registration failure with an HTTP status and code."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> Dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


# --- database -------------------------------------------------------------


def db_path() -> str:
    return os.environ.get("TH_DB_PATH") or DEFAULT_DB_PATH


def connect() -> sqlite3.Connection:
    path = db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_db() -> sqlite3.Connection:
    """Per-request connection, closed by the app teardown handler."""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(_exc: Optional[BaseException] = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db() -> None:
    """Create the schema if it does not exist yet (idempotent)."""
    connection = connect()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                first_name    TEXT NOT NULL,
                last_name     TEXT NOT NULL,
                email         TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
            """
        )
        # emails are stored lowercased, so this makes uniqueness case-insensitive
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        connection.commit()
    finally:
        connection.close()


def _row_to_user(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "email": row["email"],
        "full_name": f"{row['first_name']} {row['last_name']}".strip(),
        "created_at": row["created_at"],
    }


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """The user shape returned to clients: never includes the password hash."""
    return {
        "id": user["id"],
        "first_name": user["first_name"],
        "last_name": user["last_name"],
        "email": user["email"],
        "full_name": user["full_name"],
    }


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    connection = get_db()
    row = connection.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_id(user_id: Any) -> Optional[Dict[str, Any]]:
    try:
        key = int(user_id)
    except (TypeError, ValueError):
        return None
    connection = get_db()
    row = connection.execute("SELECT * FROM users WHERE id = ?", (key,)).fetchone()
    return _row_to_user(row) if row else None


# --- validation -----------------------------------------------------------


def clean_name(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthError("invalid_request", f"{field.replace('_', ' ').title()} is required.")
    cleaned = " ".join(value.split())
    if len(cleaned) > MAX_NAME:
        raise AuthError("invalid_request", f"{field.replace('_', ' ').title()} is too long (max {MAX_NAME}).")
    return cleaned


def clean_email(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthError("invalid_request", "Email is required.")
    email = value.strip().lower()
    if len(email) > MAX_EMAIL or not EMAIL_RE.match(email):
        raise AuthError("invalid_request", "Enter a valid email address.")
    return email


def clean_password(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise AuthError("invalid_request", "Password is required.")
    if len(value) < MIN_PASSWORD:
        raise AuthError("invalid_request", f"Password must be at least {MIN_PASSWORD} characters.")
    if len(value) > MAX_PASSWORD:
        raise AuthError("invalid_request", f"Password is too long (max {MAX_PASSWORD} characters).")
    return value


# --- registration / login -------------------------------------------------


def register_user(
    first_name: Any,
    last_name: Any,
    email: Any,
    password: Any,
    confirm_password: Any = None,
) -> Dict[str, Any]:
    """Validate and create an account, returning the stored user."""
    first = clean_name(first_name, "first_name")
    last = clean_name(last_name, "last_name")
    address = clean_email(email)
    secret = clean_password(password)

    if confirm_password is not None and confirm_password != password:
        raise AuthError("password_mismatch", "The two passwords do not match.")

    if get_user_by_email(address) is not None:
        raise AuthError("email_taken", "That email is already registered.", 409)

    connection = get_db()
    try:
        cursor = connection.execute(
            "INSERT INTO users (first_name, last_name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                first,
                last,
                address,
                generate_password_hash(secret),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:  # race: someone registered the same email
        connection.rollback()
        raise AuthError("email_taken", "That email is already registered.", 409) from exc

    user = get_user_by_id(cursor.lastrowid)
    assert user is not None
    return user


def authenticate(email: Any, password: Any) -> Dict[str, Any]:
    """Return the user for valid credentials, else raise a generic 401."""
    invalid = AuthError("invalid_credentials", "Incorrect email or password.", 401)
    if not isinstance(email, str) or not isinstance(password, str):
        raise invalid

    user_row: Optional[Dict[str, Any]] = None
    connection = get_db()
    row = connection.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if row is not None:
        user_row = dict(row)

    # always run a hash comparison so timing does not reveal whether the email exists
    stored = user_row["password_hash"] if user_row else generate_password_hash("not-a-real-password")
    matches = check_password_hash(stored, password)

    if user_row is None or not matches:
        raise invalid
    return _row_to_user_dict(user_row)


# --- account updates ------------------------------------------------------


def _account_row(user_id: Any) -> sqlite3.Row:
    """The full user row (including the hash) for the authenticated id."""
    try:
        key = int(user_id)
    except (TypeError, ValueError):
        raise AuthError("unauthorized", "Authentication required.", 401) from None
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (key,)).fetchone()
    if row is None:
        raise AuthError("unauthorized", "Account no longer exists.", 401)
    return row


def _password_matches(row: sqlite3.Row, password: Any) -> bool:
    if not isinstance(password, str) or not password:
        return False
    return check_password_hash(row["password_hash"], password)


def update_profile(
    user_id: Any,
    first_name: Any,
    last_name: Any,
    email: Any,
    current_password: Any = None,
) -> Dict[str, Any]:
    """Update the names and email of one account.

    Changing the email address is sensitive: it requires the account's current
    password, so a stolen token cannot lock the owner out.
    """
    first = clean_name(first_name, "first_name")
    last = clean_name(last_name, "last_name")
    address = clean_email(email)

    row = _account_row(user_id)
    if address != row["email"]:
        if not _password_matches(row, current_password):
            raise AuthError(
                "invalid_credentials", "Your current password is incorrect.", 401
            )
        existing = get_user_by_email(address)
        if existing is not None and existing["id"] != row["id"]:
            raise AuthError("email_taken", "That email is already registered.", 409)

    connection = get_db()
    try:
        connection.execute(
            "UPDATE users SET first_name = ?, last_name = ?, email = ? WHERE id = ?",
            (first, last, address, row["id"]),
        )
        connection.commit()
    except sqlite3.IntegrityError as exc:  # race: someone claimed the email first
        connection.rollback()
        raise AuthError("email_taken", "That email is already registered.", 409) from exc

    user = get_user_by_id(row["id"])
    assert user is not None
    return user


def change_password(
    user_id: Any,
    current_password: Any,
    new_password: Any,
    confirm_password: Any = None,
) -> None:
    """Replace the password after proving the current one."""
    secret = clean_password(new_password)
    if confirm_password is not None and confirm_password != new_password:
        raise AuthError("password_mismatch", "The two passwords do not match.")

    row = _account_row(user_id)
    if not _password_matches(row, current_password):
        raise AuthError("invalid_credentials", "Your current password is incorrect.", 401)

    connection = get_db()
    connection.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(secret), row["id"]),
    )
    connection.commit()


def _row_to_user_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "email": row["email"],
        "full_name": f"{row['first_name']} {row['last_name']}".strip(),
        "created_at": row["created_at"],
    }


# --- tokens ---------------------------------------------------------------


def token_lifetime() -> timedelta:
    try:
        days = int(os.environ.get("JWT_TTL_DAYS", "7"))
    except ValueError:
        days = 7
    return timedelta(days=max(1, days))


def secret_key() -> str:
    """Signing key from ``JWT_SECRET``; a random per-boot key if unset (dev only)."""
    global _secret_fallback
    configured = os.environ.get("JWT_SECRET")
    if configured:
        return configured
    if _secret_fallback is None:
        _secret_fallback = secrets.token_urlsafe(48)
        logger.warning(
            "JWT_SECRET is not set: using a random key for this process. "
            "Tokens will stop working when the server restarts."
        )
    return _secret_fallback


def make_token(user: Dict[str, Any]) -> "tuple[str, datetime]":
    """Sign a JWT for ``user``; returns ``(token, expires_at)``."""
    now = datetime.now(timezone.utc)
    expires_at = now + token_lifetime()
    payload = {
        "sub": str(user["id"]),
        "id": user["id"],
        "email": user["email"],
        "full_name": user["full_name"],
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, secret_key(), algorithm=ALGORITHM), expires_at


def decode_token(token: str) -> Dict[str, Any]:
    """Verify a token and return its claims, raising :class:`AuthError` when invalid."""
    try:
        return jwt.decode(token, secret_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token_expired", "Your session has expired. Please log in again.", 401) from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("unauthorized", "Invalid authentication token.", 401) from exc


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def auth_payload(user: Dict[str, Any]) -> Dict[str, Any]:
    """The body returned by register/login: token, expiry and the public user."""
    token, expires_at = make_token(user)
    return {"token": token, "expires_at": iso(expires_at), "user": public_user(user)}


# --- middleware -----------------------------------------------------------


def bearer_token() -> Optional[str]:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def current_user() -> Optional[Dict[str, Any]]:
    """Claims of the authenticated caller, set by the middleware."""
    return getattr(g, "user", None)


def _reject(code: str, message: str):
    if request.path.startswith("/api/"):
        return jsonify({"error": {"code": code, "message": message}}), 401
    return redirect("/login")


def init_app(app: Flask) -> None:
    """Register the teardown handler and the authentication middleware."""
    app.teardown_appcontext(close_db)

    @app.before_request
    def authenticate():  # pragma: no cover - exercised through the routes
        if request.method == "OPTIONS" or is_public(request.path):
            return None
        token = bearer_token()
        if not token:
            return _reject("unauthorized", "Authentication required.")
        try:
            g.user = decode_token(token)
        except AuthError as exc:
            return _reject(exc.code, exc.message)
        return None

    @app.errorhandler(AuthError)
    def _handle_auth_error(exc: AuthError):
        return jsonify(exc.to_dict()), exc.status
