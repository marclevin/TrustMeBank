"""All database models. The schema is small enough to live in one file.

See SPEC.md section 3 for the meaning of every entity and field.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mockbank.db import Base
from mockbank.ids import new_id

Money = Numeric(18, 2)


def utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("cus"))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="personal")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    accounts: Mapped[list["Account"]] = relationship(
        back_populates="customer", order_by="Account.created_at"
    )

    @property
    def can_login(self) -> bool:
        return self.is_active and self.kind != "system"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("acc"))
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    account_number: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    account_type: Mapped[str] = mapped_column(String(16), nullable=False, default="current")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="ZAR")
    balance: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0.00"))
    allow_overdraft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    customer: Mapped[Customer] = relationship(back_populates="accounts")


class Journal(Base):
    __tablename__ = "journals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("jnl"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(35))
    payment_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("payments.id", ondelete="SET NULL"), unique=True
    )
    booked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="journal")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("txn"))
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, nullable=False)
    journal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("journals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    balance_after: Mapped[Decimal] = mapped_column(Money, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(35))
    counterparty_name: Mapped[str] = mapped_column(String(255), nullable=False)
    counterparty_account_number: Mapped[str] = mapped_column(String(10), nullable=False)
    booked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    journal: Mapped[Journal] = relationship(back_populates="transactions")
    account: Mapped[Account] = relationship()

    __table_args__ = (Index("ix_transactions_account_seq", "account_id", "seq"),)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("app"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    owner_label: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    client_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    webhook_url: Mapped[str | None] = mapped_column(String(1024))
    webhook_secret: Mapped[str] = mapped_column(String(128), nullable=False)
    send_transaction_events: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Consent(Base):
    __tablename__ = "consents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("cns"))
    customer_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship()
    application: Mapped[Application] = relationship()

    def is_usable(self, now: datetime) -> bool:
        return self.status == "active" and self.expires_at > now


class AuthorizationCode(Base):
    __tablename__ = "authorization_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    consent_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("consents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    consent: Mapped[Consent] = relationship()


class Token(Base):
    __tablename__ = "tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # access | refresh
    consent_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("consents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    consent: Mapped[Consent] = relationship()


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("pay"))
    application_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consent_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("consents.id", ondelete="SET NULL"), index=True
    )
    debtor_account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    creditor_account_number: Mapped[str] = mapped_column(String(10), nullable=False)
    creditor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Money, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="ZAR")
    reference: Mapped[str] = mapped_column(String(35), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    redirect_uri: Mapped[str | None] = mapped_column(String(1024))
    journal_id: Mapped[str | None] = mapped_column(String(32))
    process_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    authorised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    application: Mapped[Application] = relationship()
    debtor_account: Mapped[Account] = relationship()

    __table_args__ = (
        UniqueConstraint("application_id", "idempotency_key", name="uq_payments_idempotency"),
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("evt"))
    application_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payment_id: Mapped[str | None] = mapped_column(String(32), index=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # exact JSON body sent
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_status_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_response_body: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    application: Mapped[Application] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(32))
    target_id: Mapped[str | None] = mapped_column(String(64), index=True)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
