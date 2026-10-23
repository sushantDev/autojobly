"""Email and password accounts. Sessions live in the portal, not here."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Optional

from .db import db, utc_now, utc_today

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(check, digest)


def login_oauth(email: str, provider: str = "") -> dict[str, Any]:
    """Create an account that can sign in only through Google or LinkedIn."""
    now = utc_now()
    email_n = email.strip().lower()
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (email, password_hash, subscription_status, oauth_provider, created_at, updated_at)
            VALUES (?, ?, 'inactive', ?, ?, ?)
            """,
            (email_n, f"oauth${secrets.token_hex(16)}", provider or None, now, now),
        )
        user_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row)


def set_oauth_provider(user_id: int, provider: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE users SET oauth_provider = ?, updated_at = ? WHERE id = ?",
            (provider, utc_now(), user_id),
        )


def uses_oauth_password(user: dict[str, Any] | None) -> bool:
    return bool(user and str(user.get("password_hash") or "").startswith("oauth$"))


def create_user(email: str, password: str) -> dict[str, Any]:
    now = utc_now()
    email_n = email.strip().lower()
    with db() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (email, password_hash, subscription_status, created_at, updated_at)
            VALUES (?, ?, 'inactive', ?, ?)
            """,
            (email_n, hash_password(password), now, now),
        )
        user_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row)


def get_user(user_id: int) -> Optional[dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    with db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    return dict(row) if row else None


def authenticate(email: str, password: str) -> Optional[dict[str, Any]]:
    user = get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def set_subscription(
    user_id: int,
    status: str,
    customer_id: str | None = None,
    subscription_id: str | None = None,
) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE users SET
                subscription_status = ?,
                stripe_customer_id = COALESCE(?, stripe_customer_id),
                stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                updated_at = ?
            WHERE id = ?
            """,
            (status, customer_id, subscription_id, utc_now(), user_id),
        )


def user_is_paying(user: dict[str, Any] | None) -> bool:
    if not user or user.get("subscription_status") != "active":
        return False
    until = (user.get("paid_until") or "").strip()
    if not until:
        return True
    return until >= utc_today()
