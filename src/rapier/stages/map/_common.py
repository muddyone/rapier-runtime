"""Shared plumbing for map mode (SPARRING spec v1.3 §3.7).

Map mode runs SPARK and Pattern Lock and emits the field itself, with its own
checks. Everything a check decides is decided in code from the run's own
record; models only generate, attack and judge "new?".

State lives in ``env.meta["map"]``. Model calls go through :func:`call`, which
records each call (role, vendor, region) in ``map["calls"]``: the "probed"
label is read from that record, never from what a model says about itself.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..._json import parse_json_lenient
from ...envelope import Envelope
from ...models import ModelSpec, Policy, available_vendors, build_client, default_model

MAX_TOKENS = 8000


def sha256(obj: Any) -> str:
    raw = obj if isinstance(obj, str) else json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                                       default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def state(env: Envelope) -> dict:
    return env.meta.setdefault("map", {"calls": []})


def vendors(env: Envelope, ctx) -> dict:
    """Generator and Challenger vendors for the whole map run, fixed on first use
    so every stage puts the Challenger on the same, distinct vendor."""
    m = state(env)
    if "vendors" not in m:
        gen_v, chal_v = (ctx.policy or Policy()).resolve(
            available_vendors(),
            primary_pref=ctx.config.get("generator_vendor") or env.meta.get("author_vendor"),
            secondary_pref=ctx.config.get("challenger_vendor"))
        m["vendors"] = {"generator": gen_v, "challenger": chal_v if chal_v != gen_v else None,
                        "cross_vendor": bool(chal_v) and chal_v != gen_v}
    return m["vendors"]


def client_for(vendor: str):
    """Seam for tests: build a model client for ``vendor``."""
    return build_client(ModelSpec(vendor, default_model(vendor), max_tokens=MAX_TOKENS))


def call(env: Envelope, *, role: str, vendor: str, purpose: str, system: str, prompt: str,
         region: str | None = None) -> Any:
    """One model call, recorded before its result is used. Returns parsed JSON,
    or raises; callers decide what a failure means for their check."""
    m = state(env)
    rec = {"role": role, "vendor": vendor, "purpose": purpose, "region": region, "ok": False}
    m["calls"].append(rec)
    text = client_for(vendor).complete(system, prompt).text
    rec["ok"] = True
    return parse_json_lenient(text)


def region_list(m: dict) -> list[dict]:
    return list(((m.get("frame") or {}).get("regions")) or [])
