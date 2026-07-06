"""The shared verification service — offline (pack backend) + parity with the
vendored entry point. No keys, no network."""
from __future__ import annotations


def test_citation_gate_pack_backend_offline():
    from rapier.verify import service

    arts = [
        {
            "concern_id": "a1",
            "artifact_ref": "#2",
            "concern_text": "leans on fact #2",
            "load_bearing": True,
            "pack_text": "1. foo\n2. bar the baz",
        }
    ]
    verdicts, summary = service.verify_artifacts(arts, pack_text="1. foo\n2. bar the baz")
    assert summary["gate"] == "clean"
    assert summary["counts"]["verified"] == 1
    assert verdicts[0]["status"] == "verified"
    assert verdicts[0]["backend"] == "in-pack"


def test_service_does_not_alter_vendored_behavior():
    """Parity by construction: the wrapper must return exactly what the vendored
    entry point returns for the same input."""
    from rapier.verify import service
    from rapier.verify import _bootstrap

    arts = [
        {
            "concern_id": "a1",
            "artifact_ref": "#2",
            "concern_text": "x",
            "load_bearing": True,
            "pack_text": "1. a\n2. b",
        }
    ]
    v1, s1 = service.verify_artifacts(arts, pack_text="1. a\n2. b")
    v2, s2 = _bootstrap.verify_run(arts, "1. a\n2. b", False, False)
    assert s1 == s2
    assert v1 == v2


def test_refuted_url_never_reads_clean_offline():
    """A refuted load-bearing artifact must not produce a clean gate — the
    vendored decide() asserts this invariant; confirm it survives the wrapper."""
    from rapier.verify import service

    # a missing pack fact (#9 not in pack) resolves as not-checked/unverifiable,
    # so the gate must not be 'clean' for a load-bearing artifact.
    arts = [
        {
            "concern_id": "a1",
            "artifact_ref": "#9",
            "concern_text": "leans on a fact not in the pack",
            "load_bearing": True,
            "pack_text": "1. a\n2. b",
        }
    ]
    _verdicts, summary = service.verify_artifacts(arts, pack_text="1. a\n2. b")
    assert summary["gate"] in ("flagged", "blocked")
