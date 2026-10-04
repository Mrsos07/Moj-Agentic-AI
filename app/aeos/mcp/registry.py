"""
MCP registry — pluggable directory of tool servers.

Servers register themselves here; the Capability Layer resolves the server that
owns a requested tool. New MCP servers become available platform-wide simply by
registering, with zero changes to employee logic (Open/Closed Principle).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ..core.exceptions import MCPNotFoundError
from .base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp")


class MCPRegistry:
    """Central registry + router for MCP servers."""

    def __init__(self) -> None:
        self._servers: Dict[str, IMCPServer] = {}
        self._tool_index: Dict[str, str] = {}  # tool -> server name

    def register(self, server: IMCPServer) -> None:
        self._servers[server.name] = server
        for tool in server.tools:
            self._tool_index[tool] = server.name
        logger.info("MCP server registered: %s (tools: %s)", server.name, server.tools)

    def unregister(self, name: str) -> None:
        server = self._servers.pop(name, None)
        if server is None:
            return
        self._tool_index = {t: s for t, s in self._tool_index.items() if s != name}
        close = getattr(getattr(server, "client", None), "close", None)
        if callable(close):
            close()

    def get(self, name: str) -> IMCPServer:
        if name not in self._servers:
            raise MCPNotFoundError(f"MCP server '{name}' not registered")
        return self._servers[name]

    def has(self, name: str) -> bool:
        """Is this MCP server registered? (lets a capability degrade gracefully)"""
        return name in self._servers

    def server_for_tool(self, tool: str) -> Optional[IMCPServer]:
        name = self._tool_index.get(tool)
        return self._servers.get(name) if name else None

    def call(self, request: MCPRequest) -> MCPResponse:
        """Route a request to the server that owns the tool."""
        server = self._servers.get(request.context.get("server", ""))
        if server is None:
            server = self.server_for_tool(request.tool)
        if server is None:
            return MCPResponse.fail(f"no MCP server provides tool '{request.tool}'")
        try:
            return server.call(request)
        except Exception as exc:  # noqa: BLE001
            logger.exception("MCP call failed: %s.%s", server.name, request.tool)
            return MCPResponse.fail(str(exc))

    def list_servers(self) -> List[Dict]:
        return [
            {"name": s.name, "tools": s.tools, "healthy": s.health()}
            for s in self._servers.values()
        ]
