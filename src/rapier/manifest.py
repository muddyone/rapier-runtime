"""The manifest loader — the method as declarative data.

A manifest is a YAML file that lists the stages of a pipeline, each with its
config and its role→model bindings. Editing the manifest changes the *method*
without touching the engine; that is what makes the method modular and
upgradeable (swap a model, reorder a stage, add a module).

Parsed with ``yaml.safe_load`` — never ``load`` — so a manifest can never
execute arbitrary Python (threat model: untrusted config / deserialization).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from .models import ModelSpec, Policy
from .pipeline import Pipeline, StageSpec


@dataclass
class Manifest:
    name: str
    stages: list[StageSpec]
    policy: Policy | None = None

    @classmethod
    def load(cls, path: str) -> "Manifest":
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ValueError("manifest must be a YAML mapping at the top level")
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        name = data.get("name", "pipeline")
        raw_stages = data.get("pipeline")
        if not isinstance(raw_stages, list) or not raw_stages:
            raise ValueError("manifest 'pipeline' must be a non-empty list")

        stages: list[StageSpec] = []
        for i, entry in enumerate(raw_stages):
            if not isinstance(entry, dict) or "stage" not in entry:
                raise ValueError(f"pipeline[{i}] must be a mapping with a 'stage' key")
            roles: dict[str, ModelSpec] = {}
            for role, binding in (entry.get("roles") or {}).items():
                if "vendor" not in binding or "model" not in binding:
                    raise ValueError(
                        f"pipeline[{i}].roles.{role} needs both 'vendor' and 'model'"
                    )
                roles[role] = ModelSpec(
                    vendor=binding["vendor"],
                    model=binding["model"],
                    prompt_template=binding.get("prompt"),
                    max_tokens=binding.get("max_tokens", 1024),
                    temperature=binding.get("temperature", 1.0),
                )
            stages.append(
                StageSpec(stage=entry["stage"], config=entry.get("config") or {}, roles=roles)
            )

        policy = None
        pol = data.get("policy")
        if isinstance(pol, dict):
            policy = Policy(
                vendors=pol.get("vendors"),
                independence=pol.get("independence", "preferred"),
                avoid_jurisdictions=pol.get("avoid_jurisdictions") or [],
            )
        return cls(name=name, stages=stages, policy=policy)

    def build(self) -> Pipeline:
        return Pipeline.from_manifest(self)
