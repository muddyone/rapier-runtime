"""D-165 live check (NOT run until D-165 is answered a/c and verified): three calls on
the D-157 run's own failing prompts, under a hard $1.00 cap, on the current code.
Haiku generate B2 (malformed last time) and B8 (cut off last time); GPT-5.5 expand B7
(malformed last time); plus the largest Haiku generate and GPT-5.5 expand prompts. On-contract = the reply parses AND norm_items accepts it, with
a non-cut-off stop reason."""
import json, os, sys
sys.path.insert(0, "src")
from rapier import budget
from rapier.stages.map import _common as C, field as F
from rapier._json import parse_json_lenient
import jsonschema
R = os.path.expanduser("~/.rapier/runs/20261115015600-map")
rows = [json.loads(l) for l in open(f"{R}/transcript.jsonl")]
calls = json.load(open(f"{R}/map.json"))["calls"]
def pick(purpose, region):
    i = next(i for i, c in enumerate(calls) if c["purpose"] == purpose and c["region"] == region)
    return rows[i]
def biggest(purpose):
    xs = [(len(rows[i]["system"] + rows[i]["prompt"]), c["region"]) for i, c in enumerate(calls)
          if c["purpose"] == purpose and c.get("retry_of") is None]
    return max(xs)[1]
checks = [("anthropic", "generate", "B2"), ("anthropic", "generate", "B8"), ("openai", "expand", "B7")]
for vendor, purpose in (("anthropic", "generate"), ("openai", "expand")):   # sparring-3: + the biggest prompts
    b = biggest(purpose)
    if (vendor, purpose, b) not in checks:
        checks.append((vendor, purpose, b))
print("checks:", checks)
tok = budget.set_budget(1.00)
out = []
try:
    for vendor, purpose, region in checks:
        r = pick(purpose, region)
        before = budget.snapshot()["spent_usd"]
        resp = C.client_for(vendor, schema=C.ITEMS_SCHEMA).complete(r["system"], r["prompt"])
        items = F.norm_items(parse_json_lenient(resp.text))
        # rr-1: prove the schema was really applied: the raw reply must be strict JSON
        # that validates against ITEMS_SCHEMA, not merely parse leniently.
        try:
            jsonschema.validate(json.loads(resp.text), C.ITEMS_SCHEMA); conforms = "yes"
        except Exception as exc:  # noqa: BLE001
            conforms = f"no: {str(exc)[:160]}"
        cut, how = C._is_cut_off(resp, resp.text)
        out.append({"vendor": vendor, "model": resp.model, "purpose": purpose, "region": region,
                    "stop_reason": resp.stop_reason, "chars": len(resp.text), "cut_off": cut,
                    "schema_conforms": conforms,
                    "on_contract": items is not None and not cut and conforms == "yes", "items": None if items is None else len(items),
                    "usd": round(budget.snapshot()["spent_usd"] - before, 4)})
        print(out[-1], flush=True)
finally:
    snap = budget.snapshot(); budget.reset_budget(tok)
print("SPEND", json.dumps(snap))
print("ALL ON-CONTRACT" if out and all(o["on_contract"] for o in out) and len(out) == len(checks) else "NOT ALL ON-CONTRACT")
