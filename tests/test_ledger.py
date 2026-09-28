import threading
from decimal import Decimal

import pytest

from tests.conftest import account_by_number
from trustmebank.db import new_session
from trustmebank.models import Account, Journal, Transaction
from trustmebank.services import seed as seed_service
from trustmebank.services.ledger import (
    AccountClosed,
    InsufficientFunds,
    InvalidTransfer,
    check_integrity,
    post_transfer,
)


def test_transfer_updates_both_balances_and_balances_journal(db):
    alice = account_by_number(db, "1000123456")
    remitx = account_by_number(db, "1000987654")
    a0, r0 = alice.balance, remitx.balance
    result = post_transfer(
        db,
        debit_account_id=alice.id,
        credit_account_id=remitx.id,
        amount=Decimal("100.25"),
        description="test",
        reference="T1",
    )
    db.commit()
    db.expire_all()
    assert account_by_number(db, "1000123456").balance == a0 - Decimal("100.25")
    assert account_by_number(db, "1000987654").balance == r0 + Decimal("100.25")
    legs = db.query(Transaction).filter_by(journal_id=result.journal.id).all()
    assert sorted(t.amount for t in legs) == [Decimal("-100.25"), Decimal("100.25")]
    assert result.debit.counterparty_name == "RemitX (Pty) Ltd"
    assert result.credit.counterparty_name == "Alice Ndlovu"
    assert result.debit.balance_after == a0 - Decimal("100.25")


def test_insufficient_funds_refused(db):
    carol = account_by_number(db, "1000345678")
    remitx = account_by_number(db, "1000987654")
    with pytest.raises(InsufficientFunds):
        post_transfer(
            db,
            debit_account_id=carol.id,
            credit_account_id=remitx.id,
            amount=carol.balance + Decimal("0.01"),
            description="too much",
        )
    db.rollback()


def test_system_account_may_overdraw(db):
    treasury = account_by_number(db, seed_service.TREASURY_ACCOUNT_NUMBER)
    bob = account_by_number(db, "1000234567")
    before = treasury.balance
    post_transfer(
        db,
        debit_account_id=treasury.id,
        credit_account_id=bob.id,
        amount=Decimal("1.00"),
        description="top up",
        kind="seed",
    )
    db.commit()
    db.expire_all()
    assert account_by_number(db, seed_service.TREASURY_ACCOUNT_NUMBER).balance == before - 1


def test_invalid_transfers(db):
    alice = account_by_number(db, "1000123456")
    with pytest.raises(InvalidTransfer):
        post_transfer(
            db, debit_account_id=alice.id, credit_account_id=alice.id, amount=Decimal("1"), description="x"
        )
    with pytest.raises(InvalidTransfer):
        post_transfer(
            db,
            debit_account_id=alice.id,
            credit_account_id="acc_missing",
            amount=Decimal("1"),
            description="x",
        )
    with pytest.raises(InvalidTransfer):
        post_transfer(
            db,
            debit_account_id=alice.id,
            credit_account_id="acc_missing",
            amount=Decimal("0"),
            description="x",
        )
    db.rollback()


def test_closed_account_refused(db):
    alice = account_by_number(db, "1000123456")
    bob = account_by_number(db, "1000234567")
    bob.status = "closed"
    db.flush()
    with pytest.raises(AccountClosed):
        post_transfer(
            db, debit_account_id=alice.id, credit_account_id=bob.id, amount=Decimal("1"), description="x"
        )
    db.rollback()


def test_integrity_after_seed(db):
    report = check_integrity(db)
    assert report.ok, report
    assert report.accounts_checked >= 7


def test_concurrent_transfers_keep_ledger_consistent(db):
    """Many threads move money in both directions; balances must equal the sum of transactions."""
    alice = account_by_number(db, "1000123456")
    bob = account_by_number(db, "1000234567")
    alice_id, bob_id = alice.id, bob.id
    total_before = alice.balance + bob.balance
    errors: list[Exception] = []

    def work(i: int) -> None:
        session = new_session()
        try:
            debit, credit = (alice_id, bob_id) if i % 2 == 0 else (bob_id, alice_id)
            post_transfer(
                session,
                debit_account_id=debit,
                credit_account_id=credit,
                amount=Decimal("1.00"),
                description=f"concurrent {i}",
            )
            session.commit()
        except InsufficientFunds:
            session.rollback()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=work, args=(i,)) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    db.expire_all()
    report = check_integrity(db)
    assert report.ok, report
    assert (
        account_by_number(db, "1000123456").balance + account_by_number(db, "1000234567").balance
        == total_before
    )
    assert db.query(Journal).filter(Journal.description.like("concurrent %")).count() == 40
    # Every account referenced still exists
    assert db.get(Account, alice_id) is not None
