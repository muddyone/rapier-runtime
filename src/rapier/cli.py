"""The ``rapier`` CLI.

    rapier sparring --request "should we do X?"      # full ceremony
    rapier spar     --request "should we do X?"       # Resolver only
    rapier proposer --request "should we do X?"       # Proposer only
    rapier run --manifest path.yaml --request "..."   # a custom manifest

``spar`` / ``sparring`` are the thin adapters the SPARRING skills call.
"""
from __future__ import annotations

import argparse
import sys

from . import stages  # noqa: F401  (ensure built-in stages are registered)
from .manifest import Manifest
from .presets import VERIFY_MODES, load_preset


def _run(manifest: Manifest, request: str, ledger_dir: str | None, show_proposer: bool = False) -> int:
    env = manifest.build().run(
        request, ledger_root=ledger_dir, log=lambda msg: print(f"· {msg}", file=sys.stderr)
    )
    # Prefer the composed report if the pipeline produced one.
    out = env.meta.get("report_md") or env.recommendation or ""
    # --show-proposer: prepend the first half's handoff report (SPARRING is two
    # parts — the Proposer commits an option, the Resolver pressure-tests it).
    proposer_md = env.meta.get("proposer_report_md")
    if show_proposer and proposer_md:
        out = f"{proposer_md}\n\n---\n\n{out}"
    print(out)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rapier", description="Rapier Runtime — run a SPARRING method from a manifest or preset."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--request", required=True, help="the decision/request text")
        p.add_argument("--ledger-dir", default=None, help="write run artifacts (transcript, report, records) here")

    for preset in ("spar", "sparring", "proposer"):
        p = sub.add_parser(preset, help=f"run the '{preset}' ceremony preset")
        add_common(p)
        if preset in ("spar", "sparring"):  # resolver knobs — no-ops for proposer-only
            p.add_argument(
                "--settle", type=int, default=0, metavar="N",
                help="extra review-and-revise rounds on the recommendation for decision-stability (default 0)",
            )
            p.add_argument(
                "--verify", choices=list(VERIFY_MODES), default="gate",
                help="external-canon citation gate: off | gate (default) | round",
            )
        if preset == "sparring":  # the Proposer half only exists in the full ceremony
            p.add_argument(
                "--show-proposer", action="store_true",
                help="also surface the Proposer report (the committed option + its standing objections) above the Resolver report",
            )

    run = sub.add_parser("run", help="run a custom manifest")
    run.add_argument("--manifest", required=True, help="path to a pipeline manifest (YAML)")
    add_common(run)

    args = parser.parse_args(argv)

    if args.cmd in ("spar", "sparring", "proposer"):
        preset = load_preset(
            args.cmd, settle=getattr(args, "settle", 0), verify=getattr(args, "verify", "gate")
        )
        return _run(preset, args.request, args.ledger_dir, show_proposer=getattr(args, "show_proposer", False))
    if args.cmd == "run":
        return _run(Manifest.load(args.manifest), args.request, args.ledger_dir)
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
