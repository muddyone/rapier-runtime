"""Frame stage — the front-door classifier (a first-class engine capability).

Frame is the input-typing gate that runs BEFORE Propose/Resolve. It answers one
question — *what kind of input is this?* — and, when the input is a proposition,
runs the Presentation (the Earnedness Rubric) to decide whether the proposition
is ready for the Resolver or must go back to Propose.

Design split, per the runtime's ethos (control flow in code; only genuine
judgment delegated to a model): the model judges the *input type* and the three
Earnedness gates; this stage *derives* the presentation verdict, the failed
gate, and the route deterministically from those judgments (``_derive``). So the
routing is auditable and never a model's free-form choice — and the dangerous
misclassification (a question evaluated as a committed decision) is structurally
impossible: only an EARNED proposition ever routes to ``resolve``.

Frame classifies and records to ``env.meta["frame"]``; it does not itself branch
the pipeline (the pipeline is linear). The caller reads
``env.meta["frame"]["route"]`` and dispatches to the matching preset
(``proposer`` / ``spar`` / ``sparring``).
"""
from __future__ import annotations

from typing import Any

from .._json import parse_json_lenient
from ..envelope import Envelope
from ..stage import StageContext, TransformStage, register_stage

INPUT_TYPES = ("question", "proposition", "hybrid")

_SYSTEM = """You are the Frame classifier at the front door of a SPARRING \
ceremony. Classify the user's input and, if it is a proposition, run the \
Presentation (the Earnedness Rubric). Judge only — do NOT answer the question \
or evaluate the decision.

INPUT TYPE (pick exactly one):
- "question": interrogative / open — no option is committed to yet.
- "proposition": a single decision asserted as chosen, with reasoning.
- "hybrid": a leaning — one candidate named but explicitly still open \
("I'm inclined toward X, but is that right?").

If (and only if) input_type is "proposition", judge the three Earnedness gates \
(each a strict boolean):
- G1 singular_commitment: exactly ONE option is asserted as chosen (not a menu, \
not "A or B?").
- G2 load_bearing_reason: at least one stated-or-obvious rationale whose REMOVAL \
would flip the commitment. Test it counterfactually — a decorative reason that, \
removed, leaves the commitment standing does NOT count.
- G3 decidable_specificity: the claim is concrete enough to be graded \
true/false or good/bad.
Also judge the soft signal (does not gate; informational):
- S1 alternative_awareness: evidence that other options were seen and set aside.

Set "anchor": for a "hybrid", the candidate the user leans toward; for a \
"proposition", the asserted decision; for a "question", null.

Respond STRICT JSON:
{"input_type": "question|proposition|hybrid",
 "gates": {"G1": bool, "G2": bool, "G3": bool, "S1": bool},
 "anchor": "<text or null>",
 "basis": "<one sentence: why this classification>",
 "confidence": <0.0-1.0>}
For a question, "gates" may be omitted or all false — they are ignored."""


def _derive(
    input_type: str,
    gates: Any,
    anchor: Any,
    *,
    basis: str = "",
    confidence: float | None = None,
) -> dict[str, Any]:
    """Map (input_type, gate judgments) → the routing verdict, deterministically.

    Routing lives here, in code — never in the model's free-form output — so it
    is auditable. Only an EARNED proposition routes to ``resolve``; everything
    else routes to ``propose``, so a question can never be silently evaluated as
    a committed decision.
    """

    def _b(k: str) -> bool:  # tolerant bool read of a possibly-missing gate
        return bool(gates.get(k)) if isinstance(gates, dict) else False

    if input_type == "question":
        presentation, earned_gate_failed, route, anchor = "n/a", "none", "propose", None
    elif input_type == "hybrid":
        # A leaning: the candidate is seeded into Propose's field, not evaluated.
        presentation, earned_gate_failed, route = "n/a", "none", "propose"
    elif input_type == "proposition":
        # The Presentation: G1→G2→G3, first failure names the tripped gate.
        failed = "none"
        for g in ("G1", "G2", "G3"):
            if not _b(g):
                failed = g
                break
        earned = failed == "none"
        presentation = "pass" if earned else "fail"
        earned_gate_failed = failed
        if earned:
            route, anchor = "resolve", None  # control mark — cleared for the piste
        else:
            route = "propose"  # demoted — back to the armory
            # Keep the assertion as a seed only when it is a leaning (G2 fail);
            # a menu (G1) is a choice-question and a vague claim (G3) needs
            # sharpening — neither seeds a single candidate.
            if failed != "G2":
                anchor = None
    else:  # pragma: no cover — guarded by the caller
        presentation, earned_gate_failed, route, anchor = "n/a", "none", "propose", None

    if isinstance(anchor, str) and not anchor.strip():
        anchor = None

    return {
        "input_type": input_type,
        "presentation": presentation,
        "earned_gate_failed": earned_gate_failed,
        "route": route,
        "anchor": anchor,
        "alternative_awareness": _b("S1"),
        "basis": basis,
        "confidence": confidence,
    }


def _fail_safe(reason: str) -> dict[str, Any]:
    """No usable judgment → treat as a question routed to Propose.

    The dangerous error is evaluating a question as a committed decision, so the
    safe default never routes to ``resolve``.
    """
    frame = _derive("question", {}, None, basis=f"{reason} — conservative default", confidence=0.0)
    frame["classification_error"] = reason
    return frame


@register_stage("frame")
class FrameStage(TransformStage):
    def run(self, env: Envelope, ctx: StageContext) -> Envelope:
        client = ctx.clients.get("framer")
        if client is None:
            frame = _fail_safe("no_framer_client")
            env.meta["frame"] = frame
            env.add_trace("frame", self.kind, "no framer client — defaulted to question/propose (fail-safe)", **frame)
            return env

        system = ctx.config.get("system", _SYSTEM)
        raw = client.complete(system=system, prompt=env.request).text
        d = parse_json_lenient(raw)
        input_type = d.get("input_type") if isinstance(d, dict) else None
        if input_type not in INPUT_TYPES:
            frame = _fail_safe("unparseable")
            env.meta["frame"] = frame
            env.add_trace("frame", self.kind, "unparseable classification — defaulted to question/propose (fail-safe)", **frame)
            return env

        gates = d.get("gates") if isinstance(d.get("gates"), dict) else {}
        anchor = d.get("anchor")
        basis = str(d.get("basis", ""))
        try:
            confidence = float(d.get("confidence"))
        except (TypeError, ValueError):
            confidence = None

        frame = _derive(input_type, gates, anchor, basis=basis, confidence=confidence)
        env.meta["frame"] = frame
        detail = ""
        if frame["presentation"] != "n/a":
            detail = f" (presentation={frame['presentation']}, failed={frame['earned_gate_failed']})"
        env.add_trace("frame", self.kind, f"{frame['input_type']} → {frame['route']}{detail}", **frame)
        return env
