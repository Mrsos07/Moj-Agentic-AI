# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
# يحمّله app/aeos/mcp/catalog.py عند الإقلاع؛ المسار القديم في app/aeos/mcp/servers اسم بديل.
"""
NewsMCP — جلب أحدث عناصر خلاصة RSS/أخبار قوقل.
"""
from __future__ import annotations

import logging
from typing import List
from app.aeos.core.interfaces import ILLMProvider
from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse
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
            from app.aeos.security.egress import read_page
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(read_page(feed_url), "xml")
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
