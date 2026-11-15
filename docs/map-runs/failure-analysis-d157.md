# Map-run reply failures: analysis after the D-157 run (2026-11-15, $0)

All recorded replies from the map runs that generated: the D-100 run
(`20261114135731-map`, Opus 4.8 Generator), run 3 (`20261115010859-map`) and run 4
(`20261115015600-map`, the D-157 run), both with a Haiku 5.5 Generator. Runs 1 and 2
(`20261114185558-map`, `20261114190132-map`) made one frame-attack call each, both clean.

## Bad replies, by model and call type

| Model | Call | Replies | Cut off | Malformed JSON |
|---|---|---|---|---|
| Haiku 5.5 | generate | 25 | 2 | 1 (`"class": a`, unquoted) |
| Haiku 5.5 | merge | 24 | 0 | 0 |
| Opus 4.8 | generate | 47 | 0 | 1 (a locus string closed with `'`) |
| GPT-5.5 | expand | 71 | 0 | 2 (both an unescaped `"status": "deferred"` quoted from the source) |
| GPT-5.5 | completeness-attack | 30 | 0 | 1 (D-100: an anchor object not closed, 377k-char prompt) |
| GPT-5.5 | match, filter, frame-attack, frame-draft | 65 | 0 | 0 |

## Causes

- **Haiku cut-offs are its hidden thinking using up the output limit.** Billed output
  is about 1 character per token for Haiku and 0.87 for GPT-5.5, against roughly 4 for
  plain text, so most output tokens are reasoning that's never shown. Haiku 5.5 thinks
  adaptively by default (effort `medium`). Map calls cap output at 8,000 tokens. The
  cut-off replies stopped at 8,578 and 8,949 visible characters, while complete replies
  reached 9,717. So visible length doesn't predict a cut-off; thinking plus text does.
- **Malformed JSON is the model's reliability on this contract,** not prompt size.
  Failed and clean generate calls had the same input size (about 22k characters), and so
  did expand calls (about 28k). Both GPT-5.5 failures are the same source text (a quoted
  JSON fragment inside backticks), so the same source can trigger it again.
- **The schema isn't the cause.** The contract is small and the slips are syntax errors,
  not shape errors.

## Options

"Peak" is the highest point of spend so far plus the next call's worst-case reservation.
If it passes $8, the cap refuses that call. Simulated on run 4's own call order:
peak $7.41, spend $6.66.

| Option | Fixes | Build | Per run | Peak |
|---|---|---|---|---|
| Haiku output limit 16,000 tokens | cut-offs (the cause) | $0, one constant | about +$0.00 to $0.07 | $7.41 |
| Structured outputs (Anthropic `output_config.format`, OpenAI `json_schema` strict) | malformed JSON, all 5 cases on record | $0 code; a few cents of live checks | no surcharge; first-call schema latency | $7.41 |
| Format-repair retry, budget N | malformed replies, by resending | $0 | N=1 +$0.28, N=2 +$0.54 worst case | N=1 $7.69, N=2 $7.95, N=3 $8.21 (any role); expand only: $7.53 / $7.64 / $7.75 |
| Sonnet 5.5 Generator | unknown (Opus 4.8 also had 1 of 47) | $0 | about +$1.33 | $8.74, over the cap |
| Tolerant parser (unambiguous repairs only) | 1 of 5 on record (B2) | $0 | $0 | $7.41 |

## $0 replay (`scripts/replay-map.py`)

- Run 4 through the current code (6f629c79): all 72 calls served from the record, no
  gaps. REVIEW, defer. `did_not_run` names B2's generation and, new, B7's expansion. B7
  is capped at YIELDING, and the B8 retry is disclosed.
- Run 3 through the current code: B4's reply (no stop reason recorded) is detected as
  unclosed JSON and retried. With a scripted good reply, the retry is used. After that,
  later prompts differ from the record, so the replay can't go further. Those gaps
  show as checks that didn't run, as R-1 requires.
- Run 4 with its last matcher reply scripted as cut off: detected, not retried (the
  run's one retry is spent), disclosed, defer.
