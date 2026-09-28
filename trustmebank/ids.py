"""Prefixed random identifiers such as acc_7f3k9d2m1q."""

import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix: str, length: int = 12) -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}_{body}"


def new_account_number() -> str:
    """Ten digit account number starting with 10."""
    return "10" + "".join(secrets.choice(string.digits) for _ in range(8))
