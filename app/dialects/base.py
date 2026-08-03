"""Core data model shared by every set-file dialect.

Everything downstream of parsing (scoring, range refinement, API) speaks ONLY
this normalized model. Dialects are the only code that knows about on-disk
formats. A dialect must round-trip: ``render(parse(raw)) == raw`` for any file
it claims (via ``detect``) to understand, as long as no Param is mutated.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

ParamKind = Literal[
    "number",   # numeric optimizable input
    "bool",     # true/false optimizable input
    "enum",     # integer-coded enum input
    "string",   # free-text input the EA actually reads (e.g. a URL)
    "label",    # pseudo-input used as a section header in the dialog
    "divider",  # pseudo-input rendered as a separator line (----)
    "comment",  # a comment / blank line, preserved verbatim
]


@dataclass
class Param:
    """One normalized input parameter.

    ``raw_line`` is preserved so an untouched parameter re-renders byte-for-byte.
    Regeneration from the typed fields is only used when a value is deliberately
    changed (e.g. by the range optimizer), where the original formatting no
    longer needs to be reproduced.
    """

    name: str
    kind: ParamKind
    current: float | bool | str | None = None
    start: float | None = None
    step: float | None = None
    stop: float | None = None
    optimize: bool = False
    raw_line: str = ""

    @property
    def is_tunable(self) -> bool:
        """Has a numeric range at all (excludes labels/dividers/comments/strings).

        Tunable ≠ selected: the EA author may fill in a range but leave the MT5
        optimize flag off. Use ``optimize`` to know what's actually being searched.
        """
        return self.kind in ("number", "bool", "enum") and self.start is not None


@dataclass
class ParsedSet:
    """A fully parsed set file plus the byte-level facts needed to re-emit it."""

    dialect: str
    codec: str            # python codec used for the body, e.g. "utf-16-le"
    bom: bytes            # leading BOM bytes to re-prepend, or b""
    newline: str          # "\r\n" or "\n"
    trailing_newline: bool
    params: list[Param]

    def tunable(self) -> list[Param]:
        """Every input with a range, regardless of the optimize flag."""
        return [p for p in self.params if p.is_tunable]

    def optimizable(self) -> list[Param]:
        """The real search space: tunable inputs currently flagged to optimize."""
        return [p for p in self.params if p.is_tunable and p.optimize]

    def by_name(self, name: str) -> Param | None:
        for p in self.params:
            if p.name == name:
                return p
        return None


@runtime_checkable
class SetDialect(Protocol):
    """Pluggable format handler. Register instances in the dialect registry."""

    name: str

    def detect(self, text: str) -> float:
        """Return confidence 0..1 that this dialect owns ``text`` (BOM stripped)."""
        ...

    def parse(self, text: str, *, codec: str, bom: bytes, newline: str,
              trailing_newline: bool) -> ParsedSet:
        ...

    def render(self, parsed: ParsedSet) -> bytes:
        ...
