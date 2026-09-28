"""A small in-memory sliding window rate limiter.

Single process only, which matches the deployment (one uvicorn worker). Keys:
- /api/*         : bearer token (or client IP when absent)
- /oauth/token   : client_id from the form or Basic header (or IP)
- POST /login    : client IP
"""

import threading
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mockbank.config import get_settings


class SlidingWindow:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # crude memory bound
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - window_seconds}
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindow()


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path
        key: str | None = None
        limit = 0
        if path.startswith("/api/"):
            auth = request.headers.get("authorization", "")
            key = "api:" + (auth[-32:] if auth else _client_ip(request))
            limit = settings.rate_limit_api_per_minute
        elif path == "/oauth/token":
            key = "token:" + _client_ip(request)
            limit = settings.rate_limit_token_per_minute
        elif path in ("/login", "/admin/login") and request.method == "POST":
            key = "login:" + _client_ip(request)
            limit = settings.rate_limit_login_per_minute
        if key is not None and not limiter.allow(key, limit):
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Slow down and retry in a minute.",
                        "details": {},
                    }
                },
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
