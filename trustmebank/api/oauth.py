"""POST /oauth/token"""

import base64

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from trustmebank.db import get_db
from trustmebank.errors import OAuthError
from trustmebank.schemas import OAuthErrorOut, TokenOut
from trustmebank.services import oauth

router = APIRouter(tags=["OAuth"])


def _basic_credentials(request: Request) -> tuple[str | None, str | None]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        user, _, secret = decoded.partition(":")
        return user or None, secret or None
    except Exception:  # noqa: BLE001
        return None, None


@router.post(
    "/oauth/token",
    response_model=TokenOut,
    responses={400: {"model": OAuthErrorOut}, 401: {"model": OAuthErrorOut}},
    summary="Exchange an authorization code, or refresh an access token",
    description=(
        "Form encoded (application/x-www-form-urlencoded), as in RFC 6749.\n\n"
        "**Authorization code:** `grant_type=authorization_code&code=...&redirect_uri=...`"
        "&client_id=...&client_secret=...`\n\n"
        "**Refresh:** `grant_type=refresh_token&refresh_token=...&client_id=...&client_secret=...`\n\n"
        "Client credentials may be sent in the form body or as HTTP Basic auth. "
        "Codes are single use and expire after 5 minutes. Access tokens expire after one hour; "
        "refresh tokens are rotated on every use."
    ),
)
def token(
    request: Request,
    grant_type: str = Form(...),
    code: str | None = Form(default=None),
    redirect_uri: str | None = Form(default=None),
    refresh_token: str | None = Form(default=None),
    client_id: str | None = Form(default=None),
    client_secret: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    basic_id, basic_secret = _basic_credentials(request)
    app = oauth.authenticate_client(db, client_id or basic_id, client_secret or basic_secret)
    if grant_type == "authorization_code":
        result = oauth.exchange_code(db, app=app, code=code, redirect_uri=redirect_uri)
    elif grant_type == "refresh_token":
        result = oauth.refresh_tokens(db, app=app, refresh_token=refresh_token)
    else:
        raise OAuthError("unsupported_grant_type", "grant_type must be authorization_code or refresh_token.")
    db.commit()
    return result
