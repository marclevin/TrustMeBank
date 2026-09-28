"""Payment initiation, authorisation and settlement. See SPEC section 8."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mockbank.audit import audit
from mockbank.config import get_settings
from mockbank.errors import APIError
from mockbank.models import Account, Application, Consent, Customer, Payment, utcnow
from mockbank.schemas import PaymentCreate, payment_to_dict
from mockbank.services import webhooks
from mockbank.services.ledger import AccountClosed, InsufficientFunds, post_transfer

AWAITING = "AWAITING_AUTHORISATION"
PROCESSING = "PROCESSING"
COMPLETED = "COMPLETED"
REJECTED = "REJECTED"
FAILED = "FAILED"
TERMINAL = {COMPLETED, REJECTED, FAILED}


def _request_hash(body: PaymentCreate) -> str:
    canonical = json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def create_payment(
    db: Session,
    *,
    application: Application,
    consent: Consent,
    customer: Customer,
    body: PaymentCreate,
    idempotency_key: str | None,
    ip: str | None = None,
) -> tuple[Payment, bool]:
    """Validate and create a payment in AWAITING_AUTHORISATION. Commits.

    Returns (payment, replayed). `replayed` is True when an idempotent replay returned the
    original payment.
    """
    request_hash = _request_hash(body)
    if idempotency_key:
        existing = db.execute(
            select(Payment).where(
                Payment.application_id == application.id, Payment.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_hash != request_hash:
                raise APIError(
                    409,
                    "idempotency_key_reused",
                    "This Idempotency-Key was already used with a different request body.",
                    {"payment_id": existing.id},
                )
            return existing, True

    amount: Decimal = body.parsed_amount()

    debtor = db.get(Account, body.debtor_account_id)
    if debtor is None or debtor.customer_id != customer.id or debtor.status != "active":
        raise APIError(404, "not_found", "debtor_account_id is not one of the customer's active accounts.")

    creditor = db.execute(
        select(Account).where(Account.account_number == body.creditor_account_number)
    ).scalar_one_or_none()
    if creditor is None or creditor.status != "active" or creditor.customer.kind == "system":
        raise APIError(
            422, "invalid_creditor",
            "creditor_account_number is not an active MockBank account. Payments can only be made to "
            "accounts that exist at MockBank.",
        )
    if creditor.id == debtor.id:
        raise APIError(422, "invalid_creditor", "The creditor account must differ from the debtor account.")

    if body.redirect_uri is not None and body.redirect_uri not in application.redirect_uris:
        raise APIError(
            422, "invalid_redirect_uri",
            "redirect_uri must exactly match one of the application's registered redirect URIs.",
            {"registered": application.redirect_uris},
        )

    payment = Payment(
        application_id=application.id,
        consent_id=consent.id,
        debtor_account_id=debtor.id,
        creditor_account_number=creditor.account_number,
        creditor_name=creditor.customer.full_name,
        amount=amount,
        currency="ZAR",
        reference=body.reference.strip(),
        status=AWAITING,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        redirect_uri=body.redirect_uri,
    )
    db.add(payment)
    audit(
        db, actor_type="application", actor_id=application.id, action="payment.created",
        target_type="payment", target_id=payment.id,
        details={"amount": str(amount), "debtor_account_id": debtor.id,
                 "creditor_account_number": creditor.account_number, "customer_id": customer.id},
        ip=ip,
    )
    try:
        db.commit()
    except IntegrityError:
        # Two concurrent requests with the same Idempotency-Key: return the one that won.
        db.rollback()
        existing = db.execute(
            select(Payment).where(
                Payment.application_id == application.id, Payment.idempotency_key == idempotency_key
            )
        ).scalar_one()
        if existing.request_hash != request_hash:
            raise APIError(409, "idempotency_key_reused",
                           "This Idempotency-Key was already used with a different request body.") from None
        return existing, True
    return payment, False


def _webhook_data(payment: Payment) -> dict:
    data = payment_to_dict(payment, get_settings().base_url)
    data.pop("authorisation_url", None)
    return data


def approve_payment(db: Session, payment: Payment, customer: Customer, ip: str | None = None) -> Payment:
    """Customer approved on the authorisation page. Commits.

    With no processing delay the payment is settled in the same transaction.
    """
    settings = get_settings()
    payment = db.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update()
    ).scalar_one()
    if payment.status != AWAITING:
        db.rollback()
        return payment
    now = utcnow()
    payment.status = PROCESSING
    payment.authorised_at = now
    payment.process_after = now + timedelta(seconds=settings.payment_processing_delay_seconds)
    audit(
        db, actor_type="customer", actor_id=customer.id, action="payment.approved",
        target_type="payment", target_id=payment.id, ip=ip,
    )
    if settings.payment_processing_delay_seconds <= 0:
        _settle_locked(db, payment)
    db.commit()
    return payment


def reject_payment(db: Session, payment: Payment, customer: Customer, ip: str | None = None) -> Payment:
    payment = db.execute(
        select(Payment).where(Payment.id == payment.id).with_for_update()
    ).scalar_one()
    if payment.status != AWAITING:
        db.rollback()
        return payment
    payment.status = REJECTED
    payment.completed_at = utcnow()
    audit(
        db, actor_type="customer", actor_id=customer.id, action="payment.rejected",
        target_type="payment", target_id=payment.id, ip=ip,
    )
    webhooks.enqueue(
        db, application=payment.application, event_type="payment.rejected",
        data=_webhook_data(payment), payment_id=payment.id,
    )
    db.commit()
    return payment


def _settle_locked(db: Session, payment: Payment) -> None:
    """Settle a PROCESSING payment whose row is already locked. Does not commit."""
    creditor = db.execute(
        select(Account).where(Account.account_number == payment.creditor_account_number)
    ).scalar_one_or_none()
    now = utcnow()
    try:
        if creditor is None:
            raise AccountClosed("creditor account no longer exists")
        result = post_transfer(
            db,
            debit_account_id=payment.debtor_account_id,
            credit_account_id=creditor.id,
            amount=payment.amount,
            description=f"Payment via {payment.application.name}",
            reference=payment.reference,
            kind="payment",
            payment_id=payment.id,
        )
    except (InsufficientFunds, AccountClosed) as exc:
        payment.status = FAILED
        payment.failure_reason = exc.code
        payment.completed_at = now
        audit(
            db, actor_type="system", actor_id=None, action="payment.failed",
            target_type="payment", target_id=payment.id, details={"reason": exc.code},
        )
        webhooks.enqueue(
            db, application=payment.application, event_type="payment.failed",
            data=_webhook_data(payment), payment_id=payment.id,
        )
        return
    payment.status = COMPLETED
    payment.journal_id = result.journal.id
    payment.completed_at = now
    audit(
        db, actor_type="system", actor_id=None, action="payment.completed",
        target_type="payment", target_id=payment.id,
        details={"journal_id": result.journal.id, "amount": str(payment.amount)},
    )
    webhooks.enqueue(
        db, application=payment.application, event_type="payment.completed",
        data=_webhook_data(payment), payment_id=payment.id,
    )
    webhooks.enqueue_transaction_created(db, result.debit)
    webhooks.enqueue_transaction_created(db, result.credit)


def settle_payment(db: Session, payment_id: str) -> Payment | None:
    """Idempotent settlement entry point used by the worker. Commits."""
    payment = db.execute(
        select(Payment).where(Payment.id == payment_id).with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    if payment is None:
        db.rollback()
        return None
    if payment.status != PROCESSING:
        db.rollback()
        return payment
    _settle_locked(db, payment)
    db.commit()
    return payment


def settle_due(db: Session, limit: int = 50) -> int:
    """Settle every PROCESSING payment whose delay has elapsed. Returns count settled."""
    ids = db.execute(
        select(Payment.id)
        .where(Payment.status == PROCESSING, Payment.process_after <= utcnow())
        .order_by(Payment.process_after)
        .limit(limit)
    ).scalars().all()
    db.rollback()
    count = 0
    for pid in ids:
        if settle_payment(db, pid) is not None:
            count += 1
    return count
