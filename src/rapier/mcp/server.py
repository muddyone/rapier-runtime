"""The ``rapier mcp`` stdio server — frame / proposer / spar / sparring / doctor as MCP tools,
and saved runs as MCP resources (``rapier://runs``, ``rapier://runs/{run_id}``,
``rapier://runs/{run_id}/record``).

Optional: requires the ``mcp`` extra (``pip install "rapier-runtime[mcp]"``). The
SDK is imported lazily inside :func:`build_server`, so importing this module (and
the rest of the CLI) never needs it. Keys are supplied by the MCP client in the
server's ``env`` block and read from the environment like everywhere else — the
engine still reads no secret from a file.

Tools return a **structured** result (recommendation + trust rider markdown, the
definitiveness verdict, the live grounding summary, cross-vendor status, and any
forwarded standing objections). A ceremony is many model calls and can run for
minutes, so the tools **stream per-stage progress** to the client: the engine's
run-log lines are bridged from the worker thread to MCP ``info`` + ``report_progress``
notifications, so the client shows life instead of a hang.

Built on the mcp 2.x SDK (``MCPServer``; it was ``FastMCP`` in 1.x).

NOTE: this module deliberately does *not* use ``from __future__ import
annotations`` — MCPServer evaluates each tool's signature, and the lazily-imported
``Context`` annotation must be a real class object at definition time, not a
string it cannot resolve.
"""
import json
import os
import sys
from typing import Any, Callable

# Cap on runs listed individually in resources/list; every run stays readable by URI
# and is indexed in rapier://runs.
_MAX_LISTED_RUNS = 200


async def _run_with_progress(
    fn: Callable[..., dict],
    ctx: Any,
    total: int,
    timeout_s: float | None = None,
    ledger_root: str | None = None,
    **kwargs: Any,
) -> dict:
    """Run a blocking tool ``fn(log=…, cancel=…, ledger_root=…, **kwargs)`` in a
    worker thread while streaming its log lines to the MCP client, with
    cooperative cancellation and an optional timeout.

    Every line goes out as an ``info`` log message, and each ``stage:`` line also
    ticks progress with that line as the progress message. The mcp 2.x SDK
    deprecates the logging helpers and delivers them only to clients that opt in,
    so the progress message is what keeps every client showing the stage.

    ``ctx`` may be ``None`` (a unit test with no session), in which case the run
    proceeds silently. On client cancellation or timeout the cancel flag is set,
    so the (abandoned) worker stops at the next stage boundary rather than being
    killed mid-call. Progress ticks once per pipeline stage (``stage:`` log lines).
    """
    import queue as _queue
    import threading

    import anyio

    q: _queue.Queue = _queue.Queue()
    sentinel = object()
    box: dict[str, dict] = {}
    cancel_event = threading.Event()

    def _log(msg: str) -> None:
        q.put(str(msg))

    def _work() -> None:
        box["result"] = fn(
            log=_log, cancel=cancel_event.is_set, ledger_root=ledger_root, **kwargs
        )

    async def _drain() -> None:
        done = 0
        while True:
            try:
                msg = q.get_nowait()
            except _queue.Empty:
                await anyio.sleep(0.02)
                continue
            if msg is sentinel:
                return
            if ctx is not None:
                try:
                    await ctx.info(msg)
                except Exception:
                    pass  # logging is best-effort; never fail a run over it
                if msg.startswith("stage:"):
                    done += 1
                    try:
                        await ctx.report_progress(done, total, message=msg)
                    except Exception:
                        pass  # progress is best-effort; never fail a run over it

    timed_out = False
    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain)
            if timeout_s:
                try:
                    with anyio.fail_after(timeout_s):
                        await anyio.to_thread.run_sync(_work, abandon_on_cancel=True)
                except TimeoutError:
                    timed_out = True
            else:
                await anyio.to_thread.run_sync(_work, abandon_on_cancel=True)
            q.put(sentinel)
    finally:
        cancel_event.set()  # stop the (possibly abandoned) worker at the next boundary

    if timed_out:
        return {"ok": False, "error": f"timed out after {timeout_s}s; partial run abandoned"}
    return box.get("result", {"ok": False, "error": "run produced no result"})


def build_server():
    """Construct the MCP server with Rapier's tools and run resources.

    Raises ``ImportError`` if the ``mcp`` extra is not installed (handled by
    :func:`serve`).
    """
    import warnings

    from mcp.server.mcpserver import Context, MCPServer  # optional dependency
    from mcp.shared.exceptions import MCPDeprecationWarning
    from mcp.types import Resource

    from .. import __version__
    from ..presets import load_preset
    from . import tools

    # ctx.info is deprecated (SEP-2577) but still reaches clients that opt in to
    # logs; keep sending it without the SDK warning on the server's stderr. The
    # warning is raised inside the SDK, so match its text, not the calling module.
    warnings.filterwarnings("ignore", message="The logging capability is deprecated",
                            category=MCPDeprecationWarning)
    # Run persistence (governance default: ON). Ceremonies are written to a
    # durable run dir and become retrievable via list_runs / get_run. Precedence:
    # RAPIER_NO_PERSIST opts out entirely; else RAPIER_MCP_LEDGER pins the location
    # for this server; else the shared default (~/.rapier/runs, RAPIER_RUNS_DIR
    # overrides). Every tool's report carries a THE RECORD line with the path.
    from ..ledger import default_runs_root, persistence_disabled
    if persistence_disabled():
        ledger_root = None
    else:
        ledger_root = os.environ.get("RAPIER_MCP_LEDGER") or default_runs_root()

    class _RapierServer(MCPServer):
        async def list_resources(self):
            # MCPServer lists only resources registered at startup, so runs saved
            # after the server started would never appear. Add each saved run's
            # report as a concrete resource every time a client asks.
            listed = await super().list_resources()
            index = tools.run_index(ledger_root)
            for run in (index.get("runs") or [])[:_MAX_LISTED_RUNS]:
                listed.append(Resource(
                    uri=run["report_uri"],
                    name=run["run_id"],
                    title=f"{run['kind']}: {run['topic']}" if run["topic"] else run["run_id"],
                    description=f"verdict: {run['verdict']}" if run["verdict"] else None,
                    mime_type="text/plain",
                ))
            return listed

    # The version goes in the initialize handshake; without it 2.x reports "".
    server = _RapierServer("rapier", version=__version__)

    def _stage_total(name: str, settle: int = 0, verify: str = "gate",
                     seed: list | None = None, depth: str = "standard") -> int:
        try:
            return len(load_preset(name, settle=settle, verify=verify,
                                   seed=seed, depth=depth).stages)
        except Exception:
            return 0

    @server.tool()
    async def spar(
        request: str, settle: int = 0, verify: str = "gate",
        frame: dict | None = None, timeout_s: float = 0, ctx: Context = None,
    ) -> dict:
        """Run the SPARRING Resolver on a decision: one grounded, cross-vendor
        challenge plus a definitiveness gate. Returns a recommendation, a trust
        rider, and the grounding verdict. ``settle`` adds review-and-revise rounds
        (default 0); ``verify`` is off|gate|round for the external-canon gate;
        ``frame`` is a Frame classification (from the ``frame`` tool) recorded on
        the ledger row; ``timeout_s`` > 0 caps the run (partial run abandoned)."""
        return await _run_with_progress(
            tools.run_spar, ctx, _stage_total("spar", settle, verify),
            timeout_s=timeout_s or None, ledger_root=ledger_root,
            request=request, settle=settle, verify=verify, frame=frame,
        )

    @server.tool()
    async def sparring(
        request: str, settle: int = 0, verify: str = "gate",
        report_all: bool = False, seed: list | None = None, depth: str = "standard",
        frame: dict | None = None, timeout_s: float = 0, ctx: Context = None,
    ) -> dict:
        """Run the full SPARRING ceremony (Proposer, then Resolver) on a decision.
        ``report_all`` also returns the Proposer handoff (the committed option and
        its standing objections). ``seed`` injects candidate options into SPARK's
        field (repeatable; not privileged — each must win on merit); ``depth`` is
        shallow|standard|deep Proposer divergence; ``frame`` is a Frame
        classification recorded on the ledger row; ``timeout_s`` > 0 caps the run."""
        return await _run_with_progress(
            tools.run_sparring, ctx,
            _stage_total("sparring", settle, verify, seed=seed, depth=depth),
            timeout_s=timeout_s or None, ledger_root=ledger_root,
            request=request, settle=settle, verify=verify, report_all=report_all,
            seed=seed, depth=depth, frame=frame,
        )

    @server.tool()
    async def proposer(
        request: str, seed: list | None = None, depth: str = "standard",
        timeout_s: float = 0, ctx: Context = None,
    ) -> dict:
        """Run the SPARRING Proposer only (SPARK → Pattern Lock → the Cut): generate
        and converge on a committed proposition with standing objections — no
        Resolver pass. ``seed`` injects candidate options into SPARK's field
        (repeatable; not privileged — each survives only if it wins on merit);
        ``depth`` is shallow|standard|deep divergence; ``timeout_s`` > 0 caps the run."""
        return await _run_with_progress(
            tools.run_proposer, ctx, _stage_total("proposer", depth=depth),
            timeout_s=timeout_s or None, ledger_root=ledger_root,
            request=request, seed=seed, depth=depth,
        )

    @server.tool()
    async def frame(
        request: str, timeout_s: float = 0, ctx: Context = None,
    ) -> dict:
        """Classify an input at the front door (the Presentation) and recommend a
        route. Returns the Frame — input_type (question | proposition | hybrid |
        earned/unearned decision), route (propose | resolve), and readiness — so a
        client can branch before running a ceremony. Does not run SPARK or the
        Resolver. ``timeout_s`` > 0 caps the run."""
        return await _run_with_progress(
            tools.run_frame, ctx, _stage_total("frame"),
            timeout_s=timeout_s or None, ledger_root=ledger_root,
            request=request,
        )

    @server.tool()
    def rapier_doctor() -> dict:
        """Report which AI vendor keys this server has (env-var names only, never
        values) and whether cross-vendor review is available."""
        return tools.doctor()

    # Kept for clients that only call tools; the resources below are the browsable form.
    @server.tool()
    def list_runs() -> dict:
        """List persisted run ids. Clients that support resources can browse rapier://runs instead."""
        return tools.list_runs(ledger_root)

    @server.tool()
    def get_run(run_id: str) -> dict:
        """Fetch a persisted run's report + verdict by id. Also readable as rapier://runs/{run_id}."""
        return tools.get_run(ledger_root, run_id)

    # ── saved runs as resources ──────────────────────────────────────────────
    @server.resource("rapier://runs", name="runs", mime_type="application/json",
                     description="Every saved run, newest first: kind, topic, verdict, and "
                                 "the URIs of its report and full record.")
    def runs_index() -> str:
        return json.dumps(tools.run_index(ledger_root), indent=2)

    @server.resource("rapier://runs/{run_id}", name="run-report", mime_type="text/plain",
                     description="A saved run's report, as the CLI printed it.")
    def run_report(run_id: str) -> str:
        return tools.run_report(ledger_root, run_id)

    @server.resource("rapier://runs/{run_id}/record", name="run-record",
                     mime_type="application/json",
                     description="A saved run's full record (redacted on write): request, "
                                 "recommendation, trust rider, verdict, trace, meta.")
    def run_record(run_id: str) -> str:
        return json.dumps(tools.run_record(ledger_root, run_id), indent=2, default=str)

    return server


def _installed_mcp_version() -> str | None:
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version("mcp")
    except PackageNotFoundError:
        return None


def serve() -> int:
    """Run the stdio MCP server. Returns non-zero with a hint if the SDK is absent."""
    try:
        server = build_server()
    except ImportError:
        installed = _installed_mcp_version()
        major = installed.split(".")[0] if installed else ""
        if major.isdigit() and int(major) != 2:
            print(
                f"The MCP server needs the mcp SDK 2.x, but {installed} is installed\n"
                "(its server API differs). Install a 2.x SDK:\n"
                '    pip install "mcp>=2.3,<3"',
                file=sys.stderr,
            )
        else:
            print(
                'The MCP server needs the optional "mcp" extra:\n'
                '    pip install "rapier-runtime[mcp]"',
                file=sys.stderr,
            )
        return 1
    server.run()  # stdio transport by default
    return 0
