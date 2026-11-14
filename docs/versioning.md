# Versioning and maturity

Rapier keeps three things separate, because they answer different questions:

1. **The version number** says whether an upgrade can break you.
2. **The maturity stage** says how settled the software is.
3. **The evidence status** says how far the method itself has been shown to work.

The evidence never goes into the version number. A 1.0 release would mean the software
contract is stable, not that the method is proven.

## 1. Version number: Semantic Versioning, written as PEP 440

Versions follow [Semantic Versioning 2.0](https://semver.org/), written in
[PEP 440](https://peps.python.org/pep-0440/) form for PyPI.

**The public contract** is what a version number protects:

- the CLI: commands, flags, and exit codes;
- the manifest format: stage names, roles, and config keys;
- the MCP server: tool names and their parameters;
- the run record: the files under `~/.rapier/runs/<run>/` and the fields in `ceremony.json`.

Anything else, including prompt text, report wording, and internal modules, can change in any
release.

**Before 1.0:** a breaking change to the contract bumps the minor number (0.6 → 0.7). Everything
else bumps the patch (0.6.1 → 0.6.2). A deprecated name keeps working, with a warning, for at least
one minor release before it is removed. The CHANGELOG says so when it goes.

**From 1.0:** a breaking change bumps the major number.

## 2. Maturity stage

The stage is shown in two places that must agree: the PyPI `Development Status` classifier in
`pyproject.toml`, and the Status line in the README. A stage is earned by meeting its criteria, not
by declaring it.

| Stage | Classifier | Criteria |
|---|---|---|
| Pre-alpha | `2 - Pre-Alpha` | The method doesn't yet run end to end. |
| Alpha | `3 - Alpha` | The whole method runs end to end, is published, and is tested. The contract can still change in any minor release. |
| Beta | `4 - Beta` | The contract is frozen except for fixes. The deprecation policy holds. A security review is done. There are users beyond the maintainer. Every check reports whether it ran: a check that never ran is never reported as one that found nothing. The calling agent can't lower a check the operator requires. |
| Stable | `5 - Production/Stable` | Released as 1.0. The contract is under Semantic Versioning. |

## 3. Evidence status, per instrument

Each governance instrument is rated on its own:

- **Exploratory:** built and used, with no pre-registered test of whether it helps.
- **Tested:** a pre-registered study was run, and its result is published, whatever it found.
- **Validated:** a pre-registered study found the effect it predicted.

The instruments are the definitiveness (correctness) gate, the citation gate, anchored correction,
and the two-part report with its trust rider.

## Where Rapier is now (2026-11-14)

- **Version:** 0.6.1.
- **Stage:** Alpha. The full ceremony runs end to end, it is on PyPI, the tests pass, and
  cross-vendor runs are live-proven. The contract is still changing.
- **Evidence:** every instrument is exploratory. Two pre-registered studies of the resolver are
  published. In a blind rater test, raters didn't prefer the resolver's answers to a single AI pass.
  Iterating review toward agreement gave no cleaner answers than one review. Neither study
  validated the instruments above.
- **Open for Beta:** a stable contract, a security review, users beyond the maintainer, and the two
  check-reporting criteria, which are open work today
  (`docs/chatlogs/202611141130.inflight.accountable-agents-gaps.md`, items 1–3).
