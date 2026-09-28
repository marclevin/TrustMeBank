from datetime import timedelta

from mockbank.models import Consent, Token, utcnow
from mockbank.security import sha256
from tests.conftest import (
    ALICE,
    authorize,
    bearer,
    build_redirect,
    exchange,
    get_token,
    login,
    query_of,
)


def test_unknown_client_shows_error_page_and_never_redirects(client):
    login(client)
    r = client.get(
        "/oauth/authorize?response_type=code&client_id=app_nope&redirect_uri=http://evil.test/&scope=accounts&state=s"
    )
    assert r.status_code == 400 and "Unknown or inactive client_id" in r.text


def test_unregistered_redirect_uri_shows_error_page(client, registered_app):
    application, _ = registered_app
    login(client)
    url = build_redirect(
        "/oauth/authorize",
        {
            "response_type": "code",
            "client_id": application.id,
            "redirect_uri": "http://evil.test/callback",
            "scope": "accounts",
            "state": "s",
        },
    )
    r = client.get(url)
    assert r.status_code == 400 and "does not exactly match" in r.text


def test_invalid_scope_redirects_with_error(client, registered_app):
    application, _ = registered_app
    login(client)
    url = build_redirect(
        "/oauth/authorize",
        {
            "response_type": "code",
            "client_id": application.id,
            "redirect_uri": "http://localhost:5000/callback",
            "scope": "accounts admin",
            "state": "s1",
        },
    )
    r = client.get(url)
    assert r.status_code == 303
    params = query_of(r.headers["location"])
    assert params["error"] == "invalid_scope" and params["state"] == "s1"


def test_missing_state_is_rejected(client, registered_app):
    application, _ = registered_app
    login(client)
    url = build_redirect(
        "/oauth/authorize",
        {
            "response_type": "code",
            "client_id": application.id,
            "redirect_uri": "http://localhost:5000/callback",
            "scope": "accounts",
        },
    )
    r = client.get(url)
    assert r.status_code == 303 and query_of(r.headers["location"])["error"] == "invalid_request"


def test_anonymous_user_is_sent_to_login_and_back(client, registered_app):
    application, _ = registered_app
    url = build_redirect(
        "/oauth/authorize",
        {
            "response_type": "code",
            "client_id": application.id,
            "redirect_uri": "http://localhost:5000/callback",
            "scope": "accounts",
            "state": "s",
        },
    )
    r = client.get(url)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Foauth%2Fauthorize")


def test_deny_redirects_with_access_denied(client, registered_app):
    application, _ = registered_app
    login(client)
    location = authorize(client, application, decision="deny", state="st8")
    params = query_of(location)
    assert params == {"error": "access_denied", "state": "st8"}


def test_code_is_single_use_and_replay_revokes_tokens(client, api, registered_app):
    application, secret = registered_app
    login(client)
    params = query_of(authorize(client, application))
    token = exchange(api, application, secret, params["code"])
    assert api.get("/api/v1/me", headers=bearer(token)).status_code == 200
    replay = api.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": "http://localhost:5000/callback",
            "client_id": application.id,
            "client_secret": secret,
        },
    )
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    denied = api.get("/api/v1/me", headers=bearer(token))
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "invalid_token"


def test_wrong_secret_and_wrong_redirect_uri(client, api, registered_app):
    application, secret = registered_app
    login(client)
    params = query_of(authorize(client, application))
    bad = api.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": "http://localhost:5000/callback",
            "client_id": application.id,
            "client_secret": "nope",
        },
    )
    assert bad.status_code == 401 and bad.json()["error"] == "invalid_client"
    mismatch = api.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": "http://localhost:5000/payments/return",
            "client_id": application.id,
            "client_secret": secret,
        },
    )
    assert mismatch.status_code == 400 and "redirect_uri" in mismatch.json()["error_description"]


def test_basic_auth_client_credentials(client, api, registered_app):
    application, secret = registered_app
    login(client)
    params = query_of(authorize(client, application))
    r = api.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": params["code"],
            "redirect_uri": "http://localhost:5000/callback",
        },
        auth=(application.id, secret),
    )
    assert r.status_code == 200 and r.json()["access_token"].startswith("mbat_")


def test_refresh_rotates_tokens(client, api, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    r = api.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": application.id,
            "client_secret": secret,
        },
    )
    assert r.status_code == 200
    new = r.json()
    assert new["access_token"] != token["access_token"]
    assert api.get("/api/v1/me", headers=bearer(new)).status_code == 200
    again = api.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": application.id,
            "client_secret": secret,
        },
    )
    assert again.status_code == 400  # old refresh token is dead


def test_expired_access_token(client, api, db, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    row = db.get(Token, sha256(token["access_token"]))
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    r = api.get("/api/v1/me", headers=bearer(token))
    assert r.status_code == 401 and "expired" in r.json()["error"]["message"]


def test_revoking_consent_kills_tokens(client, api, db, registered_app):
    application, secret = registered_app
    token = get_token(client, api, application, secret)
    page = client.get("/connected-apps")
    assert "Test App" in page.text
    from tests.conftest import csrf_from

    r = client.post(f"/connected-apps/{token['consent_id']}/revoke", data={"csrf": csrf_from(page.text)})
    assert r.status_code == 303
    assert api.get("/api/v1/accounts", headers=bearer(token)).status_code == 401
    assert db.get(Consent, token["consent_id"]).status == "revoked"


def test_reconsent_replaces_previous_consent(client, api, db, registered_app):
    application, secret = registered_app
    first = get_token(client, api, application, secret, scopes="accounts balances")
    second = get_token(client, api, application, secret, scopes="accounts")
    assert first["consent_id"] != second["consent_id"]
    assert api.get("/api/v1/accounts", headers=bearer(first)).status_code == 401
    me = api.get("/api/v1/me", headers=bearer(second)).json()
    assert me["scopes"] == ["accounts"] and me["full_name"] == "Alice Ndlovu"


def test_missing_and_garbage_tokens(api):
    assert api.get("/api/v1/accounts").json()["error"]["code"] == "missing_token"
    assert api.get("/api/v1/accounts", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_unsupported_grant(api, registered_app):
    application, secret = registered_app
    r = api.post(
        "/oauth/token", data={"grant_type": "password", "client_id": application.id, "client_secret": secret}
    )
    assert r.status_code == 400 and r.json()["error"] == "unsupported_grant_type"


def test_swagger_and_openapi_available(api):
    assert api.get("/docs").status_code == 200
    schema = api.get("/openapi.json").json()
    assert "/api/v1/payments" in schema["paths"]
    flows = schema["components"]["securitySchemes"]["OAuth2AuthorizationCodeBearer"]["flows"]
    assert flows["authorizationCode"]["tokenUrl"] == "http://testserver/oauth/token"
    assert ALICE[0] in schema["info"]["description"]
