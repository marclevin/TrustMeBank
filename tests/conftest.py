"""Test fixtures. Tests run against a real PostgreSQL database (TEST_DATABASE_URL)."""

import os
import re
from urllib.parse import parse_qs, urlparse

os.environ.setdefault(
    "TEST_DATABASE_URL", "postgresql+psycopg://mockbank:mockbank@localhost:5432/mockbank_test"
)
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["WEBHOOK_WORKER_ENABLED"] = "false"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_PASSWORD"] = "test-admin"
os.environ["PAYMENT_PROCESSING_DELAY_SECONDS"] = "0"
os.environ["RATE_LIMIT_API_PER_MINUTE"] = "0"
os.environ["RATE_LIMIT_LOGIN_PER_MINUTE"] = "0"
os.environ["RATE_LIMIT_TOKEN_PER_MINUTE"] = "0"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from mockbank.config import get_settings  # noqa: E402
from mockbank.db import Base, get_engine, new_session  # noqa: E402
from mockbank.main import app  # noqa: E402
from mockbank.models import Account, Application, Customer  # noqa: E402
from mockbank.services import admin as admin_service  # noqa: E402
from mockbank.services import seed as seed_service  # noqa: E402
from mockbank.services.oauth import build_redirect  # noqa: E402

ALICE = ("alice@example.com", "alice123")
BOB = ("bob@example.com", "bob123")
REMITX_ACCOUNT_NUMBER = "1000987654"
CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')


@pytest.fixture(scope="session", autouse=True)
def database():
    get_settings.cache_clear()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    with new_session() as db:
        seed_service.seed(db)
    yield engine


@pytest.fixture
def db():
    session = new_session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client():
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def api():
    """A second client for API calls, without the browser session cookie."""
    with TestClient(app, follow_redirects=False) as c:
        yield c


def csrf_from(html: str) -> str:
    match = CSRF_RE.search(html)
    assert match, "no csrf token in page"
    return match.group(1)


def login(client: TestClient, email: str = ALICE[0], password: str = ALICE[1]) -> None:
    client.cookies.clear()  # start a fresh browser session
    page = client.get("/login")
    response = client.post(
        "/login", data={"email": email, "password": password, "csrf": csrf_from(page.text)}
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"].startswith("/"), response.headers["location"]


def admin_login(client: TestClient) -> None:
    page = client.get("/admin/login")
    response = client.post("/admin/login", data={"password": "test-admin", "csrf": csrf_from(page.text)})
    assert response.status_code == 303, response.text


@pytest.fixture
def registered_app(db):
    """A fresh application with a webhook URL. Returns (Application, client_secret)."""
    result = admin_service.register_application(
        db,
        name="Test App",
        owner_label="pytest",
        redirect_uris=["http://localhost:5000/callback", "http://localhost:5000/payments/return"],
        webhook_url="http://tpp.test/webhooks/mockbank",
    )
    db.commit()
    db.refresh(result.application)
    return result.application, result.client_secret


def authorize(
    client: TestClient,
    application: Application,
    scopes: str = "accounts balances transactions payments",
    state: str = "xyz123",
    redirect_uri: str = "http://localhost:5000/callback",
    decision: str = "approve",
):
    """Drive the consent screen as a logged-in customer. Returns the redirect Location."""
    url = build_redirect(
        "/oauth/authorize",
        {
            "response_type": "code",
            "client_id": application.id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
        },
    )
    page = client.get(url)
    assert page.status_code == 200, page.text
    response = client.post(
        "/oauth/authorize",
        data={
            "decision": decision,
            "client_id": application.id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": state,
            "csrf": csrf_from(page.text),
        },
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def exchange(
    api: TestClient,
    application: Application,
    secret: str,
    code: str,
    redirect_uri: str = "http://localhost:5000/callback",
) -> dict:
    response = api.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": application.id,
            "client_secret": secret,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def get_token(
    client: TestClient,
    api: TestClient,
    application: Application,
    secret: str,
    scopes: str = "accounts balances transactions payments",
    email: str = ALICE[0],
    password: str = ALICE[1],
) -> dict:
    """Log in, consent and exchange. Returns the token response."""
    login(client, email, password)
    location = authorize(client, application, scopes=scopes)
    params = query_of(location)
    assert params["state"] == "xyz123"
    return exchange(api, application, secret, params["code"])


def bearer(token: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['access_token']}"}


def account_by_number(db, number: str) -> Account:
    return db.query(Account).filter_by(account_number=number).one()


def customer_by_email(db, email: str) -> Customer:
    return db.query(Customer).filter_by(email=email).one()
