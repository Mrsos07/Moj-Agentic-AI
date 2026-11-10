# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
"""
PixabayMCP — صور وفيديوهات مجانية الحقوق من Pixabay.

منقول إلى Python من Unlock-MCP/pixabay-mcp-server (MIT — انظر UPSTREAM.md) بالأدوات نفسها:
``search_images`` و``search_videos`` و``get_image_by_id`` و``get_video_by_id``، ليعمل داخل
العملية بلا Node (صورة الدوكر Python فقط) ويستفيد منه الموظفون مباشرة.

- المفتاح: ``PIXABAY_API_KEY`` في ‎.env (لا يُعاد ولا يُسجَّل؛ يُحذف من أي رسالة خطأ).
- شروط Pixabay: تخزين النتائج 24 ساعة (لا طلبات متكررة)، وحد 100 طلب/دقيقة.
- روابط ``largeImageURL`` مؤقتة (24 ساعة): تُستخدم للنشر الفوري لا للحفظ الدائم.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.pixabay")

_API = "https://pixabay.com/api/"
_TTL = 24 * 3600
_cache: Dict[tuple, tuple] = {}
_lock = threading.Lock()

_IMAGE_PARAMS = ("q", "lang", "image_type", "orientation", "category", "min_width", "min_height",
                 "colors", "editors_choice", "safesearch", "order", "page", "per_page")
_VIDEO_PARAMS = ("q", "lang", "video_type", "category", "min_width", "min_height", "editors_choice",
                 "safesearch", "order", "page", "per_page")


def api_key() -> str:
    return os.getenv("PIXABAY_API_KEY", "").strip()


def _image(h: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": h.get("id"), "kind": "image", "tags": h.get("tags", ""), "page_url": h.get("pageURL", ""),
            "url": h.get("largeImageURL") or h.get("webformatURL") or "", "preview": h.get("previewURL", ""),
            "width": h.get("imageWidth"), "height": h.get("imageHeight"), "likes": h.get("likes", 0),
            "views": h.get("views", 0), "downloads": h.get("downloads", 0), "user": h.get("user", "")}


def _video(h: Dict[str, Any]) -> Dict[str, Any]:
    v = h.get("videos") or {}
    # medium (~1280px) يكفي لـX ويبقى الحجم معقولاً؛ large قد يتجاوز مئات الميغابايت
    pick = next((v[k] for k in ("medium", "small", "large", "tiny") if (v.get(k) or {}).get("url")), {})
    return {"id": h.get("id"), "kind": "video", "tags": h.get("tags", ""), "page_url": h.get("pageURL", ""),
            "url": pick.get("url", ""), "preview": pick.get("thumbnail") or (v.get("tiny") or {}).get("thumbnail", ""),
            "width": pick.get("width"), "height": pick.get("height"), "size": pick.get("size"),
            "duration": h.get("duration"), "likes": h.get("likes", 0), "views": h.get("views", 0),
            "user": h.get("user", "")}


class PixabayMCP(IMCPServer):
    name = "pixabay"

    @property
    def tools(self) -> List[str]:
        return ["search_images", "search_videos", "get_image_by_id", "get_video_by_id"]

    def _get(self, video: bool, params: Dict[str, Any]) -> Dict[str, Any]:
        key = api_key()
        if not key:
            raise RuntimeError("Pixabay غير مُهيّأ: أضف PIXABAY_API_KEY في ‎.env (مجاني من pixabay.com/api/docs)")
        clean = {k: v for k, v in params.items() if v not in (None, "")}
        ck = (video, tuple(sorted((k, str(v)) for k, v in clean.items())))
        with _lock:
            hit = _cache.get(ck)
            if hit and time.time() - hit[0] < _TTL:
                return hit[1]
        import requests

        resp = requests.get(_API + ("videos/" if video else ""), params={"key": key, **clean}, timeout=20)
        if resp.status_code == 429:
            raise RuntimeError("تجاوز حد Pixabay (100 طلب/دقيقة) — أعد المحاولة بعد قليل")
        if resp.status_code != 200:
            raise RuntimeError(f"Pixabay HTTP {resp.status_code}: {resp.text[:160].replace(key, '***')}")
        data = resp.json()
        with _lock:
            if len(_cache) > 500:
                _cache.clear()
            _cache[ck] = (time.time(), data)
        return data

    def call(self, request: MCPRequest) -> MCPResponse:
        tool = (request.tool or "").split(".")[-1]
        p = dict(request.params or {})
        try:
            if tool in ("search_images", "search_videos"):
                video = tool == "search_videos"
                q = str(p.get("q") or p.get("query") or "").strip()[:100]
                if not q:
                    return MCPResponse.fail("q (search term) is required")
                params = {k: p[k] for k in (_VIDEO_PARAMS if video else _IMAGE_PARAMS) if k in p}
                params.update(q=q, safesearch=str(p.get("safesearch", "true")).lower(),
                              per_page=max(3, min(200, int(p.get("per_page") or 20))))
                data = self._get(video, params)
                hits = [(_video if video else _image)(h) for h in data.get("hits") or []]
                return MCPResponse.ok(results=hits, total=data.get("totalHits", len(hits)), query=q,
                                      kind="video" if video else "image")
            if tool in ("get_image_by_id", "get_video_by_id"):
                video = tool == "get_video_by_id"
                item_id = str(p.get("id") or "").strip()
                if not item_id.isdigit():
                    return MCPResponse.fail("id (numeric Pixabay id) is required")
                hits = self._get(video, {"id": item_id}).get("hits") or []
                if not hits:
                    return MCPResponse.fail(f"no Pixabay {'video' if video else 'image'} with id {item_id}")
                return MCPResponse.ok(item=(_video if video else _image)(hits[0]))
            return MCPResponse.fail(f"unknown tool {tool}")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).replace(api_key() or "\0", "***")
            logger.info("pixabay %s failed: %s", tool, msg)
            return MCPResponse.fail(msg)


def first_url(result: Optional[Dict[str, Any]]) -> str:
    items = (result or {}).get("results") or []
    return items[0]["url"] if items else ""
