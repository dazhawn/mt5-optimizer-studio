"""Range optimizer — the "cut future testing time" engine.

Given a *scored* run, look at how the top passes distribute over each EA input
and decide, per input:

* **fixed**   — the input never varied in this run; leave it alone.
* **pin**     — the top passes don't concentrate on it (it doesn't discriminate)
                or they all converged to one value → fix it and drop it from the
                search (``optimize=N``). This is where the time savings come from.
* **narrow**  — the top passes cluster in a sub-range → keep optimizing it, but
                tighten ``start``/``stop`` to that promising region.

The decisions are then written back into a *base* set file **in its original
dialect** (via the Phase 1 engine), producing a drop-in ``.set`` whose next MT5
optimization enumerates a fraction of the original combinations.

Everything is derived from the run's own pass data, so it's EA-agnostic.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict

from app.dialects import parse_set, render_set


@dataclass
class InputPlan:
    name: str
    kind: str            # "numeric" | "categorical"
    action: str          # "fixed" | "pin" | "narrow"
    reason: str
    sensitivity: float   # 0..1 (concentration for numeric, modal fraction for categorical)
    best_value: object
    distinct_all: int
    distinct_top: int
    new_start: float | None = None
    new_stop: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return v
    f = float(v)
    return str(int(f)) if f.is_integer() else f"{f:g}"


def analyze(passes: list[dict], *, top_frac: float = 0.10, min_top: int = 25,
            narrow_min_conc: float = 0.15) -> dict:
    """Analyze scored passes (already sorted best-first) into per-input plans.

    ``passes`` items: ``{"pass_no", "robustness_score", "inputs": {name: value}}``.
    """
    n = len(passes)
    if n == 0:
        return {"top_used": 0, "kept": 0, "best_pass": None, "plans": [],
                "summary": {"fixed": 0, "pin": 0, "narrow": 0}}

    k = min(n, max(min_top, int(n * top_frac)))
    top = passes[:k]
    best = passes[0]

    names: set[str] = set()
    for p in passes:
        names.update(p["inputs"].keys())

    plans: list[InputPlan] = []
    for name in sorted(names):
        all_vals = [p["inputs"].get(name) for p in passes if p["inputs"].get(name) is not None]
        top_vals = [p["inputs"].get(name) for p in top if p["inputs"].get(name) is not None]
        if not all_vals or not top_vals:
            continue
        best_val = best["inputs"].get(name)

        if all(_is_number(v) for v in all_vals):
            a = [float(v) for v in all_vals]
            t = [float(v) for v in top_vals]
            da, dt = sorted(set(a)), sorted(set(t))
            arange = max(a) - min(a)
            if len(da) <= 1:
                plans.append(InputPlan(name, "numeric", "fixed",
                                       "never varied in this run", 0.0, best_val,
                                       len(da), len(dt)))
                continue
            tmin, tmax = min(t), max(t)
            conc = 1 - ((tmax - tmin) / arange) if arange > 0 else 1.0
            if len(dt) == 1:
                plans.append(InputPlan(name, "numeric", "pin",
                                       f"top {k} passes all use {_fmt(dt[0])}",
                                       1.0, dt[0], len(da), len(dt)))
            elif conc < narrow_min_conc:
                plans.append(InputPlan(name, "numeric", "pin",
                                       f"low impact — top still span {(1 - conc) * 100:.0f}% of range",
                                       conc, best_val, len(da), len(dt)))
            else:
                plans.append(InputPlan(name, "numeric", "narrow",
                                       f"top cluster in {(1 - conc) * 100:.1f}% of the full range",
                                       conc, best_val, len(da), len(dt),
                                       new_start=tmin, new_stop=tmax))
        else:
            ca, ct = set(all_vals), set(top_vals)
            modal_val, modal_cnt = Counter(top_vals).most_common(1)[0]
            frac = modal_cnt / len(top_vals)
            if len(ca) <= 1:
                plans.append(InputPlan(name, "categorical", "fixed",
                                       "never varied in this run", 0.0, best_val,
                                       len(ca), len(ct)))
            else:
                plans.append(InputPlan(name, "categorical", "pin",
                                       f"top {frac * 100:.0f}% use {modal_val}",
                                       frac, modal_val, len(ca), len(ct)))

    summary = {a: sum(1 for p in plans if p.action == a)
               for a in ("fixed", "pin", "narrow")}
    return {"top_used": k, "kept": n,
            "best_pass": {"pass_no": best["pass_no"],
                          "robustness_score": best.get("robustness_score")},
            "plans": plans, "summary": summary}


def _edit_pipe_line(raw: str, *, current=None, start=None, stop=None,
                    optimize: bool | None = None) -> str:
    """Edit an ``name=cur||start||step||stop||flag`` line, preserving untouched
    tokens (notably ``step``) exactly."""
    name, rhs = raw.split("=", 1)
    parts = rhs.split("||")
    while len(parts) < 5:
        parts.append("")
    if current is not None:
        parts[0] = _fmt(current)
    if start is not None:
        parts[1] = _fmt(start)
    if stop is not None:
        parts[3] = _fmt(stop)
    if optimize is not None:
        parts[4] = "Y" if optimize else "N"
    return f"{name}={'||'.join(parts)}"


def search_space_size(parsed) -> int:
    """Product of step counts over every optimize=Y pipe input (guards bad ranges)."""
    total = 1
    for p in parsed.params:
        if "||" not in p.raw_line:
            continue
        parts = p.raw_line.split("=", 1)[1].split("||")
        if len(parts) < 5 or parts[4].strip().upper() != "Y":
            continue
        try:
            start, step, stop = float(parts[1]), float(parts[2]), float(parts[3])
        except ValueError:
            continue
        steps = int((stop - start) / step) + 1 if step > 0 and stop >= start else 1
        total *= max(1, steps)
    return total


def apply_to_setfile(base_raw: bytes, plans: list[InputPlan]) -> dict:
    """Write the plans into a base set file. Returns refined bytes + space sizes."""
    before = search_space_size(parse_set(base_raw))
    parsed = parse_set(base_raw)
    by_name = {p.name: p for p in plans}

    applied = 0
    for param in parsed.params:
        if "||" not in param.raw_line:
            continue
        plan = by_name.get(param.name)
        if plan is None or plan.action == "fixed":
            continue
        if plan.action == "pin":
            param.raw_line = _edit_pipe_line(param.raw_line,
                                             current=plan.best_value, optimize=False)
            param.optimize = False
        elif plan.action == "narrow":
            param.raw_line = _edit_pipe_line(param.raw_line, current=plan.best_value,
                                             start=plan.new_start, stop=plan.new_stop,
                                             optimize=True)
            param.optimize, param.start, param.stop = True, plan.new_start, plan.new_stop
        applied += 1

    after = search_space_size(parsed)
    return {"refined_bytes": render_set(parsed), "before": before, "after": after,
            "applied": applied}


def refine_run(db, run_id: int, *, base_raw: bytes | None = None,
               top_frac: float = 0.10, min_top: int = 25) -> dict:
    """Analyze a scored run and (if a base set is given) emit a narrowed set file."""
    from app import models

    rows = (db.query(models.Pass)
            .filter(models.Pass.run_id == run_id,
                    models.Pass.robustness_score.isnot(None))
            .order_by(models.Pass.robustness_score.desc())
            .all())
    passes = [{"pass_no": r.pass_no, "robustness_score": r.robustness_score,
               "inputs": r.inputs_json or {}} for r in rows]

    analysis = analyze(passes, top_frac=top_frac, min_top=min_top)

    result = {
        "run_id": run_id,
        "top_used": analysis["top_used"],
        "kept": analysis["kept"],
        "best_pass": analysis["best_pass"],
        "summary": analysis["summary"],
        "plans": [p.as_dict() for p in analysis["plans"]],
    }

    if base_raw is not None:
        out = apply_to_setfile(base_raw, analysis["plans"])
        ratio = (out["before"] / out["after"]) if out["after"] else None
        result["search_space"] = {
            "before": out["before"], "after": out["after"],
            "reduction_x": round(ratio, 1) if ratio else None,
        }
        result["_refined_bytes"] = out["refined_bytes"]

    return result
