# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
# يحمّله app/aeos/mcp/catalog.py عند الإقلاع؛ المسار القديم في app/aeos/mcp/servers اسم بديل.
"""
SearchMCP — بحث ويب حقيقي عبر Tavily (المزوّد الوحيد في النظام).

كان يدعم Brave وSerpAPI أيضاً؛ أُزيلا ليبقى مزوّد بحث واحد ومفتاح واحد (TAVILY_API_KEY)
يخدم البحث المدمج وكاشف الترندات وخادم Tavily البعيد معاً.
"""
from __future__ import annotations

import logging
import os
from typing import List

from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.core")

#: المزوّد ← متغيّر البيئة الذي يفعّله (اسم القاموس محفوظ للتوافق مع من يستورده)
SEARCH_PROVIDER_KEYS = {"tavily": "TAVILY_API_KEY"}
TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def search_provider() -> tuple:
    """(«tavily»، المفتاح) إن وُجد المفتاح، وإلا («»، «»)."""
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    return ("tavily", key) if key else ("", "")


def tavily_search(query: str, *, max_results: int = 5, topic: str = "general",
                  time_range: str = "", timeout: float = 20) -> list:
    """نداء Tavily واحد — يستخدمه البحث المدمج وكاشف الترندات. يرفع استثناءً عند الفشل."""
    import requests

    key = search_provider()[1]
    if not key:
        raise RuntimeError("TAVILY_API_KEY غير موجود في .env")
    body = {"query": query, "max_results": max_results, "search_depth": "basic", "topic": topic}
    if time_range:
        body["time_range"] = time_range
    resp = requests.post(TAVILY_SEARCH_URL, json=body, headers={"Authorization": f"Bearer {key}"},
                         timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("results") or []


class SearchMCP(IMCPServer):
    """بحث ويب حقيقي عبر Tavily.

    بلا مفتاح يفشل صراحةً بـ«غير مُهيّأ» بدل أن يعيد نجاحاً بقائمة فارغة: النجاح
    الفارغ كان يُقرأ كـ«لا نتائج» فيبدو البحث شغّالاً وهو غير موصول أصلاً.
    النتائج موحّدة: ``[{title, url, snippet}]``.
    """

    name = "search"

    @property
    def tools(self) -> List[str]:
        return ["web_search"]

    def health(self) -> bool:
        return bool(search_provider()[0])

    def call(self, request: MCPRequest) -> MCPResponse:
        query = str(request.params.get("query") or "").strip()
        if not query:
            return MCPResponse.fail("query is required")
        limit = max(1, min(int(request.params.get("limit", 5) or 5), 10))
        if not search_provider()[0]:
            return MCPResponse.fail("search provider not configured — أضف TAVILY_API_KEY إلى .env")
        try:
            items = [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
                     for r in tavily_search(query, max_results=limit)]
        except Exception as exc:  # noqa: BLE001
            status = getattr(getattr(exc, "response", None), "status_code", "")
            return MCPResponse.fail(f"tavily search failed: {type(exc).__name__} {status}".strip())
        items = [i for i in items if i["url"]][:limit]
        return MCPResponse.ok(results=items, provider="tavily", query=query)
