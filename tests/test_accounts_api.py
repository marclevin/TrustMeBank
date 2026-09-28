from datetime import date, timedelta

from mockbank.models import utcnow
from tests.conftest import BOB, bearer, get_token


def test_scope_enforcement(client, api, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret, scopes="accounts")
    accounts = api.get("/api/v1/accounts", headers=bearer(token)).json()["data"]
    assert accounts and "balance" not in accounts[0]
    acc = accounts[0]["id"]
    r = api.get(f"/api/v1/accounts/{acc}/balance", headers=bearer(token))
    assert r.status_code == 403 and r.json()["error"]["code"] == "insufficient_scope"
    assert r.json()["error"]["details"]["required_scope"] == "balances"
    assert api.get(f"/api/v1/accounts/{acc}/transactions", headers=bearer(token)).status_code == 403
    assert api.post("/api/v1/payments", headers=bearer(token), json={}).status_code == 403


def test_balance_included_with_balances_scope(client, api, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret, scopes="accounts balances")
    accounts = api.get("/api/v1/accounts", headers=bearer(token)).json()["data"]
    assert all("balance" in a for a in accounts)
    one = api.get(f"/api/v1/accounts/{accounts[0]['id']}", headers=bearer(token)).json()
    assert one["balance"] == accounts[0]["balance"]
    bal = api.get(f"/api/v1/accounts/{accounts[0]['id']}/balance", headers=bearer(token)).json()
    assert bal["balance"] == one["balance"] and bal["currency"] == "ZAR"


def test_other_customers_account_is_404(client, api, registered_app):
    application, secret = registered_app
    alice_token = get_token(client, api, application, secret)
    alice_accounts = api.get("/api/v1/accounts", headers=bearer(alice_token)).json()["data"]
    client.post("/logout")
    client.cookies.clear()
    bob_token = get_token(client, api, application, secret, email=BOB[0], password=BOB[1])
    r = api.get(f"/api/v1/accounts/{alice_accounts[0]['id']}", headers=bearer(bob_token))
    assert r.status_code == 404
    r = api.get(f"/api/v1/accounts/{alice_accounts[0]['id']}/transactions", headers=bearer(bob_token))
    assert r.status_code == 404


def test_pagination_walks_every_transaction_once(client, api, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    everyday = next(
        a
        for a in api.get("/api/v1/accounts", headers=bearer(token)).json()["data"]
        if a["account_number"] == "1000123456"
    )
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 7}
        if cursor:
            params["cursor"] = cursor
        page = api.get(
            f"/api/v1/accounts/{everyday['id']}/transactions", headers=bearer(token), params=params
        ).json()
        pages += 1
        seen.extend(t["id"] for t in page["data"])
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
    assert pages > 3
    assert len(seen) == len(set(seen))
    everything = api.get(
        f"/api/v1/accounts/{everyday['id']}/transactions", headers=bearer(token), params={"limit": 200}
    ).json()
    assert [t["id"] for t in everything["data"]] == seen
    first = everything["data"][0]
    assert set(first) >= {
        "id",
        "type",
        "amount",
        "description",
        "counterparty",
        "balance_after",
        "booked_at",
        "journal_id",
    }


def test_date_filters(client, api, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    everyday = next(
        a
        for a in api.get("/api/v1/accounts", headers=bearer(token)).json()["data"]
        if a["account_number"] == "1000123456"
    )
    today = utcnow().date()
    week_ago = today - timedelta(days=7)
    r = api.get(
        f"/api/v1/accounts/{everyday['id']}/transactions",
        headers=bearer(token),
        params={"from_date": week_ago.isoformat(), "to_date": today.isoformat(), "limit": 200},
    ).json()
    assert r["data"]
    for t in r["data"]:
        booked = date.fromisoformat(t["booked_at"][:10])
        assert week_ago <= booked <= today
    r = api.get(
        f"/api/v1/accounts/{everyday['id']}/transactions",
        headers=bearer(token),
        params={"to_date": "2001-01-01"},
    ).json()
    assert r["data"] == [] and r["has_more"] is False


def test_bad_cursor_and_validation_errors(client, api, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    acc = api.get("/api/v1/accounts", headers=bearer(token)).json()["data"][0]["id"]
    r = api.get(f"/api/v1/accounts/{acc}/transactions", headers=bearer(token), params={"cursor": "!!"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    r = api.get(f"/api/v1/accounts/{acc}/transactions", headers=bearer(token), params={"limit": 0})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    assert r.json()["error"]["details"]["fields"][0]["field"] == "query.limit"
