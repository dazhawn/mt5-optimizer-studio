"""Dialect registry + the public parse/render API.

Public surface (everything else imports from here):

    parse_set(raw: bytes)   -> ParsedSet
    render_set(parsed)      -> bytes
    detect_dialect(raw)     -> (name, confidence)

Encoding is sniffed once, up front, then the highest-confidence dialect handles
the decoded text. Add a new EA format by implementing the SetDialect protocol
and appending an instance to ``_REGISTRY``.
"""
from __future__ import annotations

from .base import Param, ParsedSet, SetDialect
from .inline_pipe import InlinePipeDialect
from .plain_kv import PlainKvDialect
from .vanilla_mt5 import VanillaMt5Dialect

# Order matters only as a tie-breaker; detection confidence decides the winner.
_REGISTRY: list[SetDialect] = [
    VanillaMt5Dialect(),
    InlinePipeDialect(),
    PlainKvDialect(),
]


def sniff_encoding(raw: bytes) -> tuple[str, bytes]:
    """Return (python_codec, bom_bytes) for a raw set file."""
    if raw[:2] == b"\xff\xfe":
        return "utf-16-le", raw[:2]
    if raw[:2] == b"\xfe\xff":
        return "utf-16-be", raw[:2]
    if raw[:3] == b"\xef\xbb\xbf":
        return "utf-8", raw[:3]  # utf-8 with BOM; body encodes as plain utf-8
    # No BOM: detect UTF-16LE-without-BOM by its characteristic null bytes.
    sample = raw[:512]
    if sample and sample.count(0) > len(sample) // 4:
        return "utf-16-le", b""
    return "utf-8", b""


def _decode(raw: bytes) -> tuple[str, str, bytes, str, bool]:
    codec, bom = sniff_encoding(raw)
    body = raw[len(bom):]
    text = body.decode(codec)
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing_newline = text.endswith(newline)
    return text, codec, bom, newline, trailing_newline


def detect_dialect(raw: bytes) -> tuple[str, float]:
    text, *_ = _decode(raw)
    best_name, best_conf = "plain_kv", 0.0
    for d in _REGISTRY:
        c = d.detect(text)
        if c > best_conf:
            best_name, best_conf = d.name, c
    return best_name, best_conf


def parse_set(raw: bytes) -> ParsedSet:
    text, codec, bom, newline, trailing = _decode(raw)
    best: SetDialect | None = None
    best_conf = -1.0
    for d in _REGISTRY:
        c = d.detect(text)
        if c > best_conf:
            best, best_conf = d, c
    assert best is not None
    return best.parse(text, codec=codec, bom=bom, newline=newline,
                      trailing_newline=trailing)


def render_set(parsed: ParsedSet) -> bytes:
    for d in _REGISTRY:
        if d.name == parsed.dialect:
            return d.render(parsed)
    raise ValueError(f"unknown dialect: {parsed.dialect}")


__all__ = [
    "Param", "ParsedSet", "SetDialect",
    "parse_set", "render_set", "detect_dialect", "sniff_encoding",
]
