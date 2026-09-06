"""
Image MCP server — visual content for the AI creator.

يمنح الموظف الذكي "يداً بصرية": يولّد صورة أصلية تخدم المنشور بدل الاعتماد على
صور الأخبار فقط (التي كثيراً ما تكون شعار مصدر أو غير مرتبطة). الصورة تُولَّد
عبر مزوّد المنصة ثم تُعاد كرابط HTTP يستطيع مُنزّل الميديا في ``app/x`` جلبه.

Tools:
    - ``generate_image(prompt, size?, style?)`` → ``{url, local_path, prompt}``

**توليد الصور هو المسار الوحيد غير المتوافق شكلاً بين المزوّدين**، ولهذا
مساران هنا لا مسار واحد بـ ``base_url`` مختلف:
- OpenAI: ``POST /v1/images/generations`` ويكفي فيه ``client.images.generate``
  من حزمة ``openai``.
- OpenRouter: ``POST /api/v1/images`` — مسار مختلف تماماً، فـ SDK سيضرب
  ``/images/generations`` ويعيد 404. لذلك نستخدم طلب HTTP مباشراً. الرد يحمل
  البايتات في ``data[0].b64_json`` (أو ``image_url``/``url``).

سلوك آمن:
- بلا مفتاح (OpenRouter أو OpenAI) يعيد فشلاً نظيفاً فيستمر المنشور نصّاً.
- إن أعاد المزوّد صورة base64 (نماذج gpt-image) نحفظها في ``static/uploads``
  ونبني رابطاً محلياً (``AEOS_PUBLIC_BASE_URL`` أو 127.0.0.1:PORT) — سيرفر X
  يعمل على نفس الجهاز فيصل إليه.
- الصور المولّدة تُنظَّف تلقائياً: نحتفظ بأحدث ``_KEEP_FILES`` صورة فقط.

متغيرات البيئة:
- ``OPENROUTER_IMAGE_MODEL`` — الافتراضي ``openai/gpt-image-1`` (اسم مؤهَّل
  بالمزوّد، وهو ما تفرضه OpenRouter).
- ``AEOS_IMAGE_MODEL``       — موديل الصور على مسار OpenAI (``gpt-image-1``؛
  أزالت OpenAI ‏dall-e-3).
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
        return bool(self._enabled() and self._endpoint().available)

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _enabled() -> bool:
        return os.getenv("AEOS_IMAGES_ENABLED", "1").strip() not in ("0", "false", "no")

    @staticmethod
    def _endpoint():
        from app.core.llm_provider import resolve_llm

        return resolve_llm()

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
        endpoint = self._endpoint()
        if not endpoint.available:
            return MCPResponse.fail("no OPENROUTER_API_KEY / OPENAI_API_KEY configured")

        # gpt-image-1 لا dall-e-3: واجهة OpenAI أزالت dall-e-3 نهائياً
        # ‏("The model 'dall-e-3' does not exist"، مؤكد فعلياً 2026-08-05)
        model = request.params.get("model") or endpoint.image_model
        size = request.params.get("size") or os.getenv("AEOS_IMAGE_SIZE", "1024x1024")

        try:
            if endpoint.is_openrouter:
                url, b64 = self._generate_openrouter(endpoint, model, prompt, size)
            else:
                url, b64 = self._generate_openai(endpoint, model, prompt, size)
        except Exception as exc:  # noqa: BLE001
            logger.warning("image generation failed (%s/%s): %s",
                           endpoint.provider, model, exc)
            return MCPResponse.fail(f"image generation failed: {exc}")

        local_path = ""
        if not url:
            if not b64:
                return MCPResponse.fail("image provider returned neither url nor b64")
            saved = self._save_b64(b64)
            if saved is None:
                return MCPResponse.fail("could not persist generated image")
            local_path = str(saved)
            url = f"{self._public_base()}/static/uploads/{saved.name}"

        logger.info("generated image for employee %s (%s/%s)",
                    request.employee_id, endpoint.provider, model)
        return MCPResponse.ok(url=url, local_path=local_path, prompt=prompt, model=model)

    # ---- providers -------------------------------------------------------
    @staticmethod
    def _generate_openai(endpoint, model: str, prompt: str, size: str):
        """مسار OpenAI الأصلي عبر الـ SDK (``/images/generations``)."""
        from openai import OpenAI

        client = OpenAI(**endpoint.client_kwargs())
        # لا نمرّر response_format: واجهة OpenAI الحالية ترفضه حتى مع dall-e-3
        # ‏(Unknown parameter، شوهد 2026-08-05). المعالجة أدناه تتعامل مع
        # الحالتين أصلاً: رابط مباشر أو b64 يُحفظ محلياً.
        result = client.images.generate(model=model, prompt=prompt[:3800], size=size, n=1)
        item = (getattr(result, "data", None) or [None])[0]
        if item is None:
            raise RuntimeError("image provider returned no data")
        return (getattr(item, "url", "") or ""), (getattr(item, "b64_json", "") or "")

    @staticmethod
    def _generate_openrouter(endpoint, model: str, prompt: str, size: str):
        """مسار OpenRouter: ``POST /api/v1/images`` بطلب HTTP مباشر.

        لا نستعمل الـ SDK هنا لأنه يضرب ``/images/generations`` فيعيد 404.
        و``size`` غير موجود في هذه الواجهة — البديل ``aspect_ratio``، فنشتقّه
        من الأبعاد بدل إرسال مفتاح تتجاهله (أو ترفضه) الواجهة.
        """
        import requests

        payload = {"model": model, "prompt": prompt[:3800]}
        ratio = ImageMCP._aspect_ratio(size)
        if ratio:
            payload["aspect_ratio"] = ratio

        resp = requests.post(endpoint.images_url, headers=endpoint.auth_headers(),
                             json=payload, timeout=180)
        if resp.status_code != 200:
            raise RuntimeError(f"openrouter {resp.status_code}: {resp.text[:300]}")
        item = ((resp.json() or {}).get("data") or [None])[0]
        if not isinstance(item, dict):
            raise RuntimeError("image provider returned no data")
        # OpenRouter يعيد البايتات في b64_json عادةً؛ بعض المزوّدين يعيدون
        # رابطاً — نتعامل مع الشكلين بدل افتراض واحد.
        url = item.get("url") or ""
        if not url and isinstance(item.get("image_url"), dict):
            url = item["image_url"].get("url") or ""
        b64 = item.get("b64_json") or ""
        if url.startswith("data:"):  # data URL ⇒ نعالجه كـ b64 ونحفظه محلياً
            b64, url = url.split(",", 1)[-1], ""
        return url, b64

    @staticmethod
    def _aspect_ratio(size: str) -> str:
        """"1024x1024" → "1:1". يعيد "" لو تعذّر الاشتقاق."""
        try:
            width, height = (int(part) for part in size.lower().split("x", 1))
        except (TypeError, ValueError):
            return ""
        if width <= 0 or height <= 0:
            return ""
        from math import gcd

        divisor = gcd(width, height)
        return f"{width // divisor}:{height // divisor}"
