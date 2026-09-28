"""The contract.

register app -> authorize -> exchange -> accounts -> payment -> approve -> ledger -> webhook
"""

import json
from decimal import Decimal

import httpx

from tests.conftest import (
    REMITX_ACCOUNT_NUMBER,
    account_by_number,
    bearer,
    csrf_from,
    get_token,
)
from trustmebank import worker
from trustmebank.models import Journal, Payment, Transaction, WebhookDelivery
from trustmebank.security import verify_webhook_signature


def test_full_lifecycle(client, api, db, registered_app):
    application, secret = registered_app

    # 1-3. Authorize and exchange the code for a token.
    token = get_token(client, api, application, secret)
    assert token["token_type"] == "Bearer"
    assert token["scope"] == "accounts balances transactions payments"
    assert token["access_token"].startswith("tmbat_")

    # 4. Retrieve accounts.
    accounts = api.get("/api/v1/accounts", headers=bearer(token)).json()["data"]
    everyday = next(a for a in accounts if a["account_number"] == "1000123456")
    assert everyday["balance"] == "15240.50" or Decimal(everyday["balance"]) > 0
    debtor_before = Decimal(everyday["balance"])
    creditor_before = account_by_number(db, REMITX_ACCOUNT_NUMBER).balance

    # 5. Initiate a payment.
    created = api.post(
        "/api/v1/payments",
        headers={**bearer(token), "Idempotency-Key": "lifecycle-1"},
        json={
            "debtor_account_id": everyday["id"],
            "creditor_account_number": REMITX_ACCOUNT_NUMBER,
            "amount": "500.00",
            "currency": "ZAR",
            "reference": "REM-92831",
            "redirect_uri": "http://localhost:5000/payments/return",
        },
    )
    assert created.status_code == 201, created.text
    payment = created.json()
    assert payment["status"] == "AWAITING_AUTHORISATION"
    assert payment["authorisation_url"] == f"http://testserver/payments/{payment['payment_id']}/authorise"
    assert account_by_number(db, "1000123456").balance == debtor_before  # nothing moved yet

    # 6-7. Customer approves on the bank's page.
    page = client.get(f"/payments/{payment['payment_id']}/authorise")
    assert page.status_code == 200
    assert "Test App wants to make the following payment" in page.text
    assert "R500.00" in page.text
    decided = client.post(
        f"/payments/{payment['payment_id']}/authorise",
        data={"decision": "approve", "csrf": csrf_from(page.text)},
    )
    assert decided.status_code == 303
    assert decided.headers["location"] == (
        f"http://localhost:5000/payments/return?payment_id={payment['payment_id']}&status=COMPLETED"
    )

    # Ledger updated atomically.
    db.expire_all()
    assert account_by_number(db, "1000123456").balance == debtor_before - Decimal("500.00")
    assert account_by_number(db, REMITX_ACCOUNT_NUMBER).balance == creditor_before + Decimal("500.00")
    row = db.get(Payment, payment["payment_id"])
    assert row.status == "COMPLETED" and row.journal_id
    journal = db.get(Journal, row.journal_id)
    assert journal.payment_id == row.id
    legs = db.query(Transaction).filter_by(journal_id=journal.id).all()
    assert sum(t.amount for t in legs) == 0 and len(legs) == 2

    # The TPP sees the completed payment and the transaction on the statement.
    fetched = api.get(f"/api/v1/payments/{payment['payment_id']}", headers=bearer(token)).json()
    assert fetched["status"] == "COMPLETED" and fetched["journal_id"] == journal.id
    txns = api.get(f"/api/v1/accounts/{everyday['id']}/transactions", headers=bearer(token)).json()
    top = txns["data"][0]
    assert top["amount"] == "-500.00" and top["payment_id"] == row.id and top["reference"] == "REM-92831"
    assert top["counterparty"]["account_number"] == REMITX_ACCOUNT_NUMBER

    # 8. Webhook was queued in the same transaction and is delivered with a valid signature.
    delivery = db.query(WebhookDelivery).filter_by(payment_id=row.id, event_type="payment.completed").one()
    assert delivery.status == "pending"

    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        stats = worker.run_once(http)
    assert stats["webhooks_attempted"] >= 1
    hit = next(r for r in received if json.loads(r.content)["data"]["payment_id"] == row.id)
    assert hit.url == "http://tpp.test/webhooks/trustmebank"
    assert hit.headers["X-TrustMeBank-Event"] == "payment.completed"
    assert hit.headers["X-TrustMeBank-Delivery-Id"] == delivery.id
    assert verify_webhook_signature(
        application.webhook_secret, hit.content, hit.headers["X-TrustMeBank-Signature"]
    )
    assert not verify_webhook_signature("wrong-secret", hit.content, hit.headers["X-TrustMeBank-Signature"])
    body = json.loads(hit.content)
    assert body["id"] == delivery.id and body["event"] == "payment.completed"
    assert body["data"]["amount"] == "500.00" and body["data"]["reference"] == "REM-92831"
    assert body["data"]["status"] == "COMPLETED" and body["data"]["journal_id"] == journal.id

    db.expire_all()
    assert db.get(WebhookDelivery, delivery.id).status == "delivered"

    # 9. Idempotent replay returns the same payment.
    replay = api.post(
        "/api/v1/payments",
        headers={**bearer(token), "Idempotency-Key": "lifecycle-1"},
        json={
            "debtor_account_id": everyday["id"],
            "creditor_account_number": REMITX_ACCOUNT_NUMBER,
            "amount": "500.00",
            "currency": "ZAR",
            "reference": "REM-92831",
            "redirect_uri": "http://localhost:5000/payments/return",
        },
    )
    assert replay.status_code == 200 and replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["payment_id"] == row.id
