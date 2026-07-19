# Scope: Rapier MCP server

**Goal.** Give any MCP client (Claude Desktop, Claude Code, Cursor, …) the
`spar` / `sparring` capability as first-class tools — the *public, generic*
equivalent of the private Loom slash-commands. A user installs Rapier, points
their MCP client at it, and gets adversarial review in-editor with no Loom and
no bespoke skill files.

This is a thin adapter. The engine already does all the work; the MCP layer only
translates a tool call into `load_preset(...).build().run(request, …)` and shapes
the result back. No methodology logic lives here.

## Non-goals
- Not a hosted service. It runs locally as the user's own process, BYO keys.
- Not a replacement for the CLI/library — a third front-end onto the same engine.
- Does **not** expose `run --manifest` (arbitrary manifest execution is a security
  surface we don't want a model driving); only the vetted presets.

## Tools exposed
| Tool | Maps to | Params | Returns |
|------|---------|--------|---------|
| `frame` | frame preset | `request` (str, req) | the Presentation: `input_type`, `route` (propose\|resolve), `readiness` — classify before running a ceremony |
| `proposer` | proposer-only preset | `request`, `seed` (list=None), `depth` (shallow\|standard\|deep =standard) | committed proposition + standing objections + structured block |
| `spar` | resolver-only preset | `request`, `settle` (int=0), `verify` (off\|gate\|round =gate), `frame` (dict=None) | two-part report (recommendation + trust rider) + structured block |
| `sparring` | full ceremony | `request`, `settle`, `verify`, `report_all` (bool=false), `seed`, `depth`, `frame` | Proposer + Resolver reports + structured block |
| `rapier_doctor` | vendor detection | — | which vendors are configured (names only, never values), whether cross-vendor is possible, what's missing |

All four ceremony tools take `timeout_s` (float=0; >0 caps the run). `seed`/`depth`
mirror the CLI's `--seed`/`--depth`; `frame` accepts the dict from the `frame` tool
(the CLI's `--frame`, recorded on the ceremony-ledger row). `proposer`-only was
deferred in v1 (low standalone demand) and added here once the front door landed.

### Tool output shape
Return **both** a human-readable markdown report *and* a structured payload, so a
client can render prose or branch on the result:
- `report_md` — the composed two-part report (what the CLI prints).
- `verdict` — `PASS | REVIEW | FAIL | unchecked` (the definitiveness gate).
- `grounding` — `{gate, grounding_rate, verified, refuted, unverifiable}` from the
  now-live citation gate (the external-canon check).
- `cross_vendor` — bool, plus `author_vendor` / `reviewer_vendor` (honest
  degradation is visible, not hidden).
- `standing_objections` — forwarded Proposer dissent, when present.

All outputs pass through `secrets.redact_obj` (already the ledger's discipline)
before leaving the process.

## Transport & packaging
- **Transport:** stdio (the standard for a local, client-launched MCP server).
  SSE/HTTP is a later option and not needed for the desktop/editor use case.
- **Entry point:** a `rapier mcp` subcommand (one package, one binary, discoverable
  via `rapier --help`) rather than a separate `rapier-mcp` script. MCP client
  config then launches `rapier mcp`.
- **Dependency:** the official MCP Python SDK (`mcp`). Rapier's runtime deps are
  deliberately tiny (`pyyaml`, `requests`), so ship MCP as an **optional extra**:
  `pip install "rapier-runtime[mcp]"`. `rapier mcp` prints an actionable install
  hint if the extra is missing. Core stays lean; MCP users opt in.

### Client config (documentation deliverable)
```jsonc
{
  "mcpServers": {
    "rapier": {
      "command": "rapier",
      "args": ["mcp"],
      "env": { "ANTHROPIC_API_KEY": "…", "OPENAI_API_KEY": "…" }
    }
  }
}
```
Keys travel in the server's `env` block (the client launches the subprocess with
it) — this is the MCP-native answer to key configuration, and it keeps the engine
env-only (no file-read of secrets). `rapier_doctor` is how a user confirms the
config took.

## Long-running calls
A `sparring` ceremony is many model calls and can run for minutes. v1 must:
- Emit **MCP progress notifications** per stage (the engine already takes a `log`
  callback — route it to progress) so the client shows life, not a hang.
- Support **cancellation** (honor the MCP cancel; stop between stages).
- Set client-visible expectations: `spar` is quick, `sparring` is a longer ceremony.

## Security
Runs locally; the only network is the vendor APIs plus the grounding registries
(MITRE/Crossref/IETF/URL-liveness, which already carry an SSRF guard). Reuse the
existing redaction on every tool result. No new secret surface — keys stay in env.

## Milestones
- **MCP-0 ✅ (done, 2d4a017):** `rapier mcp` stdio server exposing `spar` +
  `sparring` + `rapier_doctor`, optional `[mcp]` extra, client-config docs.
- **MCP-1 ✅ (done, 6752181):** structured output (verdict + grounding +
  cross_vendor + standing_objections), per-stage progress notifications.
- **MCP-2 ✅ (done, 508298b):** cooperative cancellation, per-tool `timeout_s`,
  ledger-run access (`list_runs`/`get_run`).
- **MCP-3 ✅ (persist by default):** runs persist to `~/.rapier/runs` by default
  (was opt-in via `RAPIER_MCP_LEDGER`; that var now just *relocates* the store
  for this server). `RAPIER_NO_PERSIST` opts out. Every tool's report carries a
  **THE RECORD** section naming the run directory, so a Desktop user can see the
  audit trail exists and where — governance is only real if the record is visible.

Follow-ups: consider exposing runs as MCP *resources* (currently tools).

## Open decisions
1. `rapier mcp` subcommand (recommended) vs a separate `rapier-mcp` entry point.
2. Optional `[mcp]` extra (recommended) vs a core dependency.
3. Whether to surface `proposer` as a tool in v1 (recommend: no).
4. Progress granularity — per-stage (recommended) vs per-phase.

## Ties to key-configuration
The MCP `env`-block config and the `rapier_doctor` tool are half the answer to
first-run key friction; the CLI side (a preflight error + `rapier doctor`) is the
other half. See the key-config plan (tracked separately).
