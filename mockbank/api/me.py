"""GET /api/v1/me"""

from fastapi import APIRouter, Depends

from mockbank.api.deps import get_auth
from mockbank.schemas import ErrorOut, MeOut, iso
from mockbank.services.oauth import AuthContext

router = APIRouter(prefix="/api/v1", tags=["Accounts"])


@router.get(
    "/me",
    response_model=MeOut,
    responses={401: {"model": ErrorOut}},
    summary="Who is connected",
    description="Returns the customer behind the token and the scopes they granted. Any valid token works.",
)
def me(auth: AuthContext = Depends(get_auth)):
    return {
        "customer_id": auth.customer.id,
        "full_name": auth.customer.full_name,
        "consent_id": auth.consent.id,
        "scopes": auth.scopes,
        "consent_expires_at": iso(auth.consent.expires_at),
        "application_id": auth.application.id,
    }
