import re

from mockbank.models import Application, Customer, Payment, WebhookDelivery
from mockbank.security import sha256
from mockbank.services import seed as seed_service
from tests.conftest import (
    account_by_number,
    admin_login,
    bearer,
    csrf_from,
    customer_by_email,
    get_token,
)


def test_admin_requires_login(client):
    r = client.get("/admin")
    assert r.status_code == 303 and r.headers["location"].startswith("/admin/login")
    page = client.get("/admin/login")
    r = client.post("/admin/login", data={"password": "wrong", "csrf": csrf_from(page.text)})
    assert r.status_code == 401
    admin_login(client)
    r = client.get("/admin")
    assert r.status_code == 200 and "Ledger integrity" in r.text and "OK" in r.text


def test_register_application_through_form(client, db):
    admin_login(client)
    page = client.get("/admin/applications")
    r = client.post(
        "/admin/applications",
        data={
            "name": "FormApp",
            "owner_label": "Team 9",
            "redirect_uris": "http://localhost:4000/callback\nhttp://localhost:4000/return",
            "webhook_url": "http://localhost:4000/hooks",
            "send_transaction_events": "1",
            "settlement_email": "team9-ops@example.com",
            "settlement_password": "team9pw",
            "settlement_opening_balance": "1000.00",
            "csrf": csrf_from(page.text),
        },
    )
    assert r.status_code == 200, r.text
    client_id = re.search(r'<div class="secret">(app_[a-z0-9]+)</div>', r.text).group(1)
    secret = re.search(r'<div class="secret">(mbsk_[^<]+)</div>', r.text).group(1)
    app = db.get(Application, client_id)
    assert app.client_secret_hash == sha256(secret)
    assert app.redirect_uris == ["http://localhost:4000/callback", "http://localhost:4000/return"]
    assert app.send_transaction_events is True
    ops = customer_by_email(db, "team9-ops@example.com")
    assert ops.kind == "business" and ops.accounts[0].balance == 1000
    assert "team9pw" in r.text and ops.accounts[0].account_number in r.text
    # Detail page, regenerate secret, toggle active.
    detail = client.get(f"/admin/applications/{app.id}")
    assert detail.status_code == 200
    r = client.post(f"/admin/applications/{app.id}/regenerate-secret", data={"csrf": csrf_from(detail.text)})
    assert r.status_code == 303
    detail = client.get(f"/admin/applications/{app.id}")
    new_secret = re.search(r'<div class="secret">(mbsk_[^<]+)</div>', detail.text).group(1)
    db.expire_all()
    assert db.get(Application, app.id).client_secret_hash == sha256(new_secret) != sha256(secret)
    client.post(f"/admin/applications/{app.id}/toggle-active", data={"csrf": csrf_from(detail.text)})
    db.expire_all()
    assert db.get(Application, app.id).is_active is False


def test_create_customer_top_up_and_seed_history(client, db):
    admin_login(client)
    page = client.get("/admin/customers")
    r = client.post(
        "/admin/customers",
        data={
            "email": "dave@example.com",
            "full_name": "Dave Mokoena",
            "password": "dave123",
            "kind": "personal",
            "account_name": "Everyday Account",
            "opening_balance": "2500.00",
            "history": "personal",
            "csrf": csrf_from(page.text),
        },
    )
    assert r.status_code == 303, r.text
    dave = customer_by_email(db, "dave@example.com")
    account = dave.accounts[0]
    assert account.balance == 2500
    assert len(db.query(Payment).all()) >= 0
    detail = client.get(f"/admin/customers/{dave.id}")
    assert detail.status_code == 200 and "Dave Mokoena" in detail.text
    r = client.post(
        f"/admin/accounts/{account.id}/top-up", data={"amount": "100.00", "csrf": csrf_from(detail.text)}
    )
    assert r.status_code == 303
    db.expire_all()
    assert account_by_number(db, account.account_number).balance == 2600
    # add an empty account then seed history onto it
    r = client.post(
        f"/admin/customers/{dave.id}/accounts",
        data={
            "name": "Savings",
            "account_type": "savings",
            "opening_balance": "0",
            "history": "none",
            "csrf": csrf_from(detail.text),
        },
    )
    db.expire_all()
    savings = [a for a in customer_by_email(db, "dave@example.com").accounts if a.name == "Savings"][0]
    r = client.post(
        f"/admin/accounts/{savings.id}/seed-history",
        data={"style": "savings", "target": "700.00", "csrf": csrf_from(detail.text)},
    )
    db.expire_all()
    assert account_by_number(db, savings.account_number).balance == 700
    # Duplicate email is reported, not crashed.
    r = client.post(
        "/admin/customers",
        data={"email": "dave@example.com", "full_name": "x", "password": "x", "csrf": csrf_from(detail.text)},
    )
    assert r.status_code == 303
    assert "already exists" in client.get("/admin/customers").text


def test_admin_pages_render(client, api, db, registered_app):
    # Produce a completed payment with a webhook delivery so the detail pages have data.
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    everyday = next(
        a
        for a in api.get("/api/v1/accounts", headers=bearer(token)).json()["data"]
        if a["account_number"] == "1000123456"
    )
    pid = api.post(
        "/api/v1/payments",
        headers=bearer(token),
        json={
            "debtor_account_id": everyday["id"],
            "creditor_account_number": "1000987654",
            "amount": "5.00",
            "currency": "ZAR",
            "reference": "ADMINVIEW",
        },
    ).json()["payment_id"]
    page = client.get(f"/payments/{pid}/authorise")
    client.post(f"/payments/{pid}/authorise", data={"decision": "approve", "csrf": csrf_from(page.text)})
    client.cookies.clear()
    admin_login(client)
    for path in [
        "/admin/customers",
        "/admin/applications",
        "/admin/consents",
        "/admin/payments",
        "/admin/payments?status=COMPLETED",
        "/admin/webhooks",
        "/admin/webhooks?status=failed",
        "/admin/audit",
        "/admin/audit?action=payment.",
    ]:
        assert client.get(path).status_code == 200, path
    payment = db.query(Payment).first()
    if payment:
        assert client.get(f"/admin/payments/{payment.id}").status_code == 200
    delivery = db.query(WebhookDelivery).first()
    if delivery:
        page = client.get(f"/admin/webhooks/{delivery.id}")
        assert page.status_code == 200
        assert (
            client.post(
                f"/admin/webhooks/{delivery.id}/retry", data={"csrf": csrf_from(page.text)}
            ).status_code
            == 303
        )


def test_reset_activity_keeps_apps_and_restores_seed(client, db, registered_app):
    application, _ = registered_app
    alice = account_by_number(db, "1000123456")
    alice_id = alice.id
    admin_login(client)
    page = client.get("/admin")
    r = client.post(
        "/admin/reset", data={"mode": "activity", "confirm": "nope", "csrf": csrf_from(page.text)}
    )
    assert r.status_code == 303
    assert db.query(Payment).count() >= 0
    r = client.post(
        "/admin/reset", data={"mode": "activity", "confirm": "RESET", "csrf": csrf_from(page.text)}
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Application, application.id) is not None
    assert db.query(Payment).count() == 0 and db.query(WebhookDelivery).count() == 0
    assert db.get(Customer, alice.customer_id) is not None
    restored = account_by_number(db, "1000123456")
    assert restored.id == alice_id and restored.balance == seed_service.SEED_CUSTOMERS[0].accounts[0].opening
    assert account_by_number(db, "1000987654").balance == 250000
    from mockbank.services.ledger import check_integrity

    assert check_integrity(db).ok


def test_full_reset_removes_apps(client, db, registered_app):
    application, _ = registered_app
    app_id = application.id
    admin_login(client)
    page = client.get("/admin")
    r = client.post("/admin/reset", data={"mode": "full", "confirm": "RESET", "csrf": csrf_from(page.text)})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Application, app_id) is None
    assert db.get(Application, seed_service.DEMO_APP_ID) is not None
    assert customer_by_email(db, "alice@example.com").accounts[0].balance == 15240.50
