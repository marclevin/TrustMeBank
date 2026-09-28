"""Webhook outbox: enqueue in the caller's transaction, deliver from the worker.

See SPEC section 9.
"""

import json
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from trustmebank.audit import audit
from trustmebank.config import get_settings
from trustmebank.models import Application, Consent, Transaction, WebhookDelivery, utcnow
from trustmebank.security import sign_webhook

log = logging.getLogger("trustmebank.webhooks")

# Seconds to wait before attempt 2, 3, 4, 5, 6. After the last one the delivery is `failed`.
RETRY_SCHEDULE = [30, 120, 600, 1800, 3600]
MAX_ATTEMPTS = len(RETRY_SCHEDULE) + 1


def _iso(dt) -> str:
    return dt.astimezone().isoformat().replace("+00:00", "Z") if dt else None


def enqueue(
    db: Session,
    *,
    application: Application,
    event_type: str,
    data: dict,
    payment_id: str | None = None,
) -> WebhookDelivery | None:
    """Create a pending delivery. Returns None if the application has no webhook URL."""
    if not application.webhook_url or not application.is_active:
        return None
    delivery = WebhookDelivery(
        application_id=application.id,
        event_type=event_type,
        payment_id=payment_id,
        url=application.webhook_url,
        payload="",
        status="pending",
        attempts=0,
        next_attempt_at=utcnow(),
    )
    db.add(delivery)
    db.flush()  # assigns the id used in the envelope
    envelope = {
        "id": delivery.id,
        "event": event_type,
        "created_at": utcnow().isoformat().replace("+00:00", "Z"),
        "data": data,
    }
    delivery.payload = json.dumps(envelope, separators=(",", ":"), sort_keys=False)
    return delivery


def enqueue_transaction_created(db: Session, transaction: Transaction) -> None:
    """Notify every opted-in application with an active `transactions` consent for the owner."""
    from trustmebank.schemas import transaction_to_dict

    account = transaction.account
    now = utcnow()
    consents = (
        db.execute(
            select(Consent).where(
                Consent.customer_id == account.customer_id,
                Consent.status == "active",
                Consent.expires_at > now,
            )
        )
        .scalars()
        .all()
    )
    notified: set[str] = set()
    for consent in consents:
        if "transactions" not in consent.scopes or consent.application_id in notified:
            continue
        app = db.get(Application, consent.application_id)
        if app is None or not app.send_transaction_events:
            continue
        notified.add(app.id)
        enqueue(db, application=app, event_type="transaction.created", data=transaction_to_dict(transaction))


def attempt_delivery(db: Session, delivery: WebhookDelivery, client: httpx.Client) -> bool:
    """Try to deliver once and update the row. Does not commit. Returns True on success."""
    app = db.get(Application, delivery.application_id)
    if app is None:
        delivery.status = "failed"
        delivery.last_error = "application deleted"
        return False
    body = delivery.payload.encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "TrustMeBank-Webhooks/1.0",
        "X-TrustMeBank-Event": delivery.event_type,
        "X-TrustMeBank-Delivery-Id": delivery.id,
        "X-TrustMeBank-Signature": sign_webhook(app.webhook_secret, body),
    }
    now = utcnow()
    delivery.attempts += 1
    delivery.last_attempt_at = now
    ok = False
    try:
        response = client.post(
            delivery.url, content=body, headers=headers, timeout=get_settings().webhook_timeout_seconds
        )
        delivery.last_status_code = response.status_code
        delivery.last_response_body = response.text[:2000]
        delivery.last_error = None
        ok = 200 <= response.status_code < 300
        if not ok:
            delivery.last_error = f"HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        delivery.last_status_code = None
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:500]
        delivery.last_response_body = None

    if ok:
        delivery.status = "delivered"
        delivery.delivered_at = now
    elif delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "failed"
    else:
        delay = RETRY_SCHEDULE[delivery.attempts - 1]
        delivery.status = "pending"
        delivery.next_attempt_at = now + timedelta(seconds=delay)
    audit(
        db,
        actor_type="system",
        actor_id=None,
        action="webhook.delivered" if ok else "webhook.attempt_failed",
        target_type="webhook_delivery",
        target_id=delivery.id,
        details={
            "application_id": app.id,
            "event": delivery.event_type,
            "attempt": delivery.attempts,
            "status_code": delivery.last_status_code,
            "error": delivery.last_error,
        },
    )
    log.info(
        "webhook %s %s attempt %s -> %s",
        delivery.id,
        delivery.event_type,
        delivery.attempts,
        "delivered" if ok else (delivery.last_error or "failed"),
    )
    return ok


def retry_now(db: Session, delivery: WebhookDelivery) -> None:
    """Admin action: put a delivery back in the queue for immediate retry."""
    delivery.status = "pending"
    delivery.next_attempt_at = utcnow()
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.attempts = MAX_ATTEMPTS - 1  # allow one more attempt


def deliver_due(db: Session, client: httpx.Client, limit: int = 20) -> int:
    """Deliver every due pending webhook. One transaction per delivery. Returns count attempted."""
    count = 0
    for _ in range(limit):
        delivery = db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "pending", WebhookDelivery.next_attempt_at <= utcnow())
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if delivery is None:
            db.rollback()
            break
        attempt_delivery(db, delivery, client)
        db.commit()
        count += 1
    return count
