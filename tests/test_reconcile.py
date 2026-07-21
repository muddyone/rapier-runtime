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
