"""Render the Markdown guides in docs/ at /guide/<name> so the deployed bank carries its docs."""

import re
from pathlib import Path

import markdown
from fastapi import APIRouter, Request

from mockbank.errors import WebError
from mockbank.web.templating import render

router = APIRouter(include_in_schema=False)

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"
GUIDES = [
    ("getting-started", "Getting started"),
    ("oauth", "OAuth and consent"),
    ("payments", "Payments"),
    ("webhooks", "Webhooks"),
    ("api-reference", "API reference"),
    ("curl-examples", "curl examples"),
    ("admin-guide", "Administrator guide"),
]
_SAFE = re.compile(r"^[a-z0-9-]+$")


@router.get("/guide")
def guide_index(request: Request):
    return render(request, "guide.html", {"guides": GUIDES, "body": None, "title": "Guides"})


@router.get("/guide/{name}")
def guide_page(request: Request, name: str):
    if not _SAFE.match(name):
        raise WebError(404, "Not found", "No such guide.")
    path = DOCS_DIR / f"{name}.md"
    if not path.exists():
        raise WebError(404, "Not found", "No such guide.")
    text = path.read_text(encoding="utf-8")
    # Links between guides are written as relative .md links in the repo; rewrite for /guide.
    text = re.sub(r"\]\(([a-z0-9-]+)\.md(#[^)]*)?\)", r"](/guide/\1\2)", text)
    body = markdown.markdown(text, extensions=["fenced_code", "tables", "toc"])
    title = next((label for key, label in GUIDES if key == name), name)
    return render(request, "guide.html", {"guides": GUIDES, "body": body, "title": title})
