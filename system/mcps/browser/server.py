# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
# يحمّله app/aeos/mcp/catalog.py عند الإقلاع؛ المسار القديم في app/aeos/mcp/servers اسم بديل.
"""
BrowserMCP — قراءة صفحة ويب (requests + bs4).
"""
from __future__ import annotations

import logging
from typing import List
from app.aeos.core.interfaces import ILLMProvider
from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse
logger = logging.getLogger("aeos.mcp.core")


class BrowserMCP(IMCPServer):
    name = "browser"

    @property
    def tools(self) -> List[str]:
        return ["read_page"]

    def call(self, request: MCPRequest) -> MCPResponse:
        url = request.params.get("url", "")
        if not url:
            return MCPResponse.fail("url is required")
        try:
            import requests
            from bs4 import BeautifulSoup

            resp = requests.get(url, timeout=15, headers={"User-Agent": "AEOS/1.0"})
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = " ".join(soup.get_text(" ").split())
            return MCPResponse.ok(url=url, title=(soup.title.string if soup.title else ""),
                                  text=text[:5000])
        except Exception as exc:  # noqa: BLE001
            return MCPResponse.fail(str(exc))
