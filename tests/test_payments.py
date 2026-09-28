from decimal import Decimal

import pytest

from tests.conftest import (
    BOB,
    REMITX_ACCOUNT_NUMBER,
    account_by_number,
    bearer,
    csrf_from,
    get_token,
)
from trustmebank.config import get_settings
from trustmebank.models import Payment, WebhookDelivery
from trustmebank.services import payments as payment_service


def _create(api, token, account_id, **overrides):
    body = {
        "debtor_account_id": account_id,
        "creditor_account_number": REMITX_ACCOUNT_NUMBER,
        "amount": "50.00",
        "currency": "ZAR",
        "reference": "TEST",
    }
    body.update(overrides)
    headers = bearer(token)
    key = overrides.pop("_key", None)
    if key:
        headers["Idempotency-Key"] = key
    return api.post("/api/v1/payments", headers=headers, json=body)


@pytest.fixture
def alice_session(client, api, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    everyday = next(
        a
        for a in api.get("/api/v1/accounts", headers=bearer(token)).json()["data"]
        if a["account_number"] == "1000123456"
    )
    return application, token, everyday


def test_validation(api, alice_session):
    _, token, everyday = alice_session
    r = _create(api, token, everyday["id"], amount=50.0)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = _create(api, token, everyday["id"], amount="1.234")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_amount"
    r = _create(api, token, everyday["id"], amount="-5")
    assert r.status_code == 422
    r = _create(api, token, everyday["id"], currency="USD")
    assert r.status_code == 422
    r = _create(api, token, everyday["id"], creditor_account_number="9999999999")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_creditor"
    r = _create(api, token, everyday["id"], creditor_account_number="1000000000")  # treasury
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_creditor"
    r = _create(api, token, everyday["id"], creditor_account_number="1000123456")  # self
    assert r.status_code == 422
    r = _create(api, token, "acc_doesnotexist")
    assert r.status_code == 404
    r = _create(api, token, everyday["id"], redirect_uri="http://evil.test/return")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_redirect_uri"
    r = _create(api, token, everyday["id"], reference="x" * 36)
    assert r.status_code == 422


def test_idempotency_conflict(api, alice_session):
    _, token, everyday = alice_session
    first = _create(api, token, everyday["id"], _key="k-conflict", reference="A")
    assert first.status_code == 201
    other = _create(api, token, everyday["id"], _key="k-conflict", reference="B")
    assert other.status_code == 409 and other.json()["error"]["code"] == "idempotency_key_reused"
    same = _create(api, token, everyday["id"], _key="k-conflict", reference="A")
    assert same.status_code == 200 and same.json()["payment_id"] == first.json()["payment_id"]


def test_reject(client, api, db, alice_session):
    application, token, everyday = alice_session
    pid = _create(api, token, everyday["id"], reference="REJ").json()["payment_id"]
    before = account_by_number(db, "1000123456").balance
    page = client.get(f"/payments/{pid}/authorise")
    r = client.post(f"/payments/{pid}/authorise", data={"decision": "reject", "csrf": csrf_from(page.text)})
    assert r.status_code == 200 and "REJECTED" in r.text  # no redirect_uri -> result page
    db.expire_all()
    assert db.get(Payment, pid).status == "REJECTED"
    assert account_by_number(db, "1000123456").balance == before
    assert db.query(WebhookDelivery).filter_by(payment_id=pid, event_type="payment.rejected").count() == 1
    # A second decision changes nothing.
    page = client.get(f"/payments/{pid}/authorise")
    assert "already been handled" in page.text
    client.post(f"/payments/{pid}/authorise", data={"decision": "approve", "csrf": csrf_from(page.text)})
    db.expire_all()
    assert db.get(Payment, pid).status == "REJECTED"


def test_insufficient_funds_fails_payment(client, api, db, alice_session):
    _, token, everyday = alice_session
    huge = str(Decimal(everyday["balance"]) + 1)
    pid = _create(api, token, everyday["id"], amount=huge, reference="BIG").json()["payment_id"]
    page = client.get(f"/payments/{pid}/authorise")
    r = client.post(f"/payments/{pid}/authorise", data={"decision": "approve", "csrf": csrf_from(page.text)})
    assert r.status_code == 200 and "FAILED" in r.text
    db.expire_all()
    row = db.get(Payment, pid)
    assert row.status == "FAILED" and row.failure_reason == "insufficient_funds" and row.journal_id is None
    assert db.query(WebhookDelivery).filter_by(payment_id=pid, event_type="payment.failed").count() == 1
    assert account_by_number(db, "1000123456").balance == Decimal(everyday["balance"])


def test_other_customer_cannot_see_or_approve(client, api, db, alice_session):
    application, token, everyday = alice_session
    pid = _create(api, token, everyday["id"], reference="OTHER").json()["payment_id"]
    client.cookies.clear()
    from tests.conftest import login

    login(client, *BOB)
    page = client.get(f"/payments/{pid}/authorise")
    assert page.status_code == 403
    r = client.post(
        f"/payments/{pid}/authorise",
        data={"decision": "approve", "csrf": csrf_from(client.get("/accounts").text)},
    )
    assert r.status_code == 403
    assert db.get(Payment, pid).status == "AWAITING_AUTHORISATION"


def test_anonymous_cannot_approve(client, api, db, alice_session):
    _, token, everyday = alice_session
    pid = _create(api, token, everyday["id"], reference="ANON").json()["payment_id"]
    client.cookies.clear()
    r = client.get(f"/payments/{pid}/authorise")
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    r = client.post(f"/payments/{pid}/authorise", data={"decision": "approve", "csrf": "x"})
    assert r.status_code == 303
    assert db.get(Payment, pid).status == "AWAITING_AUTHORISATION"


def test_csrf_required(client, api, db, alice_session):
    _, token, everyday = alice_session
    pid = _create(api, token, everyday["id"], reference="CSRF").json()["payment_id"]
    r = client.post(f"/payments/{pid}/authorise", data={"decision": "approve", "csrf": "wrong"})
    assert r.status_code == 403
    assert db.get(Payment, pid).status == "AWAITING_AUTHORISATION"


def test_settle_twice_is_noop(client, api, db, alice_session):
    _, token, everyday = alice_session
    pid = _create(api, token, everyday["id"], reference="TWICE").json()["payment_id"]
    page = client.get(f"/payments/{pid}/authorise")
    client.post(f"/payments/{pid}/authorise", data={"decision": "approve", "csrf": csrf_from(page.text)})
    db.expire_all()
    after = account_by_number(db, "1000123456").balance
    payment_service.settle_payment(db, pid)
    db.expire_all()
    assert account_by_number(db, "1000123456").balance == after
    assert db.query(WebhookDelivery).filter_by(payment_id=pid).count() == 1


def test_delayed_processing(client, api, db, alice_session, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "payment_processing_delay_seconds", 60)
    _, token, everyday = alice_session
    pid = _create(api, token, everyday["id"], reference="DELAY").json()["payment_id"]
    page = client.get(f"/payments/{pid}/authorise")
    r = client.post(f"/payments/{pid}/authorise", data={"decision": "approve", "csrf": csrf_from(page.text)})
    assert "PROCESSING" in r.text
    db.expire_all()
    row = db.get(Payment, pid)
    assert row.status == "PROCESSING" and row.journal_id is None
    assert api.get(f"/api/v1/payments/{pid}", headers=bearer(token)).json()["status"] == "PROCESSING"
    assert payment_service.settle_due(db) == 0  # not yet due
    row = db.get(Payment, pid)
    row.process_after = row.authorised_at
    db.commit()
    assert payment_service.settle_due(db) == 1
    db.expire_all()
    assert db.get(Payment, pid).status == "COMPLETED"


def test_list_payments_and_isolation(client, api, alice_session, registered_app):
    application, token, everyday = alice_session
    for i in range(3):
        _create(api, token, everyday["id"], reference=f"LIST{i}")
    r = api.get("/api/v1/payments", headers=bearer(token), params={"limit": 2}).json()
    assert len(r["data"]) == 2 and r["has_more"]
    r2 = api.get(
        "/api/v1/payments", headers=bearer(token), params={"limit": 2, "cursor": r["next_cursor"]}
    ).json()
    assert r2["data"] and not {p["payment_id"] for p in r2["data"]} & {p["payment_id"] for p in r["data"]}
    awaiting = api.get(
        "/api/v1/payments", headers=bearer(token), params={"status": "awaiting_authorisation"}
    ).json()
    assert all(p["status"] == "AWAITING_AUTHORISATION" for p in awaiting["data"])
    # Bob cannot read Alice's payment through the same application.
    pid = r["data"][0]["payment_id"]
    client.cookies.clear()
    bob_token = get_token(client, api, application, registered_app[1], email=BOB[0], password=BOB[1])
    assert api.get(f"/api/v1/payments/{pid}", headers=bearer(bob_token)).status_code == 404
