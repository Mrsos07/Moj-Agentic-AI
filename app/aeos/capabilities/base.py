"""
Capability contract.

A Capability is a *business-level* action the employee can request — "Publish
Content", "Reply to User", "Research Topic". The employee expresses intent as a
capability; the capability decides which MCP server(s) it needs and how to use
them. This completely decouples business logic from MCP implementation.

Capabilities receive an :class:`ExecutionContext` (employee config, memory,
workspace, MCP registry) so they can resolve credentials just-in-time and stay
isolated per employee.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from ..cognition.context import ExecutionContext


@dataclass
class CapabilityRequest:
    name: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilityResult:
    success: bool
    output: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    capability: str = ""

    @classmethod
    def ok(cls, capability: str = "", **output: Any) -> "CapabilityResult":
        return cls(success=True, capability=capability, output=output)

    @classmethod
    def fail(cls, error: str, capability: str = "") -> "CapabilityResult":
        return cls(success=False, error=error, capability=capability)


class ICapability(abc.ABC):
    """One business-level capability."""

    #: unique capability name (e.g. "publish_content")
    name: str
    #: MCP servers this capability may use (for validation/introspection)
    required_mcp: List[str] = []

    @abc.abstractmethod
    def execute(
        self, request: CapabilityRequest, context: "ExecutionContext"
    ) -> CapabilityResult:
        ...
