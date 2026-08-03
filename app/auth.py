"""Single-login auth gate (MVP).

Enabled only when ``APP_PASSWORD`` is set in the environment — so local dev stays
frictionless and the deployed instance is gated. A signed session cookie
(SessionMiddleware) records the login. Designed to evolve: swap ``_check`` for a
per-user lookup when multi-tenant accounts land.
"""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter()


def auth_enabled() -> bool:
    return bool(os.getenv("APP_PASSWORD"))


def _check(password: str) -> bool:
    expected = os.getenv("APP_PASSWORD", "")
    return bool(expected) and hmac.compare_digest(password, expected)


def require_auth(request: Request) -> None:
    """Dependency: guard a route. Redirects pages to /login, 401s the API."""
    if not auth_enabled() or request.session.get("authed"):
        return
    if request.url.path.startswith("/api/"):
        raise HTTPException(status_code=401, detail="authentication required")
    raise HTTPException(status_code=307, headers={"Location": "/login"})


_PAGE = """<!doctype html><meta charset=utf-8><title>Sign in · MT5 Optimizer Studio</title>
<style>
 body{{background:#0d1117;color:#e6edf3;font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif;
   display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}}
 form{{background:#161b22;border:1px solid #2a3240;border-radius:12px;padding:28px;width:300px}}
 h1{{font-size:16px;margin:0 0 18px}}
 input{{width:100%;box-sizing:border-box;background:#1c2330;border:1px solid #2a3240;
   color:#e6edf3;border-radius:8px;padding:10px;margin-bottom:12px;font-size:14px}}
 button{{width:100%;background:#3b82f6;color:#fff;border:0;border-radius:8px;padding:10px;
   font-weight:600;cursor:pointer;font-size:14px}}
 .err{{color:#ef4444;font-size:13px;margin-bottom:10px}}
</style>
<form method=post action=/login><h1>⚙️ MT5 Optimizer Studio</h1>{err}
<input type=password name=password placeholder=Password autofocus autocomplete=current-password>
<button type=submit>Sign in</button></form>"""


@router.get("/login", response_class=HTMLResponse)
def login_page():
    return _PAGE.format(err="")


@router.post("/login")
def login_submit(request: Request, password: str = Form("")):
    if _check(password):
        request.session["authed"] = True
        return RedirectResponse("/", status_code=303)
    return HTMLResponse(_PAGE.format(err='<div class="err">Wrong password.</div>'),
                        status_code=401)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
