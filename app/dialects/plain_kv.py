"""Fallback dialect: plain ``key=value`` with no optimization metadata.

Always returns a low but non-zero confidence so it wins only when no richer
dialect recognizes the file. Nothing is marked optimizable; every assignment is
captured as a string input so the file still round-trips and displays.
"""
from __future__ import annotations

import re

from .base import Param, ParsedSet

_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)$")


class PlainKvDialect:
    name = "plain_kv"

    def detect(self, text: str) -> float:
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith(";") and "=" in s:
                return 0.05  # minimal: only used as a last resort
        return 0.0

    def parse(self, text: str, *, codec: str, bom: bytes, newline: str,
              trailing_newline: bool) -> ParsedSet:
        params: list[Param] = []
        raw_lines = text.split(newline)
        if trailing_newline and raw_lines and raw_lines[-1] == "":
            raw_lines.pop()
        for line in raw_lines:
            s = line.strip()
            if not s or s.startswith(";") or "=" not in line:
                params.append(Param(name="", kind="comment", raw_line=line))
                continue
            name, val = line.split("=", 1)
            v = val.strip()
            if v.lower() in ("true", "false"):
                cur, kind = v.lower() == "true", "bool"
            elif _NUMBER.match(v):
                cur, kind = float(v), "number"
            else:
                cur, kind = v, "string"
            params.append(Param(name=name.strip(), kind=kind, current=cur,
                                 raw_line=line))
        return ParsedSet(
            dialect=self.name, codec=codec, bom=bom, newline=newline,
            trailing_newline=trailing_newline, params=params,
        )

    def render(self, parsed: ParsedSet) -> bytes:
        body = parsed.newline.join(p.raw_line for p in parsed.params)
        if parsed.trailing_newline:
            body += parsed.newline
        return parsed.bom + body.encode(parsed.codec)
