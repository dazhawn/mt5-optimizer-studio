"""Inline-pipe dialect (the "Dark Moon" family and similar EAs).

Line grammar::

    ; comment / header line
    Name=DARK MOON                                  -> label
    Separo0=------------------------------------     -> divider
    DarkAbsoluteTrendLink=https://...                -> string input
    Lots=0.01||0.01||0.01||0.100000||Y               -> number, optimize=Y
    MoneyManagement=false||false||0||true||Y         -> bool, optimize=Y
    MagicNumber=122334||122334||1||1223340||N        -> number, optimize=N

Optimizable inputs encode ``current||start||step||stop||{Y|N}`` where the final
token is the MT5 "optimize this input" flag.
"""
from __future__ import annotations

import re

from .base import Param, ParsedSet

_DIVIDER = re.compile(r"^-{5,}$")
_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")


def _looks_numeric(s: str) -> bool:
    return bool(_NUMBER.match(s.strip()))


def _coerce(s: str) -> float | bool | str:
    t = s.strip()
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    if _looks_numeric(t):
        f = float(t)
        return int(f) if f.is_integer() and "." not in t else f
    return t


class InlinePipeDialect:
    name = "inline_pipe"

    def detect(self, text: str) -> float:
        param_lines = 0
        pipe_lines = 0
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith(";") or "=" not in s:
                continue
            param_lines += 1
            rhs = s.split("=", 1)[1]
            if "||" in rhs and rhs.rsplit("||", 1)[-1].strip().upper() in ("Y", "N"):
                pipe_lines += 1
        if param_lines == 0:
            return 0.0
        return pipe_lines / param_lines

    def parse(self, text: str, *, codec: str, bom: bytes, newline: str,
              trailing_newline: bool) -> ParsedSet:
        params: list[Param] = []
        for line in text.split(newline):
            params.append(self._parse_line(line))
        if trailing_newline and params and params[-1].raw_line == "":
            # split() on a trailing-newline string yields a final empty element;
            # drop it so it isn't double-counted (render re-adds the newline).
            params.pop()
        return ParsedSet(
            dialect=self.name, codec=codec, bom=bom, newline=newline,
            trailing_newline=trailing_newline, params=params,
        )

    def _parse_line(self, line: str) -> Param:
        s = line.strip()
        if not s or s.startswith(";") or "=" not in line:
            return Param(name="", kind="comment", raw_line=line)

        name, rhs = line.split("=", 1)
        name = name.strip()

        if "||" in rhs:
            parts = [p.strip() for p in rhs.split("||")]
            flag = parts[-1].upper() if parts else "N"
            optimize = flag == "Y"
            cur_raw = parts[0] if len(parts) > 0 else ""
            start = parts[1] if len(parts) > 1 else None
            step = parts[2] if len(parts) > 2 else None
            stop = parts[3] if len(parts) > 3 else None
            cur = _coerce(cur_raw)
            if isinstance(cur, bool):
                kind = "bool"
            elif isinstance(cur, (int, float)):
                kind = "number"
            else:
                kind = "string"
            return Param(
                name=name, kind=kind, current=cur,
                start=float(start) if start and _looks_numeric(start) else None,
                step=float(step) if step and _looks_numeric(step) else None,
                stop=float(stop) if stop and _looks_numeric(stop) else None,
                optimize=optimize, raw_line=line,
            )

        # No pipes: a label, divider, or genuine string input.
        value = rhs.strip()
        if _DIVIDER.match(value):
            kind = "divider"
        elif value and (value.isupper() or " " in value) and "://" not in value:
            kind = "label"
        else:
            kind = "string"
        return Param(name=name, kind=kind, current=value, raw_line=line)

    def render(self, parsed: ParsedSet) -> bytes:
        body = parsed.newline.join(p.raw_line for p in parsed.params)
        if parsed.trailing_newline:
            body += parsed.newline
        return parsed.bom + body.encode(parsed.codec)
