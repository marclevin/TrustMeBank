"""Customer pages: accounts, transactions, transfers, connected apps."""

from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from trustmebank.audit import audit
from trustmebank.db import get_db
from trustmebank.errors import WebError
from trustmebank.models import Account, Consent, Customer, Payment, Transaction
from trustmebank.money import InvalidAmount, parse_amount
from trustmebank.services import oauth as oauth_service
from trustmebank.services import webhooks
from trustmebank.services.ledger import LedgerError, post_transfer
from trustmebank.web.deps import check_csrf, client_ip, current_customer
from trustmebank.web.templating import flash, render

router = APIRouter(include_in_schema=False)


def _own_account(db: Session, customer: Customer, account_id: str) -> Account:
    account = db.get(Account, account_id)
    if account is None or account.customer_id != customer.id:
        raise WebError(404, "Account not found", "That account does not exist or is not yours.")
    return account


@router.get("/accounts")
def accounts(request: Request, customer: Customer = Depends(current_customer), db: Session = Depends(get_db)):
    rows = (
        db.execute(select(Account).where(Account.customer_id == customer.id).order_by(Account.created_at))
        .scalars()
        .all()
    )
    pending = (
        db.execute(
            select(Payment)
            .join(Account, Account.id == Payment.debtor_account_id)
            .where(Account.customer_id == customer.id, Payment.status == "AWAITING_AUTHORISATION")
            .order_by(Payment.created_at.desc())
        )
        .scalars()
        .all()
    )
    total = sum((a.balance for a in rows), Decimal("0.00"))
    return render(request, "accounts.html", {"accounts": rows, "total": total, "pending_payments": pending})


@router.get("/accounts/{account_id}")
def account_detail(
    request: Request,
    account_id: str,
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
):
    account = _own_account(db, customer, account_id)
    txns = (
        db.execute(
            select(Transaction)
            .options(joinedload(Transaction.journal))
            .where(Transaction.account_id == account.id)
            .order_by(Transaction.seq.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )
    return render(request, "account_detail.html", {"account": account, "transactions": txns})


@router.get("/transfer")
def transfer_form(
    request: Request, customer: Customer = Depends(current_customer), db: Session = Depends(get_db)
):
    rows = (
        db.execute(select(Account).where(Account.customer_id == customer.id, Account.status == "active"))
        .scalars()
        .all()
    )
    return render(request, "transfer.html", {"accounts": rows, "form": {}, "error": None})


@router.post("/transfer")
def transfer(
    request: Request,
    from_account_id: str = Form(...),
    to_account_number: str = Form(...),
    amount: str = Form(...),
    reference: str = Form(default=""),
    csrf: str = Form(default=""),
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    rows = (
        db.execute(select(Account).where(Account.customer_id == customer.id, Account.status == "active"))
        .scalars()
        .all()
    )
    form = {
        "from_account_id": from_account_id,
        "to_account_number": to_account_number,
        "amount": amount,
        "reference": reference,
    }

    def fail(message: str):
        return render(
            request, "transfer.html", {"accounts": rows, "form": form, "error": message}, status_code=422
        )

    source = _own_account(db, customer, from_account_id)
    target = db.execute(
        select(Account).where(Account.account_number == to_account_number.strip())
    ).scalar_one_or_none()
    if target is None or target.status != "active" or target.customer.kind == "system":
        return fail("That account number does not exist at TrustMeBank.")
    try:
        value = parse_amount(amount)
    except InvalidAmount as exc:
        return fail(str(exc))
    reference = reference.strip()[:35]
    try:
        result = post_transfer(
            db,
            debit_account_id=source.id,
            credit_account_id=target.id,
            amount=value,
            description=f"Transfer to {target.customer.full_name}"
            if target.customer_id != customer.id
            else f"Transfer to {target.name}",
            reference=reference or None,
        )
        # The credit side reads better as "Transfer from <name>".
        result.credit.description = (
            f"Transfer from {customer.full_name}"
            if target.customer_id != customer.id
            else f"Transfer from {source.name}"
        )
        webhooks.enqueue_transaction_created(db, result.debit)
        webhooks.enqueue_transaction_created(db, result.credit)
        audit(
            db,
            actor_type="customer",
            actor_id=customer.id,
            action="transfer.posted",
            target_type="journal",
            target_id=result.journal.id,
            details={"amount": str(value), "to": target.account_number},
            ip=client_ip(request),
        )
        db.commit()
    except LedgerError as exc:
        db.rollback()
        return fail(
            {"insufficient_funds": "Insufficient funds.", "account_closed": "That account is closed."}.get(
                exc.code, str(exc)
            )
        )
    flash(request, f"Transfer of R{value:,.2f} to {target.account_number} completed.", "success")
    return RedirectResponse(f"/accounts/{source.id}", status_code=303)


@router.get("/connected-apps")
def connected_apps(
    request: Request, customer: Customer = Depends(current_customer), db: Session = Depends(get_db)
):
    consents = oauth_service.active_consents_for_customer(db, customer.id)
    return render(
        request, "connected_apps.html", {"consents": consents, "scope_labels": oauth_service.SCOPES}
    )


@router.post("/connected-apps/{consent_id}/revoke")
def revoke(
    request: Request,
    consent_id: str,
    csrf: str = Form(default=""),
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    consent = db.get(Consent, consent_id)
    if consent is None or consent.customer_id != customer.id:
        raise WebError(404, "Not found", "That consent does not exist.")
    oauth_service.revoke_consent(db, consent, actor_type="customer", actor_id=customer.id)
    db.commit()
    flash(request, f"Access for {consent.application.name} has been revoked.", "success")
    return RedirectResponse("/connected-apps", status_code=303)
