"""Database engine/session setup.

Defaults to a local SQLite file for development; set ``DATABASE_URL`` to a
Postgres DSN in production (see docs/BUILD_SPEC.md §7).
"""
from __future__ import annotations

import os
import pathlib

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Anchor the default SQLite file to the project root so it resolves the same way
# regardless of the process working directory (e.g. when the dev server is
# launched from elsewhere).
_DEFAULT_DB = pathlib.Path(__file__).resolve().parents[1] / "mt5studio.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DEFAULT_DB.as_posix()}")

# Managed Postgres (Render/Heroku/etc.) hands out "postgres://…"; SQLAlchemy 2.0
# with psycopg3 needs the explicit "postgresql+psycopg://…" scheme.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

_is_sqlite = DATABASE_URL.startswith("sqlite")
_connect_args = {"check_same_thread": False} if _is_sqlite else {}
# pool_pre_ping avoids stale-connection errors against a managed cloud database.
engine = create_engine(DATABASE_URL, connect_args=_connect_args, future=True,
                       pool_pre_ping=not _is_sqlite)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401 — ensure models are registered
    Base.metadata.create_all(bind=engine)
