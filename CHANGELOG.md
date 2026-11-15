# Changelog

Notable changes to `rapier-runtime`. Earlier releases are summarized in
[`docs/STATUS.md`](docs/STATUS.md) and on the
[Releases](https://github.com/muddyone/rapier-runtime/releases) page.

## Unreleased

### Added: a hard dollar cap on a run

`--max-usd` on every ceremony command, `max_usd` on the MCP tools, and
`RAPIER_MAX_USD` as a ceiling no flag or argument can raise. Before each model
call the run reserves that call's worst case (its input plus its full output
limit, at the model's list price) and refuses the call if it could pass the cap,
so a run can stop short of the cap but never exceed it. A refused call's check
reads as not run; a map stopped at the cap is REVIEW, "incomplete run", final
state `defer`. With a cap set, a model with no listed price is refused. Spend
by model is in `budget.json` and the report.

The cap holds per run and per request:
- Each run has its own budget, so two runs in one process (concurrent MCP
  calls, or a run abandoned on timeout that is still finishing) can't share or
  clear each other's cap.
- A cap that isn't a finite number of dollars, 0 or more (NaN, infinity,
  negative) is refused before the run: CLI usage error, MCP `ok: false`,
  `final_state: "refuse"`, `refused_by: "max_usd"`. A `RAPIER_MAX_USD` like that
  fails closed to 0.
- Every request is reserved and settled, retries included. A 429/5xx, or an
  error before anything was sent, is released; a timeout, a connection dropped
  after sending, a 4xx or an unreadable body is charged in full.
- The input estimate is the payload's UTF-8 byte length plus a per-message
  allowance, an upper bound on its tokens.
- Every vendored caller that reaches a vendor outside Rapier's model layer
  (the grounding judge, the claim mapper, cite_check's extraction, the original
  reviewer clients) refuses under a cap, and the check reads as not run.
- The spend record (`budget.json` and the envelope) is written on every exit,
  a crash or an interrupt included.
- `RAPIER_PRICES` may add a model or raise a price, never lower one; a price
  must be above 0.
- Usage that doesn't name both input and output tokens is charged the full
  reservation. Anthropic cache-write and cache-read tokens are charged (1.25x
  and 0.1x input), and the worst case assumes a full cache write. Rapier sends
  no `cache_control`.
- A call is settled at the price it was reserved at.
- MCP `max_usd=0` is a cap of $0. Left out, there is no cap.
- Every number that feeds a reservation or a charge must be finite,
  non-negative and under a ceiling, or the call is refused (or the run, before
  it starts): the output limit (a whole number from 1 to 200,000, checked again
  when a manifest loads), prices (above 0 and at most $1,000 per million
  tokens), the cap (at most $1,000 per run), each usage count (a whole number
  up to 50 million, else the full reservation is charged), and Retry-After
  (0 to 30 seconds, else the backoff). Reservations and charges never go below 0.
- The budget is set and reset around the whole run, setup included, and a run
  never clears an enclosing budget.
- A call that costs more than it reserved (a vendor billing past the limit) is
  charged in full, never clamped; the run stops there and the report says by
  how much.

### Changed: map mode follows SPARRING specification v1.5, section 3.7

The class a citation check is made fit for real sources (D-128 a).
- **Looser where only layout differs.** String escapes (`\"`) are removed. In a
  table row, a quote's fragments between pipes must appear in order within one
  row. In a diagram (a fenced block drawn with box or arrow characters), each
  fragment of four or more letters or digits must be inside one cell, a shorter
  one must equal a whole cell, and consecutive fragments must be in cells that
  touch. That means on one line with only separators between them, or on
  adjacent lines with overlapping columns; adjacent lines alone are not enough.
- **Tighter where meaning can change.**
  - Each ellipsis fragment needs 3 or more words, with at most 300 characters
    between fragments; outside those limits the quote is misquoted (an elision).
  - An ellipsis that skips a negation, modal or exclusion word (only, except,
    unless, without, never) inside one sentence is fabricated. A skip across a
    sentence break is misquoted; if it also skips such a word, the map gets a
    REVIEW trigger, *possible reversal by ellipsis*.
  - A leading or trailing ellipsis that cuts at one of those words is fabricated,
    unless the source has its own ellipsis there.
  - The gap rules don't apply inside fenced code or data blocks.
  - Direction and quantity words (before/after, start/end, all/some and so on)
    are protected in near-quotes. The list, the 3-word minimum and the 300-character
    gap are in the manifest.
- **Gate and rider.** The 10% REVIEW line counts near-quotes and elisions both;
  the rider counts them separately and lists every possible reversal. A frame
  declared at its cap is noted as having no room for frame-attack additions.
- Protected words are compared by stem: "starting" for "ending" is a protected
  change, "starts" for "starting" is not. A near-quote's window doesn't count
  its own unmatched edge words as differences. A diagram need not be fenced
  (two or more consecutive lines drawn with box or arrow characters), and a cell
  spans consecutive lines. A quote whose "..." is in the source itself is found
  verbatim. A bare single common word stays fabricated even when it is a cell.
- `scripts/rescore-map.py` re-scores a saved run at $0 under v1.4 and v1.5. On
  the D-100 record: v1.4 890 / 40 / 18, v1.5 872 / 70 / 6 (7.4% misquoted: 32
  near-quotes, 38 elisions), possible reversal on 18 items.

### Changed: map mode follows SPARRING specification v1.4, section 3.7

- **Units and blocks.** A map attacks at most 12 units (a region, or a declared
  block of regions), unless the frame declares a higher cap (`max_units`).
  Above the cap, blocks group the regions along one seam (`section` or `lens`).
  A supplied frame above the cap without blocks, or with invalid blocks, is
  refused before any call (final state `refuse`). A drafted frame is grouped by
  the framer, never the Generator. A frame-attack addition that takes the frame
  past its cap stops the run before generation. Generation, attack and labels
  run per unit, and a block's label claims nothing about any single region.
- **Call plan.** Before generation the run computes its planned calls, an upper
  bound (6 per unit plus the frame calls, 7 with the support check). Over a
  declared call budget (`--max-calls`, MCP `max_calls`), it does not start. The
  rider reports planned and actual calls. A unit cut short by the dollar cap
  names that cause, and the gate is REVIEW (incomplete run).
- **Labels by claimed class.** A new attack candidate claiming class a or b
  keeps its unit from HELD, whatever the citation check finds.
- **Overlap.** The matcher judges each attack's candidates in one call, against
  the whole field. A new candidate is credited to the attacking unit and tagged
  with any other unit it fits.
- **Class a check.** A quote that fails but is at least 0.8 word-level similar
  to a same-length passage, with no negation, modal or number among the
  differing words, is *misquoted*: demoted to class c and shown beside the
  passage. The threshold is in the manifest. Anything else fails as fabricated.
  An ellipsis quote must match in order within its cited section, and a failed
  fragment is judged on its own. This replaces the v1.3 build's 300-character
  gap and three-word minimum for ellipsis quotes.
- **Gate.** REVIEW when more than 10% of class a anchors are misquoted.
- **Checker fixes found by re-scoring the D-100 record:** a locus with two
  sections checks each quote against its own section; box-drawing and arrow
  characters and literal `\n` fold to spaces; a single-quoted quote may
  contain an apostrophe; a bare identifier of 6 or more characters found
  verbatim counts as found.
- `--frame-attack-from RUN_DIR` on `rapier map` carries an earlier run's frame
  and frame-attack verbatim from its record (same source only), and `--frame`
  then gives the blocks that regroup it. The frame is frozen: no second
  frame-attack runs, and the rider says so (sparring-3's ruling on v1.5 §3.7).
- `--frame FILE` (JSON regions, blocks and `max_units`) on `rapier map`; MCP
  `map` takes `blocks`, `max_units` and `max_calls`.

### Added: map mode (SPARRING specification v1.3, section 3.7)

Specification: SPARRING v1.3, §3.7 "Terminal divergence: the map", public at
[sparring-publicaccess `framework/sparring-specification.md`](https://github.com/muddyone/sparring-publicaccess/blob/21752d0d/framework/sparring-specification.md).

`rapier map`, the `map` preset and the MCP `map` tool map a problem space
instead of deciding: the Proposer's field is the product, with its own checks.
The Cut and the Resolver do not run.

- **A search frame first.** Supplied with `--region` / `regions`, or drafted by
  a separate Frame call (never the Generator) and marked drafted. A Challenger
  on another vendor attacks the frame before any generation; the frame is then
  recorded with a hash.
- **Per region:** a scoped generation call (probe), the Challenger's expand
  pass, and after Pattern Lock one completeness attack under a fixed, hashed
  contract. A fixed, hashed matcher decides "new?"; ties, and a matcher that
  fails, count as new. Labels HELD / YIELDING / UNPROBED are read from the
  run's own record.
- **Per item:** region(s), anchor class (a cited source, b cited ground, c
  speculative), origin, role and vendor, merged ids. A class a locus must exist
  in the source (a miss is a fabricated anchor); a class b anchor needs a title
  and an entry identifier, or drops to c. An optional support check.
- **The map gate** (PASS / REVIEW / FAIL) and a **coverage rider** with the
  frame, region labels, what the gate claims, anchor and origin counts, merges,
  a coverage estimate only when there are enough trials, and the fixed limit
  sentences. No item budget and no ranking.
- **Final states:** FAIL gives block; a check that did not run gives defer; any
  other REVIEW gives escalate; PASS gives act. With one vendor the attacks run
  but are not independent, so regions are YIELDING. The operator minimum and
  `require_independent` apply; a custom map manifest without its checks is
  refused under a minimum.

## 0.6.3 — 2026-11-14

### Fixed: an unreadable policy file is no longer reported as the minimum refusing the run

When the operator policy file could not be read or checked (bad JSON, an
unknown level, a non-boolean `require_independent`, a folder Rapier can't
search), 0.6.2 stopped the run, correctly, but credited the stop to the
operator minimum (`refused_by: "operator_minimum"`, `final_state: "refuse"`).
The minimum was never read, so it decided nothing. The run still stops (MCP
`ok: false`, CLI exit 2), now with `final_state: "defer"`, no `refused_by`, and
`operator_policy: "did-not-run: <reason>"`. A readable policy that refuses a
request is unchanged: `refuse`, credited to the control that decided.

## 0.6.2 — 2026-11-14

### Added: every run names its final state

The report's BOTTOM LINE opens with a final state, using the six names from the
Accountable Agents paper, and the same value is in the trust rider, `ceremony.json`
(`final_state`, `final_state_next`) and the MCP result:

- `act`: the advice cleared the checks that ran, and the report names them and any
  that were switched off. Rapier only advises; acting on it is yours.
- `block`: a specific failed the correctness check, or a reference was refuted.
- `defer`: a declared check or the challenge review did not run on the answer
  (crashed, no key, a reviewer error, or ran before the answer was written), or
  the operator requires an independent review and none happened. A control that
  did not run decided nothing, so this is never `block` and never `act`.
- `escalate`: a specific reads ambiguously between fact and estimate; your call.
- `non-decision`: the Proposer hit its round cap without agreeing on one option.
  The report says so at the top, and in the Proposer report. The Resolver still
  tests the strongest candidate, but choosing between the options is yours.
- `refuse`: the run was refused before it started (operator minimum or
  independence requirement).

A citation check that was switched off or never declared doesn't change the
state, but `act` says it didn't run. A review that failed at run time (the
reviewer is fail-soft) is reported as not run, never as an independent challenge.

### Added: the operator can require an independent review

`"require_independent": true` in the operator policy file refuses any review
ceremony (spar, sparring, proposer, or a custom manifest with a review or
Proposer stage) when no second vendor is available. Without it, a single-vendor
run goes ahead and the report says the review was not independent, as before.
Only the operator can set this: there is no tool argument or environment
variable for it. The refusal names the policy file and whether it is out of the
caller's reach (MCP: `refused_by: "operator_require_independent"`; CLI: exit 2).
`rapier doctor` shows the setting and warns when only one vendor is configured.

### Added: a hash of the method that ran

`ceremony.json` gains `method_hash` and `method_resolved_hash` (plus
`rapier_version`), and THE RECORD shows both, shortened. `method_hash` covers
the Rapier version, every stage in order with its config and roles, and the
vendor policy. `method_resolved_hash` is the same after vendor substitution, so
a run on different keys shows up as the same method on different vendors. To
check for drift, compare runs on the same inputs under the same `method_hash`.

### Added: what the method declared, compared with what ran

- **WHAT RAN**, a new report section, lists every stage the method declared
  and what happened to it: ran, FAILED (the run went on without it), skipped
  (with the stage's reason) or not reached (cancelled). The pipeline records
  this itself, so a stage that writes no trace still appears. Also in the trust
  rider (`stages_run`) and `ceremony.json` (`stages_declared`,
  `stages_failed_soft`, `stages_skipped`).
- **Checked before a run.** A pipeline that writes a report but declares no
  correctness gate, or puts a gate after `compose` where its result can't reach
  the report, gets a warning in the log, the report and `ceremony.json`
  (`manifest_warnings`). `rapier doctor` checks every built-in ceremony and
  shows the operator minimum. `rapier doctor --manifest m.yaml` checks a custom
  manifest and exits 1 if it warns or would be refused.
- **Custom manifests meet the operator minimum.** `rapier run --manifest` with
  no citation gate before `compose` counts as `verify=off` and is refused under
  a `gate` minimum.

### Added: an operator minimum for the checks, which the caller cannot lower

Over MCP the agent whose answer is being checked chose `verify`, so it could
switch the citation check off. The operator can now set a floor:

- **`/etc/rapier/policy.json`** (`%PROGRAMDATA%\rapier\policy.json` on
  Windows), `{"min_verify": "gate"}`. The path is fixed, not taken from the
  environment, so a caller cannot point Rapier at a different file. The report
  calls the minimum out of the calling agent's reach only when Rapier is not
  running as root and neither owns nor can write the file or any folder above
  it (for example, owned by root, readable by the user that runs Rapier). A
  file that can't be checked, read or parsed fails closed.
- **`RAPIER_MIN_VERIFY`** also sets a floor, but whoever launches the process
  controls its environment, so the report always calls it within the caller's
  reach.
- A request below the floor is **refused**, with an error naming the floor and
  its source (MCP: `ok: false`, `refused_by: "operator_minimum"`; CLI: exit 2).
  A policy file that can't be read fails closed. Levels: `off` < `gate` ≤
  `round`.
- **The report says who set the level:** "switched off, by caller (MCP tool
  argument)", the default or the `--verify` flag, and whether an operator
  minimum stood behind it. `ceremony.json` gains `verify_set_by`,
  `verify_minimum`, `verify_minimum_source` and `verify_minimum_external`.

### Fixed: a check that did not run no longer reads like one that found nothing

The report used one sentence for "the correctness check found no specifics" and
"the checker was unavailable". With `verify=off` the registry section said the
answer "cited nothing externally checkable", though nothing had been looked up.

- **Three outcomes, kept apart**, for both the correctness check and the
  citation check: `ran`, `nothing-to-check`, and `did-not-run: <reason>`. A
  crash (caught fail-soft), a missing key, `verify=off`, and a check left out of
  the pipeline are all `did-not-run`, and the report says it is not a pass.
- **A run with no hard specifics** now says the check found nothing to check,
  instead of "every hard specific it states traces to a fact you gave".
- **Recorded everywhere the reader or a tool looks:** the report, the trust
  rider (`correctness_check`, `citation_check`), `ceremony.json`
  (`*_check` and `*_check_reason`) and the MCP result.

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
