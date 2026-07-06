"""Control-flow tests for the pipeline — mocked models, no network, no keys."""
from __future__ import annotations

from rapier.manifest import Manifest


def _echo_manifest() -> Manifest:
    return Manifest.from_dict(
        {
            "name": "echo",
            "pipeline": [
                {
                    "stage": "echo",
                    "config": {"note": "t"},
                    "roles": {"author": {"vendor": "mock", "model": "m1"}},
                }
            ],
        }
    )


def test_echo_pipeline_runs_end_to_end():
    env = _echo_manifest().build().run("decide X")
    assert env.request == "decide X"
    assert env.recommendation == "[mock:m1] decide X"


def test_pipeline_records_a_trace_entry_per_stage():
    env = _echo_manifest().build().run("decide X")
    assert [t.stage for t in env.trace] == ["echo"]
    assert env.trace[0].kind == "transform"
    assert env.trace[0].data.get("chars") == len(env.recommendation)


def test_stage_without_client_echoes_verbatim():
    m = Manifest.from_dict(
        {"pipeline": [{"stage": "echo", "config": {}, "roles": {}}]}
    )
    env = m.build().run("verbatim please")
    assert env.recommendation == "verbatim please"


def test_transcript_captures_every_model_call(tmp_path):
    import json

    _echo_manifest().build().run("hello world", ledger_root=str(tmp_path))
    run_dir = next(tmp_path.iterdir())
    tx = run_dir / "transcript.jsonl"
    assert tx.exists()
    lines = [json.loads(l) for l in tx.read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["vendor"] == "mock"
    assert set(lines[0]) == {"vendor", "model", "system", "prompt", "response"}


def test_transcript_sink_reset_after_run(tmp_path):
    from rapier import models

    _echo_manifest().build().run("x", ledger_root=str(tmp_path))
    assert models._transcript_sink is None  # cleaned up so it can't leak across runs


def test_ledger_persists_redacted_run(tmp_path):
    m = _echo_manifest()
    env = m.build().run("decide X", ledger_root=str(tmp_path))
    runs = list(tmp_path.iterdir())
    assert len(runs) == 1
    assert (runs[0] / "envelope.json").exists()
    assert (runs[0] / "ledger.jsonl").exists()
    # owner-only permissions
    assert oct((runs[0] / "envelope.json").stat().st_mode)[-3:] == "600"
