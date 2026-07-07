"""The convergence primitive — the two-agent Generator×Challenger loop, once.

SPARK, Pattern Lock, and the Cut are all this loop with a different Challenger
function (expand / filter / close) and exit goal. Written once here; the phase
stages configure it.

Each round: the Generator produces/extends a payload; the Challenger applies its
function and raises artifact-cited concerns; both emit ``agree``. The phase
converges only when **both** agree (the proposer can't close alone). At the cap
it exits unresolved.

G3 (convergence integrity) is built in, not bolted on:
- *Instrumentation* — every round is recorded; ``no_op`` flags a phase whose
  final payload never moved off the round-1 proposal (a rubber-stamp).
- *A reopen-capable integrity check* — an optional cross-vendor predicate that,
  on a both-agree, can judge the convergence premature and reopen the phase
  (up to ``reopen_cap``), so convergence is verified, not merely self-reported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ConvergenceRound:
    index: int
    generator: dict[str, Any]
    challenger: dict[str, Any]
    both_agree: bool


@dataclass
class ConvergenceResult:
    converged: bool
    payload: Any
    rounds: list[ConvergenceRound] = field(default_factory=list)
    resolved_at: int | None = None
    no_op: bool = False
    integrity_reopened: int = 0


# generator(prev_payload, challenger_concerns) -> {"payload", "agree", "reasoning"}
Generator = Callable[[Any, Any], dict]
# challenger(payload) -> {"concerns", "agree", "reasoning"}
Challenger = Callable[[Any], dict]


def _default_delta(a: Any, b: Any) -> bool:
    return a != b  # True == "changed"


def run_convergence(
    generator: Generator,
    challenger: Challenger,
    cap: int,
    *,
    integrity: Callable[[Any, list[ConvergenceRound]], bool] | None = None,
    reopen_cap: int = 1,
    delta: Callable[[Any, Any], bool] | None = None,
    log: Callable[[str], None] = lambda _m: None,
) -> ConvergenceResult:
    delta = delta or _default_delta
    rounds: list[ConvergenceRound] = []
    reopened = 0

    gen = generator(None, None)
    first_payload = gen.get("payload")

    for i in range(cap):
        chal = challenger(gen.get("payload"))
        both = bool(gen.get("agree")) and bool(chal.get("agree"))
        rounds.append(ConvergenceRound(i + 1, gen, chal, both))

        if both:
            if integrity is not None and reopened < reopen_cap and not integrity(gen.get("payload"), rounds):
                reopened += 1
                log(f"convergence-integrity: reopened phase (premature) [{reopened}/{reopen_cap}]")
                gen = generator(gen.get("payload"), chal.get("concerns"))
                continue
            no_op = not delta(first_payload, gen.get("payload"))
            return ConvergenceResult(True, gen.get("payload"), rounds, i + 1, no_op, reopened)

        # Only revise if another challenge round follows. A final, un-challenged
        # revision must NOT become the committed payload: the phase exits on the
        # option the Challenger actually last evaluated, so the committed option
        # and its standing objections (rounds[-1].challenger) refer to the SAME
        # payload — no committed-vs-objection mismatch.
        if i < cap - 1:
            gen = generator(gen.get("payload"), chal.get("concerns"))

    no_op = not delta(first_payload, gen.get("payload"))
    return ConvergenceResult(False, gen.get("payload"), rounds, None, no_op, reopened)
