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


def _seeded_generator(generator, seeds: list[str]):
    """Wrap the SPARK generator so round 1 starts from a seeded candidate set.

    A Frame anchor (a hybrid's leaning, or a demoted G2-fail proposition's
    assertion) enters SPARK's field as its "prior round" options instead of an
    empty field. The divergent SPARK generator then expands *around* the seed —
    it is **not** privileged; it survives only if it wins Pattern Lock + the Cut
    on the merits downstream (those phases already read ``env.options``, so the
    seed's fate carries through with no further plumbing).

    Only round 1 is seeded: ``run_convergence`` calls ``generator(None, None)``
    for the opening round and passes the real prior payload on every later round,
    so gating on ``prev_payload is None`` targets the opening round exactly.
    """

    def wrapped(prev_payload, concerns):
        if prev_payload is None:
            prev_payload = list(seeds)  # seed the field for the opening round
        return generator(prev_payload, concerns)

    return wrapped


class _PhaseStage(ConvergenceStage):
    PHASE = ""

    def _resolve_seed(self, env: Envelope, ctx: StageContext) -> tuple[list[str], str | None]:
        """Resolve the seed candidate(s) for this phase, or ``([], None)``.

        Only the **expand** phase (SPARK) seeds the field; Pattern Lock (filter)
        and the Cut (close) operate on the already-seeded option set, so
        re-injecting there would wrongly re-add a dropped option. An explicit
        ``config["seed"]`` (routed from ``--seed`` / the skill's Frame dispatch)
        wins over the in-envelope Frame anchor, so a deliberate seed always takes
        precedence over an inferred one.
        """
        if PHASES[self.PHASE].function != "expand":
            return [], None
        raw = ctx.config.get("seed")
        source = "config"
        if not raw:
            raw = (env.meta.get("frame") or {}).get("anchor")
            source = "frame"
        if not raw:
            return [], None
        seeds = [raw] if isinstance(raw, str) else list(raw)
        seeds = [str(s).strip() for s in seeds if str(s).strip()]
        return (seeds, source) if seeds else ([], None)

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
        seed, seed_source = self._resolve_seed(env, ctx)
        if seed:
            generator = _seeded_generator(generator, seed)
            env.meta.setdefault("proposer", {})["seed"] = {"seeds": seed, "source": seed_source}
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
            + (f" reopened={result.integrity_reopened}" if result.integrity_reopened else "")
            + (f" seeded={len(seed)}" if seed else ""),
            converged=result.converged,
            no_op=result.no_op,
            cross_vendor=cross_vendor,
            seeded=len(seed),
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
