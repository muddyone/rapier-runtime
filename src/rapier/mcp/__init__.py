"""Rapier's MCP server (optional — needs the ``mcp`` extra).

``pip install "rapier-runtime[mcp]"`` then run ``rapier mcp``. The SDK is imported
lazily inside :func:`build_server`, so importing this package (and the rest of the
CLI) never requires the extra.
"""
from .server import build_server, serve

__all__ = ["build_server", "serve"]
