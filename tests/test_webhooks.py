import json
from datetime import timedelta

import httpx

from mockbank import worker
from mockbank.models import Application, WebhookDelivery, utcnow
from mockbank.security import sign_webhook, verify_webhook_signature
from mockbank.services import webhooks
from mockbank.services.webhooks import MAX_ATTEMPTS, RETRY_SCHEDULE
from tests.conftest import REMITX_ACCOUNT_NUMBER, bearer, csrf_from, get_token


def test_signature_roundtrip():
    body = b'{"id":"evt_1","event":"payment.completed"}'
    header = sign_webhook("whsec_test", body, timestamp=1727517601)
    assert header.startswith("t=1727517601,v1=")
    assert not verify_webhook_signature("whsec_test", body, header)  # too old
    fresh = sign_webhook("whsec_test", body)
    assert verify_webhook_signature("whsec_test", body, fresh)
    assert not verify_webhook_signature("whsec_test", body + b" ", fresh)
    assert not verify_webhook_signature("whsec_test", body, "garbage")


def _pending(db, application):
    return webhooks.enqueue(
        db,
        application=application,
        event_type="payment.completed",
        data={"payment_id": "pay_x", "amount": "1.00"},
    )


def test_retry_schedule_and_give_up(db, registered_app):
    application, _ = registered_app
    delivery = _pending(db, application)
    db.commit()

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with httpx.Client(transport=httpx.MockTransport(failing)) as http:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            delivery.next_attempt_at = utcnow() - timedelta(seconds=1)
            db.commit()
            webhooks.deliver_due(db, http)
            db.expire_all()
            row = db.get(WebhookDelivery, delivery.id)
            assert row.attempts == attempt
            assert row.last_status_code == 500 and row.last_error == "HTTP 500"
            if attempt < MAX_ATTEMPTS:
                assert row.status == "pending"
                expected = row.last_attempt_at + timedelta(seconds=RETRY_SCHEDULE[attempt - 1])
                assert abs((row.next_attempt_at - expected).total_seconds()) < 1
            else:
                assert row.status == "failed"
    # Not due anymore: nothing happens.
    with httpx.Client(transport=httpx.MockTransport(failing)) as http:
        assert webhooks.deliver_due(db, http) == 0
    # Admin retry gives it one more go, which succeeds.
    webhooks.retry_now(db, row)
    db.commit()
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(204))) as http:
        assert webhooks.deliver_due(db, http) == 1
    db.expire_all()
    assert db.get(WebhookDelivery, delivery.id).status == "delivered"


def test_network_error_is_recorded(db, registered_app):
    application, _ = registered_app
    delivery = _pending(db, application)
    db.commit()

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with httpx.Client(transport=httpx.MockTransport(broken)) as http:
        webhooks.deliver_due(db, http)
    db.expire_all()
    row = db.get(WebhookDelivery, delivery.id)
    assert row.status == "pending" and row.last_status_code is None and "ConnectError" in row.last_error


def test_no_webhook_url_means_no_delivery(db):
    app = Application(
        name="silent",
        client_secret_hash="x",
        redirect_uris=["http://a/b"],
        webhook_secret="whsec_s",
        webhook_url=None,
    )
    db.add(app)
    db.flush()
    assert webhooks.enqueue(db, application=app, event_type="payment.completed", data={}) is None
    db.rollback()


def test_transaction_created_is_opt_in(client, api, db, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    everyday = next(
        a
        for a in api.get("/api/v1/accounts", headers=bearer(token)).json()["data"]
        if a["account_number"] == "1000123456"
    )

    def pay(reference):
        pid = api.post(
            "/api/v1/payments",
            headers=bearer(token),
            json={
                "debtor_account_id": everyday["id"],
                "creditor_account_number": REMITX_ACCOUNT_NUMBER,
                "amount": "1.00",
                "currency": "ZAR",
                "reference": reference,
            },
        ).json()["payment_id"]
        page = client.get(f"/payments/{pid}/authorise")
        client.post(f"/payments/{pid}/authorise", data={"decision": "approve", "csrf": csrf_from(page.text)})
        return pid

    pid = pay("OPT0")
    events = [d.event_type for d in db.query(WebhookDelivery).filter_by(application_id=application.id).all()]
    assert "transaction.created" not in events

    db.get(Application, application.id).send_transaction_events = True
    db.commit()
    pid = pay("OPT1")
    rows = (
        db.query(WebhookDelivery)
        .filter_by(application_id=application.id, event_type="transaction.created")
        .all()
    )
    assert len(rows) == 1  # only Alice's side; RemitX has not consented to this app
    data = json.loads(rows[0].payload)["data"]
    assert data["payment_id"] == pid and data["amount"] == "-1.00" and data["account_id"] == everyday["id"]


def test_worker_run_once_delivers(db, registered_app):
    application, _ = registered_app
    _pending(db, application)
    db.commit()
    seen = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(200))[1])
    ) as http:
        stats = worker.run_once(http)
    assert stats["webhooks_attempted"] >= 1 and seen
    assert seen[0].headers["User-Agent"].startswith("MockBank-Webhooks")
