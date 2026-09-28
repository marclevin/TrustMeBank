"""Admin operations that are more than a single query."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mockbank.audit import audit
from mockbank.ids import new_account_number
from mockbank.models import Account, Application, Customer
from mockbank.security import hash_password, new_secret, sha256
from mockbank.services import seed as seed_service


def parse_redirect_uris(raw: str) -> list[str]:
    uris = []
    for line in raw.replace(",", "\n").splitlines():
        uri = line.strip()
        if uri and uri not in uris:
            uris.append(uri)
    return uris


def unique_account_number(db: Session) -> str:
    while True:
        number = new_account_number()
        if db.execute(select(Account.id).where(Account.account_number == number)).first() is None:
            return number


def create_customer(
    db: Session, *, email: str, full_name: str, password: str, kind: str = "personal"
) -> Customer:
    email = email.strip().lower()
    if db.execute(select(Customer.id).where(Customer.email == email)).first() is not None:
        raise ValueError(f"A customer with email {email} already exists.")
    customer = Customer(
        email=email, full_name=full_name.strip(), password_hash=hash_password(password), kind=kind
    )
    db.add(customer)
    db.flush()
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.customer_created",
        target_type="customer",
        target_id=customer.id,
        details={"email": email},
    )
    return customer


def create_account(
    db: Session,
    *,
    customer: Customer,
    name: str,
    account_type: str = "current",
    opening_balance: Decimal | None = None,
    history: str = "none",
) -> Account:
    account = Account(
        customer_id=customer.id,
        name=name.strip(),
        account_number=unique_account_number(db),
        account_type=account_type,
    )
    db.add(account)
    db.flush()
    if opening_balance and opening_balance > 0:
        if history != "none":
            seed_service.seed_history(db, account, history, opening_balance)
        else:
            seed_service.top_up(db, account, opening_balance, "Opening balance")
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.account_created",
        target_type="account",
        target_id=account.id,
        details={"customer_id": customer.id, "opening_balance": str(opening_balance or 0)},
    )
    return account


@dataclass
class RegisteredApplication:
    application: Application
    client_secret: str
    settlement_customer: Customer | None = None
    settlement_account: Account | None = None
    settlement_password: str | None = None


def register_application(
    db: Session,
    *,
    name: str,
    owner_label: str,
    redirect_uris: list[str],
    webhook_url: str | None,
    send_transaction_events: bool = False,
    settlement_email: str | None = None,
    settlement_password: str | None = None,
    settlement_opening_balance: Decimal | None = None,
) -> RegisteredApplication:
    secret = new_secret("mbsk_", 24)
    app = Application(
        name=name.strip(),
        owner_label=owner_label.strip(),
        client_secret_hash=sha256(secret),
        redirect_uris=redirect_uris,
        webhook_url=(webhook_url or "").strip() or None,
        webhook_secret=new_secret("whsec_", 24),
        send_transaction_events=send_transaction_events,
    )
    db.add(app)
    db.flush()
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.application_registered",
        target_type="application",
        target_id=app.id,
        details={"name": app.name},
    )
    result = RegisteredApplication(application=app, client_secret=secret)
    if settlement_email:
        password = settlement_password or new_secret("", 9)
        customer = create_customer(
            db,
            email=settlement_email,
            full_name=f"{name.strip()} (Pty) Ltd",
            password=password,
            kind="business",
        )
        account = create_account(
            db,
            customer=customer,
            name=f"{name.strip()} Settlement Account",
            account_type="business",
            opening_balance=settlement_opening_balance or Decimal("0.00"),
            history="none",
        )
        result.settlement_customer = customer
        result.settlement_account = account
        result.settlement_password = password
    return result


def regenerate_client_secret(db: Session, app: Application) -> str:
    secret = new_secret("mbsk_", 24)
    app.client_secret_hash = sha256(secret)
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.client_secret_regenerated",
        target_type="application",
        target_id=app.id,
    )
    return secret


def rotate_webhook_secret(db: Session, app: Application) -> str:
    app.webhook_secret = new_secret("whsec_", 24)
    audit(
        db,
        actor_type="admin",
        actor_id="admin",
        action="admin.webhook_secret_rotated",
        target_type="application",
        target_id=app.id,
    )
    return app.webhook_secret
