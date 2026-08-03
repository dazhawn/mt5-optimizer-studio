"""SQLAlchemy 2.0 models — mirrors docs/BUILD_SPEC.md §7.

Multi-EA from day one: everything hangs off ``EA`` so the app is universal, not
Dark-Algos-specific. Optimization results (Run/Pass) are stored in an
EA-agnostic schema; ``inputs_json`` holds the per-pass input vector.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (JSON, DateTime, Float, ForeignKey, Integer, LargeBinary,
                        String, Text, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class EA(Base):
    __tablename__ = "ea"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    source_path: Mapped[str | None] = mapped_column(String(500))
    dialect: Mapped[str | None] = mapped_column(String(50))

    set_files: Mapped[list["SetFile"]] = relationship(back_populates="ea")
    runs: Mapped[list["Run"]] = relationship(back_populates="ea")


class SetFile(Base):
    __tablename__ = "set_file"
    id: Mapped[int] = mapped_column(primary_key=True)
    ea_id: Mapped[int] = mapped_column(ForeignKey("ea.id"))
    name: Mapped[str] = mapped_column(String(300))
    dialect: Mapped[str] = mapped_column(String(50))
    encoding: Mapped[str] = mapped_column(String(30))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    raw_blob: Mapped[bytes] = mapped_column(LargeBinary)

    ea: Mapped["EA"] = relationship(back_populates="set_files")
    params: Mapped[list["Param"]] = relationship(back_populates="set_file",
                                                 cascade="all, delete-orphan")


class Param(Base):
    __tablename__ = "param"
    id: Mapped[int] = mapped_column(primary_key=True)
    set_file_id: Mapped[int] = mapped_column(ForeignKey("set_file.id"))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    current: Mapped[str | None] = mapped_column(String(500))
    start: Mapped[float | None] = mapped_column(Float)
    step: Mapped[float | None] = mapped_column(Float)
    stop: Mapped[float | None] = mapped_column(Float)
    optimize: Mapped[bool] = mapped_column(default=False)
    raw_line: Mapped[str] = mapped_column(Text)

    set_file: Mapped["SetFile"] = relationship(back_populates="params")


class Run(Base):
    __tablename__ = "run"
    id: Mapped[int] = mapped_column(primary_key=True)
    ea_id: Mapped[int] = mapped_column(ForeignKey("ea.id"))
    set_file_id: Mapped[int | None] = mapped_column(ForeignKey("set_file.id"))
    symbol: Mapped[str | None] = mapped_column(String(30))
    timeframe: Mapped[str | None] = mapped_column(String(10))
    date_from: Mapped[str | None] = mapped_column(String(30))
    date_to: Mapped[str | None] = mapped_column(String(30))
    forward_from: Mapped[str | None] = mapped_column(String(30))
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    ea: Mapped["EA"] = relationship(back_populates="runs")
    passes: Mapped[list["Pass"]] = relationship(back_populates="run",
                                                cascade="all, delete-orphan")


class Pass(Base):
    """One optimization pass.

    Columns mirror the MT5 forward-optimization XML export exactly: each pass row
    carries BOTH a back-period and forward-period criterion (``back_result`` /
    ``forward_result``) plus detailed metrics and the full input vector. This is
    what makes back↔forward consistency scoring (Phase 3) a per-row computation.
    """

    __tablename__ = "pass"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("run.id"))
    pass_no: Mapped[int] = mapped_column(Integer, index=True)
    back_result: Mapped[float | None] = mapped_column(Float)
    forward_result: Mapped[float | None] = mapped_column(Float)
    profit: Mapped[float | None] = mapped_column(Float)
    expected_payoff: Mapped[float | None] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float)
    recovery_factor: Mapped[float | None] = mapped_column(Float)
    sharpe: Mapped[float | None] = mapped_column(Float)
    custom_criterion: Mapped[float | None] = mapped_column(Float)
    equity_dd_pct: Mapped[float | None] = mapped_column(Float)
    trades: Mapped[int | None] = mapped_column(Integer)
    robustness_score: Mapped[float | None] = mapped_column(Float, index=True)
    inputs_json: Mapped[dict] = mapped_column(JSON, default=dict)

    run: Mapped["Run"] = relationship(back_populates="passes")


class Account(Base):
    __tablename__ = "account"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # "mt5" | "myfxbook"
    label: Mapped[str] = mapped_column(String(200))
    ext_ref: Mapped[str | None] = mapped_column(String(300))
    linked_pass_id: Mapped[int | None] = mapped_column(ForeignKey("pass.id"))

    snapshots: Mapped[list["AccountSnapshot"]] = relationship(
        back_populates="account", cascade="all, delete-orphan")


class AccountSnapshot(Base):
    __tablename__ = "account_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"))
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    equity: Mapped[float | None] = mapped_column(Float)
    balance: Mapped[float | None] = mapped_column(Float)
    dd_pct: Mapped[float | None] = mapped_column(Float)
    open_positions_json: Mapped[dict] = mapped_column(JSON, default=dict)

    account: Mapped["Account"] = relationship(back_populates="snapshots")


class ImportJob(Base):
    """Tracks an async optimization-XML import so large files don't block the
    request (and don't hit PaaS request timeouts)."""

    __tablename__ = "import_job"
    id: Mapped[int] = mapped_column(primary_key=True)
    ea_name: Mapped[str] = mapped_column(String(200))
    symbol: Mapped[str | None] = mapped_column(String(50))
    timeframe: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processing|done|error
    run_id: Mapped[int | None] = mapped_column(Integer)
    passes: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
