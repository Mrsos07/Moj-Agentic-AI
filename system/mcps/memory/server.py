# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
# يحمّله app/aeos/mcp/catalog.py عند الإقلاع؛ المسار القديم في app/aeos/mcp/servers اسم بديل.
"""
MemoryMCP — حفظ واسترجاع ذاكرة الموظف عبر MemoryManager.
"""
from __future__ import annotations

import logging
from typing import List
from app.aeos.core.interfaces import ILLMProvider
from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse
logger = logging.getLogger("aeos.mcp.core")


class MemoryMCP(IMCPServer):
    name = "memory"

    def __init__(self, memory_manager) -> None:
        self._mem = memory_manager

    @property
    def tools(self) -> List[str]:
        return ["store", "recall"]

    def call(self, request: MCPRequest) -> MCPResponse:
        from app.aeos.memory.base import MemoryLayerType

        p = request.params
        layer = MemoryLayerType(p.get("layer", "long_term"))
        if request.tool == "store":
            self._mem.remember(
                request.employee_id, layer, p.get("content", ""),
                key=p.get("key"), importance=int(p.get("importance", 2)),
            )
            return MCPResponse.ok(stored=True)
        if request.tool == "recall":
            recs = self._mem.recall(request.employee_id, layer,
                                    p.get("query", ""), int(p.get("limit", 5)))
            return MCPResponse.ok(items=[r.to_dict() for r in recs])
        return MCPResponse.fail(f"unknown memory tool '{request.tool}'")
