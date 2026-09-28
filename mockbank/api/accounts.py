"""Account information API."""

import base64
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from mockbank.api.deps import require_scope
from mockbank.db import get_db
from mockbank.errors import APIError
from mockbank.models import Account, Transaction, utcnow
from mockbank.money import fmt
from mockbank.schemas import (
    AccountList,
    AccountOut,
    BalanceOut,
    ErrorOut,
    TransactionList,
    account_to_dict,
    iso,
    transaction_to_dict,
)
from mockbank.services.oauth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["Accounts"])

COMMON_ERRORS = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}}


def _own_account(db: Session, auth: AuthContext, account_id: str) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.customer_id != auth.customer.id or account.status != "active":
        raise APIError(404, "not_found", "No such account for this customer.")
    return account


@router.get(
    "/accounts",
    response_model=AccountList,
    response_model_exclude_none=True,
    responses=COMMON_ERRORS,
    summary="List the customer's accounts",
    description=(
        "Scope: `accounts`. The `balance` field is included only when the consent also has `balances`."
    ),
)
def list_accounts(auth: AuthContext = Depends(require_scope("accounts")), db: Session = Depends(get_db)):
    accounts = (
        db.execute(
            select(Account)
            .where(Account.customer_id == auth.customer.id, Account.status == "active")
            .order_by(Account.created_at)
        )
        .scalars()
        .all()
    )
    include_balance = auth.has_scope("balances")
    return {"data": [account_to_dict(a, include_balance=include_balance) for a in accounts]}


@router.get(
    "/accounts/{account_id}",
    response_model=AccountOut,
    response_model_exclude_none=True,
    responses=COMMON_ERRORS,
    summary="Get one account",
    description="Scope: `accounts`.",
)
def get_account(
    account_id: str, auth: AuthContext = Depends(require_scope("accounts")), db: Session = Depends(get_db)
):
    account = _own_account(db, auth, account_id)
    return account_to_dict(account, include_balance=auth.has_scope("balances"))


@router.get(
    "/accounts/{account_id}/balance",
    response_model=BalanceOut,
    responses=COMMON_ERRORS,
    summary="Get an account balance",
    description="Scope: `balances`.",
)
def get_balance(
    account_id: str, auth: AuthContext = Depends(require_scope("balances")), db: Session = Depends(get_db)
):
    account = _own_account(db, auth, account_id)
    return {
        "account_id": account.id,
        "currency": account.currency,
        "balance": fmt(account.balance),
        "as_of": iso(utcnow()),
    }


def encode_cursor(seq: int) -> str:
    return base64.urlsafe_b64encode(str(seq).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return int(base64.urlsafe_b64decode(padded).decode())
    except Exception:  # noqa: BLE001
        raise APIError(400, "invalid_request", "cursor is not valid.") from None


@router.get(
    "/accounts/{account_id}/transactions",
    response_model=TransactionList,
    responses=COMMON_ERRORS,
    summary="List transactions, newest first",
    description=(
        "Scope: `transactions`. Cursor paginated: pass `next_cursor` from the previous response as "
        "`cursor`. `from_date` and `to_date` are inclusive UTC calendar dates (YYYY-MM-DD)."
    ),
)
def list_transactions(
    account_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
    auth: AuthContext = Depends(require_scope("transactions")),
    db: Session = Depends(get_db),
):
    account = _own_account(db, auth, account_id)
    stmt = (
        select(Transaction)
        .options(joinedload(Transaction.journal))
        .where(Transaction.account_id == account.id)
        .order_by(Transaction.seq.desc())
        .limit(limit + 1)
    )
    if cursor:
        stmt = stmt.where(Transaction.seq < decode_cursor(cursor))
    if from_date:
        stmt = stmt.where(Transaction.booked_at >= datetime.combine(from_date, time.min, tzinfo=UTC))
    if to_date:
        end = datetime.combine(to_date, time.min, tzinfo=UTC) + timedelta(days=1)
        stmt = stmt.where(Transaction.booked_at < end)
    rows = db.execute(stmt).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "data": [transaction_to_dict(t) for t in rows],
        "next_cursor": encode_cursor(rows[-1].seq) if has_more and rows else None,
        "has_more": has_more,
    }
