"""Password hashing, secret generation, token hashing and webhook signing."""

import hashlib
import hmac
import secrets
import time

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def new_secret(prefix: str, nbytes: int = 24) -> str:
    return f"{prefix}{secrets.token_urlsafe(nbytes)}"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def sign_webhook(secret: str, body: bytes, timestamp: int | None = None) -> str:
    """Return the X-MockBank-Signature header value: t=<unix>,v1=<hex hmac>."""
    t = int(time.time()) if timestamp is None else timestamp
    message = f"{t}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


def verify_webhook_signature(secret: str, body: bytes, header: str, tolerance_seconds: int = 300) -> bool:
    """Reference implementation of what a TPP should do. Used by the tests and the docs."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        t = int(parts["t"])
        v1 = parts["v1"]
    except (ValueError, KeyError):
        return False
    if abs(time.time() - t) > tolerance_seconds:
        return False
    expected = hmac.new(secret.encode("utf-8"), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)
