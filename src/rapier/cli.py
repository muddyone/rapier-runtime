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
from .presets import load_preset


def _run(manifest: Manifest, request: str, ledger_dir: str | None) -> int:
    env = manifest.build().run(
        request, ledger_root=ledger_dir, log=lambda msg: print(f"· {msg}", file=sys.stderr)
    )
    # Prefer the composed report if the pipeline produced one.
    print(env.meta.get("report_md") or env.recommendation or "")
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
        add_common(sub.add_parser(preset, help=f"run the '{preset}' ceremony preset"))

    run = sub.add_parser("run", help="run a custom manifest")
    run.add_argument("--manifest", required=True, help="path to a pipeline manifest (YAML)")
    add_common(run)

    args = parser.parse_args(argv)

    if args.cmd in ("spar", "sparring", "proposer"):
        return _run(load_preset(args.cmd), args.request, args.ledger_dir)
    if args.cmd == "run":
        return _run(Manifest.load(args.manifest), args.request, args.ledger_dir)
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
