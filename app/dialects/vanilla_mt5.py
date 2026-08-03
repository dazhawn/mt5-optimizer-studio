"""Vanilla MT5 dialect: the standard Strategy Tester ``.set`` layout.

Each optimizable input is written as up to five sibling lines::

    InpLots=0.10
    InpLots,F=1        ; F = optimize flag (1/0)
    InpLots,1=0.10     ; start
    InpLots,2=0.10     ; step
    InpLots,3=1.00     ; stop

Non-optimized inputs may appear as a single ``Name=value`` line. Because the
optimization metadata for one input spans several lines, ``render`` re-emits the
preserved raw lines to guarantee a byte-perfect round-trip.
"""
from __future__ import annotations

import re

from .base import Param, ParsedSet

_SUFFIX = re.compile(r"^(?P<base>.+?),(?P<suf>F|1|2|3)$")
_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")


def _num(s: str) -> float | None:
    return float(s) if _NUMBER.match(s.strip()) else None


def _coerce(s: str) -> float | bool | str:
    low = s.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    n = _num(s)
    return n if n is not None else s.strip()


class VanillaMt5Dialect:
    name = "vanilla_mt5"

    def detect(self, text: str) -> float:
        assign = 0
        meta = 0
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith(";") or "=" not in s:
                continue
            assign += 1
            key = s.split("=", 1)[0]
            if _SUFFIX.match(key):
                meta += 1
        if assign == 0:
            return 0.0
        # Presence of any ,F/,1/,2/,3 metadata is a strong signal.
        return min(1.0, meta / max(assign, 1) * 2)

    def parse(self, text: str, *, codec: str, bom: bytes, newline: str,
              trailing_newline: bool) -> ParsedSet:
        raw_lines = text.split(newline)
        if trailing_newline and raw_lines and raw_lines[-1] == "":
            raw_lines.pop()

        # Pass 1: bucket raw values by base name and suffix.
        buckets: dict[str, dict[str, str]] = {}
        order: list[str] = []
        for line in raw_lines:
            s = line.strip()
            if not s or s.startswith(";") or "=" not in s:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            m = _SUFFIX.match(key)
            base, suf = (m.group("base"), m.group("suf")) if m else (key, "V")
            if base not in buckets:
                buckets[base] = {}
                order.append(base)
            buckets[base][suf] = val.strip()

        # Pass 2: build one Param per base, but keep the FIRST raw line of the
        # group as its anchor; every raw line is retained for lossless render.
        anchor_line: dict[str, str] = {}
        for line in raw_lines:
            s = line.strip()
            if not s or s.startswith(";") or "=" not in s:
                continue
            key = line.split("=", 1)[0].strip()
            m = _SUFFIX.match(key)
            base = m.group("base") if m else key
            anchor_line.setdefault(base, line)

        params: list[Param] = []
        for line in raw_lines:  # preserve original order & every line verbatim
            params.append(self._passthrough(line, buckets, anchor_line))
        return ParsedSet(
            dialect=self.name, codec=codec, bom=bom, newline=newline,
            trailing_newline=trailing_newline, params=params,
        )

    def _passthrough(self, line: str, buckets, anchor_line) -> Param:
        s = line.strip()
        if not s or s.startswith(";") or "=" not in s:
            return Param(name="", kind="comment", raw_line=line)
        key = line.split("=", 1)[0].strip()
        m = _SUFFIX.match(key)
        base = m.group("base") if m else key
        # Emit a semantic Param only on the anchor (first) line of the group;
        # the remaining ,F/,1/,2/,3 lines are preserved as comment records so
        # render stays byte-exact without duplicating the logical parameter.
        if anchor_line.get(base) != line:
            return Param(name=key, kind="comment", raw_line=line)

        b = buckets.get(base, {})
        cur = _coerce(b.get("V", b.get("F", "")))
        optimize = b.get("F", "0").strip() == "1"
        start, step, stop = _num(b.get("1", "")) if "1" in b else None, \
            _num(b.get("2", "")) if "2" in b else None, \
            _num(b.get("3", "")) if "3" in b else None
        if isinstance(cur, bool):
            kind = "bool"
        elif isinstance(cur, (int, float)):
            kind = "number"
        else:
            kind = "string"
        return Param(name=base, kind=kind, current=cur, start=start, step=step,
                     stop=stop, optimize=optimize, raw_line=line)

    def render(self, parsed: ParsedSet) -> bytes:
        body = parsed.newline.join(p.raw_line for p in parsed.params)
        if parsed.trailing_newline:
            body += parsed.newline
        return parsed.bom + body.encode(parsed.codec)
