# Rapier Runtime

**A code-orchestrated engine that runs the SPARRING method** — grounded,
cross-vendor adversarial review for AI-in-the-loop decisions.

Most AI tooling helps you *build* agent workflows. Rapier is different: it
*governs* them. It runs a structured, adversarial, grounded review over an AI's
proposed answer — cross-vendor by construction, with mechanical grounding
checks and a correctness gate — so that confident wrongness is structurally
hard to ship. The method is [SPARRING](https://github.com/muddyone/sparring-framework);
Rapier is the runtime that executes it.

> **Method vs. runtime.** *SPARRING* is the method (the concept, described in
> its own papers and framework). *Rapier* is the runtime that executes a
> SPARRING method declared in a manifest. Upgrade the runtime, or swap a model
> in a manifest, without changing the method.

Free and open source (Apache-2.0). Not monetized.

---

## Status: M1 (Resolver ported; pre-alpha)

This is the foundation, not the finished tool. What works today:

- **The Envelope** — the typed state that flows through every stage.
- **The Stage contract** + registry — `run(envelope, ctx) -> envelope`, in two
  kinds (transform stages and, coming in M2, convergence stages).
- **The Pipeline controller** — runs a manifest's stages in order, fail-soft.
- **The model layer** — a provider abstraction where vendor/model names live
  only in config; cross-vendor is a manifest property. Ships a `mock` vendor
  (no keys, no network) plus lazy Anthropic/OpenAI clients.
- **The Resolver** *(M1)* — the SPARRING challenge half as a five-stage pipeline
  (`author → cross_review → anchored_fix → definitiveness_gate → citation_gate`,
  `manifests/sparring.spar.yaml`), wrapping the vendored, battle-tested SPARRING
  grounding/gate stack behind one **single shared verification service** (the
  collapse of the old pilot-vs-loom two-copies split).
- **The ledger** — opt-in, redacted, owner-only run persistence.
- **Security from day one** — env-only secrets + redaction, `yaml.safe_load`,
  a [threat model](docs/threat-model.md), and a [security policy](SECURITY.md).

**Not yet built:** the Proposer / convergence loop (M2), automatic artifact
extraction from the recommendation, the two-part report composition, and the
`/spar` adapter. See the roadmap below.

## Quickstart

```bash
# from the repo root (M0 dev: run from source)
PYTHONPATH=src python -m rapier.cli run \
  --manifest manifests/echo.yaml \
  --request "should we ship X?"
# -> [mock:rapier-echo-1] should we ship X?
```

The `echo` manifest uses the `mock` vendor, so it needs no API keys. Real runs
(M1+) will read `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from the environment.

## A manifest is the method

```yaml
name: echo
pipeline:
  - stage: echo
    config: { note: "hello from Rapier M0" }
    roles:
      author: { vendor: mock, model: rapier-echo-1 }
```

Editing the manifest changes the method — reorder stages, swap a model, point
two roles at two different vendors — without touching engine code.

## Roadmap

| Milestone | What |
|---|---|
| M0 | Skeleton + threat model + security baseline |
| **M1** *(here)* | Resolver ported (one shared grounding/verification service; `/spar` parity) |
| M2 | Build the Proposer (the convergence primitive; SPARK / Pattern Lock / the Cut; cross-vendor roles) |
| M3 | Full controller + adapters (the whole ceremony end-to-end) |
| M4 | Hardening + packaging + first public release |

## Development

```bash
python -m venv .venv --system-site-packages
.venv/bin/pip install pytest
.venv/bin/python -m pytest -q
```

## License

Apache-2.0. See [LICENSE](LICENSE).
