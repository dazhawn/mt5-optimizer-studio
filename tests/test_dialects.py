"""Dialect engine tests.

The headline guarantee is lossless round-trip on the REAL Dark Moon file:
``render_set(parse_set(raw)) == raw``, byte-for-byte. The rest assert that the
normalized model captured the right structure.
"""
from __future__ import annotations

import pathlib

import pytest

from app.dialects import detect_dialect, parse_set, render_set, sniff_encoding

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DARK_MOON = FIXTURES / "dark_moon_v1.set"


@pytest.fixture
def dark_moon_bytes() -> bytes:
    return DARK_MOON.read_bytes()


def test_encoding_sniff(dark_moon_bytes):
    codec, bom = sniff_encoding(dark_moon_bytes)
    assert codec == "utf-16-le"
    assert bom == b"\xff\xfe"


def test_dialect_detected(dark_moon_bytes):
    name, conf = detect_dialect(dark_moon_bytes)
    assert name == "inline_pipe"
    assert conf > 0.5


def test_round_trip_is_byte_exact(dark_moon_bytes):
    parsed = parse_set(dark_moon_bytes)
    assert render_set(parsed) == dark_moon_bytes


def test_optimize_flags(dark_moon_bytes):
    parsed = parse_set(dark_moon_bytes)
    lots = parsed.by_name("Lots")
    magic = parsed.by_name("MagicNumber")
    assert lots is not None and lots.kind == "number"
    assert lots.optimize is True
    assert lots.start == 0.01 and lots.stop == 0.10
    assert magic is not None and magic.optimize is False


def test_kind_classification(dark_moon_bytes):
    parsed = parse_set(dark_moon_bytes)
    mm = parsed.by_name("MoneyManagement")
    sep = parsed.by_name("Separo0")
    link = parsed.by_name("DarkAbsoluteTrendLink")
    assert mm is not None and mm.kind == "bool"
    assert sep is not None and sep.kind == "divider"
    assert link is not None and link.kind == "string"


def test_optimizable_excludes_labels(dark_moon_bytes):
    parsed = parse_set(dark_moon_bytes)
    names = {p.name for p in parsed.optimizable()}
    assert "Lots" in names
    assert "Separo0" not in names       # divider
    assert "MagicNumber" not in names   # present but optimize=N
    assert "DarkMoon" not in names      # label


# --- ArchAngel X family: real files, different encodings & edge cases -------

REAL_FIXTURES = [
    "dark_moon_v1.set",             # UTF-16LE + BOM
    "archangel_live.set",           # UTF-8 + BOM, all inputs fixed
    "archangel_opt.set",            # UTF-8 + BOM, 18 inputs flagged Y
    "archangel_template_1173.set",  # UTF-8 no BOM, 42 inputs flagged Y
]


@pytest.mark.parametrize("fname", REAL_FIXTURES)
def test_real_fixture_round_trip(fname):
    raw = (FIXTURES / fname).read_bytes()
    assert render_set(parse_set(raw)) == raw


def test_archangel_encodings_distinguished():
    live = parse_set((FIXTURES / "archangel_live.set").read_bytes())
    tmpl = parse_set((FIXTURES / "archangel_template_1173.set").read_bytes())
    assert live.codec == "utf-8" and live.bom == b"\xef\xbb\xbf"
    assert tmpl.codec == "utf-8" and tmpl.bom == b""


def test_archangel_live_vs_opt_selection():
    live = parse_set((FIXTURES / "archangel_live.set").read_bytes())
    opt = parse_set((FIXTURES / "archangel_opt.set").read_bytes())
    assert len(live.optimizable()) == 0    # LIVE file: nothing flagged to search
    assert len(opt.optimizable()) == 18    # OPT file: real search space


def test_archangel_real_world_edge_cases():
    p = parse_set((FIXTURES / "archangel_template_1173.set").read_bytes())
    assert p.by_name("tradingSessionMsg").kind == "label"        # embedded '=' in value
    assert p.by_name("inpStrategyDescription").current == ""      # empty value
    assert p.by_name("inpCheckLockProfit").current == -1          # negative number
    assert p.by_name("inpMondayTradingSession").kind == "string"  # time-range string
    assert p.by_name("inpUseAtrComment1").kind == "label"         # bracketed note


# --- synthetic vanilla MT5 fixture (no real sample yet) --------------------

VANILLA = (
    "; vanilla mt5 set\r\n"
    "InpLots=0.10\r\n"
    "InpLots,F=1\r\n"
    "InpLots,1=0.10\r\n"
    "InpLots,2=0.10\r\n"
    "InpLots,3=1.00\r\n"
    "InpUseTrail=false\r\n"
).encode("utf-8")


def test_vanilla_detect_and_round_trip():
    name, conf = detect_dialect(VANILLA)
    assert name == "vanilla_mt5"
    assert conf > 0.0
    parsed = parse_set(VANILLA)
    assert render_set(parsed) == VANILLA
    lots = parsed.by_name("InpLots")
    assert lots is not None and lots.optimize is True
    assert lots.start == 0.10 and lots.stop == 1.00
