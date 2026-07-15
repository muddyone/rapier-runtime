"""CLI argument handling — request resolution + input validation.

No network or keys: these exercise how the `request` reaches the pipeline
(inline, from a file, or stdin) and the guardrails around it.
"""
from __future__ import annotations

import argparse
import io

import pytest

from rapier.cli import _resolve_request, main


def _ns(**kw):
    base = {"request": None, "request_file": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_request_inline():
    assert _resolve_request(_ns(request="decide X")) == "decide X"


def test_resolve_request_from_file(tmp_path):
    p = tmp_path / "pack.md"
    p.write_text("DECISION: migrate to X?\n\nCONTEXT\n- multi-line pack\n", encoding="utf-8")
    got = _resolve_request(_ns(request_file=str(p)))
    assert "DECISION: migrate to X?" in got and "multi-line pack" in got


def test_resolve_request_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("piped decision"))
    assert _resolve_request(_ns(request_file="-")) == "piped decision"


def test_request_and_request_file_are_mutually_exclusive():
    # argparse must reject supplying both at once
    with pytest.raises(SystemExit):
        main(["spar", "--request", "x", "--request-file", "y"])


def test_one_of_request_or_file_is_required():
    with pytest.raises(SystemExit):
        main(["spar"])  # neither given


def test_missing_request_file_exits_2(capsys):
    rc = main(["spar", "--request-file", "/no/such/pack.md"])
    assert rc == 2
    assert "cannot read --request-file" in capsys.readouterr().err


def test_empty_request_exits_2(tmp_path, capsys):
    p = tmp_path / "empty.md"
    p.write_text("   \n", encoding="utf-8")
    rc = main(["spar", "--request-file", str(p)])
    assert rc == 2
    assert "empty" in capsys.readouterr().err


def test_seed_rejected_on_spar():
    # spar is Resolver-only — there is no SPARK to seed, so --seed is not a flag there
    with pytest.raises(SystemExit):
        main(["spar", "--request", "x", "--seed", "y"])


def test_seed_accepted_on_proposer(monkeypatch):
    # --seed parses on the Proposer preset; short-circuit at preflight so the
    # test never touches a vendor/network path.
    monkeypatch.setattr("rapier.onboarding.preflight_error", lambda: "no keys (test)")
    rc = main(["proposer", "--request", "x", "--seed", "Use Postgres"])
    assert rc == 2  # parsed OK, then stopped at the no-keys preflight


def test_version_flag_exits_0_and_prints(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "rapier-runtime" in capsys.readouterr().out


def test_progress_non_tty_numbers_stages_no_control_chars():
    import io

    from rapier.cli import _Progress

    buf = io.StringIO()  # isatty() -> False, so the plain (piped) path
    p = _Progress(total=2, stream=buf)
    p.log("stage: author (transform)")
    p.log("stage: compose (transform)")
    p.done()
    out = buf.getvalue()
    assert "[1/2] Drafting the recommendation" in out
    assert "[2/2] Composing the report" in out
    assert "\r" not in out  # no spinner/carriage-returns when piped
