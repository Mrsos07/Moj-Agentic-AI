# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
# يحمّله app/aeos/mcp/catalog.py عند الإقلاع؛ المسار القديم في app/aeos/mcp/servers اسم بديل.
"""
SearchMCP — بحث ويب حقيقي (Tavily / Brave / SerpAPI).
"""
from __future__ import annotations

import logging
from typing import List
from app.aeos.core.interfaces import ILLMProvider
from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse
logger = logging.getLogger("aeos.mcp.core")


#: مزوّدو البحث المدعومون: متغير البيئة الذي يفعّل كلاً منهم
SEARCH_PROVIDER_KEYS = {
    "tavily": "TAVILY_API_KEY",
    "brave": "BRAVE_SEARCH_API_KEY",
    "serpapi": "SERPAPI_KEY",
}


def search_provider() -> tuple:
    """(اسم المزوّد، المفتاح) — ``SEARCH_PROVIDER`` صريحاً، وإلا أول مفتاح موجود."""
    import os

    wanted = (os.getenv("SEARCH_PROVIDER") or "").strip().lower()
    order = [wanted] if wanted in SEARCH_PROVIDER_KEYS else list(SEARCH_PROVIDER_KEYS)
    for name in order:
        key = (os.getenv(SEARCH_PROVIDER_KEYS[name]) or "").strip()
        if key:
            return name, key
    return "", ""


class SearchMCP(IMCPServer):
    """بحث ويب حقيقي (Tavily / Brave / SerpAPI) — يختاره مفتاح البيئة.

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
        import requests

        query = str(request.params.get("query") or "").strip()
        if not query:
            return MCPResponse.fail("query is required")
        limit = max(1, min(int(request.params.get("limit", 5) or 5), 10))
        provider, key = search_provider()
        if not provider:
            return MCPResponse.fail(
                "search provider not configured — أضف TAVILY_API_KEY أو "
                "BRAVE_SEARCH_API_KEY أو SERPAPI_KEY إلى .env")
        try:
            if provider == "tavily":
                resp = requests.post(
                    "https://api.tavily.com/search",
                    json={"query": query, "max_results": limit, "search_depth": "basic"},
                    headers={"Authorization": f"Bearer {key}"}, timeout=20)
                resp.raise_for_status()
                items = [{"title": r.get("title", ""), "url": r.get("url", ""),
                          "snippet": r.get("content", "")}
                         for r in resp.json().get("results") or []]
            elif provider == "brave":
                resp = requests.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": limit},
                    headers={"X-Subscription-Token": key, "Accept": "application/json"},
                    timeout=20)
                resp.raise_for_status()
                items = [{"title": r.get("title", ""), "url": r.get("url", ""),
                          "snippet": r.get("description", "")}
                         for r in (resp.json().get("web") or {}).get("results") or []]
            else:
                resp = requests.get(
                    "https://serpapi.com/search.json",
                    params={"engine": "google", "q": query, "num": limit, "api_key": key},
                    timeout=20)
                resp.raise_for_status()
                items = [{"title": r.get("title", ""), "url": r.get("link", ""),
                          "snippet": r.get("snippet", "")}
                         for r in resp.json().get("organic_results") or []]
        except Exception as exc:  # noqa: BLE001
            # لا نُظهر المفتاح: رسالة requests قد تحوي الرابط كاملاً (SerpAPI يضعه فيه)
            status = getattr(getattr(exc, "response", None), "status_code", "")
            return MCPResponse.fail(f"{provider} search failed: {type(exc).__name__} {status}".strip())
        items = [i for i in items if i["url"]][:limit]
        return MCPResponse.ok(results=items, provider=provider, query=query)
