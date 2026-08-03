"""
Standard MCP servers every employee owns.

- ContentMCP  : generates/summarizes text via the LLM provider.
- MemoryMCP   : store/retrieve knowledge through the MemoryManager.
- BrowserMCP  : browse/read a web page (best-effort via requests+bs4).
- NewsMCP     : fetch recent items from an RSS/news feed.
- SearchMCP   : web search (stub — pluggable; returns empty until wired).

These are deliberately thin: they expose tools, not business logic. Future
servers (image generation, filesystem, analytics) plug in the same way.
"""
from __future__ import annotations

import logging
from typing import List

from ...core.interfaces import ILLMProvider
from ..base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.core")


# استضافات الصور العامة/المضللة التي يعيدها Google News (شعار المصدر وليست
# صورة الخبر). نرفضها حتى لا نُرفق صورة خاطئة بكل منشور.
_GENERIC_IMAGE_HOSTS = (
    "googleusercontent.com",
    "gstatic.com",
    "news.google.com",
    "ssl.gstatic.com",
)


def _is_generic_news_image(url: str) -> bool:
    """True if the image URL is a generic Google News placeholder/source-logo."""
    u = (url or "").strip().lower()
    if not u:
        return False
    return any(host in u for host in _GENERIC_IMAGE_HOSTS)


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


class MemoryMCP(IMCPServer):
    name = "memory"

    def __init__(self, memory_manager) -> None:
        self._mem = memory_manager

    @property
    def tools(self) -> List[str]:
        return ["store", "recall"]

    def call(self, request: MCPRequest) -> MCPResponse:
        from ...memory.base import MemoryLayerType

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


class NewsMCP(IMCPServer):
    name = "news"

    @property
    def tools(self) -> List[str]:
        return ["fetch_feed"]

    def call(self, request: MCPRequest) -> MCPResponse:
        feed_url = request.params.get("feed_url", "")
        if not feed_url:
            return MCPResponse.fail("feed_url is required")
        try:
            import requests
            from bs4 import BeautifulSoup

            resp = requests.get(
                feed_url, timeout=15,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AEOS/1.0)"},
            )
            soup = BeautifulSoup(resp.content, "xml")
            items = []
            for item in soup.find_all("item")[: int(request.params.get("limit", 10))]:
                # summary from <description> (strip any HTML tags)
                desc_raw = item.description.text if item.description else ""
                summary = " ".join(
                    BeautifulSoup(desc_raw, "html.parser").get_text(" ").split()
                ) if desc_raw else ""
                # best-effort media extraction (enclosure / media:content / thumbnail)
                media_url = ""
                enc = item.find("enclosure")
                if enc and enc.get("url"):
                    media_url = enc.get("url")
                if not media_url:
                    mc = item.find("media:content") or item.find("content", recursive=False)
                    if mc and mc.get("url"):
                        media_url = mc.get("url")
                if not media_url:
                    mt = item.find("media:thumbnail")
                    if mt and mt.get("url"):
                        media_url = mt.get("url")
                # Google News RSS يعطي دائماً صورة عامة (شعار المصدر على
                # googleusercontent/gstatic) وليست صورة الخبر الحقيقية — نتجاهلها
                # حتى لا نُرفق صورة مضللة، ويبقى النشر نصياً بمحتوى جيد.
                if _is_generic_news_image(media_url):
                    media_url = ""
                items.append({
                    "title": (item.title.text if item.title else ""),
                    "link": (item.link.text if item.link else ""),
                    "published": (item.pubDate.text if item.pubDate else ""),
                    "summary": summary[:500],
                    "media_url": media_url,
                    "source": (item.source.text if item.source else ""),
                })
            return MCPResponse.ok(items=items)
        except Exception as exc:  # noqa: BLE001
            return MCPResponse.fail(str(exc))


class SearchMCP(IMCPServer):
    name = "search"

    @property
    def tools(self) -> List[str]:
        return ["web_search"]

    def call(self, request: MCPRequest) -> MCPResponse:
        # Pluggable stub: wire to a real search provider (SerpAPI/Brave) later.
        return MCPResponse.ok(results=[], note="search provider not configured")
