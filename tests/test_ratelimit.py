from trustmebank.config import get_settings
from trustmebank.ratelimit import limiter


def test_api_rate_limit(api, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_api_per_minute", 3)
    limiter.reset()
    headers = {"Authorization": "Bearer tmbat_ratelimit_test"}
    codes = [api.get("/api/v1/me", headers=headers).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    last = api.get("/api/v1/me", headers=headers)
    assert last.json()["error"]["code"] == "rate_limited" and last.headers["Retry-After"] == "60"
    # A different token has its own bucket.
    assert api.get("/api/v1/me", headers={"Authorization": "Bearer other"}).status_code == 401
    limiter.reset()


def test_login_rate_limit(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_login_per_minute", 2)
    limiter.reset()
    codes = [
        client.post("/login", data={"email": "x", "password": "y", "csrf": "z"}).status_code for _ in range(3)
    ]
    assert codes[-1] == 429 and 429 not in codes[:2]
    limiter.reset()
