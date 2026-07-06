"""SPARK / Pattern Lock / the Cut as convergence stages.

Each runs the convergence primitive with its phase config, putting the Generator
and Challenger on **distinct vendors** (G1, via the V4 vendor layer), recording
every round (G3 instrumentation + G4 persistence), and optionally running the
cross-vendor convergence-integrity reopen check (G3, `integrity_check: true`).
"""
from __future__ import annotations

from ...convergence import run_convergence
from ...envelope import Envelope
from ...models import ModelSpec, Policy, available_vendors, build_client, default_model
from ...stage import ConvergenceStage, StageContext, register_stage
from .phases import PHASES, integrity_check, make_agents

_PROPOSER_MAX_TOKENS = 8000  # thinking-model headroom (same reason as the gate floor)


class _PhaseStage(ConvergenceStage):
    PHASE = ""

    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        cfg = PHASES[self.PHASE]
        gen_v, chal_v = (ctx.policy or Policy()).resolve(
            available_vendors(),
            primary_pref=ctx.config.get("generator_vendor") or env.meta.get("author_vendor"),
            secondary_pref=ctx.config.get("challenger_vendor"),
        )
        if gen_v is None:
            env.add_trace(self.PHASE, self.kind, "no vendor available — skipped")
            return env

        gen_model = ctx.config.get("generator_model") or default_model(gen_v)
        gen_client = build_client(ModelSpec(gen_v, gen_model, max_tokens=_PROPOSER_MAX_TOKENS))
        chal_vendor = chal_v or gen_v  # single-vendor: challenger shares the vendor (degraded)
        chal_model = ctx.config.get("challenger_model") or default_model(chal_vendor)
        chal_client = build_client(ModelSpec(chal_vendor, chal_model, max_tokens=_PROPOSER_MAX_TOKENS))
        cross_vendor = bool(chal_v) and chal_v != gen_v

        phase_input = cfg.read_input(env)
        generator, challenger = make_agents(gen_client, chal_client, cfg, env.request, phase_input)
        cap = int(ctx.config.get("cap", cfg.default_cap))
        integ = integrity_check(chal_client, cfg, env.request) if ctx.config.get("integrity_check") else None

        result = run_convergence(generator, challenger, cap, integrity=integ, delta=cfg.delta, log=ctx.log)
        cfg.write_output(env, result.payload)

        env.meta.setdefault("proposer", {})[self.PHASE] = {
            "generator_vendor": gen_v,
            "challenger_vendor": chal_vendor,
            "cross_vendor": cross_vendor,
            "converged": result.converged,
            "rounds": len(result.rounds),
            "resolved_at": result.resolved_at,
            "no_op": result.no_op,
            "integrity_reopened": result.integrity_reopened,
        }
        # Disagreement-at-cap: when a phase exits unresolved, surface the
        # Challenger's standing (artifact-cited) objections so the held
        # disagreement is legible, not silently dropped.
        if not result.converged and result.rounds:
            env.meta["proposer"][self.PHASE]["standing_objections"] = (
                result.rounds[-1].challenger.get("concerns") or []
            )
        env.meta.setdefault("proposer_rounds", {})[self.PHASE] = [
            {
                "round": r.index,
                "both_agree": r.both_agree,
                "gen_agree": r.generator.get("agree"),
                "chal_agree": r.challenger.get("agree"),
                "n_concerns": len(r.challenger.get("concerns") or []),
                "n_theatrical_dropped": r.challenger.get("theatrical_dropped", 0),
            }
            for r in result.rounds
        ]
        env.add_trace(
            self.PHASE,
            self.kind,
            f"gen={gen_v} chal={chal_vendor} cross_vendor={cross_vendor} "
            f"converged={result.converged} rounds={len(result.rounds)} no_op={result.no_op}"
            + (f" reopened={result.integrity_reopened}" if result.integrity_reopened else ""),
            converged=result.converged,
            no_op=result.no_op,
            cross_vendor=cross_vendor,
        )
        return env


@register_stage("spark")
class SparkStage(_PhaseStage):
    PHASE = "spark"


@register_stage("pattern_lock")
class PatternLockStage(_PhaseStage):
    PHASE = "pattern_lock"


@register_stage("cut")
class CutStage(_PhaseStage):
    PHASE = "cut"
