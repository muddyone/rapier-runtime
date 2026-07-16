# Rapier — dev status

_Last updated: 2026-07-16 (HEAD 222e9221, 167 tests)._ A running "where we are /
what's next" so a new session can continue without reconstructing from git log.

## Done
- **Engine M0–M3** — full SPARRING ceremony (Proposer → Resolver) end-to-end;
  presets `frame` / `spar` / `sparring` / `proposer`; `--settle` / `--verify`
  (resolver) and `--depth` / `--seed` (proposer) knobs.
- **The front door: input typing (Frame → Propose → Resolve).** A `frame` stage +
  `rapier frame` subcommand classifies an input as `question | proposition |
  hybrid` and, for a proposition, runs the Presentation (the Earnedness Rubric —
  G1 singular commitment / G2 load-bearing reason [counterfactual] / G3 decidable
  specificity). The model judges the type + gates; the **route is derived in
  code** (`stages/frame.py::_derive`), so only an *earned* proposition reaches
  `resolve` — a question can never be silently graded as a decision. Emits
  `{input_type, readiness, earned_gate_failed, route, anchor, …}`. (The verdict
  field is `readiness`, not `presentation`, to avoid colliding with the
  definitiveness gate's separate "presentation" concept.)
- **Seeded generation.** SPARK ingests a seeded candidate (a hybrid's leaning or
  a demoted G2-fail proposition — the Frame `anchor`) via `--seed`, injected into
  the opening round's field. It competes **without privilege**: carried through
  Pattern Lock + the Cut, it survives only on the merits (verified: a strong seed
  wins the Cut, a weak one loses). Composes with `--depth`.
- **A Proposer depth knob** (`--depth shallow|standard|deep`). `standard` is the
  unchanged default; `shallow` is a quick answer without full SPARK divergence
  (caps 2/1/1, no integrity reopen — ~3–4× faster); `deep` widens the field
  (8/3/3). Expressed as the per-phase convergence caps (`presets.py`).
- **Ceremony-ledger input-type fields + drift closed.** `compose.py::_ceremony_row`
  now records the front-door classification (`input_type, readiness,
  earned_gate_failed, anchor, routed_to, offramp_taken, demoted`) — seeded from
  the separate `rapier frame` call via `--frame <path>` (→ `Pipeline.run(seed_meta=…)`)
  — plus the ceremony-description fields it previously omitted relative to the
  in-session fallback (`iterations, held_at_cap, strongest_quote, verify_mode,
  grounding_coherence, artifact_path`). Both write sites now share one schema.
- **External-canon grounding actually fires in a normal run.** The citation gate
  now extracts CWE / DOI / RFC / URL / `path:line` from the recommendation and
  verifies them against MITRE / Crossref / IETF / URL-liveness — no model in the
  loop. (`stages/resolver/_extract.py` + `citation_gate.py`.) Previously the
  machinery existed but nothing fed it, so it always skipped.
- **First-run keys UX.** No-keys runs fail loudly (CLI preflight, exit 2), plus
  `rapier doctor` (which key env vars are set — names only) and `rapier init`
  (writes `.env.example`). Secrets stay **env-only** — the shell or MCP client
  populates the environment; the engine never reads a secret from a file.
  (`onboarding.py`.)
- **Vendor-adaptive roles.** A preset role whose vendor key is absent is remapped
  to a policy-resolved available vendor (a declared vendor is respected when its
  key IS present; `mock` is always kept). BYO-any-vendor genuinely works.
  (`pipeline._resolve_role_spec`.)
- **MCP server (MCP-0/1/2).** `rapier mcp` stdio subcommand, optional `[mcp]`
  extra (core stays `requests`+`pyyaml`). Tools: `spar`, `sparring`,
  `rapier_doctor`, `list_runs`, `get_run`. Structured output; per-stage progress;
  cooperative cancellation; per-tool `timeout_s`; opt-in run persistence via
  `RAPIER_MCP_LEDGER`. Scope + milestones: `docs/mcp-server-scope.md`. This is the
  **public, generic equivalent of the Loom `/spar` `/sparring` slash-commands**
  (which are Loom-only artifacts a generic user does not have).
- **rapierruntime.com is live.** DNS → VPS `160.153.180.205`, Let's Encrypt via
  cPanel AutoSSL, HTTP→HTTPS redirect. Currently serves the "in development" page
  (`site/coming-soon.html` deployed as `index.html`); the full MVP landing
  (`site/index.html`) is in the repo, ready to swap at M4. See `site/README.md`.

## Next (priority order)
1. **M4 publish.** Build + publish to PyPI (`rapier-runtime`), then swap the VPS
   `index.html` from `coming-soon.html` to the full landing — resolving the
   landing's placeholders first (real pip name, and the paper / PyPI / SPARRING-
   spec links; Paper 1 is now public, so the paper link resolves). This is what
   makes `pip install rapier-runtime` real.
2. **MCP:** end-to-end live-session test with a real client (Claude Desktop);
   optionally expose runs as MCP *resources* rather than tools. (The MCP tools
   still expose `spar` / `sparring` only — surfacing `frame` / `proposer` and the
   `--depth` / `--seed` knobs is a small follow-on if wanted.)
3. **Paper 2 (the Proposer)** stays parked until the engine is fully shipped.
   Note: the Proposer study runs *on* this engine — Frame, seeded generation, and
   the depth knob are the instrument it will exercise.

_(Done since 2026-07-08: the input-typing front door + seeded generation + depth
knob + ledger fields — see Done above. The Loom submodule pin is current — it was
advanced with each increment, last `222e9221` — so the earlier "bump the pin"
item is cleared.)_

## Things a new session should know
- **Two repos.** This engine (`muddyone/rapier-runtime`, public) vs. the SPARRING
  method + paper (private `muddyone/sparring`; public artifacts in
  `muddyone/sparring-publicaccess`).
- **Interfaces.** Generic users get the `rapier` CLI, the Python API, and the MCP
  server. `/spar` `/sparring` slash-commands are **Loom** artifacts, not shipped
  from here.
- **Secrets are env-only by design** (`secrets.py`) — do not add file-reading of
  keys; populate the environment upstream instead.
- **Tests.** `pytest -q`. On machines using the vendored `.tools/` pytest, run
  with a native-filesystem `TMPDIR` (the ledger owner-perms test needs real Unix
  perms, which `/mnt/c` DrvFs can't hold).
