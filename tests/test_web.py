from decimal import Decimal

from tests.conftest import ALICE, BOB, account_by_number, csrf_from, login


def test_login_logout_and_bad_password(client):
    r = client.get("/accounts")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Faccounts"
    page = client.get("/login")
    bad = client.post("/login", data={"email": ALICE[0], "password": "nope", "csrf": csrf_from(page.text)})
    assert bad.status_code == 401 and "Incorrect email or password" in bad.text
    login(client)
    r = client.get("/accounts")
    assert r.status_code == 200 and "Everyday Account" in r.text and "R" in r.text
    r = client.post("/logout", data={"csrf": csrf_from(client.get("/accounts").text)})
    assert r.status_code == 303
    assert client.get("/accounts").status_code == 303


def test_system_customers_cannot_log_in(client):
    page = client.get("/login")
    r = client.post(
        "/login",
        data={"email": "treasury@mockbank.internal", "password": "anything", "csrf": csrf_from(page.text)},
    )
    assert r.status_code == 401


def test_next_redirect_refuses_external_urls(client):
    page = client.get("/login?next=https://evil.test/phish")
    r = client.post(
        "/login",
        data={
            "email": ALICE[0],
            "password": ALICE[1],
            "csrf": csrf_from(page.text),
            "next": "https://evil.test/phish",
        },
    )
    assert r.headers["location"] == "/accounts"
    client.cookies.clear()
    page = client.get("/login")
    r = client.post(
        "/login",
        data={"email": ALICE[0], "password": ALICE[1], "csrf": csrf_from(page.text), "next": "//evil.test"},
    )
    assert r.headers["location"] == "/accounts"


def test_account_detail_and_other_customers_account(client, db):
    login(client)
    alice = account_by_number(db, "1000123456")
    bob = account_by_number(db, "1000234567")
    r = client.get(f"/accounts/{alice.id}")
    assert r.status_code == 200 and "ACME Corp Salary" in r.text
    assert client.get(f"/accounts/{bob.id}").status_code == 404


def test_transfer_via_ui(client, db):
    login(client)
    alice = account_by_number(db, "1000123456")
    remitx = account_by_number(db, "1000987654")
    a0, r0 = alice.balance, remitx.balance
    page = client.get("/transfer")
    r = client.post(
        "/transfer",
        data={
            "from_account_id": alice.id,
            "to_account_number": "1000987654",
            "amount": "250.00",
            "reference": "DEP-42",
            "csrf": csrf_from(page.text),
        },
    )
    assert r.status_code == 303, r.text
    db.expire_all()
    assert account_by_number(db, "1000123456").balance == a0 - Decimal("250.00")
    assert account_by_number(db, "1000987654").balance == r0 + Decimal("250.00")
    # Recipient sees the reference and the sender's name.
    client.cookies.clear()
    login(client, "remitx@example.com", "remitx123")
    text = client.get(f"/accounts/{remitx.id}").text
    assert "DEP-42" in text and "Alice Ndlovu" in text and "Transfer from Alice Ndlovu" in text


def test_transfer_errors(client, db):
    login(client, *BOB)
    bob = account_by_number(db, "1000234567")
    page = client.get("/transfer")
    csrf = csrf_from(page.text)
    r = client.post(
        "/transfer",
        data={"from_account_id": bob.id, "to_account_number": "1234567890", "amount": "1.00", "csrf": csrf},
    )
    assert r.status_code == 422 and "does not exist" in r.text
    r = client.post(
        "/transfer",
        data={
            "from_account_id": bob.id,
            "to_account_number": "1000123456",
            "amount": "999999.00",
            "csrf": csrf,
        },
    )
    assert r.status_code == 422 and "Insufficient funds" in r.text
    r = client.post(
        "/transfer",
        data={"from_account_id": bob.id, "to_account_number": "1000123456", "amount": "1.999", "csrf": csrf},
    )
    assert r.status_code == 422 and "two decimal" in r.text
    r = client.post(
        "/transfer",
        data={"from_account_id": bob.id, "to_account_number": "1000000000", "amount": "1.00", "csrf": csrf},
    )
    assert r.status_code == 422  # system accounts are not valid targets


def test_home_and_guides(client):
    assert client.get("/").status_code == 200
    assert client.get("/guide").status_code == 200
    r = client.get("/guide/getting-started")
    assert r.status_code == 200
    assert client.get("/guide/../etc").status_code in (404, 400)
    assert client.get("/guide/nope").status_code == 404
    assert client.get("/nonexistent").status_code == 404
