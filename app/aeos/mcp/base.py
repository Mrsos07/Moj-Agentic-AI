"""
MCP server contract.

An MCP server exposes a set of named tools. The AI Employee NEVER calls MCP
servers directly — the Capability Layer does, on its behalf. This keeps business
logic fully decoupled from MCP implementations, and lets future MCP servers be
plugged in without changing employee logic.

Each server declares the tools it provides so the registry can route capability
requests to the right server, and so tooling/introspection is possible.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class MCPRequest:
    """A call into an MCP tool, always scoped to one employee."""

    employee_id: str
    tool: str
    params: Dict[str, Any] = field(default_factory=dict)
    # secrets/credentials resolved by the capability layer just-in-time
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResponse:
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @classmethod
    def ok(cls, **data: Any) -> "MCPResponse":
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str) -> "MCPResponse":
        return cls(success=False, error=error)


class IMCPServer(abc.ABC):
    """A pluggable tool server."""

    #: unique server name, e.g. "x", "browser", "content", "news"
    name: str

    @property
    @abc.abstractmethod
    def tools(self) -> List[str]:
        """Names of the tools this server provides."""

    @abc.abstractmethod
    def call(self, request: MCPRequest) -> MCPResponse:
        """Execute a tool call."""

    def health(self) -> bool:
        """Whether the server is reachable/usable. Override if meaningful."""
        return True
