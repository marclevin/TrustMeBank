"""The ledger. post_transfer is the only function that moves money.

Rules (SPEC section 4):
- every journal is balanced: one debit and one credit of equal size
- account.balance is a cache updated in the same transaction
- accounts are locked in ascending id order to avoid deadlocks
- a balance may go negative only if the account allows overdraft or the global setting is on
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mockbank.config import get_settings
from mockbank.models import Account, Journal, Transaction, utcnow


class LedgerError(Exception):
    code = "ledger_error"


class InsufficientFunds(LedgerError):
    code = "insufficient_funds"


class AccountClosed(LedgerError):
    code = "account_closed"


class InvalidTransfer(LedgerError):
    code = "invalid_transfer"


@dataclass
class TransferResult:
    journal: Journal
    debit: Transaction
    credit: Transaction


def lock_accounts(db: Session, *ids: str) -> dict[str, Account]:
    """Lock the given accounts FOR UPDATE in ascending id order. Returns them keyed by id."""
    result: dict[str, Account] = {}
    for account_id in sorted(set(ids)):
        account = db.execute(
            select(Account).where(Account.id == account_id).with_for_update()
        ).scalar_one_or_none()
        if account is None:
            raise InvalidTransfer(f"account {account_id} does not exist")
        result[account_id] = account
    return result


def post_transfer(
    db: Session,
    *,
    debit_account_id: str,
    credit_account_id: str,
    amount: Decimal,
    description: str,
    reference: str | None = None,
    kind: str = "transfer",
    payment_id: str | None = None,
    debit_counterparty_name: str | None = None,
    credit_counterparty_name: str | None = None,
    booked_at: datetime | None = None,
) -> TransferResult:
    """Move `amount` from the debit account to the credit account.

    Does not commit. The caller owns the transaction so it can include other rows
    (payment status, webhook outbox) atomically.
    """
    if amount <= 0:
        raise InvalidTransfer("amount must be positive")
    if debit_account_id == credit_account_id:
        raise InvalidTransfer("debit and credit accounts must differ")

    accounts = lock_accounts(db, debit_account_id, credit_account_id)
    debit_account = accounts[debit_account_id]
    credit_account = accounts[credit_account_id]

    if debit_account.status != "active":
        raise AccountClosed(f"account {debit_account.account_number} is closed")
    if credit_account.status != "active":
        raise AccountClosed(f"account {credit_account.account_number} is closed")

    new_debit_balance = debit_account.balance - amount
    if new_debit_balance < 0 and not (
        debit_account.allow_overdraft or get_settings().allow_negative_balances
    ):
        raise InsufficientFunds(f"account {debit_account.account_number} has insufficient funds")
    new_credit_balance = credit_account.balance + amount

    when = booked_at or utcnow()
    journal = Journal(
        kind=kind,
        description=description,
        reference=reference,
        payment_id=payment_id,
        booked_at=when,
    )
    db.add(journal)
    db.flush()

    debit_account.balance = new_debit_balance
    credit_account.balance = new_credit_balance

    debit = Transaction(
        journal_id=journal.id,
        account_id=debit_account.id,
        amount=-amount,
        balance_after=new_debit_balance,
        description=description,
        reference=reference,
        counterparty_name=debit_counterparty_name or credit_account.customer.full_name,
        counterparty_account_number=credit_account.account_number,
        booked_at=when,
    )
    credit = Transaction(
        journal_id=journal.id,
        account_id=credit_account.id,
        amount=amount,
        balance_after=new_credit_balance,
        description=description,
        reference=reference,
        counterparty_name=credit_counterparty_name or debit_account.customer.full_name,
        counterparty_account_number=debit_account.account_number,
        booked_at=when,
    )
    db.add(debit)
    db.add(credit)
    db.flush()
    return TransferResult(journal=journal, debit=debit, credit=credit)


@dataclass
class IntegrityReport:
    accounts_checked: int
    journals_checked: int
    balance_mismatches: list[dict]
    unbalanced_journals: list[dict]

    @property
    def ok(self) -> bool:
        return not self.balance_mismatches and not self.unbalanced_journals


def check_integrity(db: Session) -> IntegrityReport:
    """Verify cached balances equal the sum of transactions and journals sum to zero."""
    sums = dict(
        db.execute(
            select(Transaction.account_id, func.coalesce(func.sum(Transaction.amount), 0)).group_by(
                Transaction.account_id
            )
        ).all()
    )
    mismatches = []
    accounts = db.execute(select(Account)).scalars().all()
    for account in accounts:
        expected = Decimal(sums.get(account.id, 0)).quantize(Decimal("0.01"))
        if expected != account.balance:
            mismatches.append(
                {"account_id": account.id, "cached": str(account.balance), "sum": str(expected)}
            )
    unbalanced = [
        {"journal_id": jid, "sum": str(total)}
        for jid, total in db.execute(
            select(Transaction.journal_id, func.sum(Transaction.amount))
            .group_by(Transaction.journal_id)
            .having(func.sum(Transaction.amount) != 0)
        ).all()
    ]
    journal_count = db.execute(select(func.count(Journal.id))).scalar_one()
    return IntegrityReport(
        accounts_checked=len(accounts),
        journals_checked=journal_count,
        balance_mismatches=mismatches,
        unbalanced_journals=unbalanced,
    )
