"""Tests for the reconcile stage.

The arithmetic is a pure function on purpose, so most of this needs no model and no
network: a stored extraction must always produce the same verdict.
"""
from __future__ import annotations

from rapier.stages.reconcile import (
    DEFAULT_TOLERANCE,
    reconcile_relations,
    summarize,
)


def rel(label, op, total, comps, **kw):
    return {
        "label": label,
        "operation": op,
        "aggregate": {"value": total, "quote": kw.get("quote", f"total is {total}"),
                      "location": kw.get("loc", "§1")},
        "components": [{"label": f"c{i}", "value": v, "quote": f"{v}", "location": "§2"}
                       for i, v in enumerate(comps)],
    }


def test_sum_that_agrees():
    r = reconcile_relations([rel("seats", "sum", 100, [40, 60])])[0]
    assert r.status == "agrees"
    assert r.computed == 100
    assert r.delta == 0


def test_sum_that_does_not_agree_is_a_mismatch():
    r = reconcile_relations([rel("seats", "sum", 100, [40, 50])])[0]
    assert r.status == "mismatch"
    assert r.computed == 90
    assert r.delta == -10


def test_rounding_inside_tolerance_is_not_a_defect():
    # 999.9 vs 1000 is honest rounding in a summary line, not a transposed digit.
    r = reconcile_relations([rel("revenue", "sum", 1000.0, [500.0, 499.9])])[0]
    assert r.status == "agrees"


def test_transposed_digit_is_caught():
    r = reconcile_relations([rel("revenue", "sum", 1000.0, [500.0, 590.0])])[0]
    assert r.status == "mismatch"


def test_small_figures_are_not_waved_through():
    """The old floor of 1 let any figure under 1 agree with nearly anything."""
    assert reconcile_relations([_expr("p", 6e-5, "0.004")])[0].status == "mismatch"
    assert reconcile_relations([rel("share", "sum", 0.12, [0.10, 0.05])])[0].status == "mismatch"


def test_small_figures_rounded_to_their_shown_digits_agree():
    cases = [(6e-5, "4*0.025**3*0.975 + 0.025**4"),  # 6.13e-5, written to one digit
             (0.083, "0.167*0.5"),                    # 0.0835, an exact half-unit
             (0.12, "sqrt(0.435/30)")]                # 0.1204
    for stated, expr in cases:
        assert reconcile_relations([_expr("x", stated, expr)])[0].status == "agrees", expr


def test_rounding_allowance_reads_whole_numbers_to_the_unit():
    from rapier.stages.reconcile import _rounding_allowance
    assert _rounding_allowance(6e-5) == 5e-6
    assert _rounding_allowance(0.12) == 0.005
    assert _rounding_allowance(85.0) == 0.5
    assert _rounding_allowance(1000.0) == 0.5


def test_count_operation():
    assert reconcile_relations([rel("phases", "count", 3, [1, 1, 1])])[0].status == "agrees"
    assert reconcile_relations([rel("phases", "count", 4, [1, 1, 1])])[0].status == "mismatch"


def test_product_operation():
    r = reconcile_relations([rel("cost", "product", 250, [50, 5])])[0]
    assert r.status == "agrees"


def test_percent_of_operation():
    r = reconcile_relations([rel("share", "percent_of", 25.0, [25, 100])])[0]
    assert r.status == "agrees"
    bad = reconcile_relations([rel("share", "percent_of", 30.0, [25, 100])])[0]
    assert bad.status == "mismatch"


def test_percent_of_with_zero_base_is_unverifiable_not_a_crash():
    r = reconcile_relations([rel("share", "percent_of", 10.0, [5, 0])])[0]
    assert r.status == "unverifiable"


def test_missing_components_are_unverifiable_never_a_pass():
    """The failure this guards: an aggregate nobody could ground reading as verified."""
    r = reconcile_relations([rel("total", "sum", 100, [])])[0]
    assert r.status == "unverifiable"
    assert "not stated" in r.note


def test_non_numeric_aggregate_is_unverifiable():
    bad = {"label": "x", "operation": "sum",
           "aggregate": {"value": "about a hundred", "quote": "q", "location": "§1"},
           "components": [{"label": "c", "value": 40, "quote": "q", "location": "§2"}]}
    assert reconcile_relations([bad])[0].status == "unverifiable"


def test_booleans_are_not_treated_as_quantities():
    r = reconcile_relations([rel("flags", "sum", 1, [True, False])])[0]
    assert r.status == "unverifiable"


def test_unknown_operation_is_unverifiable():
    r = reconcile_relations([rel("x", "interpolate", 10, [1, 2])])[0]
    assert r.status == "unverifiable"
    assert "could not be applied" in r.note


def test_difference_operation():
    """A gap is first-minus-rest, not a sum (the "20-point effect" false alarm)."""
    r = reconcile_relations([rel("effect", "difference", 0.20, [0.45, 0.25])])[0]
    assert r.status == "agrees"


def _expr(label, total, expr, quote="q"):
    return {"label": label, "operation": "expression", "expr": expr,
            "aggregate": {"value": total, "quote": quote, "location": "§1"}, "components": []}


def test_expression_results_that_are_right_agree():
    """The five correct figures valkyrie-sparring-1 saw flagged as wrong in a stats answer."""
    results = reconcile_relations([
        _expr("SE", 0.120, "sqrt((0.45*0.55 + 0.25*0.75)/30)"),
        _expr("interval upper", 0.435, "0.20 + 1.96*0.120"),
        _expr("n per arm", 85, "((1.96 + 0.84)**2 * 0.435) / 0.04"),
        _expr("P(>=3 of 4)", 6e-5, "4*0.025**3*0.975 + 0.025**4"),
    ])
    assert [r.status for r in results] == ["agrees"] * 4


def test_expression_that_is_wrong_is_a_mismatch():
    r = reconcile_relations([_expr("n per arm", 60, "((1.96 + 0.84)**2 * 0.435) / 0.04")])[0]
    assert r.status == "mismatch"
    assert r.expr


def test_expression_that_cannot_be_evaluated_is_not_checked():
    for bad in ["", "p1*(1-p1)", "__import__('os')", "(-1)**0.5", "1/0", "9.0**9**9"]:
        r = reconcile_relations([_expr("x", 1, bad)])[0]
        assert r.status == "unverifiable", bad
        assert "not checked" in r.note


def test_sum_whose_quote_is_a_formula_is_not_checked():
    """A mislabelled formula must not be reported as a wrong total."""
    r = reconcile_relations([rel("SE", "sum", 0.120, [0.45, 0.25, 30],
                                 quote="\u221a[ (0.45\u00b70.55 + 0.25\u00b70.75)/30 ] \u2248 0.120")])[0]
    assert r.status == "unverifiable"
    assert "formula" in r.note


def test_bold_and_unit_rates_do_not_hide_a_real_total():
    r = reconcile_relations([rel("cost", "sum", 100, [40, 50],
                                 quote="**Total: $100/seat**")])[0]
    assert r.status == "mismatch"


def test_missing_or_other_operation_is_not_checked_never_summed():
    no_op = rel("x", None, 10, [3, 4])
    other = rel("x", "other", 10, [3, 4])
    for r in reconcile_relations([no_op, other]):
        assert r.status == "unverifiable"
        assert "not checked" in r.note


def test_empty_input_is_unchecked_not_pass():
    assert summarize(reconcile_relations([]))["verdict"] == "UNCHECKED"


def test_all_unverifiable_is_unchecked_not_pass():
    """Extraction that grounded nothing must not read as a clean bill of health."""
    s = summarize(reconcile_relations([rel("t", "sum", 100, [])]))
    assert s["verdict"] == "UNCHECKED"
    assert s["checked"] == 0


def test_verdicts_and_counts():
    results = reconcile_relations([
        rel("a", "sum", 100, [40, 60]),
        rel("b", "sum", 100, [40, 50]),
        rel("c", "sum", 100, []),
    ])
    s = summarize(results)
    assert s["verdict"] == "MISMATCH"
    assert s["counts"] == {"agrees": 1, "mismatch": 1, "unverifiable": 1}
    assert s["checked"] == 2


def test_verdict_is_reproducible_from_a_stored_extraction():
    """The point of doing arithmetic in code: same input, same verdict, no model."""
    stored = [rel("a", "sum", 100, [40, 50])]
    first = summarize(reconcile_relations(stored))
    second = summarize(reconcile_relations(stored))
    assert first == second


def test_tolerance_is_configurable():
    r = rel("x", "sum", 100.0, [95.0])
    assert reconcile_relations([r], tolerance=DEFAULT_TOLERANCE)[0].status == "mismatch"
    assert reconcile_relations([r], tolerance=0.10)[0].status == "agrees"


# ── report surface ────────────────────────────────────────────────────────────
# A gate nobody can see is not a gate. These pin what the trust rider says, and in
# particular that "checked and clean" never renders the same as "could not check".

from rapier.envelope import Envelope
from rapier.stages.resolver.compose import _reconcile_body


def _flat(text):
    """Report bodies are hard-wrapped to the measure, so a phrase can straddle a newline.
    Assert against normalised whitespace or the test breaks on the wrap, not the wording."""
    return " ".join(text.split())


def _env(meta):
    e = Envelope(request="x")
    e.meta["reconcile"] = meta
    return e


def test_report_distinguishes_clean_from_unchecked():
    clean = _reconcile_body(_env({"verdict": "PASS", "checked": 3,
                                  "counts": {"agrees": 3, "mismatch": 0, "unverifiable": 0},
                                  "relations": []}))
    unchecked = _reconcile_body(_env({"verdict": "UNCHECKED", "checked": 0,
                                      "counts": {"agrees": 0, "mismatch": 0, "unverifiable": 2},
                                      "relations": []}))
    assert "agreed" in _flat(clean)
    assert "not a pass" in _flat(unchecked)
    assert clean != unchecked


def test_report_quotes_both_sides_of_a_mismatch():
    body = _reconcile_body(_env({
        "verdict": "MISMATCH", "checked": 1,
        "counts": {"agrees": 0, "mismatch": 1, "unverifiable": 0},
        "relations": [{"label": "annual cost", "status": "mismatch", "operation": "sum",
                       "stated": 100.0, "computed": 90.0,
                       "aggregate_quote": "total annual cost is $100k",
                       "aggregate_location": "§3"}]}))
    flat = _flat(body)
    assert "annual cost" in flat
    assert "100" in flat and "90" in flat
    assert "§3" in flat


def test_report_handles_a_run_with_no_gate_at_all():
    assert "No stated totals" in _flat(_reconcile_body(Envelope(request="x")))


def test_report_says_what_was_not_checked():
    body = _flat(_reconcile_body(_env({
        "verdict": "PASS", "checked": 2,
        "counts": {"agrees": 2, "mismatch": 0, "unverifiable": 3}, "relations": []})))
    assert "agreed" in body and "3 other figure(s) were not checked" in body


def test_report_shows_the_formula_behind_a_mismatch():
    body = _flat(_reconcile_body(_env({
        "verdict": "MISMATCH", "checked": 1,
        "counts": {"agrees": 0, "mismatch": 1, "unverifiable": 0},
        "relations": [{"label": "n", "status": "mismatch", "operation": "expression",
                       "expr": "7.84*0.435/0.04", "stated": 60.0, "computed": 85.26,
                       "aggregate_quote": "\u2248 60 per cell", "aggregate_location": "\u00a72"}]})))
    assert "evaluating 7.84*0.435/0.04" in body
