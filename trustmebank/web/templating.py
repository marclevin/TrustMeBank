"""Jinja2 environment and helpers shared by the HTML routers."""

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from trustmebank import __version__
from trustmebank.config import get_settings
from trustmebank.money import fmt, fmt_rand

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["rand"] = fmt_rand
templates.env.filters["money"] = fmt
templates.env.globals["version"] = __version__


def _dt(value):
    if value is None:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")


templates.env.filters["dt"] = _dt


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    ctx = {
        "request": request,
        "settings": get_settings(),
        "customer": getattr(request.state, "customer", None),
        "is_admin": bool(request.session.get("admin")) if "session" in request.scope else False,
        "csrf_token": request.session.get("csrf") if "session" in request.scope else "",
        "flash": request.session.pop("flash", None) if "session" in request.scope else None,
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def render_error(request: Request, status_code: int, title: str, message: str) -> HTMLResponse:
    return render(
        request,
        "error.html",
        {"title": title, "message": message, "status_code": status_code},
        status_code=status_code,
    )


def flash(request: Request, message: str, level: str = "info") -> None:
    request.session["flash"] = {"message": message, "level": level}
