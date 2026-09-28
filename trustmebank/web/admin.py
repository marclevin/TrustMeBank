"""Administrator UI under /admin."""

import secrets

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from trustmebank.audit import audit
from trustmebank.config import get_settings
from trustmebank.db import get_db
from trustmebank.errors import WebError
from trustmebank.models import (
    Account,
    Application,
    AuditLog,
    Consent,
    Customer,
    Journal,
    Payment,
    Transaction,
    WebhookDelivery,
)
from trustmebank.money import InvalidAmount, parse_amount
from trustmebank.security import hash_password
from trustmebank.services import admin as admin_service
from trustmebank.services import oauth as oauth_service
from trustmebank.services import seed as seed_service
from trustmebank.services import webhooks
from trustmebank.services.ledger import check_integrity
from trustmebank.web.deps import check_csrf, client_ip, ensure_csrf, require_admin, safe_next
from trustmebank.web.templating import flash, render

router = APIRouter(prefix="/admin", include_in_schema=False)
protected = APIRouter(prefix="/admin", include_in_schema=False, dependencies=[Depends(require_admin)])


# ----------------------------------------------------------------------------- auth


@router.get("/login")
def login_form(request: Request, next: str | None = None):
    ensure_csrf(request)
    request.state.customer = None
    return render(request, "admin/login.html", {"error": None, "next": safe_next(next, "/admin")})


@router.post("/login")
def login(
    request: Request,
    password: str = Form(...),
    next: str = Form(default="/admin"),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    request.state.customer = None
    if not secrets.compare_digest(password, get_settings().admin_password):
        audit(db, actor_type="admin", actor_id=None, action="admin.login_failed", ip=client_ip(request))
        db.commit()
        return render(
            request, "admin/login.html", {"error": "Incorrect password.", "next": next}, status_code=401
        )
    request.session["admin"] = True
    audit(db, actor_type="admin", actor_id="admin", action="admin.login", ip=client_ip(request))
    db.commit()
    return RedirectResponse(safe_next(next, "/admin"), status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form(default="")):
    check_csrf(request, csrf)
    request.session.pop("admin", None)
    return RedirectResponse("/admin/login", status_code=303)


# ----------------------------------------------------------------------------- dashboard


def _count(db: Session, model, *where):
    return db.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


@protected.get("")
def dashboard(request: Request, db: Session = Depends(get_db)):
    stats = {
        "customers": _count(db, Customer, Customer.kind != "system"),
        "accounts": _count(db, Account),
        "applications": _count(db, Application),
        "consents": _count(db, Consent, Consent.status == "active"),
        "payments": _count(db, Payment),
        "payments_pending": _count(db, Payment, Payment.status == "AWAITING_AUTHORISATION"),
        "journals": _count(db, Journal),
        "webhooks_pending": _count(db, WebhookDelivery, WebhookDelivery.status == "pending"),
        "webhooks_failed": _count(db, WebhookDelivery, WebhookDelivery.status == "failed"),
    }
    integrity = check_integrity(db)
    return render(request, "admin/dashboard.html", {"stats": stats, "integrity": integrity})


@protected.post("/reset")
def reset(
    request: Request,
    mode: str = Form(...),
    confirm: str = Form(default=""),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    if confirm != "RESET":
        flash(request, "Type RESET in the confirmation box to reset.", "error")
        return RedirectResponse("/admin", status_code=303)
    if mode == "full":
        seed_service.reset_all(db)
        flash(request, "Full reset complete. All data was replaced by the seed.", "success")
    else:
        seed_service.reset_activity(db)
        flash(request, "Activity reset complete. Customers and applications were kept.", "success")
    return RedirectResponse("/admin", status_code=303)


# ----------------------------------------------------------------------------- customers


@protected.get("/customers")
def customers(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(select(Customer).order_by(Customer.kind, Customer.created_at)).scalars().all()
    return render(request, "admin/customers.html", {"customers": rows})


@protected.post("/customers")
def create_customer(
    request: Request,
    email: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
    kind: str = Form(default="personal"),
    account_name: str = Form(default="Everyday Account"),
    opening_balance: str = Form(default="0"),
    history: str = Form(default="none"),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    try:
        opening = parse_amount(opening_balance, allow_zero=True)
        customer = admin_service.create_customer(
            db,
            email=email,
            full_name=full_name,
            password=password,
            kind="business" if kind == "business" else "personal",
        )
        if account_name.strip():
            admin_service.create_account(
                db,
                customer=customer,
                name=account_name,
                account_type="business" if kind == "business" else "current",
                opening_balance=opening,
                history=history,
            )
        db.commit()
    except (ValueError, InvalidAmount) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/admin/customers", status_code=303)
    flash(request, f"Customer {customer.email} created.", "success")
    return RedirectResponse(f"/admin/customers/{customer.id}", status_code=303)


@protected.get("/customers/{customer_id}")
def customer_detail(request: Request, customer_id: str, db: Session = Depends(get_db)):
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise WebError(404, "Not found", "No such customer.")
    consents = (
        db.execute(
            select(Consent).where(Consent.customer_id == customer.id).order_by(Consent.created_at.desc())
        )
        .scalars()
        .all()
    )
    return render(request, "admin/customer_detail.html", {"c": customer, "consents": consents})


@protected.post("/customers/{customer_id}/accounts")
def add_account(
    request: Request,
    customer_id: str,
    name: str = Form(...),
    account_type: str = Form(default="current"),
    opening_balance: str = Form(default="0"),
    history: str = Form(default="none"),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise WebError(404, "Not found", "No such customer.")
    try:
        opening = parse_amount(opening_balance, allow_zero=True)
        admin_service.create_account(
            db,
            customer=customer,
            name=name,
            account_type=account_type,
            opening_balance=opening,
            history=history,
        )
        db.commit()
        flash(request, "Account created.", "success")
    except (ValueError, InvalidAmount) as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(f"/admin/customers/{customer.id}", status_code=303)


@protected.post("/accounts/{account_id}/top-up")
def top_up(
    request: Request,
    account_id: str,
    amount: str = Form(...),
    description: str = Form(default="Deposit"),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    account = db.get(Account, account_id)
    if account is None:
        raise WebError(404, "Not found", "No such account.")
    try:
        value = parse_amount(amount)
        seed_service.top_up(db, account, value, description.strip() or "Deposit")
        audit(
            db,
            actor_type="admin",
            actor_id="admin",
            action="admin.top_up",
            target_type="account",
            target_id=account.id,
            details={"amount": str(value)},
        )
        db.commit()
        flash(request, f"Deposited R{value:,.2f} into {account.account_number}.", "success")
    except InvalidAmount as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(f"/admin/customers/{account.customer_id}", status_code=303)


@protected.post("/accounts/{account_id}/seed-history")
def seed_history(
    request: Request,
    account_id: str,
    style: str = Form(default="personal"),
    target: str = Form(...),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    account = db.get(Account, account_id)
    if account is None:
        raise WebError(404, "Not found", "No such account.")
    try:
        value = parse_amount(target, allow_zero=True)
        if account.balance != 0:
            raise InvalidAmount("History can only be seeded on an account with a zero balance.")
        count = seed_service.seed_history(db, account, style, value)
        audit(
            db,
            actor_type="admin",
            actor_id="admin",
            action="admin.history_seeded",
            target_type="account",
            target_id=account.id,
            details={"journals": count},
        )
        db.commit()
        flash(request, f"Seeded {count} transactions.", "success")
    except (InvalidAmount, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(f"/admin/customers/{account.customer_id}", status_code=303)


@protected.post("/customers/{customer_id}/password")
def reset_password(
    request: Request,
    customer_id: str,
    password: str = Form(...),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise WebError(404, "Not found", "No such customer.")
    customer.password_hash = hash_password(password)
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.password_reset",
        target_type="customer",
        target_id=customer.id,
    )
    db.commit()
    flash(request, "Password updated.", "success")
    return RedirectResponse(f"/admin/customers/{customer.id}", status_code=303)


@protected.post("/customers/{customer_id}/toggle-active")
def toggle_customer(
    request: Request, customer_id: str, csrf: str = Form(default=""), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    customer = db.get(Customer, customer_id)
    if customer is None or customer.kind == "system":
        raise WebError(404, "Not found", "No such customer.")
    customer.is_active = not customer.is_active
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.customer_toggled",
        target_type="customer",
        target_id=customer.id,
        details={"is_active": customer.is_active},
    )
    db.commit()
    return RedirectResponse(f"/admin/customers/{customer.id}", status_code=303)


# ----------------------------------------------------------------------------- applications


@protected.get("/applications")
def applications(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(select(Application).order_by(Application.created_at)).scalars().all()
    return render(request, "admin/applications.html", {"applications": rows})


@protected.post("/applications")
def register_application(
    request: Request,
    name: str = Form(...),
    owner_label: str = Form(default=""),
    redirect_uris: str = Form(default=""),
    webhook_url: str = Form(default=""),
    send_transaction_events: str = Form(default=""),
    settlement_email: str = Form(default=""),
    settlement_password: str = Form(default=""),
    settlement_opening_balance: str = Form(default="0"),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    uris = admin_service.parse_redirect_uris(redirect_uris)
    if not uris:
        flash(request, "At least one redirect URI is required.", "error")
        return RedirectResponse("/admin/applications", status_code=303)
    try:
        opening = parse_amount(settlement_opening_balance or "0", allow_zero=True)
        result = admin_service.register_application(
            db,
            name=name,
            owner_label=owner_label,
            redirect_uris=uris,
            webhook_url=webhook_url,
            send_transaction_events=bool(send_transaction_events),
            settlement_email=settlement_email.strip() or None,
            settlement_password=settlement_password.strip() or None,
            settlement_opening_balance=opening,
        )
        db.commit()
    except (ValueError, InvalidAmount) as exc:
        db.rollback()
        flash(request, str(exc), "error")
        return RedirectResponse("/admin/applications", status_code=303)
    return render(request, "admin/application_credentials.html", {"r": result})


@protected.get("/applications/{app_id}")
def application_detail(request: Request, app_id: str, db: Session = Depends(get_db)):
    app = db.get(Application, app_id)
    if app is None:
        raise WebError(404, "Not found", "No such application.")
    consents = (
        db.execute(
            select(Consent)
            .where(Consent.application_id == app.id)
            .order_by(Consent.created_at.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    payments = (
        db.execute(
            select(Payment)
            .where(Payment.application_id == app.id)
            .order_by(Payment.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    deliveries = (
        db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.application_id == app.id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(20)
        )
        .scalars()
        .all()
    )
    return render(
        request,
        "admin/application_detail.html",
        {
            "app": app,
            "consents": consents,
            "payments": payments,
            "deliveries": deliveries,
            "new_secret": request.session.pop("new_secret", None),
        },
    )


@protected.post("/applications/{app_id}")
def update_application(
    request: Request,
    app_id: str,
    name: str = Form(...),
    owner_label: str = Form(default=""),
    redirect_uris: str = Form(default=""),
    webhook_url: str = Form(default=""),
    send_transaction_events: str = Form(default=""),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    app = db.get(Application, app_id)
    if app is None:
        raise WebError(404, "Not found", "No such application.")
    uris = admin_service.parse_redirect_uris(redirect_uris)
    if not uris:
        flash(request, "At least one redirect URI is required.", "error")
        return RedirectResponse(f"/admin/applications/{app.id}", status_code=303)
    app.name = name.strip()
    app.owner_label = owner_label.strip()
    app.redirect_uris = uris
    app.webhook_url = webhook_url.strip() or None
    app.send_transaction_events = bool(send_transaction_events)
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.application_updated",
        target_type="application",
        target_id=app.id,
    )
    db.commit()
    flash(request, "Application updated.", "success")
    return RedirectResponse(f"/admin/applications/{app.id}", status_code=303)


@protected.post("/applications/{app_id}/regenerate-secret")
def regenerate_secret(
    request: Request, app_id: str, csrf: str = Form(default=""), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    app = db.get(Application, app_id)
    if app is None:
        raise WebError(404, "Not found", "No such application.")
    secret = admin_service.regenerate_client_secret(db, app)
    db.commit()
    request.session["new_secret"] = {"label": "New client secret", "value": secret}
    return RedirectResponse(f"/admin/applications/{app.id}", status_code=303)


@protected.post("/applications/{app_id}/rotate-webhook-secret")
def rotate_webhook_secret(
    request: Request, app_id: str, csrf: str = Form(default=""), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    app = db.get(Application, app_id)
    if app is None:
        raise WebError(404, "Not found", "No such application.")
    admin_service.rotate_webhook_secret(db, app)
    db.commit()
    flash(request, "Webhook secret rotated. Give the new secret to the team.", "success")
    return RedirectResponse(f"/admin/applications/{app.id}", status_code=303)


@protected.post("/applications/{app_id}/toggle-active")
def toggle_application(
    request: Request, app_id: str, csrf: str = Form(default=""), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    app = db.get(Application, app_id)
    if app is None:
        raise WebError(404, "Not found", "No such application.")
    app.is_active = not app.is_active
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.application_toggled",
        target_type="application",
        target_id=app.id,
        details={"is_active": app.is_active},
    )
    db.commit()
    return RedirectResponse(f"/admin/applications/{app.id}", status_code=303)


# ------------------------------------------------------ consents, payments, webhooks, audit


@protected.get("/consents")
def consents(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(select(Consent).order_by(Consent.created_at.desc()).limit(200)).scalars().all()
    return render(request, "admin/consents.html", {"consents": rows})


@protected.post("/consents/{consent_id}/revoke")
def revoke_consent(
    request: Request, consent_id: str, csrf: str = Form(default=""), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    consent = db.get(Consent, consent_id)
    if consent is None:
        raise WebError(404, "Not found", "No such consent.")
    oauth_service.revoke_consent(db, consent, actor_type="admin", actor_id="admin")
    db.commit()
    flash(request, "Consent revoked.", "success")
    return RedirectResponse(request.headers.get("referer") or "/admin/consents", status_code=303)


@protected.get("/payments")
def payments(request: Request, status: str | None = Query(default=None), db: Session = Depends(get_db)):
    stmt = select(Payment).order_by(Payment.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Payment.status == status)
    rows = db.execute(stmt).scalars().all()
    return render(request, "admin/payments.html", {"payments": rows, "status": status})


@protected.get("/payments/{payment_id}")
def payment_detail(request: Request, payment_id: str, db: Session = Depends(get_db)):
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise WebError(404, "Not found", "No such payment.")
    deliveries = (
        db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.payment_id == payment.id)
            .order_by(WebhookDelivery.created_at)
        )
        .scalars()
        .all()
    )
    txns = []
    if payment.journal_id:
        txns = (
            db.execute(select(Transaction).where(Transaction.journal_id == payment.journal_id))
            .scalars()
            .all()
        )
    log = (
        db.execute(select(AuditLog).where(AuditLog.target_id == payment.id).order_by(AuditLog.created_at))
        .scalars()
        .all()
    )
    return render(
        request,
        "admin/payment_detail.html",
        {
            "p": payment,
            "deliveries": deliveries,
            "transactions": txns,
            "log": log,
            "base_url": get_settings().base_url,
        },
    )


@protected.get("/webhooks")
def webhook_list(request: Request, status: str | None = Query(default=None), db: Session = Depends(get_db)):
    stmt = select(WebhookDelivery).order_by(WebhookDelivery.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(WebhookDelivery.status == status)
    rows = db.execute(stmt).scalars().all()
    return render(request, "admin/webhooks.html", {"deliveries": rows, "status": status})


@protected.get("/webhooks/{delivery_id}")
def webhook_detail(request: Request, delivery_id: str, db: Session = Depends(get_db)):
    delivery = db.get(WebhookDelivery, delivery_id)
    if delivery is None:
        raise WebError(404, "Not found", "No such delivery.")
    log = (
        db.execute(select(AuditLog).where(AuditLog.target_id == delivery.id).order_by(AuditLog.created_at))
        .scalars()
        .all()
    )
    return render(request, "admin/webhook_detail.html", {"d": delivery, "log": log})


@protected.post("/webhooks/{delivery_id}/retry")
def webhook_retry(
    request: Request, delivery_id: str, csrf: str = Form(default=""), db: Session = Depends(get_db)
):
    check_csrf(request, csrf)
    delivery = db.get(WebhookDelivery, delivery_id)
    if delivery is None:
        raise WebError(404, "Not found", "No such delivery.")
    webhooks.retry_now(db, delivery)
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.webhook_retry",
        target_type="webhook_delivery",
        target_id=delivery.id,
    )
    db.commit()
    flash(request, "Delivery queued. The worker retries within a few seconds.", "success")
    return RedirectResponse(f"/admin/webhooks/{delivery.id}", status_code=303)


@protected.get("/audit")
def audit_log(request: Request, action: str | None = Query(default=None), db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(300)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    rows = db.execute(stmt).scalars().all()
    return render(request, "admin/audit.html", {"entries": rows, "action": action})
