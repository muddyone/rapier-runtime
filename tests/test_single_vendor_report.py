"""A run with only one vendor's key must not describe its review as independent.

Found on a live 0.5.1 run (2026-10-16, gpt-6-sol only): the report said "an
independent, different-vendor reviewer raised each of these" while its own HOW
MUCH TO TRUST THIS section said the review was NOT independent; the progress
output blamed a missing OPENAI_API_KEY that was in fact set; and the
recommendation's LaTeX arithmetic printed as raw markup.
"""
from __future__ import annotations

from rapier.envelope import Envelope
from rapier.stage import StageContext, get_stage
from rapier.stages.resolver.compose import _plain_math, _render_report as _render


def _render_report(env: Envelope) -> str:
    """The report wraps at 64 columns; join it so phrases can be matched whole."""
    return " ".join(_render(env).split())


def _env(cross_vendor: bool) -> Envelope:
    env = Envelope(request="decide X")
    env.recommendation = r"Price it at \(H \times 0.60\) hours, about \(1 \div 0.60 \approx 1.67\)x."
    env.meta["review"] = {
        "reviewer_vendor": "gpt" if cross_vendor else "claude",
        "cross_vendor": cross_vendor,
        "objections": [{"text": r"Count quoting time in \(T_{q}\) too."}],
    }
    return env


def test_same_vendor_report_never_claims_independence():
    report = _render_report(_env(cross_vendor=False))
    assert "different-vendor" not in report
    assert "independent challenge" not in report
    assert "cross-vendor challenge" not in report
    assert "same vendor as the author (not an independent check) raised each" in report
    assert "NOT independent" in report


def test_cross_vendor_report_keeps_its_independence_claim():
    report = _render_report(_env(cross_vendor=True))
    assert "An independent, different-vendor reviewer raised each of these" in report


def test_report_renders_latex_arithmetic_as_plain_text():
    report = _render_report(_env(cross_vendor=True))
    assert "\\(" not in report and "\\times" not in report
    assert "H × 0.60" in report
    assert "1 ÷ 0.60 ≈ 1.67" in report
    assert "T_q" in report  # objections are converted too


def test_plain_math_leaves_backslashes_outside_math_alone():
    assert _plain_math(r"see C:\new\path") == r"see C:\new\path"
    assert _plain_math(r"\[\frac{a}{b} \le 5\%\]") == "(a)/(b) ≤ 5%"


def test_single_vendor_warning_names_the_real_vendor(monkeypatch, capsys):
    import sys

    from rapier.stages.resolver import _binding
    from rapier.verify import _bootstrap

    def fake_review(problem, rec, prev, vendor):
        sys.stderr.write("WARN spar-cross-review: OPENAI_API_KEY absent; falling back to "
                         "a Claude reviewer (NOT cross-vendor).\n")
        sys.stderr.write("some other diagnostic\n")
        return {"reviewer_vendor": "claude", "cross_vendor": False, "objections": []}

    monkeypatch.setattr(_bootstrap, "review", fake_review)
    monkeypatch.setattr(_binding, "bind_pair", lambda env, **kw: ("openai", None))
    env = Envelope(request="decide X")
    env.recommendation = "do Y"
    get_stage("cross_review")().run(env, StageContext())

    err = capsys.readouterr().err
    assert "OPENAI_API_KEY absent" not in err
    assert "Claude" not in err
    assert "the reviewer runs on openai, the author's vendor (NOT cross-vendor)" in err
    assert "some other diagnostic" in err
