"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from mockbank import __version__
from mockbank.config import get_settings
from mockbank.errors import install_error_handlers

log = logging.getLogger("mockbank")

API_DESCRIPTION = """
MockBank is a simulated bank for the Financial Technology course. Nothing here is real money.

**Start here:** the [Getting started guide](/guide/getting-started) walks through registering,
OAuth consent, reading accounts, initiating a payment and receiving the webhook.

**Authentication:** every `/api/v1` endpoint needs `Authorization: Bearer <access_token>`.
Tokens come from the OAuth 2.0 Authorization Code flow (`/oauth/authorize` then `/oauth/token`).
Click **Authorize** above to try the flow from this page with the seeded `app_remitx_demo`
application (secret `mbsk_remitx_demo_secret`), logging in as `alice@example.com` / `alice123`.

**Money:** amounts are strings with two decimals (`"500.00"`), currency is always `ZAR`.
"""

TAGS = [
    {"name": "OAuth", "description": "Token exchange. The consent screen itself is a browser page."},
    {"name": "Accounts", "description": "Account information. Scopes: accounts, balances, transactions."},
    {
        "name": "Payments",
        "description": "Payment initiation. Scope: payments. Money moves only after the customer approves.",
    },
    {"name": "System", "description": "Health check."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from mockbank import worker

    if settings.webhook_worker_enabled:
        worker.start()
    yield
    worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="MockBank API",
        version=__version__,
        description=API_DESCRIPTION,
        openapi_tags=TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        servers=[{"url": settings.base_url}],
        lifespan=lifespan,
        swagger_ui_init_oauth={
            "clientId": "app_remitx_demo",
            "clientSecret": "mbsk_remitx_demo_secret",
            "scopes": "accounts balances transactions payments",
            "usePkceWithAuthorizationCodeGrant": False,
        },
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        session_cookie="mockbank_session",
        same_site="lax",
        https_only=settings.session_cookie_secure,
        max_age=12 * 3600,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    from mockbank.ratelimit import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    install_error_handlers(app)

    from mockbank.api import accounts as api_accounts
    from mockbank.api import me as api_me
    from mockbank.api import oauth as api_oauth
    from mockbank.api import payments as api_payments
    from mockbank.web import accounts as web_accounts
    from mockbank.web import admin as web_admin
    from mockbank.web import auth as web_auth
    from mockbank.web import consent as web_consent
    from mockbank.web import guide as web_guide
    from mockbank.web import payments as web_payments

    app.include_router(api_oauth.router)
    app.include_router(api_me.router)
    app.include_router(api_accounts.router)
    app.include_router(api_payments.router)
    app.include_router(web_auth.router)
    app.include_router(web_accounts.router)
    app.include_router(web_consent.router)
    app.include_router(web_payments.router)
    app.include_router(web_admin.router)
    app.include_router(web_admin.protected)
    app.include_router(web_guide.router)

    @app.get("/health", tags=["System"])
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
