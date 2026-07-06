"""Manifest validation + stage registry tests."""
from __future__ import annotations

import pytest

from rapier.manifest import Manifest
from rapier.stage import get_stage


def test_missing_pipeline_raises():
    with pytest.raises(ValueError):
        Manifest.from_dict({"name": "x"})


def test_empty_pipeline_raises():
    with pytest.raises(ValueError):
        Manifest.from_dict({"pipeline": []})


def test_stage_entry_needs_stage_key():
    with pytest.raises(ValueError):
        Manifest.from_dict({"pipeline": [{"config": {}}]})


def test_role_needs_vendor_and_model():
    with pytest.raises(ValueError):
        Manifest.from_dict(
            {"pipeline": [{"stage": "echo", "roles": {"author": {"vendor": "mock"}}}]}
        )


def test_unknown_stage_lookup_raises():
    with pytest.raises(KeyError):
        get_stage("does-not-exist")


def test_known_stage_resolves():
    assert get_stage("echo").name == "echo"
