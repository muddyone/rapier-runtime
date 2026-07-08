# Rapier Runtime

**A code-orchestrated engine that runs the SPARRING method** — grounded,
cross-vendor adversarial review for AI-in-the-loop decisions.

Most AI tooling helps you *build* agent workflows. Rapier is different: it
*governs* them. It runs a structured, adversarial, grounded review over an AI's
proposed answer — cross-vendor by construction, with mechanical grounding
checks and a correctness gate — so that confident wrongness is structurally
hard to ship. The method is [SPARRING](https://github.com/muddyone/sparring-publicaccess);
Rapier is the runtime that executes it.

> **Method vs. runtime.** *SPARRING* is the method (the concept, described in
> its own papers and framework). *Rapier* is the runtime that executes a
> SPARRING method declared in a manifest. Upgrade the runtime, or swap a model
> in a manifest, without changing the method.

Free and open source (Apache-2.0). Not monetized.

---

## Status: pre-alpha — the full ceremony runs end-to-end (M0–M3 complete; M4 in progress)

This is early software, pinned pre-1.0 — but the whole method runs. What works
today:

- **The full SPARRING ceremony, end-to-end.** The **Proposer** (divergent
  generation → false-novelty filter → the Cut — a cross-vendor convergence loop)
  hands a committed option to the **Resolver** (author → cross-vendor review →
  anchored correction → a correctness *definitiveness* gate → an external-canon
  citation gate → a two-part report). Run the Resolver alone (`spar`) or the
  whole loop (`sparring`).
- **Cross-vendor by construction.** Author and reviewer are always distinct
  vendors when two keys are present, and it degrades *honestly* to single-vendor
  (and says so) when only one is. Any role can be Anthropic, OpenAI, Gemini,
  Grok, or any OpenAI-compatible endpoint — vendor and model names live only in
  a manifest.
- **A manifest *is* the method.** Reorder stages, swap a model, or point two
  roles at two vendors — with no engine-code change. Built-in presets (`spar`,
  `sparring`, `proposer`) cover the common cases; `--settle` and `--verify` tune
  the resolver.
- **Grounding + a correctness gate.** The definitiveness gate checks that every
  hard specific in the answer is traceable to the given facts or explicitly
  flagged as an estimate; the citation gate resolves cited external canon
  (CWE / RFC / DOI / …). Both wrap one shared, battle-tested verification
  service — no duplicate copies.
- **Auditable and safe by design.** Opt-in, redacted, owner-only run
  persistence; a verbatim model-call transcript; env-only secrets + redaction;
  `yaml.safe_load`; a [threat model](docs/threat-model.md) and a
  [security policy](SECURITY.md).

103 tests pass. Cross-vendor runs are live-proven (Anthropic×OpenAI, Gemini×Grok).

**Honest boundary.** The definitiveness gate, anchored correction, and the
two-part trust rider are *exploratory* governance instruments — useful, but not
yet validated by a pre-registered study. Rapier makes confident wrongness
structurally *harder to ship*; it does not make an answer correct.

## Quickstart

```bash
pip install .          # or: pip install git+https://github.com/muddyone/rapier-runtime.git

export ANTHROPIC_API_KEY=...    # author + gate
export OPENAI_API_KEY=...       # a distinct cross-vendor reviewer
                                # (or GEMINI_API_KEY / XAI_API_KEY)

rapier spar     --request "Should we adopt Kubernetes for one flat-traffic web app?"
rapier sparring --request "Monorepo or separate repos for our three services?"
```

`spar` runs the Resolver on a chosen option; `sparring` runs the full four-phase
ceremony. Add `--settle N` for extra decision-stability rounds, or
`--verify off|gate|round` to tune the citation gate. Point `--ledger-dir` at a
directory to persist the run's transcript, report, and records.

No keys? The `mock` vendor needs none:

```bash
rapier run --manifest manifests/echo.yaml --request "should we ship X?"
# -> [mock:rapier-echo-1] should we ship X?
```

Runtime dependencies are just `requests` and `pyyaml` — every vendor is called
over the wire; no provider SDKs.

## Configure your keys

Rapier reads vendor keys from the **environment only** — never from a file it
reads itself. Scaffold and check your setup:

```bash
rapier init      # writes .env.example (key names, no values)
# fill in .env, then load it into your shell:
set -a; source .env; set +a
rapier doctor    # shows which vendors are configured (names only — never values)
```

`doctor` reports each vendor's key env var as set/unset and whether cross-vendor
review is available (two or more keys). A ceremony launched with **no** keys
fails loudly with an actionable message instead of producing empty output.

## A manifest is the method

```yaml
name: echo
pipeline:
  - stage: echo
    config: { note: "hello from Rapier" }
    roles:
      author: { vendor: mock, model: rapier-echo-1 }
```

Editing the manifest changes the method — reorder stages, swap a model, point
two roles at two different vendors — without touching engine code.

## Roadmap

| Milestone | What | |
|---|---|---|
| M0 | Skeleton + threat model + security baseline | ✅ |
| M1 | Resolver ported (one shared grounding/verification service) | ✅ |
| M2 | The Proposer (convergence primitive; SPARK / Pattern Lock / the Cut; cross-vendor roles) | ✅ |
| M3 | Full controller + the `spar` / `sparring` adapters (the whole ceremony end-to-end) | ✅ |
| M4 | Hardening + packaging + first public release | in progress |

## Development

```bash
pip install -e '.[dev]'
pytest -q
```

## License

Apache-2.0. See [LICENSE](LICENSE).
