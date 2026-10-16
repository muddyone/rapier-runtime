# Changelog

Notable changes to `rapier-runtime`. Earlier releases are summarized in
[`docs/STATUS.md`](docs/STATUS.md) and on the
[Releases](https://github.com/muddyone/rapier-runtime/releases) page.

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
