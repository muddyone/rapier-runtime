"""The ``rapier`` CLI — run a manifest over a request.

    rapier run --manifest manifests/echo.yaml --request "should we do X?"

Minimal by design in M0: it exists to drive the echo pipeline end-to-end from a
shell. Richer subcommands land in later milestones.
"""
from __future__ import annotations

import argparse
import sys

from . import stages  # noqa: F401  (ensure built-in stages are registered)
from .manifest import Manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rapier",
        description="Rapier Runtime — run a SPARRING method from a manifest.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a manifest over a request")
    run.add_argument("--manifest", required=True, help="path to a pipeline manifest (YAML)")
    run.add_argument("--request", required=True, help="the decision/request text")
    run.add_argument(
        "--ledger-dir",
        default=None,
        help="write run artifacts (trace, envelope) here; omit to persist nothing",
    )

    args = parser.parse_args(argv)

    if args.cmd == "run":
        manifest = Manifest.load(args.manifest)
        env = manifest.build().run(
            args.request,
            ledger_root=args.ledger_dir,
            log=lambda msg: print(f"· {msg}", file=sys.stderr),
        )
        print(env.recommendation or "")
        return 0
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
