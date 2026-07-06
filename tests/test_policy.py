"""V3: the declarative vendor policy — preference order, independence, jurisdiction."""
from __future__ import annotations

import pytest

from rapier.manifest import Manifest
from rapier.models import Policy, PolicyError


def test_default_policy_prefers_distinct_pair():
    assert Policy().resolve(["mock", "gemini", "xai"]) == ("gemini", "xai")


def test_preference_order_overrides_frontier_default():
    p = Policy(vendors=["xai", "gemini"])
    assert p.resolve(["mock", "gemini", "xai"]) == ("xai", "gemini")


def test_independence_required_errors_on_single_vendor():
    with pytest.raises(PolicyError):
        Policy(independence="required").resolve(["mock", "gemini"])


def test_independence_preferred_degrades_to_single():
    assert Policy(independence="preferred").resolve(["mock", "gemini"]) == ("gemini", None)


def test_independence_off_never_seeks_second():
    assert Policy(independence="off").resolve(["mock", "gemini", "xai"]) == ("gemini", None)


def test_avoid_jurisdiction_filters_vendors():
    # deepseek is cn-hosted; avoid it
    p = Policy(vendors=["deepseek", "gemini"], avoid_jurisdictions=["cn"])
    primary, _secondary = p.resolve(["mock", "deepseek", "gemini"])
    assert primary == "gemini"


def test_manifest_parses_policy_block():
    m = Manifest.from_dict(
        {
            "name": "x",
            "policy": {"vendors": ["xai", "gemini"], "independence": "required", "avoid_jurisdictions": ["cn"]},
            "pipeline": [{"stage": "echo", "roles": {}}],
        }
    )
    assert isinstance(m.policy, Policy)
    assert m.policy.vendors == ["xai", "gemini"]
    assert m.policy.independence == "required"
    assert m.policy.avoid_jurisdictions == ["cn"]
    assert m.build().policy is m.policy  # policy reaches the pipeline
