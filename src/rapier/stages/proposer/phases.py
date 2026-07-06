"""Phase configs for the Proposer — SPARK, Pattern Lock, the Cut.

Each is the same convergence loop with a different Challenger function and exit
goal, plus how it reads/writes the Envelope. The Challenger's verifiable-artifact
rule (a concern must cite something checkable) is stated in every phase prompt
(G2, prompt-level; mechanical enforcement is a follow-on).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..._json import parse_json_lenient

_ARTIFACT_RULE = (
    "Every concern MUST cite a checkable artifact (a specific source, a documented "
    "pattern, or a concrete prior case) in the 'artifact' field — concerns without "
    "one are theatrical and disallowed."
)


@dataclass
class PhaseConfig:
    name: str
    function: str  # expand | filter | close
    default_cap: int
    gen_system: str
    chal_system: str
    gen_user: Callable[[str, Any, Any, Any], str]  # (request, phase_input, prev_payload, concerns)
    chal_user: Callable[[str, Any], str]           # (request, payload)
    read_input: Callable[[Any], Any]               # (env) -> phase input
    write_output: Callable[[Any, Any], None]       # (env, payload) -> None
    delta: Callable[[Any, Any], bool]              # payload change detector (no-op flag)


def _payload(d: dict) -> Any:
    if not isinstance(d, dict):
        return d
    if "payload" in d:
        return d["payload"]
    return {k: v for k, v in d.items() if k not in ("agree", "reasoning")}


def make_agents(gen_client, chal_client, cfg: PhaseConfig, request: str, phase_input: Any):
    def generator(prev_payload, concerns):
        raw = gen_client.complete(cfg.gen_system, cfg.gen_user(request, phase_input, prev_payload, concerns)).text
        d = parse_json_lenient(raw)
        return {"payload": _payload(d), "agree": bool(d.get("agree")) if isinstance(d, dict) else False,
                "reasoning": str(d.get("reasoning", "")) if isinstance(d, dict) else ""}

    def challenger(payload):
        raw = chal_client.complete(cfg.chal_system, cfg.chal_user(request, payload)).text
        d = parse_json_lenient(raw)
        return {"concerns": d.get("concerns", []) if isinstance(d, dict) else [],
                "agree": bool(d.get("agree")) if isinstance(d, dict) else False,
                "reasoning": str(d.get("reasoning", "")) if isinstance(d, dict) else ""}

    return generator, challenger


def integrity_check(chal_client, cfg: PhaseConfig, request: str):
    """A cross-vendor prematurity audit for G3: returns True iff genuine."""

    def check(payload, _rounds):
        raw = chal_client.complete(
            "You audit a deliberation for premature convergence — did the agents agree too fast or miss coverage?",
            f"DECISION:\n{request}\n\nThe agents just agreed to converge the {cfg.name} phase on:\n{payload}\n\n"
            'Respond STRICT JSON: {"genuine": bool, "reason": str}. genuine=false means premature.',
        ).text
        d = parse_json_lenient(raw)
        return bool(d.get("genuine", True))  # fail-open on parse failure

    return check


# --- option-set helpers ------------------------------------------------------
def _as_options(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        payload = payload.get("options", [])
    return [str(x) for x in (payload or [])]


def _options_delta(a: Any, b: Any) -> bool:
    return set(_as_options(a)) != set(_as_options(b))


def _cut_delta(a: Any, b: Any) -> bool:
    return (a or {}).get("committed") != (b or {}).get("committed") if isinstance(a, dict) and isinstance(b, dict) else a != b


# --- the three phases --------------------------------------------------------
PHASES: dict[str, PhaseConfig] = {
    "spark": PhaseConfig(
        name="SPARK",
        function="expand",
        default_cap=5,
        gen_system=(
            "You are the Generator in the SPARK phase of a SPARRING proposer ceremony. Propose a broad, "
            "DIVERGENT set of genuinely distinct options / framings / approaches for the decision; resist "
            'narrowing. Respond STRICT JSON: {"payload": ["option 1", "option 2", ...], "agree": bool, '
            '"reasoning": str}. Set agree=true only when the option space is saturated — no meaningful '
            "distinct option remains to add."
        ),
        chal_system=(
            "You are the Challenger in the SPARK phase; your function is EXPAND: push the Generator toward "
            "options, framings, and regions of the space it missed, and resist premature narrowing. "
            + _ARTIFACT_RULE
            + ' Respond STRICT JSON: {"concerns": [{"text": str, "artifact": str}], "agree": bool, '
            '"reasoning": str}. Set agree=true only when the option space is saturated.'
        ),
        gen_user=lambda request, _in, prev, concerns: (
            f"DECISION:\n{request}\n\nCURRENT OPTIONS (prior round; empty on round 1):\n{_as_options(prev)}\n\n"
            f"CHALLENGER EXPAND-PRESSURE (prior round):\n{concerns}\n\nProduce the fullest distinct option set."
        ),
        chal_user=lambda request, payload: (
            f"DECISION:\n{request}\n\nGENERATOR'S OPTION SET:\n{_as_options(payload)}\n\n"
            "Name missing options / framings / uncovered regions, each with a checkable artifact."
        ),
        read_input=lambda env: None,
        write_output=lambda env, payload: setattr(env, "options", _as_options(payload)),
        delta=_options_delta,
    ),
    "pattern_lock": PhaseConfig(
        name="Pattern Lock",
        function="filter",
        default_cap=2,
        gen_system=(
            "You are the Generator in the Pattern Lock phase. Cluster the given options: merge true duplicates, "
            "keep genuinely-distinct ones. Respond STRICT JSON: "
            '{"payload": ["deduped option 1", ...], "agree": bool, "reasoning": str}. '
            "agree=true when the de-duplicated set is correct."
        ),
        chal_system=(
            "You are the Challenger in the Pattern Lock phase; your function is FILTER: catch options wrongly "
            "merged (genuinely distinct collapsed together) and false-novelty options (a rephrase kept as "
            "distinct). " + _ARTIFACT_RULE
            + ' Respond STRICT JSON: {"concerns": [{"text": str, "artifact": str}], "agree": bool, "reasoning": str}.'
        ),
        gen_user=lambda request, phase_in, prev, concerns: (
            f"DECISION:\n{request}\n\nOPTIONS TO DE-DUPLICATE:\n{_as_options(phase_in)}\n\n"
            f"YOUR PRIOR DE-DUPED SET:\n{_as_options(prev)}\n\nCHALLENGER FILTER-PRESSURE:\n{concerns}\n\n"
            "Produce the correct de-duplicated option set."
        ),
        chal_user=lambda request, payload: (
            f"DECISION:\n{request}\n\nPROPOSED DE-DUPLICATED SET:\n{_as_options(payload)}\n\n"
            "Flag wrongly-merged or false-novelty options, each with a checkable artifact."
        ),
        read_input=lambda env: env.options,
        write_output=lambda env, payload: setattr(env, "options", _as_options(payload)),
        delta=_options_delta,
    ),
    "cut": PhaseConfig(
        name="the Cut",
        function="close",
        default_cap=2,
        gen_system=(
            "You are the Generator in the Cut phase. Commit to exactly ONE option from the set, with a rationale. "
            'Respond STRICT JSON: {"payload": {"committed": "the option", "rationale": str}, "agree": bool, '
            '"reasoning": str}. agree=true when this single committed option is the right one.'
        ),
        chal_system=(
            "You are the Challenger in the Cut phase; your function is CLOSE: pressure-test the choice, and "
            "counter-propose a different option from the set if your evidence base warrants it. " + _ARTIFACT_RULE
            + ' Respond STRICT JSON: {"concerns": [{"text": str, "artifact": str}], "agree": bool, "reasoning": str}.'
        ),
        gen_user=lambda request, phase_in, prev, concerns: (
            f"DECISION:\n{request}\n\nOPTION SET TO CUT FROM:\n{_as_options(phase_in)}\n\n"
            f"YOUR PRIOR COMMITMENT:\n{prev}\n\nCHALLENGER CLOSE-PRESSURE:\n{concerns}\n\n"
            "Commit to exactly one option with rationale."
        ),
        chal_user=lambda request, payload: (
            f"DECISION:\n{request}\n\nGENERATOR COMMITTED TO:\n{payload}\n\n"
            "Pressure-test this commitment or counter-propose, each with a checkable artifact."
        ),
        read_input=lambda env: env.options,
        write_output=lambda env, payload: setattr(
            env, "committed", (payload or {}).get("committed") if isinstance(payload, dict) else str(payload)
        ),
        delta=_cut_delta,
    ),
}
