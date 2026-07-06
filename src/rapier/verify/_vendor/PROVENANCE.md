# Vendored SPARRING Resolver stack

These files are vendored verbatim from the SPARRING framework / loom `spar` and
`cite-check` skills (Bart Niedner, Apache-2.0). Rapier Runtime is now the
**canonical home** for this code — the M1 port collapses the former
pilot-vs-loom two-copies split into one. Do not hand-edit here to fix behavior;
change it as Rapier code and let the loom skills become adapters (M3/M4).

Source (2026-07-06):
- lib_llm.py, verify_grounding.py, spar_cross_review.py,
  spar_definitiveness_gate.py, spar_verify_gate.py
    <- loom/skills/shared/spar/scripts/  (spar-* renamed hyphen->underscore)
- cite_check.py
    <- loom/skills/shared/cite-check/scripts/cite_check.py
