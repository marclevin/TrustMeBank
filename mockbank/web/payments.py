"""Payment authorisation page: /payments/{id}/authorise"""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from mockbank.db import get_db
from mockbank.errors import WebError
from mockbank.models import Customer, Payment
from mockbank.services import payments as payment_service
from mockbank.services.oauth import build_redirect
from mockbank.web.deps import check_csrf, client_ip, current_customer
from mockbank.web.templating import render

router = APIRouter(include_in_schema=False)


def _load(db: Session, customer: Customer, payment_id: str) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise WebError(404, "Payment not found", "That payment does not exist.")
    if payment.debtor_account.customer_id != customer.id:
        raise WebError(403, "Not your payment", "This payment was requested from another customer's account.")
    return payment


@router.get("/payments/{payment_id}/authorise")
def authorise_page(
    request: Request,
    payment_id: str,
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
):
    payment = _load(db, customer, payment_id)
    return render(request, "payment_authorise.html", {"payment": payment})


@router.post("/payments/{payment_id}/authorise")
def authorise_decision(
    request: Request,
    payment_id: str,
    decision: str = Form(...),
    csrf: str = Form(default=""),
    customer: Customer = Depends(current_customer),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    payment = _load(db, customer, payment_id)
    if decision == "approve":
        payment = payment_service.approve_payment(db, payment, customer, ip=client_ip(request))
    else:
        payment = payment_service.reject_payment(db, payment, customer, ip=client_ip(request))
    db.refresh(payment)
    if payment.redirect_uri:
        url = build_redirect(payment.redirect_uri, {"payment_id": payment.id, "status": payment.status})
        return RedirectResponse(url, status_code=303)
    return render(request, "payment_result.html", {"payment": payment})
