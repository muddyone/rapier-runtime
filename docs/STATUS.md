# Rapier — dev status

_Last updated: 2026-07-08 (HEAD 508298b, 116 tests)._ A running "where we are /
what's next" so a new session can continue without reconstructing from git log.

## Done
- **Engine M0–M3** — full SPARRING ceremony (Proposer → Resolver) end-to-end;
  presets `spar` / `sparring` / `proposer`; `--settle` / `--verify` knobs.
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
   spec links). This is what makes `pip install rapier-runtime` real.
2. **Bump the Loom submodule pin** to the current rapier HEAD. None of the
   2026-07-08 work reaches the Loom cohort until the pin advances.
3. **MCP:** end-to-end live-session test with a real client (Claude Desktop);
   optionally expose runs as MCP *resources* rather than tools.
4. **Paper 2 (the Proposer)** stays parked until the engine is fully shipped.

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
