"""
Image MCP server — visual content for the AI creator.

يمنح الموظف الذكي "يداً بصرية": يولّد صورة أصلية تخدم المنشور بدل الاعتماد على
صور الأخبار فقط (التي كثيراً ما تكون شعار مصدر أو غير مرتبطة). الصورة تُولَّد
بـ OpenAI Images ثم تُعاد كرابط HTTP يستطيع مُنزّل الميديا في ``app/x`` جلبه.

Tools:
    - ``generate_image(prompt, size?, style?)`` → ``{url, local_path, prompt}``

سلوك آمن:
- بلا مفتاح OpenAI أو بلا حزمة ``openai`` يعيد فشلاً نظيفاً فيستمر المنشور نصّاً.
- إن أعاد المزوّد صورة base64 (نماذج gpt-image) نحفظها في ``static/uploads``
  ونبني رابطاً محلياً (``AEOS_PUBLIC_BASE_URL`` أو 127.0.0.1:PORT) — سيرفر X
  يعمل على نفس الجهاز فيصل إليه.
- الصور المولّدة تُنظَّف تلقائياً: نحتفظ بأحدث ``_KEEP_FILES`` صورة فقط.

متغيرات البيئة:
- ``AEOS_IMAGE_MODEL``       — الافتراضي ``dall-e-3``.
- ``AEOS_IMAGE_SIZE``        — الافتراضي ``1024x1024``.
- ``AEOS_PUBLIC_BASE_URL``   — أصل الرابط العام للملفات الثابتة.
- ``AEOS_IMAGES_ENABLED``    — "0" لتعطيل التوليد.
"""
from __future__ import annotations

import base64
import logging
import os
import uuid
from pathlib import Path
from typing import List, Optional

from ..base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.image")

_UPLOADS_DIR = Path(__file__).resolve().parents[3].parent / "static" / "uploads"
_KEEP_FILES = 60


class ImageMCP(IMCPServer):
    name = "image"

    @property
    def tools(self) -> List[str]:
        return ["generate_image"]

    def health(self) -> bool:
        return bool(self._enabled() and self._api_key())

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _enabled() -> bool:
        return os.getenv("AEOS_IMAGES_ENABLED", "1").strip() not in ("0", "false", "no")

    @staticmethod
    def _api_key() -> str:
        key = os.getenv("OPENAI_API_KEY", "")
        if key:
            return key
        try:
            from app.core.config import settings
            return getattr(settings, "OPENAI_API_KEY", "") or ""
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _public_base() -> str:
        base = (os.getenv("AEOS_PUBLIC_BASE_URL") or "").strip().rstrip("/")
        if base:
            return base
        port = os.getenv("PORT", "8000").strip() or "8000"
        return f"http://127.0.0.1:{port}"

    @classmethod
    def _save_b64(cls, b64: str) -> Optional[Path]:
        try:
            _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            path = _UPLOADS_DIR / f"aeos-img-{uuid.uuid4().hex}.png"
            path.write_bytes(base64.b64decode(b64))
            cls._prune()
            return path
        except Exception as exc:  # noqa: BLE001
            logger.warning("saving generated image failed: %s", exc)
            return None

    @staticmethod
    def _prune() -> None:
        """احتفظ بأحدث الصور المولّدة فقط حتى لا ينمو المجلد بلا حد."""
        try:
            files = sorted(_UPLOADS_DIR.glob("aeos-img-*.png"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
            for old in files[_KEEP_FILES:]:
                old.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    # ---- MCP -------------------------------------------------------------
    def call(self, request: MCPRequest) -> MCPResponse:
        if request.tool != "generate_image":
            return MCPResponse.fail(f"unknown image tool '{request.tool}'")
        if not self._enabled():
            return MCPResponse.fail("image generation disabled (AEOS_IMAGES_ENABLED=0)")

        prompt = (request.params.get("prompt") or "").strip()
        if not prompt:
            return MCPResponse.fail("prompt is required for generate_image")
        api_key = self._api_key()
        if not api_key:
            return MCPResponse.fail("OPENAI_API_KEY not configured")

        model = request.params.get("model") or os.getenv("AEOS_IMAGE_MODEL", "dall-e-3")
        size = request.params.get("size") or os.getenv("AEOS_IMAGE_SIZE", "1024x1024")

        try:
            from openai import OpenAI
        except Exception as exc:  # noqa: BLE001
            return MCPResponse.fail(f"openai package unavailable: {exc}")

        client = OpenAI(api_key=api_key)
        kwargs = {"model": model, "prompt": prompt[:3800], "size": size, "n": 1}
        if str(model).startswith("dall-e"):
            kwargs["response_format"] = "url"
        try:
            result = client.images.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("image generation failed (%s): %s", model, exc)
            return MCPResponse.fail(f"image generation failed: {exc}")

        item = (getattr(result, "data", None) or [None])[0]
        if item is None:
            return MCPResponse.fail("image provider returned no data")

        url = getattr(item, "url", "") or ""
        local_path = ""
        if not url:
            b64 = getattr(item, "b64_json", "") or ""
            if not b64:
                return MCPResponse.fail("image provider returned neither url nor b64")
            saved = self._save_b64(b64)
            if saved is None:
                return MCPResponse.fail("could not persist generated image")
            local_path = str(saved)
            url = f"{self._public_base()}/static/uploads/{saved.name}"

        logger.info("generated image for employee %s (model=%s)", request.employee_id, model)
        return MCPResponse.ok(url=url, local_path=local_path, prompt=prompt, model=model)
