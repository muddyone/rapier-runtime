"""Reconcile stage — check that every stated total agrees with its parts.

Why this stage exists, and why it is narrow
-------------------------------------------
A 2026-07-21 study ran three reviewers over four real planning documents, scored
per-relation against 74 known cross-claim couplings by two independent vendors:

  * one review pass (what ships today)      — engaged 16% of couplings
  * four unstructured passes, findings pooled — 25%
  * a full decompose/search/recompose tree   — 32%

Pooling four plain re-reads matched or beat the tree on the documents whose couplings
needed *judgement* (what must precede what; which rules conflict). The tree won
decisively, on both scorers, on exactly one document — the billing plan, whose couplings
are **arithmetic**: a total stated in one place, its components stated in another.

So this stage implements only that. It is not Decompose, it is not Recompose, and it does
not attempt the interpretive relation classes, because the evidence did not support
building those. For those, run the review more than once — that was as good or better and
far cheaper.

The design change the study forced
----------------------------------
In the study the model did the extraction *and* the arithmetic, which makes the check just
another opinion. Here the model only **extracts** — labels, values, quotes, locations — and
**Python does the arithmetic**. A mismatch is then a fact about numbers, not a judgement,
and it is reproducible from the recorded extraction without re-running any model.

Anything the model cannot ground in two quoted locations is reported as ``unverifiable``
rather than silently dropped or silently passed. Silence and a pass must never look alike.
"""
from __future__ import annotations

import ast
import math
from decimal import Decimal, InvalidOperation
import operator
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .._json import parse_json_lenient
from ..envelope import Envelope
from ..stage import StageContext, TransformStage, register_stage

# Floating-point slack for a reconciliation to count as agreeing. Money and counts in
# planning documents are routinely rounded when summarised; 0.5% keeps honest rounding
# from being reported as a defect while still catching a transposed digit.
# The 0.5% is measured against the figures themselves, with no floor: a floor of 1 once
# let any figure under 1 "agree" with almost anything (6e-5 against 0.004). Small figures
# get their rounding slack from _rounding_allowance instead.
DEFAULT_TOLERANCE = 0.005


def _rounding_allowance(stated: float) -> float:
    """Half a unit in the last digit the stated figure shows: 6e-05 -> 5e-06, 0.12 -> 0.005.

    Whole numbers count as written to the unit, never to their trailing zeros, so "a total
    of 1000" is not read as "somewhere from 500 to 1500"."""
    try:
        exponent = Decimal(repr(stated)).normalize().as_tuple().exponent
    except (InvalidOperation, ValueError):
        return 0.0
    if not isinstance(exponent, int):  # NaN / infinity
        return 0.0
    return 0.5 * 10.0 ** min(exponent, 0)


def _agrees(stated: float, computed: float, tolerance: float) -> bool:
    delta = abs(computed - stated)
    allowed = max(tolerance * max(abs(stated), abs(computed)), _rounding_allowance(stated))
    # A hair of float slack so an exact half-unit (0.0835 stated as 0.083) is not a defect.
    return delta <= allowed * (1 + 1e-9) + 1e-12

EXTRACT_SYSTEM = """You extract ARITHMETIC RELATIONS from a document. You do not judge them.

Find every place where a number stated in ONE location should be derivable from numbers
stated in ANOTHER location: a total and its line items, a count and the things counted, a
percentage and its base, a rate applied to a quantity, a result worked out from a formula.

For each one, report the aggregate and its components, each with the exact quote and where
it appears. If the components are not stated somewhere in the document, do not invent them
-- report the aggregate with an empty component list.

Choose the operation the text ACTUALLY performs. Never default to "sum":
  - "sum": ONLY where the text asserts a total of its parts ("total", "in all", "a + b = c").
  - "difference": the aggregate is the first component minus the rest ("a - b", "a 20-point gap").
  - "product", "count", "percent_of" ([part, whole]): as named.
  - "expression": anything else written as a formula -- roots, powers, quotients, mixed
    operators, a +/- interval bound. Put the formula in "expr" exactly as the text writes it,
    as one Python arithmetic expression over numeric literals (sqrt() allowed; write
    (1.96 + 0.84)**2 for a square, 0.20 + 1.96*0.120 for the upper bound of 0.20 +/- 1.96*0.120).
  - "other": the operation cannot be recovered from the text. It will be reported as not checked.

Do NOT calculate anything. Do NOT say whether it is correct. Extraction only.

STRICT JSON:
{"relations":[{"label":str,
"operation":"sum"|"difference"|"product"|"count"|"percent_of"|"expression"|"other",
"expr":str (only for "expression"),
"aggregate":{"value":number,"quote":str,"location":str},
"components":[{"label":str,"value":number,"quote":str,"location":str}]}]}"""

# Symbols that mean a stated figure is a formula, not a total. A "sum" whose own quote carries
# one of these was mislabelled by the extractor; summing its operands would report a correct
# result as wrong, so it is left unchecked instead.
# "/" is left out because unit rates ("$40/seat") sit in honest totals; Markdown bold is
# stripped before the test so "**Total: 100**" is not read as multiplication.
_NON_ADDITIVE = re.compile(r"[\u00b7\u00d7*\u00f7\u221a^\u00b1\u00b2\u00b3\u2074]|\bsqrt\b")


def _looks_like_formula(quote: str) -> bool:
    return bool(_NON_ADDITIVE.search(re.sub(r"\*\*|__", "", quote)))

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "abs": abs, "min": min, "max": max}


def _eval_expr(expr: str) -> float | None:
    """Evaluate a numeric-literal arithmetic expression. No names, no attributes, no arbitrary
    calls; None for anything that does not parse or evaluate cleanly."""
    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) \
                and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id in _FUNCS and not n.keywords:
            return float(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError(type(n).__name__)

    try:
        out = ev(ast.parse((expr or "").strip(), mode="eval").body)
    except Exception:  # noqa: BLE001 -- any failure means "could not evaluate", never a crash
        return None
    return out if isinstance(out, float) and math.isfinite(out) else None


@dataclass
class Reconciliation:
    """One arithmetic coupling and what Python made of it."""

    label: str
    operation: str
    stated: float | None
    computed: float | None
    components: list[dict[str, Any]] = field(default_factory=list)
    status: str = "unverifiable"  # agrees | mismatch | unverifiable
    delta: float | None = None
    aggregate_quote: str = ""
    aggregate_location: str = ""
    expr: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compute(operation: str, values: list[float]) -> float | None:
    """Apply the stated operation to the component values, or None if it cannot be applied."""
    if not values:
        return None
    if operation == "sum":
        return sum(values)
    if operation == "difference":
        return values[0] - sum(values[1:])
    if operation == "count":
        return float(len(values))
    if operation == "product":
        out = 1.0
        for v in values:
            out *= v
        return out
    if operation == "percent_of":
        # Convention: [part, whole] -> the percentage the part is of the whole.
        if len(values) != 2 or values[1] == 0:
            return None
        return values[0] / values[1] * 100.0
    return None


def reconcile_relations(
    relations: list[dict[str, Any]], tolerance: float = DEFAULT_TOLERANCE
) -> list[Reconciliation]:
    """Do the arithmetic. Pure function: no model, no network, fully reproducible.

    This is deliberately the only place a pass/fail is decided, so the verdict can be
    re-derived from a stored extraction long after the run.
    """
    out: list[Reconciliation] = []
    for rel in relations or []:
        agg = rel.get("aggregate") or {}
        comps = [c for c in (rel.get("components") or []) if isinstance(c, dict)]
        # No operation means none could be recovered -- never assume a sum.
        op = str(rel.get("operation") or "other")
        r = Reconciliation(
            label=str(rel.get("label") or "(unlabelled)"),
            operation=op,
            stated=_num(agg.get("value")),
            computed=None,
            components=comps,
            aggregate_quote=str(agg.get("quote") or ""),
            aggregate_location=str(agg.get("location") or ""),
            expr=str(rel.get("expr") or ""),
        )

        values = [v for v in (_num(c.get("value")) for c in comps) if v is not None]
        if r.stated is None:
            r.note = "aggregate value not extractable as a number"
        elif op == "other":
            r.note = "not checked: the operation could not be recovered from the text"
        elif op == "sum" and _looks_like_formula(r.aggregate_quote):
            r.note = "not checked: the stated figure is a formula, not a total of its parts"
        elif op == "expression":
            r.computed = _eval_expr(r.expr)
            if r.computed is None:
                r.note = "not checked: the formula could not be evaluated"
        elif not values:
            r.note = "components were not stated in the document"
        else:
            r.computed = _compute(op, values)
            if r.computed is None:
                r.note = f"operation {op!r} could not be applied to {len(values)} component(s)"
        if r.computed is not None:
            r.delta = r.computed - r.stated
            r.status = "agrees" if _agrees(r.stated, r.computed, tolerance) else "mismatch"
        out.append(r)
    return out


def _num(v: Any) -> float | None:
    try:
        if isinstance(v, bool):  # bool is an int subclass; never a quantity here
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize(results: list[Reconciliation]) -> dict[str, Any]:
    counts = {"agrees": 0, "mismatch": 0, "unverifiable": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    # A verdict of PASS requires that something was actually checked. An extraction that
    # grounded nothing must not read as a clean bill of health.
    if counts["mismatch"]:
        verdict = "MISMATCH"
    elif counts["agrees"]:
        verdict = "PASS"
    else:
        verdict = "UNCHECKED"
    return {"counts": counts, "verdict": verdict, "checked": counts["agrees"] + counts["mismatch"]}


@register_stage("reconcile")
class ReconcileStage(TransformStage):
    """Extract arithmetic relations with a model, then verify them in code."""

    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        material = ctx.config.get("material") or env.recommendation or env.request
        tolerance = float(ctx.config.get("tolerance", DEFAULT_TOLERANCE))
        client = ctx.clients.get("author")

        if client is None:
            env.add_trace("reconcile", self.kind, "no client; nothing extracted", checked=0)
            env.meta["reconcile"] = {"counts": {"agrees": 0, "mismatch": 0, "unverifiable": 0},
                                     "verdict": "UNCHECKED", "checked": 0, "relations": []}
            return env

        resp = client.complete(system=EXTRACT_SYSTEM, prompt=material)
        payload = parse_json_lenient(resp.text) or {}
        results = reconcile_relations(payload.get("relations") or [], tolerance=tolerance)
        summary = summarize(results)
        summary["relations"] = [r.to_dict() for r in results]
        env.meta["reconcile"] = summary

        env.add_trace(
            "reconcile",
            self.kind,
            f"{summary['verdict']}: {summary['counts']['mismatch']} mismatch, "
            f"{summary['counts']['agrees']} agree, {summary['counts']['unverifiable']} unverifiable",
            **summary["counts"],
        )
        return env
