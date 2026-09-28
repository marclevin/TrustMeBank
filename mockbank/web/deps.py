"""Session helpers for the HTML routers: current customer, admin flag, CSRF, safe redirects."""

import secrets
from urllib.parse import quote

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from mockbank.db import get_db
from mockbank.errors import WebError
from mockbank.models import Customer


class LoginRequired(Exception):
    def __init__(self, next_url: str) -> None:
        self.next_url = next_url


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf"] = token
    return token


def check_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        raise WebError(403, "Form expired", "The form token was missing or invalid. Go back and try again.")


def safe_next(value: str | None, default: str = "/accounts") -> str:
    """Only allow relative paths so login cannot be used as an open redirector."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return default


def optional_customer(request: Request, db: Session = Depends(get_db)) -> Customer | None:
    customer_id = request.session.get("customer_id")
    customer = db.get(Customer, customer_id) if customer_id else None
    if customer is not None and not customer.can_login:
        customer = None
        request.session.pop("customer_id", None)
    request.state.customer = customer
    ensure_csrf(request)
    return customer


def current_customer(request: Request, customer: Customer | None = Depends(optional_customer)) -> Customer:
    if customer is None:
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        raise LoginRequired(target)
    return customer


def require_admin(request: Request) -> None:
    ensure_csrf(request)
    request.state.customer = None
    if not request.session.get("admin"):
        raise LoginRequired("/admin/login?next=" + quote(request.url.path, safe=""))


def login_redirect(next_url: str) -> RedirectResponse:
    return RedirectResponse(url=f"/login?next={quote(next_url, safe='')}", status_code=303)


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
