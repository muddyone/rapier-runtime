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


# --- vendor-adaptive author (BYO any vendor, not just the preset's default) ---

def test_resolve_role_keeps_available_and_mock():
    from rapier.pipeline import _resolve_role_spec
    from rapier.models import ModelSpec

    ok = ModelSpec(vendor="openai", model="gpt-5.2")
    out, sub = _resolve_role_spec(ok, None, ["mock", "openai"])
    assert sub is None and out is ok  # key present -> declared vendor respected

    mk = ModelSpec(vendor="mock", model="m1")
    out, sub = _resolve_role_spec(mk, None, ["mock"])
    assert sub is None and out is mk  # mock needs no key


def test_resolve_role_substitutes_unavailable_vendor():
    from rapier.pipeline import _resolve_role_spec
    from rapier.models import ModelSpec, default_model

    ms = ModelSpec(vendor="anthropic", model="claude-opus-4-8", max_tokens=8000)
    out, sub = _resolve_role_spec(ms, None, ["mock", "openai"])
    assert sub == "anthropic"
    assert out.vendor == "openai"
    assert out.model == default_model("openai")
    assert out.max_tokens == 8000  # non-vendor knobs preserved


def test_resolve_role_no_vendor_available_leaves_original():
    from rapier.pipeline import _resolve_role_spec
    from rapier.models import ModelSpec

    ms = ModelSpec(vendor="anthropic", model="x")
    out, sub = _resolve_role_spec(ms, None, ["mock"])  # only mock -> nothing to swap to
    assert sub is None and out is ms


def test_pipeline_authors_on_available_vendor_when_default_absent(monkeypatch):
    """A user with only (say) OpenAI: the anthropic-default author runs on OpenAI."""
    from rapier import pipeline as P
    from rapier.models import ModelResponse, ModelSpec

    monkeypatch.setattr(P, "available_vendors", lambda: ["mock", "openai"])

    built: list[ModelSpec] = []

    class _Stub:
        def __init__(self, spec):
            self.spec = spec

        def complete(self, system, prompt):
            return ModelResponse(text="ok", vendor=self.spec.vendor, model=self.spec.model)

    monkeypatch.setattr(P, "build_client", lambda ms: (built.append(ms), _Stub(ms))[1])

    spec = P.StageSpec(
        stage="author",
        roles={"author": ModelSpec(vendor="anthropic", model="claude-opus-4-8", max_tokens=8000)},
    )
    env = P.Pipeline([spec], name="t").run("decide X")

    assert env.meta.get("author_vendor") == "openai"  # authored on the available vendor
    assert any(ms.vendor == "openai" and ms.max_tokens == 8000 for ms in built)
    assert any("vendor substitution" in t.summary for t in env.trace)
