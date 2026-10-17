# Changelog

Notable changes to `rapier-runtime`. Earlier releases are summarized in
[`docs/STATUS.md`](docs/STATUS.md) and on the
[Releases](https://github.com/muddyone/rapier-runtime/releases) page.

## 0.6.1 — 2026-10-16

### Fixed: "WHETHER THE NUMBERS ADD UP" no longer flags correct formulas

The arithmetic check summed the numbers in any stated result, so a correct
standard error, interval, sample size or difference was reported as wrong. In
a statistics-heavy answer that was five false alarms out of five.

- **Formulas are worked out as written.** Roots, powers, quotients and ±
  bounds are evaluated in code, and a mismatch shows the formula it evaluated.
  Differences are checked as differences.
- **"Not checked" instead of a guess.** Where the operation can't be recovered
  from the text, or a "total" turns out to be a formula, the figure is counted
  as not checked, never as a mismatch. The section says how many there were.
- **Small figures are checked properly.** The agreement margin used to be
  measured against 1, so any figure under 1 agreed with almost anything. It is
  now 0.5% of the figures themselves, or half a unit of the last digit the
  text shows (`0.120` allows ±0.0005; `6×10⁻⁵` allows ±5×10⁻⁶).

## 0.6.0 — 2026-10-16

### Changed: the MCP server now runs on the mcp 2.x SDK

The `[mcp]` extra now requires `mcp>=2.3,<3` (it was `>=1.2,<2`), and the
server is built on the SDK's `MCPServer`. Tools, their results and the run
resources behave as before. Two visible differences:

- **Progress shows the stage.** Each stage's progress update now carries the
  stage name as its message. mcp 2.x sends log messages only to clients that
  ask for them, so the progress message is what every client will show.
- **`pip install -U "rapier-runtime[mcp]"` upgrades the SDK.** An environment
  pinned to mcp 1.x will be moved to 2.x. If a 1.x SDK is installed anyway,
  `rapier mcp` says so and gives the command that fixes it.

## 0.5.3 — 2026-10-16

### Added: saved runs as MCP resources

MCP clients can now browse saved runs instead of calling tools:

- `rapier://runs`: every saved run, newest first, with its kind, topic and
  verdict (JSON).
- `rapier://runs/{run_id}`: a run's report, as the CLI printed it (plain text).
- `rapier://runs/{run_id}/record`: a run's full record, redacted when it was
  saved (JSON).

Each saved run also appears in the client's resource list, titled by kind and
topic, including runs saved after the server started. The `list_runs` and
`get_run` tools still work.

### Fixed: `rapier mcp` failed on a fresh install

The `[mcp]` extra allowed any `mcp>=1.2`, so a new install got the mcp 2.x SDK,
which renamed the server API this version uses. `rapier mcp` then exited with a
message telling you to install the extra you already had. The extra is now
capped at `mcp<2`, and if a 2.x SDK is present anyway, `rapier mcp` says so and
gives the command that fixes it.

## 0.5.2 — 2026-10-16

### Fixed: the report no longer calls a same-vendor review independent

Found on a live run with a single vendor's key, where the reviewer and the
author were the same model:

- **The report contradicted itself.** "Already fixed under challenge" said
  "an independent, different-vendor reviewer raised each of these," and the
  registry section pointed to "the cross-vendor challenge," while "How much to
  trust this" correctly said the review was not independent. Every section
  now says what actually happened; a cross-vendor run reads as before.
- **The progress warning blamed the wrong key.** It said "OPENAI_API_KEY
  absent; falling back to a Claude reviewer" when the OpenAI key was set and
  no Claude model was used. It now names the vendor that ran the review.
- **Arithmetic printed as raw LaTeX.** Math the model wrote as `\( H \times
  0.60 \)` now reads `H × 0.60` in the report. Only text inside `\( \)` or
  `\[ \]` is rewritten.

## 0.5.1 — 2026-10-16

### Changed: default models for five OpenAI-compatible vendors

Five built-in defaults named models that a vendor has retired or is retiring. If
you relied on a vendor's default (no `model:` in your manifest and no
`RAPIER_MODEL_<VENDOR>` set), **your runs now use a different model.** To keep
the old one while it still works, name it in your manifest or set
`RAPIER_MODEL_<VENDOR>`.

| Vendor | Was | Now | Why |
|---|---|---|---|
| `openrouter` | `openai/gpt-4o` | `openai/gpt-6-sol` | gpt-4o stops working 2027-01-14. gpt-6-sol is OpenAI's current flagship and costs slightly less. |
| `deepseek` | `deepseek-chat` | `deepseek-flash` | `deepseek-chat` was retired 2026-07-24, so the old default already fails. |
| `groq` | `llama-3.3-70b-versatile` | `openai/gpt-oss-120b` | Shut down 2026-08-16, so the old default already fails. Groq names gpt-oss-120b as the replacement. |
| `together` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` | `openai/gpt-oss-120b` | Leaves Together's serverless tier 2026-10-22. |
| `gemini` | `gemini-2.5-flash` | `gemini-3.5-flash-lite` | 2.5 Flash is closed to new API users. Google recommends 3.5 Flash-Lite for new projects, at the same price. |

`xai` (`grok-4.3`), `mistral` (`mistral-large-latest`) and `ollama` (`llama3.1`)
are unchanged. Each was checked against its vendor's deprecation page on
2026-10-15.
