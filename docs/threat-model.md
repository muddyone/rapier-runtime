# Rapier Runtime — Threat Model

**Status:** living document, opened in M0. Security is a front-and-center,
cross-cutting concern for this project, not a final-milestone polish — because
Rapier is itself an AI-*governance* tool, its own trustworthiness is the
product. This document enumerates the surfaces, the controls, and the current
implementation status per control.

> Scope note: Rapier is *decision-support*, not a decision-maker, and it is not
> a security scanner. This model covers the runtime's own attack/exposure
> surface, plus the responsible-use posture of its outputs.

## Assets to protect

1. **User secrets** — API keys for model vendors.
2. **User decision data** — the request/"pack" and all intermediate state; may
   be sensitive or proprietary.
3. **Integrity of the ceremony** — the guarantee that grounding, gates, and
   convergence reflect real checks, not spoofed or bypassed ones.
4. **The host machine** — files, network position, and process of whoever runs
   Rapier.

## Attacker / exposure model

- A **malicious or poisoned input** in the request or supplied materials
  (prompt injection) trying to steer the agents or the tool-use.
- A **compromised or hostile dependency** (supply chain).
- An **observer of logs/artifacts** (shoulder-surf, shared CI, committed run
  dirs) harvesting secrets or decision data.
- The **network** between Rapier and model vendors / grounding sources.
- Note: the model vendors themselves are trusted-but-disclosed — sending them
  data is the tool's function, made explicit to the user (see Data egress).

## Surfaces, risks, and controls

| # | Surface | Risk | Control | Status |
|---|---------|------|---------|--------|
| S1 | **Secrets** (API keys) | leak via logs, traces, run-dirs, ledger, exceptions | env-only reads (`secrets.get_secret`); never a file or hardcoded default; every persisted/logged string passes `redact()`; defensive key-pattern scrub even for unregistered values | **M0 done** (`secrets.py`, `ledger.py`); redaction unit-tested |
| S2 | **URL fetch** (grounding verifier's URL backend, M1) | SSRF — model output steers a fetch to internal IPs / cloud-metadata endpoints (169.254.169.254), file:// etc. | allowlist of schemes/hosts; deny RFC-1918 + link-local + loopback; no redirect-to-internal; timeouts; response size caps; off by default | **M1** (backend not yet present in M0) |
| S3 | **File resolution** (`path:line` artifacts, M1) | path traversal — read outside the intended workspace via `..` or absolute/symlink escape | resolution confined to a declared workspace root; reject `..` and absolute escapes; resolve symlinks and re-check containment | **M1** |
| S4 | **Prompt injection** (request/pack content, and model outputs that request a read) | untrusted content redirects the agents or the artifact-ref resolver to attacker-chosen files/URLs | model-requested reads are treated as untrusted and validated against policy (S2/S3) *before* acting; the artifact-ref resolver is the single choke point and is constrained; injection-resistant framing | **M2** (resolver hardening lands with the Proposer build) |
| S5 | **Data egress** | user decision content is sent to Anthropic + OpenAI | explicit and documented; a redaction option for the outbound pack; an offline/dry-run mode (the `mock` vendor is the M0 seed of this); no silent third parties | **partial** — `mock` vendor exists (M0); egress disclosure + redaction option (M3) |
| S6 | **Output persistence** | run-dirs / ledgers hold sensitive decision content | owner-only perms (0600 files / 0700 dirs); `runs/` git-ignored; opt-in (no ledger unless `--ledger-dir` given); documented retention | **M0 done** (`ledger.py`, `.gitignore`); default is *persist nothing* |
| S7 | **Config / deserialization** (manifest) | a malicious manifest executes code or overreaches | `yaml.safe_load` only (never `load`); strict structural validation; no code paths from manifest values | **M0 done** (`manifest.py`) |
| S8 | **Supply chain** | compromised/typosquatted dependency | minimal + pinned deps (only `pyyaml` at core; provider SDKs are opt-in extras); `pip-audit` / Dependabot in CI; SBOM; signed releases | **M4** (core kept dep-light from M0) |
| S9 | **Arbitrary code execution** | engine is coerced into running code from model output or config | explicit non-goal: no `eval`/`exec`, no shell-from-model-output anywhere in core; enforced in review | **M0 invariant** (none present) |

## Responsible-use posture (safety of outputs)

The method's own honesty mechanisms **are** safety features and are treated as
such:

- **Two-part output + trust rider** and the **definitiveness gate** (no unmarked
  hard specifics) keep users from over-trusting a recommendation. *(Land with
  the Resolver port, M1.)*
- **Decision-support, not decision-maker** — documented framing; not a
  substitute for human judgment on high-consequence decisions.
- **Do not route around model refusals** — if an underlying model refuses (e.g.
  bio/cyber), Rapier surfaces that honestly and never re-prompts to evade it.
- **Cross-vendor by construction** reduces (not eliminates) same-vendor
  collusion/theater; the M2 convergence-integrity check hardens it further.

## Out of scope (for now, stated honestly)

- Rapier does not sandbox the underlying model providers or audit their
  handling of the data it sends them.
- It does not defend against a fully compromised host or a malicious operator
  who already controls the environment and the keys.
- Model-level jailbreak resistance is the provider's responsibility; Rapier's
  job is to not *assist* evasion (see responsible-use).

## Change log

- **2026-07-06 (M0)** — document opened; S1/S6/S7/S9 controls implemented and
  tested; S2–S5/S8 scheduled to their milestones.
