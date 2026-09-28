"""GET and POST /oauth/authorize: the consent screen."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from mockbank.db import get_db
from mockbank.models import Customer
from mockbank.services import oauth
from mockbank.web.deps import LoginRequired, check_csrf, client_ip, optional_customer
from mockbank.web.templating import render

router = APIRouter(include_in_schema=False)


def _validate_or_respond(request: Request, db: Session, params: dict):
    try:
        return oauth.validate_authorize_request(db, **params), None
    except oauth.AuthorizeError as exc:
        if exc.redirect and params.get("redirect_uri"):
            url = oauth.build_redirect(
                params["redirect_uri"],
                {"error": exc.error, "error_description": exc.description, "state": params.get("state")},
            )
            return None, RedirectResponse(url, status_code=303)
        return None, render(
            request, "error.html",
            {"title": "Invalid authorization request", "message": exc.description, "status_code": 400,
             "detail": f"error={exc.error}"},
            status_code=400,
        )


@router.get("/oauth/authorize")
def authorize(
    request: Request,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    response_type: str | None = None,
    scope: str | None = None,
    state: str | None = None,
    customer: Customer | None = Depends(optional_customer),
    db: Session = Depends(get_db),
):
    params = dict(client_id=client_id, redirect_uri=redirect_uri, response_type=response_type,
                  scope=scope, state=state)
    req, response = _validate_or_respond(request, db, params)
    if response is not None:
        return response
    if customer is None:
        target = request.url.path + "?" + request.url.query
        raise LoginRequired(target)
    return render(request, "consent.html", {"req": req, "params": params, "scope_labels": oauth.SCOPES})


@router.post("/oauth/authorize")
def authorize_decision(
    request: Request,
    decision: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    response_type: str = Form(default="code"),
    scope: str = Form(...),
    state: str = Form(...),
    csrf: str = Form(default=""),
    customer: Customer | None = Depends(optional_customer),
    db: Session = Depends(get_db),
):
    check_csrf(request, csrf)
    params = dict(client_id=client_id, redirect_uri=redirect_uri, response_type=response_type,
                  scope=scope, state=state)
    req, response = _validate_or_respond(request, db, params)
    if response is not None:
        return response
    if customer is None:
        raise LoginRequired("/oauth/authorize?" + request.url.query)
    if decision != "approve":
        url = oauth.build_redirect(req.redirect_uri, {"error": "access_denied", "state": req.state})
        return RedirectResponse(url, status_code=303)
    _consent, code = oauth.grant_consent(db, customer=customer, req=req, ip=client_ip(request))
    db.commit()
    url = oauth.build_redirect(req.redirect_uri, {"code": code, "state": req.state})
    return RedirectResponse(url, status_code=303)
