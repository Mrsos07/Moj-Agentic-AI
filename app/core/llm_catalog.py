"""
كتالوج مزوّدي النماذج الذين يربطهم المستخدم من إعداداته: OpenRouter · OpenAI · Claude · Google · Ollama.

كلهم يُستدعَون عبر حزمة ``openai`` نفسها بتغيير ``base_url`` فقط (متحقَّق حيّاً لـ Gemini
وOllama مع استدعاء الأدوات، ومن توثيق Anthropic لطبقة التوافق ``/v1/``). الفرق في:
- **سرد النماذج**: OpenAI/Google/OpenRouter عبر ``GET /models`` متوافق؛ Claude عبر واجهته
  الأصلية (``x-api-key`` + ``anthropic-version``)؛ Ollama عبر ``/api/tags``.
- **أسماء النماذج**: Google يعيد ``models/gemini-…`` (يُنزع البادئة)، OpenRouter مؤهّلة
  ``openai/gpt-4o-mini``، والبقية أسماء المزوّد مباشرة.
- **Ollama** يعمل على جهاز الخادم: عنوانه ثابت من الإعداد (``OLLAMA_BASE_URL``، افتراضياً
  127.0.0.1:11434) ولا يستطيع المستخدم تغييره — وإلا صار حقل «العنوان» بوابة لطلبات
  الخادم إلى عناوينه الداخلية.

لا تُقرأ مفاتيح المستخدم هنا: تُمرَّر من ``app.services.user_llm`` (مشفّرة في القاعدة).
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional

import requests

_TIMEOUT = 20


def _ollama_base() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")


PROVIDERS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "needs_key": True,
        "default_model": "openai/gpt-4o-mini", "key_hint": "sk-or-v1-…",
        "note": "المزوّد الافتراضي للنظام — بلا مفتاح تُستخدم إعدادات النظام.",
    },
    "openai": {
        "label": "OpenAI", "base_url": "https://api.openai.com/v1", "needs_key": True,
        "default_model": "gpt-4o-mini", "key_hint": "sk-…",
    },
    "anthropic": {
        "label": "Claude (Anthropic)", "base_url": "https://api.anthropic.com/v1/", "needs_key": True,
        "default_model": "claude-sonnet-4-5", "key_hint": "sk-ant-…",
        "note": "عبر طبقة التوافق مع OpenAI من Anthropic (استدعاء الأدوات مدعوم).",
    },
    "google": {
        "label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "needs_key": True, "default_model": "gemini-2.5-flash", "key_hint": "AIza…",
    },
    "ollama": {
        "label": "Ollama (محلي)", "base_url": None, "needs_key": False,
        "default_model": "", "key_hint": "",
        "note": "نماذج تعمل على جهاز الخادم نفسه — بلا مفتاح وبلا تكلفة.",
    },
}

# نماذج ليست للدردشة/الأدوات (صور، صوت، تضمين، …) — لا تُعرض في القائمة
_NON_CHAT = re.compile(r"(?i)(embed|whisper|tts|audio|realtime|transcribe|dall-?e|image|moderation|"
                       r"babbage|davinci|search|computer-use|aqa|veo|imagen|lyria|live|robotics|"
                       r"\bvision\b|-vision|guard)")


def base_url(provider: str) -> str:
    if provider == "ollama":
        return _ollama_base() + "/v1"
    return PROVIDERS[provider]["base_url"]


def client_kwargs(provider: str, api_key: str) -> Dict[str, Any]:
    """وسائط ``openai.OpenAI`` لهذا المزوّد (Ollama يقبل أي مفتاح غير فارغ)."""
    return {"api_key": api_key or ("ollama" if provider == "ollama" else ""), "base_url": base_url(provider)}


def list_models(provider: str, api_key: str = "") -> List[str]:
    """نماذج الدردشة المتاحة بهذا المفتاح. يرفع ``ValueError`` برسالة واضحة عند الفشل."""
    if provider not in PROVIDERS:
        raise ValueError(f"مزوّد غير معروف: {provider}")
    if PROVIDERS[provider]["needs_key"] and not api_key:
        raise ValueError("أدخل مفتاح المزوّد أولاً")
    try:
        if provider == "ollama":
            r = requests.get(_ollama_base() + "/api/tags", timeout=5)
            r.raise_for_status()
            ids = [m.get("name", "") for m in r.json().get("models") or []]
        elif provider == "anthropic":
            r = requests.get("https://api.anthropic.com/v1/models?limit=100", timeout=_TIMEOUT,
                             headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"})
            r.raise_for_status()
            ids = [m.get("id", "") for m in r.json().get("data") or []]
        else:
            r = requests.get(base_url(provider).rstrip("/") + "/models", timeout=_TIMEOUT,
                             headers={"Authorization": f"Bearer {api_key}"})
            r.raise_for_status()
            ids = [m.get("id", "") for m in r.json().get("data") or []]
    except requests.HTTPError as exc:
        code = getattr(exc.response, "status_code", 0)
        raise ValueError({401: "المفتاح غير صحيح (401)", 403: "المفتاح لا يملك صلاحية (403)"}.get(
            code, f"رفض المزوّد الطلب (HTTP {code})")) from None
    except requests.RequestException as exc:
        if provider == "ollama":
            raise ValueError(f"Ollama لا يعمل على {_ollama_base()} — شغّله ثم أعد المحاولة") from None
        raise ValueError(f"تعذّر الاتصال بالمزوّد ({type(exc).__name__})") from None
    ids = [i.removeprefix("models/") for i in ids if i]
    if provider in ("openai",):
        ids = [i for i in ids if re.match(r"(?i)^(gpt-|o\d|chatgpt-)", i)]
    elif provider == "google":
        ids = [i for i in ids if i.lower().startswith("gemini")]
    if provider != "ollama":
        ids = [i for i in ids if not _NON_CHAT.search(i)]
    return sorted(dict.fromkeys(ids))


def test(provider: str, api_key: str, model: str) -> Dict[str, Any]:
    """نداء دردشة صغير فعلي (بضعة رموز) — يكشف ما لا يكشفه سرد النماذج: نفاد الرصيد،
    نموذج غير متاح للحساب. قيس: مفتاح OpenAI يسرد 141 نموذجاً ثم يفشل كل نداء بـ 429."""
    from openai import OpenAI

    model = model or PROVIDERS[provider]["default_model"]
    if not model:
        raise ValueError("اختر نموذجاً للاختبار")
    try:
        client = OpenAI(**client_kwargs(provider, api_key), timeout=60)
        r = client.chat.completions.create(model=model, max_tokens=8,
                                           messages=[{"role": "user", "content": "Reply with: ok"}])
        return {"ok": True, "model": model, "reply": (r.choices[0].message.content or "").strip()[:40]}
    except Exception as exc:  # noqa: BLE001 - نترجم أخطاء المزوّد لرسالة مفهومة
        text = str(exc)
        status = getattr(exc, "status_code", 0)
        if status == 429 or "quota" in text.lower() or "credit" in text.lower():
            msg = "الحساب بلا رصيد أو تجاوز الحد (429) — أضف رصيداً لدى المزوّد"
        elif status in (401, 403):
            msg = f"المفتاح مرفوض ({status})"
        elif status == 404 or "not found" in text.lower():
            msg = f"النموذج «{model}» غير متاح لهذا الحساب"
        else:
            msg = f"فشل النداء: {type(exc).__name__}"
        raise ValueError(msg) from None


def public_catalog() -> List[Dict[str, Any]]:
    return [{"id": k, "label": v["label"], "needs_key": v["needs_key"], "default_model": v["default_model"],
             "key_hint": v.get("key_hint", ""), "note": v.get("note", "")} for k, v in PROVIDERS.items()]


def provider_of(name: Optional[str]) -> Optional[str]:
    return name if name in PROVIDERS else None
