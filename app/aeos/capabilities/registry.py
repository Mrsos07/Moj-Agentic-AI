"""
Capability registry / dispatcher.

The employee requests a capability by name; the registry validates that the
employee's connected MCP servers can satisfy it, then executes it. This is the
single choke point through which all employee actions flow — perfect for
auditing, rate-limiting and policy enforcement.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, List

from ..core.events import Event, EventType
from ..core.exceptions import CapabilityNotFoundError
from .base import CapabilityRequest, CapabilityResult, ICapability

if TYPE_CHECKING:
    from ..cognition.context import ExecutionContext

logger = logging.getLogger("aeos.capabilities")


class CapabilityRegistry:
    """Registry + dispatcher for capabilities."""

    def __init__(self, event_bus=None) -> None:
        self._caps: Dict[str, ICapability] = {}
        self._event_bus = event_bus

    def register(self, capability: ICapability) -> None:
        self._caps[capability.name] = capability
        logger.info("capability registered: %s", capability.name)

    def has(self, name: str) -> bool:
        return name in self._caps

    def list_capabilities(self) -> List[Dict]:
        return [{"name": c.name, "required_mcp": c.required_mcp} for c in self._caps.values()]

    def execute(
        self, request: CapabilityRequest, context: "ExecutionContext"
    ) -> CapabilityResult:
        cap = self._caps.get(request.name)
        if cap is None:
            raise CapabilityNotFoundError(f"capability '{request.name}' not registered")

        # isolation/authorization: the employee must have the required MCP servers
        missing = [m for m in cap.required_mcp if m not in context.config.mcp_servers]
        if missing:
            return CapabilityResult.fail(
                f"employee lacks required MCP server(s): {missing}", capability=cap.name
            )

        from ..security.gateway import capability
        try:
            capability(cap.name, context, request.params, cap.required_mcp)
        except PermissionError as exc:
            return CapabilityResult.fail(str(exc), capability=cap.name)
        result = cap.execute(request, context)

        if self._event_bus:
            self._event_bus.publish(Event(
                type=EventType.CAPABILITY_EXECUTED,
                employee_id=context.config.id,
                payload={"capability": cap.name, "success": result.success},
                source="capability_registry",
            ))
        return result
