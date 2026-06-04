"""Forest Soul Forge MCP connector — exposes the daemon's read / analyze /
safe-run surface to any MCP client (Claude, etc.).

Intentionally minimal __init__ (no ``mcp`` import here) so ``tools`` can be
imported + unit-tested without the FastMCP dependency. The MCP server lives in
``mcp_connector.server``.
"""
