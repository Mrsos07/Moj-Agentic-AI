"""
حلّ مزوّد الـ LLM في مكان واحد: OpenRouter أولاً، ثم OpenAI كاحتياط.

لماذا وحدة مركزية بدل تكرار المنطق؟ المشروع يستدعي الـ LLM من ثمانية أماكن
مختلفة (مزوّد الاستدلال، شات الـ WebSocket، وكيل الترندات، مصنّف الترندات،
ذاكرة الرسم البياني، توليد الصور، إعداد الوكلاء) وبعضها يقرأ ``settings``
وبعضها ``os.environ`` مباشرة. لو تكرّر منطق «أي مفتاح؟ أي base_url؟ أي اسم
موديل؟» في كل ملف، فأي تغيير في المزوّد يتطلب ثمانية تعديلات متزامنة — وواحد
منسي يعني نداءً صامتاً لمزوّد خاطئ.

نقاط أساسية عن OpenRouter (متحقَّق منها من التوثيق، لا تخمين):
- ``/chat/completions`` و ``/embeddings`` و ``/responses`` متوافقة مع OpenAI
  شكلاً، فيكفي تمرير ``base_url`` لعميل الـ SDK.
- توليد الصور **ليس** متوافقاً: المسار ``POST /api/v1/images`` لا
  ``/images/generations``، والرد في ``data[0].b64_json``. لذلك
  ``image_mcp`` يستخدم طلب HTTP مباشر عند OpenRouter.
- أسماء الموديلات مؤهَّلة بالمزوّد (``openai/gpt-4o-mini``) وليست ``gpt-4``.
  لهذا لا نمرّر ``OPENAI_MODEL`` إلى OpenRouter أبداً: الاسم غير المؤهَّل
  يُرفض، وهو فشل صامت يعيد نصّاً فارغاً من كل مسارات الاستدلال.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# نفس الموديل الذي كانت تستخدمه Graphiti عبر OpenAI مباشرة. الإبقاء عليه
# مقصود: تغيير موديل الـ embeddings يغيّر أبعاد المتجهات، فتصبح كل عقد الرسم
# البياني المخزّنة غير قابلة للبحث الدلالي وتحتاج إعادة بناء كاملة.
DEFAULT_EMBEDDING_MODEL_OPENROUTER = "openai/text-embedding-3-small"
DEFAULT_EMBEDDING_MODEL_OPENAI = "text-embedding-3-small"

DEFAULT_IMAGE_MODEL_OPENROUTER = "openai/gpt-image-1"
DEFAULT_IMAGE_MODEL_OPENAI = "gpt-image-1"


def _setting(name: str) -> str:
    """اقرأ من البيئة ثم من ``settings`` (pydantic .env).

    الترتيب مهم: بعض العمليات الفرعية (سيرفر xsuite، عملية Graphiti المعزولة)
    تتلقى المفاتيح عبر البيئة فقط ولا تُحمّل ``settings`` أصلاً.
    """
    value = (os.getenv(name) or "").strip()
    if value:
        return value
    try:
        from app.core.config import settings

        return (getattr(settings, name, "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _float_setting(name: str, default: float) -> float:
    try:
        return float(_setting(name) or default)
    except (TypeError, ValueError):
        return default


def _int_setting(name: str, default: int) -> int:
    try:
        return int(_setting(name) or default)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LLMEndpoint:
    """كل ما يحتاجه أي نداء LLM: مزوّد + مفتاح + عنوان + أسماء موديلات."""

    provider: str          # "openrouter" | "openai" | "none"
    api_key: str
    base_url: Optional[str]  # None ⇒ العنوان الافتراضي لـ OpenAI SDK
    model: str
    embedding_model: str
    image_model: str
    max_tokens: int
    temperature: float

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self.provider != "none"

    @property
    def is_openrouter(self) -> bool:
        return self.provider == "openrouter"

    @property
    def chat_url(self) -> str:
        """عنوان ``/chat/completions`` كاملاً — لمن يستخدم HTTP خاماً."""
        base = self.base_url or "https://api.openai.com/v1"
        return f"{base.rstrip('/')}/chat/completions"

    @property
    def images_url(self) -> str:
        """توليد الصور: المسار يختلف بين المزوّدين، وهذا ليس تفصيلاً تجميلياً.

        OpenRouter: ``/images``.  OpenAI: ``/images/generations``.
        """
        base = self.base_url or "https://api.openai.com/v1"
        base = base.rstrip("/")
        return f"{base}/images" if self.is_openrouter else f"{base}/images/generations"

    @property
    def extra_headers(self) -> Dict[str, str]:
        """ترويسات OpenRouter الاختيارية (تُظهر التطبيق في لوحة الاستخدام)."""
        if not self.is_openrouter:
            return {}
        headers: Dict[str, str] = {}
        referer = _setting("OPENROUTER_SITE_URL")
        title = _setting("OPENROUTER_APP_NAME")
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
        return headers

    def auth_headers(self) -> Dict[str, str]:
        """ترويسات كاملة لطلب HTTP خام."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self.extra_headers)
        return headers

    def client_kwargs(self) -> Dict[str, Any]:
        """وسائط بناء ``OpenAI`` / ``AsyncOpenAI`` من حزمة openai."""
        kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        headers = self.extra_headers
        if headers:
            kwargs["default_headers"] = headers
        return kwargs


def resolve_llm(model: Optional[str] = None) -> LLMEndpoint:
    """اختر المزوّد الفعّال. OpenRouter أولاً، ثم OpenAI، ثم لا شيء.

    Args:
        model: تجاوز صريح لاسم الموديل (يُستخدم كما هو دون تأهيل).
    """
    max_tokens = _int_setting("OPENAI_MAX_TOKENS", 2000)
    temperature = _float_setting("OPENAI_TEMPERATURE", 0.7)

    or_key = _setting("OPENROUTER_API_KEY")
    if or_key:
        return LLMEndpoint(
            provider="openrouter",
            api_key=or_key,
            base_url=_setting("OPENROUTER_BASE_URL") or OPENROUTER_BASE_URL,
            model=model or _setting("OPENROUTER_MODEL") or DEFAULT_OPENROUTER_MODEL,
            embedding_model=(_setting("OPENROUTER_EMBEDDING_MODEL")
                             or DEFAULT_EMBEDDING_MODEL_OPENROUTER),
            image_model=(_setting("OPENROUTER_IMAGE_MODEL")
                         or DEFAULT_IMAGE_MODEL_OPENROUTER),
            max_tokens=max_tokens,
            temperature=temperature,
        )

    oa_key = _setting("OPENAI_API_KEY")
    if oa_key:
        return LLMEndpoint(
            provider="openai",
            api_key=oa_key,
            base_url=_setting("OPENAI_BASE_URL") or None,
            model=model or _setting("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
            embedding_model=(_setting("OPENAI_EMBEDDING_MODEL")
                             or DEFAULT_EMBEDDING_MODEL_OPENAI),
            image_model=_setting("AEOS_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL_OPENAI,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    return LLMEndpoint(
        provider="none", api_key="", base_url=None,
        model=model or DEFAULT_OPENROUTER_MODEL,
        embedding_model=DEFAULT_EMBEDDING_MODEL_OPENROUTER,
        image_model=DEFAULT_IMAGE_MODEL_OPENROUTER,
        max_tokens=max_tokens, temperature=temperature,
    )


def llm_available() -> bool:
    return resolve_llm().available
