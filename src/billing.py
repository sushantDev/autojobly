"""Billing. Customers pay by a Khalti card link. NPR settles in Nepal. Auto-apply runs while paid_until is current."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .auth import get_user, set_subscription
from .db import add_event, db, utc_now, utc_today

PRICE_LABEL = "$9/month"
PRICE_USD = 9
APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8787")


def owner_email() -> str:
    """Operator access comes only from ADMIN_EMAIL. The address on a CV is not an admin login."""
    return os.environ.get("ADMIN_EMAIL", "").strip().lower()


def is_owner(user: dict[str, Any] | None) -> bool:
    email = str((user or {}).get("email") or "").strip().lower()
    owner = owner_email()
    return bool(email and owner and email == owner)


def latest_payment(user_id: int) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM payments
            WHERE user_id = ? AND status IN ('needs_link', 'link_ready', 'claimed', 'paid')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def request_payment(user_id: int) -> int:
    now = utc_now()
    with db() as conn:
        open_row = conn.execute(
            """
            SELECT id FROM payments
            WHERE user_id = ? AND status IN ('needs_link', 'link_ready', 'claimed')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if open_row:
            return int(open_row["id"])
        cur = conn.execute(
            """
            INSERT INTO payments (user_id, amount_usd, status, created_at, updated_at)
            VALUES (?, ?, 'needs_link', ?, ?)
            """,
            (user_id, PRICE_USD, now, now),
        )
        return int(cur.lastrowid)


def claim_payment(user_id: int, reference: str) -> None:
    ref = reference.strip()[:80]
    if len(ref) < 4:
        raise ValueError("Enter the reference from the Khalti receipt")
    with db() as conn:
        row = conn.execute(
            """
            SELECT id FROM payments
            WHERE user_id = ? AND status IN ('link_ready', 'claimed')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if not row:
            raise ValueError("There is no payment link to confirm yet")
        conn.execute(
            """
            UPDATE payments SET status = 'claimed', reference = ?, updated_at = ?
            WHERE id = ?
            """,
            (ref, utc_now(), row["id"]),
        )


def business_desk() -> dict[str, Any]:
    """Split the operator's day into the only actions they can take."""
    soon = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
    with db() as conn:
        open_rows = conn.execute(
            """
            SELECT p.*, u.email AS account_email, u.paid_until
            FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.status IN ('needs_link', 'link_ready', 'claimed')
            ORDER BY p.id ASC
            """
        ).fetchall()
        expiring = conn.execute(
            """
            SELECT u.id, u.email, u.paid_until
            FROM users u
            WHERE u.subscription_status = 'active'
              AND u.paid_until IS NOT NULL
              AND u.paid_until != ''
              AND u.paid_until <= ?
              AND NOT EXISTS (
                SELECT 1 FROM payments p
                WHERE p.user_id = u.id
                  AND p.status IN ('needs_link', 'link_ready', 'claimed')
              )
            ORDER BY u.paid_until
            """,
            (soon,),
        ).fetchall()
        active = conn.execute(
            """
            SELECT COUNT(*) AS c FROM users
            WHERE subscription_status = 'active'
              AND (paid_until IS NULL OR paid_until = '' OR paid_until >= ?)
            """,
            (utc_today(),),
        ).fetchone()
    rows = [dict(row) for row in open_rows]
    return {
        "needs_link": [row for row in rows if row["status"] == "needs_link"],
        "waiting_payment": [row for row in rows if row["status"] == "link_ready"],
        "claimed": [row for row in rows if row["status"] == "claimed"],
        "expiring": [dict(row) for row in expiring],
        "active_count": int(active["c"] if active else 0),
    }


def list_open_payments() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT p.*, u.email AS account_email
            FROM payments p
            JOIN users u ON u.id = p.user_id
            WHERE p.status IN ('needs_link', 'link_ready', 'claimed')
            ORDER BY p.id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def save_khalti_link(payment_id: int, url: str) -> None:
    clean = url.strip()
    if not clean.startswith("https://"):
        raise ValueError("Paste the https link from Khalti")
    with db() as conn:
        conn.execute(
            """
            UPDATE payments SET khalti_url = ?, status = 'link_ready', updated_at = ?
            WHERE id = ? AND status IN ('needs_link', 'link_ready', 'claimed')
            """,
            (clean, utc_now(), payment_id),
        )


def _until_after(existing: str) -> str:
    """30 days from today, or from a paid_until date that is still in the future."""
    start = datetime.now(timezone.utc)
    if existing:
        current = datetime.strptime(existing, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if current.date() >= start.date():
            start = current
    return (start + timedelta(days=30)).strftime("%Y-%m-%d")


def _grant_access(conn, user_id: int) -> str:
    user = get_user(user_id)
    until = _until_after(((user or {}).get("paid_until") or "").strip())
    conn.execute(
        """
        UPDATE users SET subscription_status = 'active', paid_until = ?, updated_at = ?
        WHERE id = ?
        """,
        (until, utc_now(), user_id),
    )
    return until


def approve_payment(payment_id: int) -> str:
    """Mark the NPR as received and turn auto-apply on for 30 days."""
    with db() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if not row:
            raise ValueError("Payment not found")
        until = _grant_access(conn, int(row["user_id"]))
        conn.execute(
            "UPDATE payments SET status = 'paid', updated_at = ? WHERE id = ?",
            (utc_now(), payment_id),
        )
    return until


def dodo_api_key() -> str:
    return os.environ.get("DODO_PAYMENTS_API_KEY", "").strip()


def dodo_product_id() -> str:
    """The $9 monthly subscription product. The browser never chooses what is sold."""
    return os.environ.get("DODO_PRODUCT_ID", "").strip()


def dodo_configured() -> bool:
    return bool(dodo_api_key() and dodo_product_id())


def _dodo_client():
    from dodopayments import DodoPayments

    # An unset or misspelled mode must never charge real cards.
    environment = "live_mode" if os.environ.get("DODO_PAYMENTS_ENVIRONMENT") == "live_mode" else "test_mode"
    return DodoPayments(bearer_token=dodo_api_key() or "unset", environment=environment)


def create_dodo_checkout(user: dict[str, Any]) -> str:
    """A hosted Dodo checkout for this account. Access starts when Dodo confirms the subscription."""
    if not dodo_configured():
        raise RuntimeError("Dodo is not configured")
    customer_id = (user.get("dodo_customer_id") or "").strip()
    session = _dodo_client().checkout_sessions.create(
        product_cart=[{"product_id": dodo_product_id(), "quantity": 1}],
        customer={"customer_id": customer_id} if customer_id else {"email": user["email"]},
        metadata={"user_id": str(user["id"])},
        return_url=f"{APP_BASE_URL}/billing",
        feature_flags={"allow_discount_code": True},
    )
    if not session.checkout_url:
        raise RuntimeError("Dodo did not return a checkout page")
    return session.checkout_url


def dodo_portal_url(user: dict[str, Any]) -> str:
    """Dodo's own page to change the card, see invoices, or cancel."""
    customer_id = (user.get("dodo_customer_id") or "").strip()
    if not customer_id:
        raise ValueError("This account has no card subscription")
    portal = _dodo_client().customers.customer_portal.create(customer_id, return_url=f"{APP_BASE_URL}/billing")
    return portal.link


def fetch_dodo_payment(payment_id: str) -> dict[str, Any]:
    """Read one payment from Dodo. The return URL alone is not proof it succeeded."""
    if not dodo_api_key():
        raise RuntimeError("Dodo is not configured")
    return _dodo_client().payments.retrieve(payment_id).model_dump(mode="json")


def fetch_dodo_subscription(subscription_id: str) -> dict[str, Any]:
    if not dodo_api_key():
        raise RuntimeError("Dodo is not configured")
    return _dodo_client().subscriptions.retrieve(subscription_id).model_dump(mode="json")


def _billing_day_after(next_billing_date: str) -> str:
    """Access runs to the renewal date plus one day, so a renewal charged a few hours late does not pause applications."""
    day = datetime.strptime(str(next_billing_date)[:10], "%Y-%m-%d")
    return (day + timedelta(days=1)).strftime("%Y-%m-%d")


def _dodo_owner(conn, subscription: dict[str, Any]) -> dict[str, Any] | None:
    """The account a subscription belongs to: the id Autojobly put in the checkout, then the saved subscription, then the email."""
    metadata = subscription.get("metadata") or {}
    customer = subscription.get("customer") or {}
    user_id = str(metadata.get("user_id") or "").strip()
    row = None
    if user_id.isdigit():
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if row is None and subscription.get("subscription_id"):
        row = conn.execute(
            "SELECT * FROM users WHERE dodo_subscription_id = ?", (subscription["subscription_id"],)
        ).fetchone()
    if row is None and customer.get("email"):
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (str(customer["email"]).strip().lower(),)
        ).fetchone()
    return dict(row) if row else None


def apply_dodo_subscription(conn, subscription: dict[str, Any], expected_user_id: int | None = None) -> int | None:
    """Make auto-apply match the subscription state Dodo reports. Returns the account that changed."""
    user = _dodo_owner(conn, subscription)
    if expected_user_id is not None and (user is None or int(user["id"]) != int(expected_user_id)):
        raise ValueError("This payment is for a different account")
    if user is None:
        return None
    status = str(subscription.get("status") or "")
    if status == "pending":
        return None
    sub_id = subscription.get("subscription_id") or None
    customer_id = (subscription.get("customer") or {}).get("customer_id") or None
    existing_until = (user.get("paid_until") or "").strip()
    owned = not user.get("dodo_subscription_id") or user.get("dodo_subscription_id") == sub_id
    if status == "active" or (status == "cancelled" and subscription.get("cancel_at_next_billing_date")):
        until = _billing_day_after(subscription.get("next_billing_date") or utc_today())
        # Days already paid through Khalti are not cut short.
        if existing_until and existing_until > until:
            until = existing_until
        conn.execute(
            """
            UPDATE users SET subscription_status = 'active', paid_until = ?, dodo_customer_id = COALESCE(?, dodo_customer_id),
                dodo_subscription_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (until, customer_id, sub_id, utc_now(), user["id"]),
        )
    elif owned:
        # on_hold, paused, failed, expired, or cancelled now. Days paid another way still count.
        keep = existing_until and existing_until >= utc_today() and user.get("dodo_subscription_id") != sub_id
        conn.execute(
            """
            UPDATE users SET subscription_status = ?, dodo_customer_id = COALESCE(?, dodo_customer_id),
                dodo_subscription_id = ?, updated_at = ?
            WHERE id = ?
            """,
            ("active" if keep else "inactive", customer_id, sub_id, utc_now(), user["id"]),
        )
    return int(user["id"])


def _record_dodo_payment(conn, user_id: int, payment: dict[str, Any]) -> None:
    payment_id = str(payment.get("payment_id") or "")
    if not payment_id:
        return
    if conn.execute("SELECT 1 FROM payments WHERE reference = ? AND status = 'paid'", (payment_id,)).fetchone():
        return
    now = utc_now()
    open_row = conn.execute(
        """
        SELECT id FROM payments
        WHERE user_id = ? AND status IN ('needs_link', 'link_ready', 'claimed')
        ORDER BY id DESC LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if open_row:
        conn.execute(
            """
            UPDATE payments SET status = 'paid', amount_usd = ?, reference = ?, note = 'Dodo card', updated_at = ?
            WHERE id = ?
            """,
            (PRICE_USD, payment_id, now, open_row["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO payments (user_id, amount_usd, status, reference, note, created_at, updated_at)
            VALUES (?, ?, 'paid', ?, 'Dodo card', ?, ?)
            """,
            (user_id, PRICE_USD, payment_id, now, now),
        )


def apply_dodo_webhook(body: bytes, headers: dict[str, str]) -> str:
    """Verify Dodo's signature, then apply the event once. A repeat delivery changes nothing."""
    import json
    import sqlite3

    key = os.environ.get("DODO_PAYMENTS_WEBHOOK_KEY", "").strip()
    if not key:
        raise RuntimeError("Dodo webhook is not configured")
    signed = {
        "webhook-id": headers.get("webhook-id", ""),
        "webhook-signature": headers.get("webhook-signature", ""),
        "webhook-timestamp": headers.get("webhook-timestamp", ""),
    }
    try:
        _dodo_client().webhooks.unwrap(body.decode("utf-8"), headers=signed, key=key)
    except Exception as exc:
        raise PermissionError("invalid signature") from exc
    event = json.loads(body)
    kind = str(event.get("type") or "")
    data = event.get("data") or {}
    with db() as conn:
        try:
            conn.execute(
                "INSERT INTO webhook_events (webhook_id, event_type, created_at) VALUES (?, ?, ?)",
                (signed["webhook-id"], kind, utc_now()),
            )
        except sqlite3.IntegrityError:
            return "duplicate"
        if kind.startswith("subscription."):
            user_id = apply_dodo_subscription(conn, data)
            if user_id:
                add_event(conn, None, "dodo_" + kind.split(".", 1)[1], data.get("status") or "", user_id=user_id)
        elif kind == "payment.succeeded":
            owner = _dodo_owner(conn, data)
            if owner:
                _record_dodo_payment(conn, int(owner["id"]), data)
        elif kind.startswith("refund.") or kind.startswith("dispute."):
            owner = _dodo_owner(conn, data)
            add_event(conn, None, "dodo_" + kind.replace(".", "_"), str(data.get("payment_id") or ""), user_id=owner["id"] if owner else None)
    return kind


def confirm_dodo_payment(user: dict[str, Any], payment_id: str, remote: dict[str, Any] | None = None) -> str:
    """Turn on 30 days after Dodo says this account paid $9. A repeat of the same payment does not add another 30."""
    payment_id = (payment_id or "").strip()
    if not payment_id.startswith("pay_") or len(payment_id) > 80 or not payment_id.replace("_", "").isalnum():
        raise ValueError("That is not a payment")
    with db() as conn:
        already = conn.execute(
            """
            SELECT paid_until FROM users
            WHERE id = ?
              AND EXISTS (
                SELECT 1 FROM payments
                WHERE user_id = users.id AND reference = ? AND status = 'paid'
              )
            """,
            (user["id"], payment_id),
        ).fetchone()
        if already:
            return (already["paid_until"] or "").strip()
    if remote is None:
        remote = fetch_dodo_payment(payment_id)
    if str(remote.get("status") or "") != "succeeded":
        raise ValueError("The payment is not confirmed yet")
    email = str(((remote.get("customer") or {}).get("email") or "")).strip().lower()
    metadata_user = str((remote.get("metadata") or {}).get("user_id") or "")
    if metadata_user != str(user["id"]) and email != str(user.get("email") or "").strip().lower():
        raise ValueError("This payment is for a different account")
    if remote.get("subscription_id"):
        # Monthly plan: Dodo renews it, and the webhook keeps paid_until on the renewal date.
        subscription = fetch_dodo_subscription(str(remote["subscription_id"]))
        with db() as conn:
            apply_dodo_subscription(conn, subscription, expected_user_id=int(user["id"]))
            _record_dodo_payment(conn, int(user["id"]), remote)
            row = conn.execute("SELECT paid_until FROM users WHERE id = ?", (user["id"],)).fetchone()
        return (row["paid_until"] or "") if row else ""
    # Tax is added on top of $9 in some countries, and a local-currency card settles to about $9.
    currency = str(remote.get("settlement_currency") or remote.get("currency") or "")
    amount = int(remote.get("settlement_amount") or remote.get("total_amount") or 0)
    if currency != "USD" or amount < PRICE_USD * 95:
        raise ValueError("This payment is not the monthly price")
    with db() as conn:
        until = _grant_access(conn, int(user["id"]))
        now = utc_now()
        open_row = conn.execute(
            """
            SELECT id FROM payments
            WHERE user_id = ? AND status IN ('needs_link', 'link_ready', 'claimed')
            ORDER BY id DESC LIMIT 1
            """,
            (user["id"],),
        ).fetchone()
        if open_row:
            conn.execute(
                """
                UPDATE payments
                SET status = 'paid', amount_usd = ?, reference = ?, note = 'Dodo payment', updated_at = ?
                WHERE id = ?
                """,
                (PRICE_USD, payment_id, now, open_row["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO payments (user_id, amount_usd, status, reference, note, created_at, updated_at)
                VALUES (?, ?, 'paid', ?, 'Dodo payment', ?, ?)
                """,
                (user["id"], PRICE_USD, payment_id, now, now),
            )
    return until


def reject_payment(payment_id: int) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE payments SET status = 'rejected', updated_at = ?
            WHERE id = ? AND status != 'paid'
            """,
            (utc_now(), payment_id),
        )


def pay_url() -> str:
    """One checkout link every customer can open, saved by the operator."""
    configured = os.environ.get("PAY_URL", "").strip()
    if configured.startswith("https://"):
        return configured
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'pay_url'").fetchone()
    value = (row["value"] if row else "") or ""
    return value if value.startswith("https://") else ""


def save_pay_url(url: str) -> None:
    clean = url.strip()
    if not clean.startswith("https://"):
        raise ValueError("The pay link must start with https://")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO settings (key, value) VALUES ('pay_url', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (clean,),
        )


def open_instant_payment(user_id: int) -> dict[str, Any] | None:
    """Show the standing pay link immediately. No per-customer wait."""
    url = pay_url()
    if not url:
        return None
    now = utc_now()
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM payments
            WHERE user_id = ? AND status IN ('needs_link', 'link_ready', 'claimed')
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if row and row["status"] == "claimed":
            return dict(row)
        if row:
            conn.execute(
                """
                UPDATE payments SET khalti_url = ?, status = 'link_ready', updated_at = ?
                WHERE id = ?
                """,
                (url, now, row["id"]),
            )
            payment_id = row["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO payments (user_id, amount_usd, status, khalti_url, note, created_at, updated_at)
                VALUES (?, ?, 'link_ready', ?, 'standing pay link', ?, ?)
                """,
                (user_id, PRICE_USD, url, now, now),
            )
            payment_id = cur.lastrowid
        saved = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
    return dict(saved) if saved else None


def dev_billing_enabled() -> bool:
    return os.environ.get("BILLING_DEV_ACTIVE") == "1" and not os.environ.get("STRIPE_SECRET")


def stripe_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET") and os.environ.get("STRIPE_PRICE_ID"))


def create_checkout(user: dict[str, Any]) -> str:
    if not stripe_configured():
        raise RuntimeError("Stripe is not configured")
    import stripe

    stripe.api_key = os.environ["STRIPE_SECRET"]
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=user["email"],
        client_reference_id=str(user["id"]),
        line_items=[{"price": os.environ["STRIPE_PRICE_ID"], "quantity": 1}],
        success_url=f"{APP_BASE_URL}/billing?checkout=success",
        cancel_url=f"{APP_BASE_URL}/billing?checkout=cancel",
        metadata={"user_id": str(user["id"])},
    )
    return session.url


def apply_webhook(payload: bytes, signature: str) -> None:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not os.environ.get("STRIPE_SECRET") or not secret:
        raise RuntimeError("Stripe webhook is not configured")
    import stripe

    stripe.api_key = os.environ["STRIPE_SECRET"]
    event = stripe.Webhook.construct_event(payload, signature, secret)
    kind = event["type"]
    obj = event["data"]["object"]

    if kind == "checkout.session.completed":
        user_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
        if not user_id:
            return
        set_subscription(
            int(user_id),
            "active",
            customer_id=obj.get("customer"),
            subscription_id=obj.get("subscription"),
        )
        return

    if kind in {"customer.subscription.updated", "customer.subscription.deleted"}:
        sub_id = obj.get("id")
        customer_id = obj.get("customer")
        stripe_status = obj.get("status") or ""
        status = "active" if stripe_status in {"active", "trialing"} else "inactive"
        if kind == "customer.subscription.deleted":
            status = "inactive"
        _set_by_stripe_id(sub_id, customer_id, status)


def _set_by_stripe_id(subscription_id: str | None, customer_id: str | None, status: str) -> None:
    from .db import db, utc_now

    with db() as conn:
        if subscription_id:
            conn.execute(
                """
                UPDATE users SET subscription_status = ?, updated_at = ?
                WHERE stripe_subscription_id = ?
                """,
                (status, utc_now(), subscription_id),
            )
        elif customer_id:
            conn.execute(
                """
                UPDATE users SET subscription_status = ?, updated_at = ?
                WHERE stripe_customer_id = ?
                """,
                (status, utc_now(), customer_id),
            )
