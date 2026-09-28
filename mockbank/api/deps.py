"""API dependencies: bearer token resolution and scope enforcement."""

from collections.abc import Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2AuthorizationCodeBearer
from sqlalchemy.orm import Session

from mockbank.db import get_db
from mockbank.errors import APIError
from mockbank.services.oauth import SCOPES, AuthContext, TokenInvalid, resolve_access_token

# Two security schemes are declared so that Swagger UI offers both "paste a token" and the full
# OAuth flow. Only one Authorization header ever arrives; both schemes read it.
bearer_scheme = HTTPBearer(auto_error=False, description="Paste an access token (mbat_...)")
oauth_scheme = OAuth2AuthorizationCodeBearer(
    authorizationUrl="/oauth/authorize",
    tokenUrl="/oauth/token",
    scopes=SCOPES,
    auto_error=False,
    description="Run the full Authorization Code flow from this page",
)


def get_auth(
    request: Request,
    db: Session = Depends(get_db),
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    _oauth: str | None = Depends(oauth_scheme),
) -> AuthContext:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise APIError(401, "missing_token", "Send Authorization: Bearer <access_token>.")
    try:
        auth = resolve_access_token(db, creds.credentials)
    except TokenInvalid as exc:
        raise APIError(401, "invalid_token", str(exc)) from None
    request.state.auth = auth
    return auth


def require_scope(scope: str) -> Callable[..., AuthContext]:
    def dependency(auth: AuthContext = Depends(get_auth)) -> AuthContext:
        if not auth.has_scope(scope):
            raise APIError(
                403,
                "insufficient_scope",
                f"This endpoint requires the '{scope}' scope. Granted scopes: {' '.join(auth.scopes)}.",
                {"required_scope": scope, "granted_scopes": auth.scopes},
            )
        return auth

    return dependency


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
