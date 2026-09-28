"""Seed data and reset. See SPEC section 3.2.

Everything here goes through the ledger, so seeded balances always reconcile.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from mockbank.audit import audit
from mockbank.config import get_settings
from mockbank.models import (
    Account,
    Application,
    AuditLog,
    AuthorizationCode,
    Consent,
    Customer,
    Journal,
    Payment,
    Token,
    Transaction,
    WebhookDelivery,
    utcnow,
)
from mockbank.security import hash_password, sha256
from mockbank.services.ledger import post_transfer

TREASURY_ACCOUNT_NUMBER = "1000000000"
CLEARING_ACCOUNT_NUMBER = "1000000001"

DEMO_APP_ID = "app_remitx_demo"
DEMO_APP_SECRET = "mbsk_remitx_demo_secret"
DEMO_APP_WEBHOOK_SECRET = "whsec_remitx_demo_secret"


@dataclass
class SeedAccount:
    name: str
    number: str
    account_type: str
    opening: Decimal
    history: str  # "personal", "savings", "business" or "none"


@dataclass
class SeedCustomer:
    email: str
    full_name: str
    password: str
    kind: str
    accounts: list[SeedAccount]


SEED_CUSTOMERS: list[SeedCustomer] = [
    SeedCustomer(
        "alice@example.com",
        "Alice Ndlovu",
        "alice123",
        "personal",
        [
            SeedAccount("Everyday Account", "1000123456", "current", Decimal("15240.50"), "personal"),
            SeedAccount("Savings Account", "1000123457", "savings", Decimal("42000.00"), "savings"),
        ],
    ),
    SeedCustomer(
        "bob@example.com",
        "Bob van der Merwe",
        "bob123",
        "personal",
        [SeedAccount("Everyday Account", "1000234567", "current", Decimal("3870.25"), "personal")],
    ),
    SeedCustomer(
        "carol@example.com",
        "Carol Pillay",
        "carol123",
        "personal",
        [SeedAccount("Everyday Account", "1000345678", "current", Decimal("980.00"), "personal")],
    ),
    SeedCustomer(
        "remitx@example.com",
        "RemitX (Pty) Ltd",
        "remitx123",
        "business",
        [
            SeedAccount(
                "RemitX Settlement Account", "1000987654", "business", Decimal("250000.00"), "business"
            )
        ],
    ),
]

MERCHANTS = [
    "Woolworths Food",
    "Checkers Sixty60",
    "Pick n Pay",
    "Uber",
    "Vodacom Airtime",
    "Engen Garage",
    "Netflix",
    "Takealot",
    "Mr Price",
    "Clicks Pharmacy",
    "City of Cape Town",
    "Spotify",
    "Nando's",
    "Vida e Caffe",
    "Bolt",
]


def get_system_account(db: Session, number: str) -> Account:
    account = db.execute(
        select(Account).where(Account.account_number == number)
    ).scalar_one_or_none()
    if account is None:
        raise RuntimeError(f"system account {number} is missing; run the seed")
    return account


def _ensure_system_customer(db: Session, email: str, name: str, number: str, acc_name: str) -> Account:
    customer = db.execute(select(Customer).where(Customer.email == email)).scalar_one_or_none()
    if customer is None:
        customer = Customer(
            email=email,
            full_name=name,
            password_hash=hash_password(sha256(email)),  # unusable, system users cannot log in
            kind="system",
        )
        db.add(customer)
        db.flush()
    account = db.execute(select(Account).where(Account.account_number == number)).scalar_one_or_none()
    if account is None:
        account = Account(
            customer_id=customer.id,
            name=acc_name,
            account_number=number,
            account_type="system",
            allow_overdraft=True,
        )
        db.add(account)
        db.flush()
    return account


def ensure_system_accounts(db: Session) -> tuple[Account, Account]:
    treasury = _ensure_system_customer(
        db, "treasury@mockbank.internal", "MockBank Treasury", TREASURY_ACCOUNT_NUMBER, "Treasury"
    )
    clearing = _ensure_system_customer(
        db, "clearing@mockbank.internal", "MockBank Clearing", CLEARING_ACCOUNT_NUMBER, "Clearing"
    )
    return treasury, clearing


def top_up(db: Session, account: Account, amount: Decimal, description: str = "Deposit") -> None:
    treasury = get_system_account(db, TREASURY_ACCOUNT_NUMBER)
    post_transfer(
        db,
        debit_account_id=treasury.id,
        credit_account_id=account.id,
        amount=amount,
        description=description,
        kind="seed",
        credit_counterparty_name="MockBank",
    )


def seed_history(db: Session, account: Account, style: str, target: Decimal) -> int:
    """Post a deterministic, realistic history for a zero-balance account ending at `target`.

    Returns the number of journals posted. Styles: personal, savings, business, none.
    """
    if account.balance != 0:
        raise ValueError("seed_history requires an account with a zero balance")
    rng_seed = account.account_number
    treasury = get_system_account(db, TREASURY_ACCOUNT_NUMBER)
    clearing = get_system_account(db, CLEARING_ACCOUNT_NUMBER)
    rng = random.Random(rng_seed)
    days = 60
    now = utcnow()
    start = now - timedelta(days=days)
    events: list[tuple[datetime, str, Decimal, str, str | None]] = []

    if style == "personal":
        salary = Decimal(rng.choice(["18500.00", "24300.00", "31250.00"]))
        for month_offset in (2, 1, 0):
            pay_day = now.replace(day=25, hour=6, minute=0, second=0, microsecond=0) - timedelta(
                days=31 * month_offset
            )
            if start <= pay_day <= now:
                events.append((pay_day, "credit", salary, "ACME Corp Salary", "SALARY"))
        day = start
        while day < now:
            for _ in range(rng.choice([0, 1, 1, 2])):
                merchant = rng.choice(MERCHANTS)
                amount = Decimal(rng.randint(2500, 95000)) / 100
                when = day + timedelta(hours=rng.randint(7, 21), minutes=rng.randint(0, 59))
                if when < now:
                    events.append((when, "debit", amount, merchant, None))
            day += timedelta(days=1)
    elif style == "savings":
        for week in range(0, days, 7):
            when = start + timedelta(days=week, hours=8)
            events.append((when, "credit", Decimal("1500.00"), "Monthly savings transfer", "SAVE"))
    elif style == "business":
        day = start
        while day < now:
            for _ in range(rng.choice([1, 2, 3])):
                amount = Decimal(rng.randint(20000, 500000)) / 100
                when = day + timedelta(hours=rng.randint(8, 18), minutes=rng.randint(0, 59))
                if when < now:
                    events.append(
                        (when, "credit", amount, "Customer deposit", f"DEP-{rng.randint(10000, 99999)}")
                    )
            day += timedelta(days=1)
        events.append(
            (start + timedelta(days=days // 2), "debit", Decimal("18999.00"), "AWS Cloud Services", "AWS-INV")
        )
    elif style != "none":
        raise ValueError(f"unknown history style {style}")

    events.sort(key=lambda e: e[0])
    net = sum((e[2] if e[1] == "credit" else -e[2]) for e in events)
    opening = (target - net).quantize(Decimal("0.01"))

    # Make sure the running balance never dips below zero: raise the opening deposit if needed.
    running = opening
    lowest = running
    for _, kind, amount, _, _ in events:
        running += amount if kind == "credit" else -amount
        lowest = min(lowest, running)
    if lowest < 0:
        opening -= lowest  # shift everything up
    if opening < 0:
        opening = Decimal("0.00")

    count = 0
    if opening > 0:
        post_transfer(
            db,
            debit_account_id=treasury.id,
            credit_account_id=account.id,
            amount=opening,
            description="Opening balance",
            kind="seed",
            credit_counterparty_name="MockBank",
            booked_at=start - timedelta(days=1),
        )
        count += 1
    for when, kind, amount, party, ref in events:
        if kind == "credit":
            post_transfer(
                db,
                debit_account_id=treasury.id,
                credit_account_id=account.id,
                amount=amount,
                description=party,
                reference=ref,
                kind="seed",
                credit_counterparty_name=party,
                booked_at=when,
            )
        else:
            post_transfer(
                db,
                debit_account_id=account.id,
                credit_account_id=clearing.id,
                amount=amount,
                description=f"Card purchase: {party}",
                reference=ref,
                kind="seed",
                debit_counterparty_name=party,
                booked_at=when,
            )
        count += 1

    # If the opening deposit was shifted up to avoid a negative running balance, the account
    # now ends above target. Bring it back with a final adjustment so seeds are exact.
    db.flush()
    db.refresh(account)
    if account.balance != target:
        diff = account.balance - target
        if diff > 0:
            post_transfer(
                db,
                debit_account_id=account.id,
                credit_account_id=clearing.id,
                amount=diff,
                description="Card purchase: Takealot",
                kind="seed",
                debit_counterparty_name="Takealot",
                booked_at=now - timedelta(hours=2),
            )
        else:
            post_transfer(
                db,
                debit_account_id=treasury.id,
                credit_account_id=account.id,
                amount=-diff,
                description="Customer deposit",
                kind="seed",
                credit_counterparty_name="MockBank",
                booked_at=now - timedelta(hours=2),
            )
        count += 1
    return count


def seed_customer(db: Session, spec: SeedCustomer) -> Customer:
    """Create a seed customer and accounts if missing; seed history on zero-balance accounts."""
    customer = db.execute(select(Customer).where(Customer.email == spec.email)).scalar_one_or_none()
    if customer is None:
        customer = Customer(
            email=spec.email,
            full_name=spec.full_name,
            password_hash=hash_password(spec.password),
            kind=spec.kind,
        )
        db.add(customer)
        db.flush()
    for acc in spec.accounts:
        account = db.execute(
            select(Account).where(Account.account_number == acc.number)
        ).scalar_one_or_none()
        if account is None:
            account = Account(
                customer_id=customer.id,
                name=acc.name,
                account_number=acc.number,
                account_type=acc.account_type,
            )
            db.add(account)
            db.flush()
        if account.balance == 0:
            seed_history(db, account, acc.history, acc.opening)
    return customer


def seed_demo_application(db: Session) -> Application:
    app = db.get(Application, DEMO_APP_ID)
    base = get_settings().base_url
    redirect_uris = [
        "http://localhost:5000/callback",
        "http://localhost:5000/payments/return",
        "http://localhost:3000/callback",
        "http://localhost:3000/payments/return",
        f"{base}/docs/oauth2-redirect",
    ]
    if app is None:
        app = Application(
            id=DEMO_APP_ID,
            name="RemitX Demo",
            owner_label="Seeded example application (used by examples/ and Swagger UI)",
            client_secret_hash=sha256(DEMO_APP_SECRET),
            redirect_uris=redirect_uris,
            webhook_url="http://host.docker.internal:5000/webhooks/mockbank",
            webhook_secret=DEMO_APP_WEBHOOK_SECRET,
            send_transaction_events=False,
        )
        db.add(app)
        db.flush()
    return app


def is_seeded(db: Session) -> bool:
    return db.execute(select(func.count(Customer.id))).scalar_one() > 0


def seed(db: Session) -> dict:
    """Idempotent: creates whatever is missing. Safe to run at every startup."""
    ensure_system_accounts(db)
    for spec in SEED_CUSTOMERS:
        seed_customer(db, spec)
    seed_demo_application(db)
    audit(db, actor_type="system", actor_id=None, action="seed.run")
    db.commit()
    return {
        "customers": db.execute(select(func.count(Customer.id))).scalar_one(),
        "accounts": db.execute(select(func.count(Account.id))).scalar_one(),
        "journals": db.execute(select(func.count(Journal.id))).scalar_one(),
    }


ACTIVITY_TABLES = [
    WebhookDelivery,
    Token,
    AuthorizationCode,
    Payment,
    Consent,
    Transaction,
    Journal,
    AuditLog,
]


def reset_activity(db: Session) -> dict:
    """Delete all activity and zero every balance, then re-seed.

    Customers, accounts and applications survive. Seeded customers get their history back,
    other accounts end at zero.
    """
    for model in ACTIVITY_TABLES:
        db.execute(delete(model))
    db.execute(text("UPDATE accounts SET balance = 0"))
    db.commit()
    db.expire_all()
    return seed(db)


def reset_all(db: Session) -> dict:
    """Delete everything and re-seed from scratch."""
    for model in ACTIVITY_TABLES + [Account, Application, Customer]:
        db.execute(delete(model))
    db.commit()
    return seed(db)
