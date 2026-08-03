"""Test setup: isolate the DB so tests never touch the real mt5studio.db.

Must run before ``app.db`` is imported, so this lives at module top level.
"""
import os
import pathlib
import tempfile

_TEST_DB = pathlib.Path(tempfile.gettempdir()) / "mt5studio_test.db"
if _TEST_DB.exists():
    _TEST_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB.as_posix()}"
