"""Shared plumbing for map mode (SPARRING spec v1.4 §3.7).

Map mode runs SPARK and Pattern Lock and emits the field itself, with its own
checks. Everything a check decides is decided in code from the run's own
record; models only generate, attack and judge "new?".

State lives in ``env.meta["map"]``. Model calls go through :func:`call`, which
records each call (role, vendor, region) in ``map["calls"]``: the "probed"
label is read from that record, never from what a model says about itself.

Generation, attack and labels run per **unit**: a region, or a block of regions
declared with the frame (v1.4, "Frame size and blocks"). ``unit_list`` is what
the stages iterate; items carry unit ids in ``regions``.
"""
from __future__ import annotations

import hashlib
import json
import re
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


RETRIES_PER_RUN = 1  # D-157 b: one in-run retry of a cut-off reply, for the whole run


def _unclosed_json(text: str | None) -> bool:
    """True when the reply opens a JSON object or array and never closes it: a
    reply that ends mid-JSON, whatever stop reason the provider gave (run
    20261115010859-map's B4 reply ended mid-string at 8,578 characters, far under
    the output limit, with no recorded stop reason)."""
    if not text:
        return False
    t = re.sub(r"^```(?:json)?\s*", "", text.strip())
    t = re.sub(r"\s*```$", "", t)
    starts = [i for i in (t.find("{"), t.find("[")) if i != -1]
    if not starts:
        return False
    depth, in_str, esc = 0, False, False
    for ch in t[min(starts):]:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return False  # a complete value closed; any problem is not a cut-off
    return depth > 0 or in_str


def _is_cut_off(resp: Any, text: str | None) -> tuple[bool, str]:
    """Whether a reply came back cut off, and how that was told."""
    if getattr(resp, "cut_off", False):
        return True, f"stop reason {getattr(resp, 'stop_reason', None)}"
    if _unclosed_json(text):
        return True, ("the reply ends inside unclosed JSON (stop reason "
                      f"{getattr(resp, 'stop_reason', None) or 'not given'})")
    return False, ""


def _send(env: Envelope, m: dict, *, role, vendor, purpose, region, system, prompt,
          retry_of: int | None = None):
    from ... import budget

    rec = {"role": role, "vendor": vendor, "purpose": purpose, "region": region, "ok": False}
    if retry_of is not None:
        rec["retry_of"] = retry_of
    m["calls"].append(rec)
    try:
        resp = client_for(vendor).complete(system, prompt)
        text = resp.text
    except budget.BudgetExceeded as exc:
        rec.update(error=str(exc)[:200], stopped_by="dollar cap")
        raise
    except Exception as exc:  # noqa: BLE001
        rec["error"] = str(exc)[:200]
        raise
    rec["ok"] = True
    stop = getattr(resp, "stop_reason", None)
    if stop:
        rec["stop_reason"] = stop
    cut, how = _is_cut_off(resp, text)
    if cut:
        rec.update(cut_off=True, cut_off_how=how)
    return rec, text


def call(env: Envelope, *, role: str, vendor: str, purpose: str, system: str, prompt: str,
         region: str | None = None) -> Any:
    """One model call, recorded before its result is used. Returns parsed JSON,
    or raises; callers decide what a failure means for their check.

    A reply that comes back cut off (a cut-off stop reason, or JSON left open) is
    sent again ONCE, with the same prompt to the same model, if the run's one
    retry (:data:`RETRIES_PER_RUN`) is unused (D-157 b). Both calls are recorded;
    the retry is listed in ``map["retries"]`` and disclosed in the rider. A second
    cut-off, or a cut-off after the retry is spent, is returned as it came, and
    the caller reads it as off-contract. The retry goes through the dollar cap
    like any other call."""
    m = state(env)
    kw = dict(role=role, vendor=vendor, purpose=purpose, region=region, system=system, prompt=prompt)
    rec, text = _send(env, m, **kw)
    if rec.get("cut_off"):
        retries = m.setdefault("retries", [])
        first = len(m["calls"]) - 1
        if len(retries) >= RETRIES_PER_RUN:
            rec["not_retried"] = "the run's one retry was already used"
            m.setdefault("cut_offs_not_retried", []).append(
                {"call": first, "role": role, "purpose": purpose, "region": region,
                 "how": rec["cut_off_how"]})
        else:
            entry = {"call": first, "role": role, "vendor": vendor, "purpose": purpose,
                     "region": region, "first_how": rec["cut_off_how"], "outcome": "not sent"}
            retries.append(entry)
            rec["retried"] = True
            try:
                rec2, text = _send(env, m, retry_of=first, **kw)
            except Exception as exc:  # noqa: BLE001
                entry["outcome"] = f"the retry failed: {str(exc)[:200]}"
                raise
            entry["retry_call"] = len(m["calls"]) - 1
            entry["outcome"] = (f"cut off again ({rec2['cut_off_how']})" if rec2.get("cut_off")
                                else "completed")
    return parse_json_lenient(text)


def cut_off_note(m: dict) -> str:
    """" (the reply was cut off: …)" when the last recorded call was, else ""."""
    last = (m.get("calls") or [{}])[-1]
    if not last.get("cut_off"):
        return ""
    how = last.get("cut_off_how") or f"stop reason {last.get('stop_reason')}"
    tail = ("; retried once, cut off again" if last.get("retry_of") is not None else
            "; not retried: the run's one retry was already used" if last.get("not_retried") else "")
    return f" (the reply was cut off: {how}{tail})"


def region_list(m: dict) -> list[dict]:
    return list(((m.get("frame") or {}).get("regions")) or [])


def unit_list(m: dict) -> list[dict]:
    """The units a map attacks: the frame's blocks, or each region on its own.
    Empty when the run stopped before generation."""
    if m.get("stopped"):
        return []
    f = m.get("frame") or {}
    return list(f.get("units") or [])


def unit_text(u: dict, regions_by_id: dict[str, dict]) -> str:
    if u.get("kind") == "block":
        members = "\n".join(f"  - {rid} {regions_by_id[rid]['name']}: {regions_by_id[rid]['definition']}"
                            for rid in u["members"] if rid in regions_by_id)
        return f"UNIT {u['id']} (a block of {len(u['members'])} regions, {u['basis']}): {u['name']}\n{members}"
    return f"UNIT {u['id']}: {u['name']} — {u['definition']}"
