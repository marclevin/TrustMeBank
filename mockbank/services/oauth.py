"""OAuth 2.0 authorization code flow, consents and tokens. See SPEC sections 5 and 6."""

from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from mockbank.audit import audit
from mockbank.config import get_settings
from mockbank.errors import OAuthError
from mockbank.models import (
    Application,
    AuthorizationCode,
    Consent,
    Customer,
    Token,
    utcnow,
)
from mockbank.security import constant_time_equal, new_secret, sha256

SCOPES: dict[str, str] = {
    "accounts": "View your accounts",
    "balances": "View your balances",
    "transactions": "View your transaction history",
    "payments": "Initiate payments from your accounts (each payment still needs your approval)",
}


class AuthorizeError(Exception):
    """Raised while validating /oauth/authorize.

    If `redirect` is True the error may be reported to the redirect URI; otherwise it must be
    shown on a MockBank page because the redirect URI itself is untrusted.
    """

    def __init__(self, error: str, description: str, redirect: bool) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.redirect = redirect


@dataclass
class AuthorizeRequest:
    application: Application
    redirect_uri: str
    scopes: list[str]
    state: str

    @property
    def scope_descriptions(self) -> list[str]:
        return [SCOPES[s] for s in self.scopes]


def parse_scopes(scope: str | None) -> list[str]:
    parts = [s for s in (scope or "").replace(",", " ").split() if s]
    # preserve order, drop duplicates
    seen: list[str] = []
    for p in parts:
        if p not in seen:
            seen.append(p)
    return seen


def validate_authorize_request(
    db: Session,
    *,
    client_id: str | None,
    redirect_uri: str | None,
    response_type: str | None,
    scope: str | None,
    state: str | None,
) -> AuthorizeRequest:
    app = db.get(Application, client_id) if client_id else None
    if app is None or not app.is_active:
        raise AuthorizeError("invalid_client", "Unknown or inactive client_id.", redirect=False)
    if not redirect_uri or redirect_uri not in app.redirect_uris:
        raise AuthorizeError(
            "invalid_redirect_uri",
            "redirect_uri does not exactly match a URI registered for this application.",
            redirect=False,
        )
    if response_type != "code":
        raise AuthorizeError("unsupported_response_type", "response_type must be 'code'.", redirect=True)
    scopes = parse_scopes(scope)
    if not scopes:
        raise AuthorizeError("invalid_scope", "At least one scope is required.", redirect=True)
    unknown = [s for s in scopes if s not in SCOPES]
    if unknown:
        raise AuthorizeError("invalid_scope", f"Unknown scope(s): {', '.join(unknown)}.", redirect=True)
    if not state:
        raise AuthorizeError(
            "invalid_request",
            "state is required. Generate a random value, store it in the user's session and "
            "compare it when the user returns.",
            redirect=True,
        )
    return AuthorizeRequest(application=app, redirect_uri=redirect_uri, scopes=scopes, state=state)


def build_redirect(redirect_uri: str, params: dict[str, str | None]) -> str:
    clean = {k: v for k, v in params.items() if v is not None}
    separator = "&" if "?" in redirect_uri else "?"
    return f"{redirect_uri}{separator}{urlencode(clean)}"


def revoke_consent(db: Session, consent: Consent, *, actor_type: str, actor_id: str | None) -> None:
    if consent.status == "revoked":
        return
    now = utcnow()
    consent.status = "revoked"
    consent.revoked_at = now
    db.execute(
        update(Token).where(Token.consent_id == consent.id, Token.revoked_at.is_(None)).values(revoked_at=now)
    )
    db.execute(
        update(AuthorizationCode)
        .where(AuthorizationCode.consent_id == consent.id, AuthorizationCode.used_at.is_(None))
        .values(used_at=now)
    )
    audit(
        db,
        actor_type=actor_type,
        actor_id=actor_id,
        action="consent.revoked",
        target_type="consent",
        target_id=consent.id,
        details={"application_id": consent.application_id, "customer_id": consent.customer_id},
    )


def grant_consent(
    db: Session, *, customer: Customer, req: AuthorizeRequest, ip: str | None = None
) -> tuple[Consent, str]:
    """Approve: replace any active consent for this customer+app and issue a code.

    Returns (consent, plaintext_code). Does not commit.
    """
    settings = get_settings()
    now = utcnow()
    existing = (
        db.execute(
            select(Consent).where(
                Consent.customer_id == customer.id,
                Consent.application_id == req.application.id,
                Consent.status == "active",
            )
        )
        .scalars()
        .all()
    )
    for old in existing:
        revoke_consent(db, old, actor_type="customer", actor_id=customer.id)

    consent = Consent(
        customer_id=customer.id,
        application_id=req.application.id,
        scopes=list(req.scopes),
        status="active",
        expires_at=now + timedelta(days=settings.consent_ttl_days),
    )
    db.add(consent)
    db.flush()

    code = new_secret("mbac_", 24)
    db.add(
        AuthorizationCode(
            code_hash=sha256(code),
            consent_id=consent.id,
            application_id=req.application.id,
            redirect_uri=req.redirect_uri,
            expires_at=now + timedelta(seconds=settings.auth_code_ttl_seconds),
        )
    )
    audit(
        db,
        actor_type="customer",
        actor_id=customer.id,
        action="consent.granted",
        target_type="consent",
        target_id=consent.id,
        details={"application_id": req.application.id, "scopes": req.scopes},
        ip=ip,
    )
    return consent, code


def authenticate_client(db: Session, client_id: str | None, client_secret: str | None) -> Application:
    app = db.get(Application, client_id) if client_id else None
    if app is None or not app.is_active or not client_secret:
        raise OAuthError("invalid_client", "Client authentication failed.", 401)
    if not constant_time_equal(sha256(client_secret), app.client_secret_hash):
        raise OAuthError("invalid_client", "Client authentication failed.", 401)
    return app


def _issue_tokens(db: Session, consent: Consent, app: Application, code_hash: str | None) -> dict:
    settings = get_settings()
    now = utcnow()
    access = new_secret("mbat_", 32)
    refresh = new_secret("mbrt_", 32)
    db.add(
        Token(
            token_hash=sha256(access),
            kind="access",
            consent_id=consent.id,
            application_id=app.id,
            code_hash=code_hash,
            expires_at=now + timedelta(seconds=settings.access_token_ttl_seconds),
        )
    )
    db.add(
        Token(
            token_hash=sha256(refresh),
            kind="refresh",
            consent_id=consent.id,
            application_id=app.id,
            code_hash=code_hash,
            expires_at=now + timedelta(seconds=settings.refresh_token_ttl_seconds),
        )
    )
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_seconds,
        "refresh_token": refresh,
        "scope": " ".join(consent.scopes),
        "consent_id": consent.id,
    }


def exchange_code(db: Session, *, app: Application, code: str | None, redirect_uri: str | None) -> dict:
    """grant_type=authorization_code. Does not commit."""
    if not code:
        raise OAuthError("invalid_request", "code is required.")
    now = utcnow()
    code_hash = sha256(code)
    row = db.execute(
        select(AuthorizationCode).where(AuthorizationCode.code_hash == code_hash).with_for_update()
    ).scalar_one_or_none()
    if row is None or row.application_id != app.id:
        raise OAuthError("invalid_grant", "Unknown authorization code.")
    if row.used_at is not None:
        # RFC 6749 4.1.2: a replayed code revokes everything issued from it.
        db.execute(
            update(Token)
            .where(Token.code_hash == code_hash, Token.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        audit(
            db,
            actor_type="application",
            actor_id=app.id,
            action="oauth.code_replayed",
            target_type="consent",
            target_id=row.consent_id,
        )
        db.commit()  # the revocation must persist even though this request fails
        raise OAuthError(
            "invalid_grant",
            "Authorization code has already been used. Tokens issued from it have been revoked.",
        )
    if row.expires_at <= now:
        raise OAuthError("invalid_grant", "Authorization code has expired. Codes last 5 minutes.")
    if not redirect_uri or redirect_uri != row.redirect_uri:
        raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request.")
    consent = db.get(Consent, row.consent_id)
    if consent is None or not consent.is_usable(now):
        raise OAuthError("invalid_grant", "The consent behind this code is no longer active.")
    row.used_at = now
    tokens = _issue_tokens(db, consent, app, code_hash)
    audit(
        db,
        actor_type="application",
        actor_id=app.id,
        action="oauth.token_issued",
        target_type="consent",
        target_id=consent.id,
        details={"grant": "authorization_code"},
    )
    return tokens


def refresh_tokens(db: Session, *, app: Application, refresh_token: str | None) -> dict:
    """grant_type=refresh_token. Rotates the refresh token. Does not commit."""
    if not refresh_token:
        raise OAuthError("invalid_request", "refresh_token is required.")
    now = utcnow()
    row = db.execute(
        select(Token).where(Token.token_hash == sha256(refresh_token)).with_for_update()
    ).scalar_one_or_none()
    if (
        row is None
        or row.kind != "refresh"
        or row.application_id != app.id
        or row.revoked_at is not None
        or row.expires_at <= now
    ):
        raise OAuthError("invalid_grant", "Refresh token is invalid, expired or revoked.")
    consent = db.get(Consent, row.consent_id)
    if consent is None or not consent.is_usable(now):
        raise OAuthError("invalid_grant", "The consent behind this token is no longer active.")
    row.revoked_at = now
    tokens = _issue_tokens(db, consent, app, row.code_hash)
    audit(
        db,
        actor_type="application",
        actor_id=app.id,
        action="oauth.token_issued",
        target_type="consent",
        target_id=consent.id,
        details={"grant": "refresh_token"},
    )
    return tokens


@dataclass
class AuthContext:
    application: Application
    consent: Consent
    customer: Customer
    token: Token

    @property
    def scopes(self) -> list[str]:
        return list(self.consent.scopes)

    def has_scope(self, scope: str) -> bool:
        return scope in self.consent.scopes


class TokenInvalid(Exception):
    pass


def resolve_access_token(db: Session, token: str) -> AuthContext:
    """Look up a bearer token and check everything that could make it unusable."""
    now = utcnow()
    row = db.execute(select(Token).where(Token.token_hash == sha256(token))).scalar_one_or_none()
    if row is None or row.kind != "access":
        raise TokenInvalid("Unknown access token.")
    if row.revoked_at is not None:
        raise TokenInvalid("Access token has been revoked.")
    if row.expires_at <= now:
        raise TokenInvalid("Access token has expired. Use the refresh token to obtain a new one.")
    consent = db.get(Consent, row.consent_id)
    if consent is None or not consent.is_usable(now):
        raise TokenInvalid("The consent behind this token has been revoked or has expired.")
    app = db.get(Application, row.application_id)
    if app is None or not app.is_active:
        raise TokenInvalid("The application is inactive.")
    customer = db.get(Customer, consent.customer_id)
    if customer is None or not customer.is_active:
        raise TokenInvalid("The customer account is inactive.")
    return AuthContext(application=app, consent=consent, customer=customer, token=row)


def active_consents_for_customer(db: Session, customer_id: str) -> list[Consent]:
    now = utcnow()
    rows = (
        db.execute(
            select(Consent)
            .where(Consent.customer_id == customer_id, Consent.status == "active")
            .order_by(Consent.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [c for c in rows if c.expires_at > now]
