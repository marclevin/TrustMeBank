"""Landing page, login and logout."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from mockbank.audit import audit
from mockbank.db import get_db
from mockbank.models import Customer
from mockbank.security import verify_password
from mockbank.web.deps import check_csrf, client_ip, ensure_csrf, optional_customer, safe_next
from mockbank.web.templating import flash, render

router = APIRouter(include_in_schema=False)


@router.get("/")
def home(request: Request, customer: Customer | None = Depends(optional_customer)):
    if customer:
        return RedirectResponse("/accounts", status_code=303)
    return render(request, "home.html")


@router.get("/login")
def login_form(
    request: Request, next: str | None = None, customer: Customer | None = Depends(optional_customer)
):
    if customer:
        return RedirectResponse(safe_next(next), status_code=303)
    ensure_csrf(request)
    return render(request, "login.html", {"next": safe_next(next), "error": None})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/accounts"),
    csrf: str = Form(default=""),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    customer = db.execute(
        select(Customer).where(Customer.email == email.strip().lower())
    ).scalar_one_or_none()
    if customer is None or not customer.can_login or not verify_password(password, customer.password_hash):
        audit(
            db,
            actor_type="customer",
            actor_id=None,
            action="login.failed",
            details={"email": email.strip().lower()},
            ip=client_ip(request),
        )
        db.commit()
        return render(
            request,
            "login.html",
            {"next": safe_next(next), "error": "Incorrect email or password.", "email": email},
            status_code=401,
        )
    request.session["customer_id"] = customer.id
    audit(db, actor_type="customer", actor_id=customer.id, action="login.success", ip=client_ip(request))
    db.commit()
    return RedirectResponse(safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form(default="")):
    check_csrf(request, csrf)
    request.session.pop("customer_id", None)
    flash(request, "You have been logged out.")
    return RedirectResponse("/login", status_code=303)
