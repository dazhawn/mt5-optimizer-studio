"""Streaming parser for MetaTrader 5 optimization XML exports.

MT5 exports the Strategy Tester "Optimization Results" grid as SpreadsheetML
2003 (an Excel XML dialect)::

    <Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" ...>
      <Worksheet ss:Name="Tester Optimizator Results">
        <Table>
          <Row>  <Cell><Data ss:Type="String">Pass</Data></Cell> ... </Row>  <!-- header -->
          <Row>  <Cell><Data ss:Type="Number">0</Data></Cell>    ... </Row>  <!-- a pass -->
          ...
        </Table>

These files are large (tens of MB, tens of thousands of passes), so we parse
with ``iterparse`` and drop each row from the tree after reading it — memory
stays flat regardless of file size.

Header columns (forward-optimization export):
    Pass | Forward Result | Back Result | Profit | Expected Payoff |
    Profit Factor | Recovery Factor | Sharpe Ratio | Custom | Equity DD % |
    Trades | <one column per EA input...>

Every non-metric column is an EA input and is collected into ``inputs``.
"""
from __future__ import annotations

from typing import Iterator
import xml.etree.ElementTree as ET

_SS = "urn:schemas-microsoft-com:office:spreadsheet"
_ROW = f"{{{_SS}}}Row"
_CELL = f"{{{_SS}}}Cell"
_DATA = f"{{{_SS}}}Data"
_TABLE = f"{{{_SS}}}Table"
_INDEX = f"{{{_SS}}}Index"
_TYPE = f"{{{_SS}}}Type"

# header text -> (Pass field name, python caster)
METRIC_COLUMNS: dict[str, tuple[str, type]] = {
    "Pass": ("pass_no", int),
    "Forward Result": ("forward_result", float),
    "Back Result": ("back_result", float),
    "Profit": ("profit", float),
    "Expected Payoff": ("expected_payoff", float),
    "Profit Factor": ("profit_factor", float),
    "Recovery Factor": ("recovery_factor", float),
    "Sharpe Ratio": ("sharpe", float),
    "Custom": ("custom_criterion", float),
    "Equity DD %": ("equity_dd_pct", float),
    "Trades": ("trades", int),
}


def _num(text: str | None) -> float | None:
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _cast(text: str | None, caster: type):
    v = _num(text)
    if v is None:
        return None
    return int(v) if caster is int else v


def _row_cells(row: ET.Element) -> list[tuple[int, str | None]]:
    """Return [(1-based column, text)] honoring sparse ``ss:Index`` cells."""
    out: list[tuple[int, str | None]] = []
    cursor = 0
    for cell in row:
        if cell.tag != _CELL:
            continue
        idx = cell.get(_INDEX)
        col = int(idx) if idx is not None else cursor + 1
        cursor = col
        data = cell.find(_DATA)
        out.append((col, data.text if data is not None else None))
    return out


def iter_passes(source) -> Iterator[dict]:
    """Yield one dict per pass: metric fields + ``inputs`` (name -> value).

    ``source`` may be a file path or a binary file-like object.
    """
    context = ET.iterparse(source, events=("start", "end"))
    table: ET.Element | None = None
    header: dict[int, str] | None = None

    for event, elem in context:
        if event == "start" and elem.tag == _TABLE and table is None:
            table = elem
            continue
        if event != "end" or elem.tag != _ROW:
            continue

        cells = _row_cells(elem)
        if header is None:
            header = {col: (text or "").strip() for col, text in cells}
        else:
            yield _build_pass(cells, header)

        # Free the row so memory stays flat on huge files.
        elem.clear()
        if table is not None:
            try:
                table.remove(elem)
            except ValueError:
                pass


def _build_pass(cells: list[tuple[int, str | None]], header: dict[int, str]) -> dict:
    metrics: dict = {field: None for field, _ in METRIC_COLUMNS.values()}
    inputs: dict = {}
    for col, text in cells:
        name = header.get(col)
        if name is None:
            continue
        if name in METRIC_COLUMNS:
            field, caster = METRIC_COLUMNS[name]
            metrics[field] = _cast(text, caster)
        else:
            num = _num(text)
            inputs[name] = num if num is not None else text
    metrics["inputs"] = inputs
    return metrics


_PASS_FIELDS = (
    "pass_no", "back_result", "forward_result", "profit", "expected_payoff",
    "profit_factor", "recovery_factor", "sharpe", "custom_criterion",
    "equity_dd_pct", "trades",
)


def persist_run(db, source, *, ea_name: str, symbol: str | None = None,
                timeframe: str | None = None, batch: int = 2000) -> tuple[int, int]:
    """Stream a run into the DB with batched bulk inserts. Returns (run_id, passes)."""
    from app import models  # local import avoids a circular dependency

    ea = db.query(models.EA).filter_by(name=ea_name).first()
    if ea is None:
        ea = models.EA(name=ea_name)
        db.add(ea)
        db.flush()
    run = models.Run(ea_id=ea.id, symbol=symbol, timeframe=timeframe)
    db.add(run)
    db.flush()

    rows: list[dict] = []
    n = 0
    for p in iter_passes(source):
        row = {f: p.get(f) for f in _PASS_FIELDS}
        row["run_id"] = run.id
        row["inputs_json"] = p["inputs"]
        rows.append(row)
        n += 1
        if len(rows) >= batch:
            db.bulk_insert_mappings(models.Pass, rows)
            rows.clear()
    if rows:
        db.bulk_insert_mappings(models.Pass, rows)
    db.commit()
    return run.id, n


def summarize(source, *, top_n: int = 5) -> dict:
    """Single streaming pass -> counts + leaderboards, without loading all rows.

    Cheap way to prove ingestion on a multi-GB-class file: keeps only the running
    top-N by back_result and by profit_factor plus aggregate counts.
    """
    import heapq

    count = 0
    with_forward = 0
    input_names: set[str] = set()
    pass_no_min: int | None = None
    pass_no_max: int | None = None
    top_back: list[tuple[float, int]] = []
    top_pf: list[tuple[float, int]] = []
    best_row: dict | None = None

    for p in iter_passes(source):
        count += 1
        if p.get("forward_result") is not None:
            with_forward += 1
        input_names.update(p["inputs"].keys())
        pn = p.get("pass_no")
        if pn is not None:
            pass_no_min = pn if pass_no_min is None else min(pass_no_min, pn)
            pass_no_max = pn if pass_no_max is None else max(pass_no_max, pn)
        if p.get("back_result") is not None:
            heapq.heappush(top_back, (p["back_result"], pn or -1))
            if len(top_back) > top_n:
                heapq.heappop(top_back)
        if p.get("profit_factor") is not None:
            heapq.heappush(top_pf, (p["profit_factor"], pn or -1))
            if len(top_pf) > top_n:
                heapq.heappop(top_pf)
            if best_row is None or p["profit_factor"] > best_row.get("profit_factor", -1):
                best_row = p

    return {
        "passes": count,
        "with_forward": with_forward,
        "input_columns": sorted(input_names),
        "pass_no_range": [pass_no_min, pass_no_max],
        "top_by_back_result": sorted(top_back, reverse=True),
        "top_by_profit_factor": sorted(top_pf, reverse=True),
        "best_by_pf_sample": best_row,
    }
