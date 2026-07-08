"""The ``rapier`` CLI.

    rapier sparring --request "should we do X?"          # full ceremony
    rapier spar     --request "should we do X?"           # Resolver only
    rapier spar     --request-file pack.md                # read the pack from a file
    rapier proposer --request "should we do X?"           # Proposer only
    rapier run --manifest path.yaml --request-file pack.md  # a custom manifest
    rapier doctor                                     # which vendor keys are set
    rapier init                                       # scaffold a .env.example
    rapier mcp                                        # run the MCP server (stdio)

``spar`` / ``sparring`` are the thin adapters the SPARRING skills call.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

from . import stages  # noqa: F401  (ensure built-in stages are registered)
from .manifest import Manifest
from .presets import VERIFY_MODES, load_preset


def _run(manifest: Manifest, request: str, ledger_dir: str | None, report_all: bool = False) -> int:
    # Always capture a run: without an explicit --ledger-dir, write the full
    # transcript + per-stage records under a temp dir and tell the user where.
    default_root = ledger_dir is None
    root = ledger_dir or os.path.join(tempfile.gettempdir(), "rapier-runs")
    env = manifest.build().run(
        request, ledger_root=root, log=lambda msg: print(f"· {msg}", file=sys.stderr)
    )
    # Prefer the composed report if the pipeline produced one.
    out = env.meta.get("report_md") or env.recommendation or ""
    # --report-all: prepend the first half's handoff report (SPARRING is two
    # parts — the Proposer commits an option, the Resolver pressure-tests it).
    proposer_md = env.meta.get("proposer_report_md")
    if report_all and proposer_md:
        out = f"{proposer_md}\n\n{out}"
    print(out)
    run_id = env.meta.get("run_id")
    if run_id:
        run_dir = os.path.join(root, run_id)
        hint = "Full transcript, per-stage records, and this report saved to:"
        if default_root:
            hint = "No --ledger-dir given, so the full transcript + records were saved to:"
        print(f"\n· {hint}\n  {run_dir}", file=sys.stderr)
    return 0


def _resolve_request(args) -> str:
    """The decision text — inline via --request, or read from --request-file
    (``-`` for stdin). The mutually-exclusive group guarantees exactly one."""
    path = getattr(args, "request_file", None)
    if path:
        if path == "-":
            return sys.stdin.read()
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return args.request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rapier", description="Rapier Runtime — run a SPARRING method from a manifest or preset."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        src = p.add_mutually_exclusive_group(required=True)
        src.add_argument("--request", help="the decision/request text, given inline")
        src.add_argument(
            "--request-file", metavar="PATH",
            help="read the decision/request text from a file (use '-' for stdin) — "
                 "friendlier than --request for a multi-line context pack",
        )
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
                "--report-all", action="store_true",
                help="also surface the Proposer report (the committed option + its standing objections) above the Resolver report",
            )

    run = sub.add_parser("run", help="run a custom manifest")
    run.add_argument("--manifest", required=True, help="path to a pipeline manifest (YAML)")
    add_common(run)

    sub.add_parser("doctor", help="check which AI vendor keys are configured")
    ip = sub.add_parser("init", help="scaffold a .env.example for vendor keys")
    ip.add_argument("--dir", default=".", help="directory to write .env.example into (default: cwd)")
    sub.add_parser("mcp", help="run the MCP server (stdio) exposing spar/sparring as tools")

    args = parser.parse_args(argv)

    if args.cmd == "doctor":
        from .onboarding import doctor_report

        print(doctor_report())
        return 0
    if args.cmd == "init":
        from .onboarding import init as _init

        _path, _created, instructions = _init(args.dir)
        print(instructions)
        return 0
    if args.cmd == "mcp":
        from .mcp import serve

        return serve()

    try:
        request = _resolve_request(args)
    except OSError as e:
        print(f"rapier: cannot read --request-file: {e}", file=sys.stderr)
        return 2
    if not (request or "").strip():
        print("rapier: the request is empty", file=sys.stderr)
        return 2

    if args.cmd in ("spar", "sparring", "proposer"):
        from .onboarding import preflight_error

        err = preflight_error()
        if err:  # no vendor keys — fail loudly, not silently
            print(err, file=sys.stderr)
            return 2
        preset = load_preset(
            args.cmd, settle=getattr(args, "settle", 0), verify=getattr(args, "verify", "gate")
        )
        return _run(preset, request, args.ledger_dir, report_all=getattr(args, "report_all", False))
    if args.cmd == "run":
        return _run(Manifest.load(args.manifest), request, args.ledger_dir)
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
