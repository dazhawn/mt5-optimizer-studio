"""Proof CLI: parse a set file and print its normalized structure.

Usage:
    python -m scripts.parse_setfile tests/fixtures/dark_moon_v1.set
"""
from __future__ import annotations

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.dialects import detect_dialect, parse_set, render_set  # noqa: E402


def main(path: str) -> int:
    raw = pathlib.Path(path).read_bytes()
    name, conf = detect_dialect(raw)
    parsed = parse_set(raw)

    total = len(parsed.params)
    tunable = parsed.tunable()
    optim = parsed.optimizable()
    kinds: dict[str, int] = {}
    for p in parsed.params:
        kinds[p.kind] = kinds.get(p.kind, 0) + 1

    round_trips = render_set(parsed) == raw

    print(f"file        : {path}")
    print(f"dialect      : {name}  (confidence {conf:.2f})")
    print(f"encoding     : {parsed.codec}  bom={parsed.bom!r}  newline={parsed.newline!r}")
    print(f"lines/params : {total}")
    print(f"kinds        : {kinds}")
    print(f"tunable      : {len(tunable)} (have ranges)")
    print(f"optimizable  : {len(optim)} (flagged Y = the real search space)")
    print(f"round-trip   : {'OK (byte-exact)' if round_trips else 'FAILED'}")
    print("\nselected search space (optimize=Y only):")
    print(f"  {'name':<34} {'start':>10} {'step':>10} {'stop':>10}")
    for p in optim:
        print(f"  {p.name:<34} {p.start!s:>10} {p.step!s:>10} {p.stop!s:>10}")
    return 0 if round_trips else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
