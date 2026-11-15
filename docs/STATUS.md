# Rapier — dev status

_Last updated: 2026-11-14 (tag v0.6.3 on main; map mode v1.5 on a branch, 928 tests)._ A running "where we are /
what's next" so a new session can continue without reconstructing from git log.

## Done
- **RELEASED (2026-11-14) — `rapier-runtime` 0.6.3 on PyPI. An unreadable
  operator policy no longer reads as the minimum refusing the run.** Git tag
  `v0.6.3` (c3e7cddd); https://pypi.org/project/rapier-runtime/0.6.3/. A policy
  file that can't be read or checked still stops the run (MCP `ok: false`, CLI
  exit 2), now as `final_state: "defer"` with `operator_policy: "did-not-run:
  …"` and no `refused_by`, because the minimum was never read. Found by the
  verifier after 0.6.2, against the rule agreed with Loom's agent-runtime lane;
  Bart D-70 a. Verified from PyPI: a clean install reports 0.6.3.
- **RELEASED (2026-11-14) — `rapier-runtime` 0.6.2 on PyPI. The six Accountable
  Agents changes** (from Thomas Gessler's two concept papers,
  doi:10.5281/zenodo.20687710 and doi:10.5281/zenodo.21850919). Git tag `v0.6.2`
  (1020a765); https://pypi.org/project/rapier-runtime/0.6.2/. Built by
  rapier-runtime-2 and passed item by item by the rapier-runtime-1 verifier in
  the first multi-project team test (D-61); Bart chose 0.6.2 (D-69). In short:
  an operator minimum for the citation check that a caller can't lower
  (`/etc/rapier/policy.json`, reported as out of reach only when Rapier neither
  owns nor can write it), and `require_independent` there; every check reports
  `ran` / `nothing-to-check` / `did-not-run: <reason>`, judged on its last run;
  a WHAT RAN section and pre-run manifest checks (`rapier doctor --manifest`);
  a final state in the papers' six names, with a labelled NON-DECISION (D-67 a);
  and a method hash (declared and as-run, including vendor bindings and the
  package code). Full list in `CHANGELOG.md`. Verified from PyPI: a clean
  install reports 0.6.2 and `rapier doctor` shows the operator minimum and the
  per-ceremony checks. Follow-up, not blocking: key the "gate before the answer"
  rule on the pipeline's stage order rather than writers' own traces.
- **RELEASED (2026-10-16) — `rapier-runtime` 0.6.1 on PyPI. The arithmetic
  check ("WHETHER THE NUMBERS ADD UP") stops flagging correct formulas.** Git
  tag `v0.6.1`; https://pypi.org/project/rapier-runtime/0.6.1/. Reported by
  valkyrie-sparring-1: a stats-heavy spar
  (sparring-framework `docs/spars/202610161853-gopnik-pilot-design-review/`)
  got five false mismatches out of five, because every stated result was
  checked as a sum of the numbers near it. Fixes: `difference` and
  `expression` operations (the formula is evaluated as written, by a
  literal-only evaluator); "not checked" where the operation can't be
  recovered, or a "sum" quote shows a formula; the agreement margin is 0.5% of
  the figures (no floor of 1) or half a unit of the last digit the quote
  shows. Also: the ledger permission test skips where the filesystem ignores
  chmod (WSL `/mnt/c`). Verified from PyPI: a clean install reports 0.6.1 and
  the reported standard-error case agrees.
- **RELEASED (2026-10-16) — `rapier-runtime` 0.6.0 on PyPI. The MCP server runs
  on the mcp 2.x SDK.** Git tag `v0.6.0`;
  https://pypi.org/project/rapier-runtime/0.6.0/. `MCPServer` replaces
  `FastMCP`; the `[mcp]` extra is `mcp>=2.3,<3`; stage progress carries the
  stage name as its message. Verified from PyPI: a clean install and an upgrade
  from 0.5.3 (mcp 1.30.0) both resolve mcp 2.3.0, and `rapier mcp` reports
  0.6.0 with 7 tools and the saved runs as resources.
- **RELEASED (2026-10-16) — `rapier-runtime` 0.5.3 on PyPI. Saved runs as MCP
  resources, and `rapier mcp` starts again on a fresh install.** Git tag
  `v0.5.3`; https://pypi.org/project/rapier-runtime/0.5.3/. Resources:
  `rapier://runs`, `rapier://runs/{run_id}`, `rapier://runs/{run_id}/record`;
  each saved run is listed individually, including runs saved after the server
  started. The fix: `[mcp]` allowed any `mcp>=1.2`, so new installs got mcp
  2.3.0, which renamed FastMCP, and 0.5.2's `rapier mcp` failed with a message
  pointing at the wrong cause. Capped at `mcp<2`. Verified from PyPI in a clean
  environment: the install resolved mcp 1.30.0 and the server listed its runs.
- **RELEASED (2026-10-16) — `rapier-runtime` 0.5.2 on PyPI. The report stops
  calling a same-vendor review independent.** Git tag `v0.5.2`;
  https://pypi.org/project/rapier-runtime/0.5.2/. A live 0.5.1 run with only an
  OpenAI key (author and reviewer both `gpt-6-sol`) showed three defects. The
  report said "an independent, different-vendor reviewer" raised the objections
  while its own trust section said the review was not independent. The progress
  warning blamed a missing OpenAI key that was set. LaTeX arithmetic printed raw.
  All three are fixed (`compose.py`, `cross_review.py`; the vendored reviewer is
  untouched) and re-checked on a second live run. 235 tests. The same day the
  repo gained the Loom secret-scan gate: the GitHub gitleaks workflow, the
  pre-commit hook and `.gitleaks.toml`.
  The live runs confirmed `gpt-6-sol` works, including the framing step at its
  1,024-token limit. **OpenRouter confirmed end to end (2026-10-16)** with only
  an OpenRouter key set: `frame` (1 call) and a full `spar` (8 calls) all went
  to `openrouter` / `openai/gpt-6-sol` with no empty responses, and the 0.5.2
  single-vendor wording held. Keys: 1Password, Sparring Framework vault, item
  `OpenRouter` (field `rapier-runtime-test` for tests).
  **Gemini default `gemini-3.5-flash-lite` confirmed live (2026-10-16):** one
  `frame` call with only the stored Gemini key returned a complete answer.
  **DeepSeek default `deepseek-flash` confirmed live (2026-10-16):** DeepSeek's
  own model list for the key is `deepseek-flash` and `deepseek-v4-pro`, and one
  `frame` call with only the DeepSeek key returned a complete answer. Key:
  1Password, Sparring Framework vault, item `DeepSeek`.
  Still never called live: the Groq and Together `openai/gpt-oss-120b`
  defaults (no keys).
- **RELEASED (2026-10-16) — `rapier-runtime` 0.5.1 on PyPI. Default models for
  five vendors replaced.** Git tag `v0.5.1`;
  https://pypi.org/project/rapier-runtime/0.5.1/. OpenRouter's default
  `openai/gpt-4o` (stops working 2027-01-14, Loom Plan 26) is now
  `openai/gpt-6-sol` (Bart's ruling). Checking every OpenAI-compatible default
  against its vendor's deprecation page found two already failing (DeepSeek
  `deepseek-chat`, Groq `llama-3.3-70b-versatile`), one leaving 2026-10-22
  (Together's Llama 3.3 Turbo) and one closed to new users (`gemini-2.5-flash`).
  The full table is in `CHANGELOG.md`, which starts with this release. None of
  the new defaults has been tried on a live run yet; that needs vendor keys.
- **RELEASED (2026-08-28) — `rapier-runtime` 0.5.0 on PyPI. The verifier stops
  calling real citations fabricated.** Git tag `v0.5.0`;
  https://pypi.org/project/rapier-runtime/0.5.0/. Eleven commits, and the
  headline is a correctness fix in the grounding verifier:
  `backend_literature` ran only a Crossref *bibliographic search* and gated every
  candidate on a cited-surname match — but a bare identifier carries no surname,
  so it could never match and fell through to `GROUNDED_REFUTED / "fabricated"`.
  A bare DOI and a bare arXiv id, the two most authoritative citation forms
  there are, were the two that always failed. `cite_check` had exposed
  `lookup_crossref_doi` and `lookup_arxiv` the whole time; the backend called
  neither. A verifier that flags real citations as fake is worse than none — it
  trains the reader to distrust the gate.
  Also in: identifier-strength resolution (DOI → arXiv → search, decisive both
  ways); new `law` (CourtListener) and `patent` (Google Patents) backends;
  evidence unioned across every identifier a reference supplies; a Proposer-side
  `traceability_gate` (RM §10.1/§13.1) so the framing step's own citations are
  grounded — verification was Resolver-only, so a Proposer could cite a
  nonexistent source and the run reported clean; the reconcile gate now runs as
  a standing check in `/spar`; and `RAPIER_MODEL_<VENDOR>` overrides the default
  model per vendor. 223 tests green.
  **Build note:** `pyproject.toml` pins `hatchling<1.30`. 1.30 emits
  Metadata-Version 2.5 (PEP 794), which the current publishing toolchain rejects
  (`twine check`: "'2.5' is not a valid metadata version") — that blocked this
  release at the gate until pinned. Lift only when `twine check` passes a 2.5 wheel.
  **Site:** no deploy needed. The full landing has been live since 2026-07-16 and
  carries no version string; verified byte-identical to `site/index.html` on
  2026-08-28.
- **RELEASED (2026-07-18) — `rapier-runtime` 0.4.0 on PyPI. Governance: runs
  persist by default + THE RECORD provenance.** Git tag `v0.4.0`;
  https://pypi.org/project/rapier-runtime/0.4.0/.
  Every ceremony now writes its durable record (verbatim transcript + per-stage
  records + verdict + report) to `~/.rapier/runs` with no flag — CLI *and* MCP
  (was: CLI wrote to a reaped temp dir; MCP wrote nothing unless
  `RAPIER_MCP_LEDGER` was set). Location overridable via `RAPIER_RUNS_DIR`; opt
  out per-run with `--no-save` / `RAPIER_NO_PERSIST`. The report gained a **THE
  RECORD** section stating the record exists and its full path (or, when opted
  out, that no trail was written) — so a Desktop/MCP user can *see* the audit
  trail, not just trust a stderr hint they never receive. `ledger.py`
  `default_runs_root()` / `persistence_disabled()`; +12 tests (186 total).
- **RELEASED (2026-07-18) — `rapier-runtime` 0.3.1 on PyPI.** Patch: `rapier
  doctor` / `--help` no longer crash on a legacy console (Windows cp1252) —
  `main()` forces UTF-8 stdout/stderr with a replacement fallback (`c82da6c`).
  Surfaced while wiring the MCP server into Claude Desktop (Windows). Git tag
  `v0.3.1`; https://pypi.org/project/rapier-runtime/0.3.1/.
- **RELEASED (2026-07-18) — `rapier-runtime` 0.3.0 on PyPI.** The MCP front-door
  tools (`frame` / `proposer`) + the `seed` / `depth` / `frame` knobs (PR #9,
  squash-merged as `432b74d`) now reach installed clients: `pip install -U
  rapier-runtime`. 0.2.0 was immutable, so this is a minor bump. Git tag `v0.3.0`
  pushed; https://pypi.org/project/rapier-runtime/0.3.0/. 172 tests green on the
  merged main.
- **LAUNCHED (2026-07-16) — M4 publish is done.** `rapier-runtime` **0.2.0** is on
  PyPI (`pip install rapier-runtime` now installs the input-typing front door,
  seeded generation, the depth knob, and the ledger fields; the pre-front-door
  `0.1.0` from 2026-07-09 is superseded). The **full MVP landing is live** at
  **rapierruntime.com** (no longer coming-soon), byte-identical to
  `site/index.html`, every link verified. Git tag `v0.2.0` pushed.
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
  extra (core stays `requests`+`pyyaml`). Tools: `frame`, `proposer`, `spar`,
  `sparring`, `rapier_doctor`, `list_runs`, `get_run` — full CLI parity (the front
  door + the `seed` / `depth` / `frame` knobs threaded through). Structured output;
  per-stage progress; cooperative cancellation; per-tool `timeout_s`; opt-in run
  persistence via `RAPIER_MCP_LEDGER`. The initialize handshake reports rapier's own
  version (not the SDK's). Scope + milestones: `docs/mcp-server-scope.md`. This is the
  **public, generic equivalent of the Loom `/spar` `/sparring` slash-commands**
  (which are Loom-only artifacts a generic user does not have).
- **rapierruntime.com — full landing deployed.** DNS → VPS `160.153.180.205`,
  Let's Encrypt via cPanel AutoSSL, HTTP→HTTPS redirect. The full MVP landing
  (`site/index.html`) is live at the docroot
  `~/public_html/rapierruntime.com/index.html` (byte-identical to the repo; all
  links — pip / PyPI / paper concept-DOI / spec / GitHub — verified 2026-07-16).
  `coming-soon.html` is retired. See `site/README.md`.

## Next (priority order)
0. **Map mode (SPARRING v1.4 §3.7, private at sparring-framework 6036a31b): built
   and passed by the verifier, NOT released.** On branch
   `worktree-demeter-rapier-runtime-2` (not on main; D-101, amended by D-115, merges
   it only with its release, on "plan 19 on-contract"). First live run (D-100,
   $20.85) FAILED: a drafted 47-region frame hit the 120-call cap, and the citation
   check misread real loci. Bart chose D-115 a: $0 work first, then ONE run of Loom
   plan 19, supplied frame, Haiku 5.5 Generator, GPT-5.5 attacker, hard cap $8.
   - **Dollar cap: passed** (rr-1, 56c26e03; 830 tests; rr-1's own 300-run fuzz
     clean). Per-run budget (contextvar) set and reset around the whole run;
     every input that feeds a charge finite, non-negative and under a ceiling
     ($1,000 cap, $1,000/M price, 200k output tokens, 50M usage) or refused;
     every retry reserved and settled; spend written on every exit; price
     overrides can't lower a price; incomplete usage charged in full; MCP
     `max_usd=0` is $0; vendored direct callers refuse under a cap; an overrun is
     charged in full and stops the run. Cross-vendor /spars D-120 ($1.10) and
     D-123 ($1.52) found S-1..S-6 and M-1/M-4, all fixed. **D-126, the last
     cross-vendor pass, PASSED** ($1.35 of $3, overrun $0). The cap is done.
   - **v1.4 rules: passed** (rr-1, 4faa3777 + 1976e28a + 1e2b4d4d). 12-unit cap and
     blocks, pre-run call plan (`--max-calls`), labels by claimed class, matcher over
     the whole field (one call per unit), misquoted demotion at 0.8, REVIEW at >10%
     misquoted; absence claims anchor to a section.
   - **v1.5 class a check: passed** (rr-1, d09b90f2; from private spec fe5f4c00,
     D-128 a). rr-1's V15-1..V15-4 (stemmed protected words, near-quote window
     edges, unfenced diagrams and multi-line cells, a source's own "...") were the
     build catching up to v1.5 as written (sparring-3's ruling; the explicit stem
     rule goes to the next spec bump, sparring W-28). Own-build $0 re-score of
     D-100 (`scripts/rescore-map.py`): v1.4 890/40/18; **v1.5 872/70/6, 7.4%
     misquoted (32 near-quotes, 38 elisions), possible reversal on 18 items**;
     rr-1 reproduced it. The 9 that moved from the first build's 863/79/6 are all
     quotes of the source's own "...". The 6 are real errors.
   - **Next, in order:** sparring-2's reference script lands on the same counts;
     sparring-3 shows Bart the new counts beside 863/79/6 in a confirm item; on his
     yes, the ONE $8 run under the brief below. v1.5 goes public (sparring W-23)
     before any release.
   - **Run brief (D-115, as relayed from rr-1 via loom-0):** `--max-usd 8` is a
     PER-RUN cap, not a wallet. Start exactly ONE live run of Loom plan 19 (supplied
     10-region frame, Haiku 5.5 Generator, GPT-5.5 attacker). No concurrent live
     runs, no retry as a fresh run. A stop at the cap is a defer that goes back to
     Bart, not a re-run.
   - **Release gate (D-101, stated in D-128 covers 5):** release if the run is
     on-contract (every stage ran, every reply met its contract). A gate FAIL on
     real errors still releases. A cap or call-budget stop, or any off-contract
     reply, does not: fix, re-review, back to Bart. Only once D-128 is answered.
   - Cost lesson: the whole field resent on every call (~90k tokens) was the bill,
     not the source.
1. ~~**Confirm the MCP server on Claude Desktop.**~~ **DONE (2026-07-18) — end to
   end.** Desktop (Windows, MSIX-packaged build) launches the `rapier` server (`py
   -m rapier.cli mcp`, `serverInfo 0.3.1`, 7 tools) AND a **live `spar` tool call
   ran** in a regular chat — returned a Resolver verdict with a grounding-gate FAIL
   (the definitiveness gate correctly flagging ungrounded specifics on a data-free
   prompt). Both flagship clients verified (Claude Code 2026-07-17, Desktop
   2026-07-18). **Two gotchas for next time:** (a) the packaged app reads its config
   from the package sandbox, NOT `%APPDATA%\Claude\` (see Things-to-know); (b) the
   local MCP server surfaces in a **regular chat**, NOT in **Cowork** mode (Cowork
   uses Skills/Plugins and will *role-play* the tool instead of calling it).
   (Optional follow-on: expose runs as MCP *resources*.)
2. **Paper 2 (the Proposer)** stays parked until the engine is fully shipped.
   Note: the Proposer study runs *on* this engine — Frame, seeded generation, and
   the depth knob are the instrument it will exercise.
3. ~~**Optional polish:** runs as MCP resources; a landing line for the front
   door / `--depth` / `--seed`.~~ **DONE (2026-10-16).** The landing line is live.
   Saved runs are MCP resources (`rapier://runs`, `rapier://runs/{run_id}`,
   `rapier://runs/{run_id}/record`); the `list_runs` / `get_run` tools stay for
   tool-only clients. Released in 0.5.3.
4. ~~**Port the MCP server to the mcp 2.x SDK.**~~ **DONE (2026-10-16), not yet
   released.** `MCPServer` replaces `FastMCP`; the `[mcp]` extra is now
   `mcp>=2.3,<3`. 250 tests pass on mcp 2.3.0, and a built wheel was checked over
   real stdio: tools, resources, a live `frame` call with progress, and the new
   run appearing in the next listing. Released in 0.6.0.

_(M4 publish is **DONE** — v0.4.0 on PyPI + the full landing live; see Done above.
Loom submodule pin bumped to the v0.4.0 release commit `514df56`.)_

## Things a new session should know
- **Two repos.** This engine (`muddyone/rapier-runtime`, public) vs. the SPARRING
  method + paper (private `muddyone/sparring`; public artifacts in
  `muddyone/sparring-publicaccess`).
- **Interfaces.** Generic users get the `rapier` CLI, the Python API, and the MCP
  server. `/spar` `/sparring` slash-commands are **Loom** artifacts, not shipped
  from here.
- **Secrets are env-only by design** (`secrets.py`) — do not add file-reading of
  keys; populate the environment upstream instead.
- **Claude Desktop MCP config (Windows, MSIX/Store build).** The packaged app's
  `%APPDATA%` is redirected into the package sandbox, so it does NOT read the
  classic `%APPDATA%\Claude\claude_desktop_config.json`. The live file is
  `…\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`
  (the app manages it — merge `mcpServers` in, preserve the other keys; use the
  in-app **Settings → Developer → Edit Config** button to open the exact file).
  Server entry: `command: py`, `args: [-m, rapier.cli, mcp]`, `env` with
  `PYTHONUTF8=1` + vendor keys. Confirmed running 2026-07-18.
- **Tests.** `pytest -q`. On machines using the vendored `.tools/` pytest, run
  with a native-filesystem `TMPDIR` (the ledger owner-perms test needs real Unix
  perms, which `/mnt/c` DrvFs can't hold).
