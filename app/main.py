"""FastAPI application entrypoint.

Run locally:
    uvicorn app.main:app --reload
"""
from __future__ import annotations

import os
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

from app.db import init_db
from app.api import setfiles, runs, portfolio
from app import auth

WEB_DIR = pathlib.Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="MT5 Optimizer Studio", version="0.1.0", lifespan=lifespan)

# Signed session cookie (used by the auth gate). SECRET_KEY must be set in prod;
# SECURE_COOKIES=true on an HTTPS host.
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "dev-only-insecure-change-me"),
    https_only=os.getenv("SECURE_COOKIES", "").lower() in ("1", "true"),
    same_site="lax",
)

# Protected routers/routes require login when APP_PASSWORD is set (else open).
_guard = [Depends(auth.require_auth)]
app.include_router(auth.router)                       # /login, /logout (open)
app.include_router(setfiles.router, dependencies=_guard)
app.include_router(runs.router, dependencies=_guard)
app.include_router(portfolio.router, dependencies=_guard)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", dependencies=_guard)
def dashboard():
    return FileResponse(WEB_DIR / "index.html")
