# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
# يحمّله app/aeos/mcp/catalog.py عند الإقلاع؛ المسار القديم في app/aeos/mcp/servers اسم بديل.
"""
ContentMCP — توليد/تلخيص/إعادة صياغة نص عبر مزوّد LLM.
"""
from __future__ import annotations

import logging
from typing import List
from app.aeos.core.interfaces import ILLMProvider
from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse
logger = logging.getLogger("aeos.mcp.core")


class ContentMCP(IMCPServer):
    name = "content"

    def __init__(self, llm: ILLMProvider) -> None:
        self._llm = llm

    @property
    def tools(self) -> List[str]:
        return ["generate", "summarize", "rewrite"]

    def call(self, request: MCPRequest) -> MCPResponse:
        p = request.params
        if not self._llm.available:
            # deterministic fallback keeps the pipeline working offline.
            # IMPORTANT: never echo the prompt back as generated content — doing
            # so caused the raw brief to be posted as a tweet. Return empty so the
            # publish capability fails cleanly instead of posting the prompt.
            if request.tool == "summarize":
                return MCPResponse.ok(text=(p.get("text", "")[:280]))
            if request.tool == "rewrite":
                return MCPResponse.ok(text=p.get("text", ""))
            return MCPResponse.ok(text="")  # generate: no offline content

        system = p.get("system", "You are an expert social media content writer.")
        if request.tool == "generate":
            text = self._llm.complete(system, p.get("prompt", ""),
                                      temperature=p.get("temperature", 0.8),
                                      max_tokens=p.get("max_tokens", 400))
        elif request.tool == "summarize":
            text = self._llm.complete(system, f"Summarize:\n\n{p.get('text','')}",
                                      temperature=0.3, max_tokens=200)
        elif request.tool == "rewrite":
            text = self._llm.complete(system, f"Rewrite in tone '{p.get('tone','')}':\n\n{p.get('text','')}",
                                      temperature=0.7, max_tokens=400)
        else:
            return MCPResponse.fail(f"unknown content tool '{request.tool}'")
        return MCPResponse.ok(text=text)
