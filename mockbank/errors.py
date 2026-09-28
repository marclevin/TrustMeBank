"""Error types and handlers.

API routes raise APIError and get the JSON envelope from SPEC section 11.
Browser routes raise WebError and get an HTML page.
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from mockbank.money import InvalidAmount


class APIError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, details: dict | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content={"error": {"code": self.code, "message": self.message, "details": self.details}},
        )


class OAuthError(Exception):
    """RFC 6749 token endpoint error."""

    def __init__(self, error: str, description: str, status_code: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code

    def to_response(self) -> JSONResponse:
        headers = {}
        if self.error == "invalid_client":
            headers["WWW-Authenticate"] = 'Basic realm="mockbank"'
        return JSONResponse(
            status_code=self.status_code,
            content={"error": self.error, "error_description": self.description},
            headers=headers,
        )


class WebError(Exception):
    def __init__(self, status_code: int, title: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.title = title
        self.message = message


def _is_api_request(request: Request) -> bool:
    path = request.url.path
    return path.startswith("/api/") or path == "/oauth/token"


def install_error_handlers(app) -> None:
    from fastapi.responses import RedirectResponse

    from mockbank.web.deps import LoginRequired
    from mockbank.web.templating import render_error

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if exc.next_url.startswith("/admin"):
            return RedirectResponse(exc.next_url, status_code=303)
        from urllib.parse import quote

        return RedirectResponse(f"/login?next={quote(exc.next_url, safe='')}", status_code=303)

    @app.exception_handler(APIError)
    async def _api_error(request: Request, exc: APIError):
        if _is_api_request(request):
            return exc.to_response()
        return render_error(request, exc.status_code, "Error", exc.message)

    @app.exception_handler(OAuthError)
    async def _oauth_error(request: Request, exc: OAuthError):
        return exc.to_response()

    @app.exception_handler(WebError)
    async def _web_error(request: Request, exc: WebError):
        return render_error(request, exc.status_code, exc.title, exc.message)

    @app.exception_handler(InvalidAmount)
    async def _invalid_amount(request: Request, exc: InvalidAmount):
        err = APIError(422, "invalid_amount", str(exc))
        if _is_api_request(request):
            return err.to_response()
        return render_error(request, 422, "Invalid amount", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        if _is_api_request(request):
            fields = []
            for e in exc.errors():
                loc = ".".join(str(p) for p in e.get("loc", []) if p not in ("body",))
                fields.append({"field": loc, "message": e.get("msg", "invalid")})
            return APIError(
                422, "validation_error", "Request validation failed.", {"fields": fields}
            ).to_response()
        return render_error(request, 422, "Invalid request", "The submitted form was invalid.")

    @app.exception_handler(404)
    async def _not_found(request: Request, exc):
        if _is_api_request(request):
            return APIError(404, "not_found", "Resource not found.").to_response()
        return render_error(request, 404, "Not found", "That page does not exist.")

    @app.exception_handler(405)
    async def _method_not_allowed(request: Request, exc):
        if _is_api_request(request):
            return APIError(405, "method_not_allowed", "Method not allowed.").to_response()
        return render_error(request, 405, "Method not allowed", "That method is not allowed.")
