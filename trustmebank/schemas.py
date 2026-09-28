"""Pydantic models for the JSON API, plus serialisers shared with webhooks and templates."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trustmebank.models import Account, Payment, Transaction
from trustmebank.money import fmt, parse_amount


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- serialisers


def account_to_dict(account: Account, *, include_balance: bool) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": account.id,
        "name": account.name,
        "account_number": account.account_number,
        "account_type": account.account_type,
        "currency": account.currency,
        "created_at": iso(account.created_at),
    }
    if include_balance:
        data["balance"] = fmt(account.balance)
    return data


def transaction_to_dict(txn: Transaction) -> dict[str, Any]:
    return {
        "id": txn.id,
        "account_id": txn.account_id,
        "type": "CREDIT" if txn.amount > 0 else "DEBIT",
        "amount": fmt(txn.amount),
        "currency": "ZAR",
        "description": txn.description,
        "reference": txn.reference,
        "counterparty": {
            "name": txn.counterparty_name,
            "account_number": txn.counterparty_account_number,
        },
        "balance_after": fmt(txn.balance_after),
        "payment_id": txn.journal.payment_id if txn.journal else None,
        "journal_id": txn.journal_id,
        "booked_at": iso(txn.booked_at),
    }


def payment_to_dict(payment: Payment, base_url: str) -> dict[str, Any]:
    return {
        "payment_id": payment.id,
        "status": payment.status,
        "debtor_account_id": payment.debtor_account_id,
        "creditor_account_number": payment.creditor_account_number,
        "creditor_name": payment.creditor_name,
        "amount": fmt(payment.amount),
        "currency": payment.currency,
        "reference": payment.reference,
        "authorisation_url": f"{base_url}/payments/{payment.id}/authorise",
        "redirect_uri": payment.redirect_uri,
        "failure_reason": payment.failure_reason,
        "journal_id": payment.journal_id,
        "created_at": iso(payment.created_at),
        "authorised_at": iso(payment.authorised_at),
        "completed_at": iso(payment.completed_at),
    }


# --------------------------------------------------------------------------- API schemas


class Counterparty(BaseModel):
    name: str = Field(examples=["RemitX (Pty) Ltd"])
    account_number: str = Field(examples=["1000987654"])


class AccountOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "acc_7f3k9d2m1q0z",
                "name": "Everyday Account",
                "account_number": "1000123456",
                "account_type": "current",
                "currency": "ZAR",
                "balance": "15240.50",
                "created_at": "2026-01-14T09:12:44Z",
            }
        }
    )
    id: str
    name: str
    account_number: str
    account_type: str
    currency: str
    balance: str | None = Field(default=None, description="Present only with the balances scope")
    created_at: str


class AccountList(BaseModel):
    data: list[AccountOut]


class BalanceOut(BaseModel):
    account_id: str
    currency: str
    balance: str = Field(examples=["15240.50"])
    as_of: str


class TransactionOut(BaseModel):
    id: str
    account_id: str
    type: str = Field(description="DEBIT or CREDIT")
    amount: str = Field(description="Signed. Negative for debits.", examples=["-500.00"])
    currency: str
    description: str
    reference: str | None
    counterparty: Counterparty
    balance_after: str
    payment_id: str | None = Field(description="Set when this transaction settled a payment")
    journal_id: str
    booked_at: str


class TransactionList(BaseModel):
    data: list[TransactionOut]
    next_cursor: str | None = Field(description="Pass as ?cursor= to fetch the next page")
    has_more: bool


class MeOut(BaseModel):
    customer_id: str
    full_name: str
    consent_id: str
    scopes: list[str]
    consent_expires_at: str
    application_id: str


class PaymentCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "debtor_account_id": "acc_7f3k9d2m1q0z",
                "creditor_account_number": "1000987654",
                "amount": "500.00",
                "currency": "ZAR",
                "reference": "REM-92831",
                "redirect_uri": "http://localhost:5000/payments/return",
            }
        }
    )
    debtor_account_id: str = Field(description="One of the consenting customer's account ids")
    creditor_account_number: str = Field(
        min_length=10, max_length=10, description="A TrustMeBank account number"
    )
    amount: str = Field(description='String with two decimals, for example "500.00"')
    currency: str = Field(default="ZAR")
    reference: str = Field(min_length=1, max_length=35, description="Shown on both statements")
    redirect_uri: str | None = Field(
        default=None,
        description=(
            "Where to send the customer after they approve or reject. Must be a registered redirect URI."
        ),
    )

    @field_validator("amount", mode="before")
    @classmethod
    def _amount_must_be_string(cls, v):
        if isinstance(v, float):
            raise ValueError('amount must be a string such as "500.00", never a float')
        return str(v)

    @field_validator("currency")
    @classmethod
    def _zar_only(cls, v: str) -> str:
        if v.upper() != "ZAR":
            raise ValueError("currency must be ZAR")
        return "ZAR"

    def parsed_amount(self) -> Decimal:
        return parse_amount(self.amount)


class PaymentOut(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "payment_id": "pay_3k2j1h0g9f8d",
                "status": "AWAITING_AUTHORISATION",
                "debtor_account_id": "acc_7f3k9d2m1q0z",
                "creditor_account_number": "1000987654",
                "creditor_name": "RemitX (Pty) Ltd",
                "amount": "500.00",
                "currency": "ZAR",
                "reference": "REM-92831",
                "authorisation_url": "http://localhost:8000/payments/pay_3k2j1h0g9f8d/authorise",
                "redirect_uri": "http://localhost:5000/payments/return",
                "failure_reason": None,
                "journal_id": None,
                "created_at": "2026-09-28T10:00:00Z",
                "authorised_at": None,
                "completed_at": None,
            }
        }
    )
    payment_id: str
    status: str = Field(description="AWAITING_AUTHORISATION, PROCESSING, COMPLETED, REJECTED or FAILED")
    debtor_account_id: str
    creditor_account_number: str
    creditor_name: str
    amount: str
    currency: str
    reference: str
    authorisation_url: str = Field(description="Redirect the customer here to approve the payment")
    redirect_uri: str | None
    failure_reason: str | None
    journal_id: str | None
    created_at: str
    authorised_at: str | None
    completed_at: str | None


class PaymentList(BaseModel):
    data: list[PaymentOut]
    next_cursor: str | None
    has_more: bool


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str
    scope: str
    consent_id: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = {}


class ErrorOut(BaseModel):
    error: ErrorBody


class OAuthErrorOut(BaseModel):
    error: str
    error_description: str
