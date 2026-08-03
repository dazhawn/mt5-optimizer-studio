"""Range optimizer tests (analysis + set-file rewriting)."""
from __future__ import annotations

from app.optimize.refine import (analyze, apply_to_setfile, search_space_size)
from app.dialects import parse_set

BASE = (
    "; base optimization template\r\n"
    "inpA=5||1||1||10||Y\r\n"     # will NARROW (top concentrate to 4..6)
    "inpB=3||0||1||6||Y\r\n"      # will PIN (top all converge to 3)
    "inpC=2||0||1||4||Y\r\n"      # will PIN (low impact — top span full range)
    "inpUse=true||false||0||true||Y\r\n"  # categorical -> PIN to modal
    "inpFixed=1||1||1||1||N\r\n"  # already off, untouched
).encode("utf-8")


def _pass(no, score, A, B, C, Use):
    return {"pass_no": no, "robustness_score": score,
            "inputs": {"inpA": A, "inpB": B, "inpC": C, "inpUse": Use}}


PASSES = [
    _pass(1, 90, 5, 3, 0, "true"),   # top 3 ...
    _pass(2, 88, 6, 3, 4, "true"),
    _pass(3, 86, 4, 3, 2, "true"),
    _pass(4, 50, 1, 0, 1, "false"),  # ... rest give full-range spread
    _pass(5, 48, 10, 6, 3, "false"),
    _pass(6, 46, 2, 1, 0, "true"),
    _pass(7, 44, 9, 5, 4, "false"),
    _pass(8, 42, 8, 2, 2, "true"),
]


def test_analyze_decisions():
    res = analyze(PASSES, top_frac=0.01, min_top=3)   # top = 3 passes
    assert res["top_used"] == 3
    plans = {p.name: p for p in res["plans"]}

    assert plans["inpA"].action == "narrow"
    assert plans["inpA"].new_start == 4 and plans["inpA"].new_stop == 6
    assert plans["inpB"].action == "pin" and plans["inpB"].best_value == 3
    assert plans["inpC"].action == "pin"                # low impact
    assert plans["inpUse"].action == "pin" and plans["inpUse"].best_value == "true"

    assert res["summary"] == {"fixed": 0, "pin": 3, "narrow": 1}
    assert res["best_pass"]["pass_no"] == 1


def test_search_space_before():
    # inpA(10) * inpB(7) * inpC(5); inpUse non-numeric range is skipped
    assert search_space_size(parse_set(BASE)) == 10 * 7 * 5


def test_apply_rewrites_setfile_and_shrinks_space():
    res = analyze(PASSES, top_frac=0.01, min_top=3)
    out = apply_to_setfile(BASE, res["plans"])
    assert out["before"] == 350
    assert out["after"] == 3            # only inpA still optimized, over 4..6
    text = out["refined_bytes"].decode("utf-8")
    assert "inpA=5||4||1||6||Y" in text     # narrowed, still Y, step preserved
    assert "inpB=3||0||1||6||N" in text     # pinned off
    assert "inpC=0||0||1||4||N" in text     # pinned off, current set to best (0)
    assert "inpUse=true||false||0||true||N" in text
    assert "inpFixed=1||1||1||1||N" in text  # untouched


def test_refined_setfile_still_parses():
    res = analyze(PASSES, top_frac=0.01, min_top=3)
    out = apply_to_setfile(BASE, res["plans"])
    reparsed = parse_set(out["refined_bytes"])   # must remain a valid set file
    assert reparsed.by_name("inpA").optimize is True
    assert reparsed.by_name("inpB").optimize is False
