"""Auth gate tests — open without APP_PASSWORD, gated with it."""
from __future__ import annotations

import warnings

from fastapi.testclient import TestClient

warnings.filterwarnings("ignore")


def test_open_when_no_password(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/runs").status_code == 200
        assert c.get("/").status_code == 200


def test_gated_when_password_set(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret")
    from app.main import app
    with TestClient(app) as c:
        # API is 401 without a session
        assert c.get("/api/runs").status_code == 401
        # dashboard redirects to /login
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == "/login"
        # wrong password rejected
        assert c.post("/login", data={"password": "nope"},
                      follow_redirects=False).status_code == 401
        # correct password sets the session, then access is granted
        ok = c.post("/login", data={"password": "secret"}, follow_redirects=False)
        assert ok.status_code == 303
        assert c.get("/api/runs").status_code == 200
        assert c.get("/").status_code == 200
        # logout clears it
        c.get("/logout", follow_redirects=False)
        assert c.get("/api/runs").status_code == 401


def test_health_always_open(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret")
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
