"""Accountable Agents item 5: record which version of the method ran."""
from rapier.models import ModelSpec
from rapier.pipeline import Pipeline, StageSpec, method_fingerprint
from rapier.presets import load_preset


def _fp(stages, available=("anthropic", "openai")):
    return method_fingerprint(stages, None, list(available))


def test_same_method_same_hash():
    a = load_preset("spar").stages
    b = load_preset("spar").stages
    assert _fp(a)["hash"] == _fp(b)["hash"]


def test_any_change_to_the_method_changes_the_hash():
    base = _fp(load_preset("spar").stages)["hash"]
    assert _fp(load_preset("spar", verify="off").stages)["hash"] != base
    assert _fp(load_preset("spar", settle=1).stages)["hash"] != base
    assert _fp(load_preset("sparring").stages)["hash"] != base


def test_version_is_part_of_the_method(monkeypatch):
    import rapier

    stages = load_preset("spar").stages
    before = _fp(stages)["hash"]
    monkeypatch.setattr(rapier, "__version__", "9.9.9")
    assert _fp(stages)["hash"] != before


def test_vendor_substitution_moves_only_the_resolved_hash():
    stages = [StageSpec(stage="author", roles={"author": ModelSpec("anthropic", "claude-x")})]
    both = _fp(stages, ("anthropic", "openai"))
    only_openai = _fp(stages, ("openai",))
    assert both["hash"] == only_openai["hash"]
    assert both["resolved_hash"] != only_openai["resolved_hash"]


def test_hash_reaches_ceremony_row_and_report():
    env = Pipeline([StageSpec(stage="echo"), StageSpec(stage="compose")], name="t").run("x")
    row = env.meta["ceremony_row"]
    assert row["method_hash"].startswith("sha256:") and len(row["method_hash"]) == 71
    assert row["method_resolved_hash"].startswith("sha256:")
    assert row["rapier_version"]
    assert row["method_hash"][:19] in env.meta["report_md"]
