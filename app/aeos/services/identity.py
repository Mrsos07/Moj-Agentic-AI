# -*- coding: utf-8 -*-
"""هوية الحساب من البرسونا — تُطبَّق مرة واحدة عند إنشاء الموظف.

عند تفعيل موظف جديد يبقى ملف حسابه على X عاماً (اسم المستخدم الخام، بلا نبذة
ولا صورة) بينما شخصيته مكتملة التعريف. هنا نبني هوية متسقة مع البرسونا:

- الاسم المعروض  ← ``persona.display_name``.
- النبذة (bio)   ← نبذة البرسونا إن كُتبت، وإلا يصوغها الـLLM من الدور
  والاهتمامات والنبرة واللهجة (فولباك حتمي بلا LLM).
- الصورة والهيدر ← لا يُرفعان هنا أبداً: صورة لم يعاينها المالك ويعتمدها
  (identity_service) كانت تنزل على الحساب فيراها «غير التي اعتمدها». ولا يُكتب
  فوق هوية اعتمدها المالك (identity_store.has_owner_identity).

التطبيق غير حاجب: يعمل في خيط خلفي (تحديث الملف يفتح متصفحاً ~دقيقة)، يُعلَّم
في ``runtime_state.flags['identity_applied']`` فلا يتكرر، ويُعطَّل بقاعدة
``apply_identity_on_create: false``. النتيجة تصل المستخدم في شات الموظف.
"""
from __future__ import annotations

import logging
import threading
from ..security.gateway import carry
from typing import Any, Dict, Optional

logger = logging.getLogger("aeos.services.identity")

_NAME_MAX = 50    # حد X لاسم العرض
_BIO_MAX = 158    # حد X للنبذة 160 — نترك هامشاً


def _fallback_bio(persona) -> str:
    """نبذة حتمية بلا LLM: الدور + الاهتمامات بصيغة مقروءة."""
    from ..employee.news_sources import INTEREST_QUERIES  # noqa: F401  (توثيق صلة)

    interests_ar = {
        "politics": "السياسة", "economy": "الاقتصاد", "sports": "الرياضة",
        "entertainment": "الترفيه", "art": "الفن", "travel": "السفر",
        "technology": "التقنية", "religion": "الدين",
    }
    topics = "، ".join(
        interests_ar.get(i, str(i)) for i in (persona.interests or [])[:3]
    )
    bits = [persona.role or "صانع محتوى"]
    if topics:
        bits.append(f"أكتب عن {topics}")
    return " | ".join(bits)[:_BIO_MAX]


def build_identity(cfg, llm=None) -> Dict[str, str]:
    """(الاسم، النبذة) المتسقان مع البرسونا — LLM أولاً وفولباك حتمي."""
    persona = cfg.persona
    name = (persona.display_name or cfg.account_username or "").strip()[:_NAME_MAX]

    bio = (persona.bio or "").strip()
    if not bio and llm is not None and getattr(llm, "available", False):
        try:
            raw = llm.complete(
                system="أنت خبير هوية علامات شخصية على منصة X.",
                prompt=(
                    "اكتب نبذة (bio) لحساب X بحد أقصى 150 حرفاً، جذابة وواضحة، "
                    "بلا هاشتاقات وبلا إيموجي مبالغ فيه (واحد كحد أقصى)، تعكس "
                    "هذه الشخصية:\n"
                    f"- الاسم: {persona.display_name}\n"
                    f"- الدور: {persona.role}\n"
                    f"- الاهتمامات: {', '.join(persona.interests or [])}\n"
                    f"- النبرة: {persona.tone} | اللهجة: {persona.dialect or 'فصحى مبسطة'}\n"
                    "أعد النبذة فقط دون أي شرح."
                ),
                temperature=0.6, max_tokens=120,
            )
            bio = (raw or "").strip().strip('"').strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("identity bio LLM failed: %s", exc)
    if not bio:
        bio = _fallback_bio(persona)
    return {"name": name, "bio": bio[:_BIO_MAX]}


def apply_persona_identity(cfg, llm=None, repo=None) -> Dict[str, Any]:
    """طبّق اسم البرسونا ونبذتها على ملف حساب X (بلا صورة). يعيد النتيجة."""
    from app.services import x_bridge

    from . import identity_store

    identity = build_identity(cfg, llm=llm)
    if identity_store.has_owner_identity(cfg.id):
        logger.info("persona identity for @%s skipped: owner-approved identity exists",
                    cfg.account_username)
        return {"ok": False, "skipped": True, "identity": identity, "error": None}

    result = x_bridge.update_profile(
        cookie_label=cfg.cookie_label,
        name=identity["name"], bio=identity["bio"],
    )
    ok = bool(result.get("success"))
    if ok and repo is not None:
        try:
            fresh = repo.get(cfg.id)
            if fresh is not None:
                fresh.runtime_state.flags["identity_applied"] = True
                repo.save(fresh)
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist identity_applied flag")
    logger.info("persona identity for @%s: ok=%s", cfg.account_username, ok)
    return {"ok": ok, "identity": identity,
            "error": None if ok else (result.get("error") or result.get("message"))}


def apply_persona_identity_async(cfg, llm=None, repo=None) -> None:
    """التطبيق في الخلفية + إبلاغ المستخدم بالنتيجة في شات الموظف."""
    rules = cfg.business_rules or {}
    if not bool(rules.get("apply_identity_on_create", True)):
        return
    if (cfg.runtime_state.flags or {}).get("identity_applied"):
        return

    def _run() -> None:
        try:
            res = apply_persona_identity(cfg, llm=llm, repo=repo)
            from .employee_data import _push

            if res.get("skipped"):
                return
            if res["ok"]:
                ident = res["identity"]
                _push(cfg.user_id,
                      f"🪪 **جهّزت اسم حسابي ونبذته بما يعكس شخصيتي:**\n"
                      f"- الاسم: {ident['name']}\n- النبذة: {ident['bio']}\n"
                      "🖼️ الصورة والهيدر لا أغيّرهما دون موافقتك: من بطاقتي اضغط "
                      "«هوية X من الشخصية» لتعاينهما، ثم «تطبيق على X» لاعتمادهما.",
                      cfg)
            else:
                _push(cfg.user_id,
                      "⚠️ لم أستطع تحديث هوية الحساب الآن "
                      f"({str(res.get('error'))[:120]}) — سأبقى أعمل طبيعياً، "
                      "ويمكنك إعادة المحاولة من إعداداتي.",
                      cfg)
        except Exception:  # noqa: BLE001
            logger.exception("apply_persona_identity_async failed for %s", cfg.id)

    threading.Thread(target=carry(_run), name=f"identity-{cfg.account_username}",
                     daemon=True).start()
