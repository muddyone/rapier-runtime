"""$0 replay: serve a saved map run's recorded replies, matched by (system, prompt),
through the CURRENT code. A call the record doesn't have gets a scripted reply, or
the replay stops. No network: available_vendors and client_for are stubbed.

A library, not a command: run it from the repo root (it puts ``src`` on the path)
and call ``replay(run_dir, frame_file, request_file, max_calls, scripted=None,
overrides=None)``, which returns ``(envelope, log)``. ``overrides`` maps a recorded
call index to a ``(text, stop_reason)`` to serve instead; ``scripted(system, prompt,
vendor)`` answers calls the record doesn't have, or returns None to stop. Used for
docs/map-runs/failure-analysis-d157.md."""
import json, os, sys, collections
sys.path.insert(0, "src")
from rapier.models import ModelResponse
from rapier.stages.map import _common as C
from rapier.pipeline import Pipeline
from rapier.presets import load_preset

def replay(run_dir, frame_file, request_file, max_calls, scripted=None, overrides=None):
    rows = [json.loads(l) for l in open(f"{run_dir}/transcript.jsonl")]
    m_old = json.load(open(f"{run_dir}/map.json"))
    stops = [c.get("stop_reason") for c in m_old["calls"]]
    q = collections.defaultdict(collections.deque)
    for i, r in enumerate(rows):
        resp = (overrides or {}).get(i, (r["response"], r.get("stop_reason") or (stops[i] if i < len(stops) else None)))
        q[(r["system"], r["prompt"])].append((resp, r["model"], i))
    log = []
    class Cl:
        def __init__(self, v): self.v = v
        def complete(self, system, prompt):
            k = (system, prompt)
            if q[k]:
                (text, stop), model, i = q[k].popleft()
                log.append(("recorded", i)); return ModelResponse(text=text, vendor=self.v, model=model, stop_reason=stop)
            if scripted:
                got = scripted(system, prompt, self.v)
                if got is not None:
                    log.append(("scripted", None)); return ModelResponse(text=got[0], vendor=self.v, model="scripted", stop_reason=got[1])
            raise RuntimeError(f"REPLAY GAP: no recorded reply for a {self.v} call (system {system[:40]!r})")
    C.available_vendors = lambda: ["anthropic", "openai"]
    C.client_for = lambda v, **kw: Cl(v)
    fd = json.load(open(frame_file))
    seed = {"verify": "gate", "map_regions": fd["regions"], "map_blocks": fd.get("blocks"), "map_max_calls": max_calls}
    env = Pipeline.from_manifest(load_preset("map")).run(open(os.path.expanduser(request_file)).read(), seed_meta=seed)
    return env, log
