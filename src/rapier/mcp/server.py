"""The ``rapier mcp`` stdio server — spar / sparring / doctor as MCP tools.

Optional: requires the ``mcp`` extra (``pip install "rapier-runtime[mcp]"``). The
SDK is imported lazily inside :func:`build_server`, so importing this module (and
the rest of the CLI) never needs it. Keys are supplied by the MCP client in the
server's ``env`` block and read from the environment like everywhere else — the
engine still reads no secret from a file.
"""
from __future__ import annotations

import sys


def build_server():
    """Construct the FastMCP server with Rapier's tools.

    Raises ``ImportError`` if the ``mcp`` extra is not installed (handled by
    :func:`serve`).
    """
    from mcp.server.fastmcp import FastMCP  # optional dependency

    from . import tools

    server = FastMCP("rapier")

    @server.tool()
    def spar(request: str, settle: int = 0, verify: str = "gate") -> dict:
        """Run the SPARRING Resolver on a decision: one grounded, cross-vendor
        challenge plus a definitiveness gate. Returns a recommendation, a trust
        rider, and the grounding verdict. ``settle`` adds review-and-revise rounds
        (default 0); ``verify`` is off|gate|round for the external-canon gate."""
        return tools.run_spar(request, settle=settle, verify=verify)

    @server.tool()
    def sparring(
        request: str, settle: int = 0, verify: str = "gate", report_all: bool = False
    ) -> dict:
        """Run the full SPARRING ceremony (Proposer, then Resolver) on a decision.
        ``report_all`` also returns the Proposer handoff (the committed option and
        its standing objections)."""
        return tools.run_sparring(
            request, settle=settle, verify=verify, report_all=report_all
        )

    @server.tool()
    def rapier_doctor() -> dict:
        """Report which AI vendor keys this server has (env-var names only, never
        values) and whether cross-vendor review is available."""
        return tools.doctor()

    return server


def serve() -> int:
    """Run the stdio MCP server. Returns non-zero with a hint if the SDK is absent."""
    try:
        server = build_server()
    except ImportError:
        print(
            'The MCP server needs the optional "mcp" extra:\n'
            '    pip install "rapier-runtime[mcp]"',
            file=sys.stderr,
        )
        return 1
    server.run()  # stdio transport by default
    return 0
