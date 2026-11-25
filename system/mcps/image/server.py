# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
# يحمّله app/aeos/mcp/catalog.py عند الإقلاع؛ المسار القديم في app/aeos/mcp/servers اسم بديل.
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
- تُحفظ الصور المولدة مشفّرة في مجلد المالك والموظف ويستلم سيرفر X مرجعاً محلياً مقيداً بالحساب.

متغيرات البيئة:
- ``OPENROUTER_IMAGE_MODEL`` — الافتراضي ``openai/gpt-image-1`` (اسم مؤهَّل
  بالمزوّد، وهو ما تفرضه OpenRouter).
- ``AEOS_IMAGE_MODEL``       — موديل الصور على مسار OpenAI (``gpt-image-1``؛
  أزالت OpenAI ‏dall-e-3).
- ``AEOS_IMAGE_SIZE``        — الافتراضي ``1024x1024``.
- ``AEOS_IMAGES_ENABLED``    — "0" لتعطيل التوليد.
"""
from __future__ import annotations

import logging
import os
from typing import List

from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.image")

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
        # الصورة في تايم لاين X تُعرض 16:9؛ المربّع (1024x1024 سابقاً) يُقصّ أعلاه وأسفله.
        # size صريح (مثل الصورة الشخصية 1024x1024) يتقدّم على الافتراضي.
        explicit_size = request.params.get("size") or ""
        size = explicit_size or os.getenv("AEOS_IMAGE_SIZE", "1536x1024")
        aspect = (request.params.get("aspect_ratio")
                  or (self._aspect_ratio(explicit_size) if explicit_size else "")
                  or os.getenv("AEOS_IMAGE_ASPECT", "16:9"))
        quality = request.params.get("quality") or os.getenv("AEOS_IMAGE_QUALITY", "high")

        try:
            if endpoint.is_openrouter:
                url, b64 = self._generate_openrouter(endpoint, model, prompt, aspect, quality)
            else:
                url, b64 = self._generate_openai(endpoint, model, prompt, size, quality)
        except Exception as exc:  # noqa: BLE001
            logger.warning("image generation failed (%s/%s): %s",
                           endpoint.provider, model, exc)
            return MCPResponse.fail(f"image generation failed: {exc}")

        local_path = ""
        if not url:
            if not b64:
                return MCPResponse.fail("image provider returned neither url nor b64")
            from app.aeos.security import media
            url = media.save(request.employee_id, b64)
            local_path = url

        logger.info("generated image for employee %s (%s/%s, %s, %s)",
                    request.employee_id, endpoint.provider, model, aspect, quality)
        return MCPResponse.ok(url=url, local_path=local_path, prompt=prompt, model=model,
                              aspect_ratio=aspect, quality=quality)

    # ---- providers -------------------------------------------------------
    @staticmethod
    def _generate_openai(endpoint, model: str, prompt: str, size: str, quality: str = ""):
        """مسار OpenAI الأصلي عبر الـ SDK (``/images/generations``)."""
        from openai import OpenAI

        client = OpenAI(**endpoint.client_kwargs())
        # لا نمرّر response_format: واجهة OpenAI الحالية ترفضه حتى مع dall-e-3
        # ‏(Unknown parameter، شوهد 2026-08-05). المعالجة أدناه تتعامل مع
        # الحالتين أصلاً: رابط مباشر أو b64 يُحفظ محلياً.
        extra = {"quality": quality} if quality and model.startswith("gpt-image") else {}
        result = client.images.generate(model=model, prompt=prompt[:3800], size=size, n=1, **extra)
        item = (getattr(result, "data", None) or [None])[0]
        if item is None:
            raise RuntimeError("image provider returned no data")
        return (getattr(item, "url", "") or ""), (getattr(item, "b64_json", "") or "")

    @staticmethod
    def _generate_openrouter(endpoint, model: str, prompt: str, aspect: str, quality: str = ""):
        """مسار OpenRouter: ``POST /api/v1/images`` بطلب HTTP مباشر.

        لا نستعمل الـ SDK هنا لأنه يضرب ``/images/generations`` فيعيد 404.
        الواجهة تأخذ ``aspect_ratio`` (16:9، 1:1…) و``quality`` (low/medium/high) — من
        توثيق OpenRouter؛ المزوّد الذي لا يدعم الجودة يتجاهلها.
        """
        import requests

        payload = {"model": model, "prompt": prompt[:3800]}
        if aspect:
            payload["aspect_ratio"] = aspect
        if quality:
            payload["quality"] = quality

        # جودة high بنسبة 16:9 تأخذ ~90 ثانية (توثيق OpenRouter: 94s لـ gpt-image-2)
        resp = requests.post(endpoint.images_url, headers=endpoint.auth_headers(),
                             json=payload, timeout=240)
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
