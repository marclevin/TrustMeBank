"""Payment initiation API."""

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from mockbank.api.accounts import decode_cursor, encode_cursor
from mockbank.api.deps import client_ip, require_scope
from mockbank.config import get_settings
from mockbank.db import get_db
from mockbank.errors import APIError
from mockbank.models import Payment
from mockbank.schemas import ErrorOut, PaymentCreate, PaymentList, PaymentOut, payment_to_dict
from mockbank.services import payments as payment_service
from mockbank.services.oauth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["Payments"])

COMMON_ERRORS = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}}


@router.post(
    "/payments",
    response_model=PaymentOut,
    status_code=201,
    responses={**COMMON_ERRORS, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Initiate a payment",
    description=(
        "Scope: `payments`. Creates a payment in `AWAITING_AUTHORISATION`. **No money moves yet.** "
        "Redirect the customer to `authorisation_url`; MockBank asks them to approve and then settles "
        "the payment, sends a `payment.completed` webhook and (if `redirect_uri` was given) sends the "
        "customer back to `{redirect_uri}?payment_id=...&status=...`.\n\n"
        "Send an `Idempotency-Key` header (any unique string, for example a UUID) so that a retried "
        "request returns the same payment instead of creating a second one."
    ),
)
def create_payment(
    body: PaymentCreate,
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=255),
    auth: AuthContext = Depends(require_scope("payments")),
    db: Session = Depends(get_db),
):
    payment, replayed = payment_service.create_payment(
        db,
        application=auth.application,
        consent=auth.consent,
        customer=auth.customer,
        body=body,
        idempotency_key=idempotency_key,
        ip=client_ip(request),
    )
    if replayed:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return payment_to_dict(payment, get_settings().base_url)


@router.get(
    "/payments",
    response_model=PaymentList,
    responses=COMMON_ERRORS,
    summary="List payments this application created for this customer",
    description="Scope: `payments`. Newest first. Filter with `status`.",
)
def list_payments(
    status: str | None = Query(default=None, description="AWAITING_AUTHORISATION, PROCESSING, COMPLETED, REJECTED or FAILED"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    auth: AuthContext = Depends(require_scope("payments")),
    db: Session = Depends(get_db),
):
    from mockbank.models import Account

    stmt = (
        select(Payment)
        .join(Account, Account.id == Payment.debtor_account_id)
        .where(Payment.application_id == auth.application.id, Account.customer_id == auth.customer.id)
        .order_by(Payment.created_at.desc(), Payment.id.desc())
        .limit(limit + 1)
    )
    if status:
        stmt = stmt.where(Payment.status == status.upper())
    if cursor:
        # cursor is an offset for payments; simple and sufficient for this list
        stmt = stmt.offset(decode_cursor(cursor))
    rows = db.execute(stmt).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    offset = (decode_cursor(cursor) if cursor else 0) + len(rows)
    base = get_settings().base_url
    return {
        "data": [payment_to_dict(p, base) for p in rows],
        "next_cursor": encode_cursor(offset) if has_more else None,
        "has_more": has_more,
    }


@router.get(
    "/payments/{payment_id}",
    response_model=PaymentOut,
    responses=COMMON_ERRORS,
    summary="Get a payment",
    description="Scope: `payments`. Poll this after the customer returns, or rely on the webhook.",
)
def get_payment(
    payment_id: str, auth: AuthContext = Depends(require_scope("payments")), db: Session = Depends(get_db)
):
    payment = db.get(Payment, payment_id)
    if (
        payment is None
        or payment.application_id != auth.application.id
        or payment.debtor_account.customer_id != auth.customer.id
    ):
        raise APIError(404, "not_found", "No such payment for this application and customer.")
    return payment_to_dict(payment, get_settings().base_url)
