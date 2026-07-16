"""The ``rapier`` CLI.

    rapier sparring --request "should we do X?"          # full ceremony
    rapier spar     --request "should we do X?"           # Resolver only
    rapier spar     --request-file pack.md                # read the pack from a file
    rapier proposer --request "should we do X?"           # Proposer only
    rapier frame    --request "should we do X?"           # classify + recommend a route
    rapier run --manifest path.yaml --request-file pack.md  # a custom manifest
    rapier doctor                                     # which vendor keys are set
    rapier init                                       # scaffold a .env.example
    rapier mcp                                        # run the MCP server (stdio)

``spar`` / ``sparring`` are the thin adapters the SPARRING skills call.
"""
from __future__ import annotations

import argparse
import itertools
import os
import re
import sys
import tempfile
import threading
import time

from . import __version__, stages  # noqa: F401  (ensure built-in stages are registered)
from .manifest import Manifest
from .presets import VERIFY_MODES, load_preset

# A ceremony is a sequence of model calls that each take tens of seconds — long
# enough that a novice fears it hung. Give each stage a plain-language label and,
# on a TTY, a live spinner + elapsed clock + "N/M" so it plainly reads as working.
_STAGE_LABELS = {
    "author": "Drafting the recommendation",
    "cross_review": "Independent cross-vendor challenge",
    "anchored_fix": "Revising to address the challenge",
    "definitiveness_gate": "Correctness check",
    "citation_gate": "Grounding citations against public registries",
    "compose": "Composing the report",
    "spark": "Widening the options (SPARK)",
    "pattern_lock": "Filtering repetition (Pattern Lock)",
    "cut": "Committing one option (the Cut)",
    "frame": "Framing the input (classify + Presentation)",
    "echo": "Echo",
}


class _Progress:
    """Render stage progress. On a TTY: an in-place spinner + elapsed seconds +
    N/M counter, animated on a background thread while the (blocking) stage runs.
    Piped/redirected: plain one-line-per-stage output, no control characters, so
    logs stay clean."""

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, total: int, stream=None):
        self.total = total
        self.stream = stream or sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.i = 0
        self._label = ""
        self._start = 0.0
        self._stop = None
        self._thread = None

    def log(self, msg: str) -> None:
        m = re.match(r"stage: (\w+)", msg)
        if m:
            self._finish()
            self.i += 1
            self._label = _STAGE_LABELS.get(m.group(1), m.group(1).replace("_", " "))
            self._begin()
        elif any(w in msg.lower() for w in ("fail", "cancel", "error")):
            if self.tty:
                self.stream.write("\r" + " " * 72 + "\r")
            print(f"· {msg.strip()}", file=self.stream, flush=True)
        elif not self.tty:
            print(f"·   {msg.strip()}", file=self.stream, flush=True)

    def _begin(self) -> None:
        if not self.tty:
            print(f"· [{self.i}/{self.total}] {self._label}…", file=self.stream, flush=True)
            return
        self._start = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        frames = itertools.cycle(self._FRAMES)
        while not self._stop.is_set():
            el = int(time.monotonic() - self._start)
            self.stream.write(f"\r  {next(frames)} [{self.i}/{self.total}] {self._label}… {el}s   ")
            self.stream.flush()
            self._stop.wait(0.1)

    def _finish(self) -> None:
        if not self._thread:
            return
        self._stop.set()
        self._thread.join(timeout=1.0)
        el = int(time.monotonic() - self._start)
        self.stream.write(f"\r  ✓ [{self.i}/{self.total}] {self._label}  ({el}s){' ' * 12}\n")
        self.stream.flush()
        self._thread = None

    def done(self) -> None:
        self._finish()


def _run(manifest: Manifest, request: str, ledger_dir: str | None, report_all: bool = False,
         seed_meta: dict | None = None) -> int:
    # Always capture a run: without an explicit --ledger-dir, write the full
    # transcript + per-stage records under a temp dir and tell the user where.
    default_root = ledger_dir is None
    root = ledger_dir or os.path.join(tempfile.gettempdir(), "rapier-runs")
    pipe = manifest.build()
    progress = _Progress(total=len(pipe.stages))
    try:
        env = pipe.run(request, ledger_root=root, log=progress.log, seed_meta=seed_meta)
    finally:
        progress.done()
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


def _run_frame(request: str, ledger_dir: str | None) -> int:
    """Run the front-door classifier and print the classification.

    stdout carries machine-readable JSON — the SPARRING skill parses it to pick
    which preset to dispatch to (``route`` = propose|resolve). stderr carries a
    one-line human summary.
    """
    import json

    from .presets import load_preset

    default_root = ledger_dir is None
    root = ledger_dir or os.path.join(tempfile.gettempdir(), "rapier-runs")
    pipe = load_preset("frame").build()
    progress = _Progress(total=len(pipe.stages))
    try:
        env = pipe.run(request, ledger_root=root, log=progress.log)
    finally:
        progress.done()

    frame = env.meta.get("frame", {})
    print(json.dumps(frame, indent=2))

    itype = frame.get("input_type", "?")
    route = frame.get("route", "?")
    line = f"· {itype} → route: {route}"
    if frame.get("readiness", "n/a") != "n/a":
        line += f"  (readiness: {frame['readiness']}, failed gate: {frame.get('earned_gate_failed')})"
    print(line, file=sys.stderr)
    run_id = env.meta.get("run_id")
    if run_id and default_root:
        print(f"· transcript saved to {os.path.join(root, run_id)}", file=sys.stderr)
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
        prog="rapier",
        description="Rapier Runtime — run a SPARRING method from a manifest or preset.",
        epilog="a ResourceForge project  ·  docs: https://rapierruntime.com  ·  "
               "update: pip install -U rapier-runtime",
    )
    parser.add_argument(
        "--version", action="version", version=f"rapier-runtime {__version__}",
        help="print the installed version and exit",
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
        if preset in ("proposer", "sparring"):  # the Proposer half seeds SPARK's field
            p.add_argument(
                "--seed", action="append", metavar="TEXT", default=None,
                help="seed a candidate option into SPARK's field (repeatable) — e.g. a Frame "
                     "anchor for a hybrid/leaning input. Not privileged: it survives only if it "
                     "wins Pattern Lock + the Cut on the merits",
            )
        if preset in ("spar", "sparring"):  # a compose stage → a ceremony-ledger row
            p.add_argument(
                "--frame", metavar="PATH", default=None,
                help="path to the JSON emitted by `rapier frame` for this input; its "
                     "classification (input_type, readiness, route, anchor, …) is recorded on "
                     "the ceremony-ledger row. Frame runs as a separate call, so the run cannot "
                     "observe it otherwise",
            )
        if preset == "sparring":  # the Proposer half only exists in the full ceremony
            p.add_argument(
                "--report-all", action="store_true",
                help="also surface the Proposer report (the committed option + its standing objections) above the Resolver report",
            )

    fr = sub.add_parser(
        "frame",
        help="classify an input (question | proposition | hybrid) and recommend a route — the front-door Presentation",
    )
    add_common(fr)

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

    if args.cmd == "frame":
        from .onboarding import preflight_error

        err = preflight_error()
        if err:  # no vendor keys — fail loudly, not silently
            print(err, file=sys.stderr)
            return 2
        return _run_frame(request, args.ledger_dir)

    if args.cmd in ("spar", "sparring", "proposer"):
        from .onboarding import preflight_error

        err = preflight_error()
        if err:  # no vendor keys — fail loudly, not silently
            print(err, file=sys.stderr)
            return 2
        preset = load_preset(
            args.cmd, settle=getattr(args, "settle", 0), verify=getattr(args, "verify", "gate"),
            seed=getattr(args, "seed", None),
        )
        # Carry context the pipeline can't observe onto the envelope for the
        # ceremony-ledger row: the resolver knobs, and the front-door Frame
        # classification (from a separate call) via --frame.
        seed_meta: dict = {
            "settle": getattr(args, "settle", 0),
            "verify": getattr(args, "verify", "gate"),
        }
        frame_path = getattr(args, "frame", None)
        if frame_path:
            try:
                import json as _json
                with open(frame_path, encoding="utf-8") as fh:
                    fj = _json.load(fh)
                if isinstance(fj, dict) and fj.get("input_type"):
                    seed_meta["frame"] = fj
            except (OSError, ValueError):
                pass  # fail-soft: a missing/garbled frame file just omits the classification
        return _run(preset, request, args.ledger_dir,
                    report_all=getattr(args, "report_all", False), seed_meta=seed_meta)
    if args.cmd == "run":
        return _run(Manifest.load(args.manifest), request, args.ledger_dir)
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
