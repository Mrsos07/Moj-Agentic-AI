"""
Persona cloning service — استنساخ **هوية** حساب X بعمق، لا عيّنة سطحية.

التدفّق:
1. سحب البروفايل بكوكيز حساب الموظف: تغريدات أصلية **مع تفاعلها وتوقيتها**،
   وردود **مع التغريدة التي ردّ عليها** (أزواج سياق) — ``read_user_profile``.
2. بناء «بصمة الصوت» (:mod:`app.aeos.employee.voice_profile`): قياسات حتمية +
   توزيع مواضيع بالتضمينات + تحليل عميق بالنموذج (الصوت، المحاور، دليل الردود،
   طريقة التفاعل، المعجم، القيم) + أمثلة مرجعية.
3. برسونا مشتقة من البصمة، واختبار مطابقة: نولّد منشورين ورداً بالهوية الجديدة
   ونقيسها على أسلوب الأصل (الطول/الإيموجي/اللهجة/عدم النسخ).

النتيجة **مقترح** يراجعه المالك قبل الحفظ (أو ``apply`` صريح من الواجهة/الشات).

الهوية لا الانتحال: لا يُنقل اسم الحساب الأصلي ولا نبذته، والتعليمات تمنع ادعاء
أنه هو أو إعادة نشر نصوصه.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from ..employee import voice_profile as VP

logger = logging.getLogger("aeos.services.persona_clone")

_TONES = ("professional", "casual", "witty", "formal", "friendly")


# ─────────────────────────── scraping ───────────────────────────
def scrape_profile(cookie_label: str, handle: str, limit: int = 40,
                   replies_limit: int = 30, headless: bool = True,
                   lock_timeout: float = 45.0,
                   tab_budget_seconds: float = 30.0) -> Dict[str, Any]:
    """اسحب تغريدات الحساب وردوده (مع سياقها) بميزانية زمنية صريحة لكل تبويب."""
    from ..mcp.base import MCPRequest
    from ..mcp.servers.playwright_x_mcp import PlaywrightXMCP

    res = PlaywrightXMCP().call(MCPRequest(
        employee_id="persona_clone", tool="read_user_profile",
        params={"handle": handle, "limit": limit, "replies_limit": replies_limit,
                "headless": headless, "lock_timeout": lock_timeout,
                "tab_budget_seconds": tab_budget_seconds},
        context={"cookie_label": cookie_label},
    ))
    if not res.success:
        raise RuntimeError(res.error or "failed to scrape target profile")
    return dict(res.data)


# ─────────────────────────── persona from voice ───────────────────────────
def _instructions(handle: str, voice: Dict[str, Any]) -> str:
    a = voice.get("analysis") or {}
    st = voice.get("style") or {}
    parts = []
    if a.get("voice_summary"):
        parts.append(a["voice_summary"])
    if a.get("pillars"):
        parts.append("تدور منشوراتك حول: " + "، ".join(p["name"] for p in a["pillars"]) + ".")
    inter = a.get("interaction") or {}
    if inter.get("engages_with") or inter.get("initiates"):
        parts.append(" ".join(x for x in (inter.get("engages_with"), inter.get("initiates")) if x))
    if st.get("dialect_guess"):
        parts.append(f"تكتب بلهجة {st['dialect_guess']}.")
    parts.append(
        f"أسلوبك مستلهم من حساب {handle}: حاكِ صوته وطريقة تفكيره وتفاعله، لكن لا تدّعِ "
        "أنك هو، ولا تستخدم اسمه، ولا تعيد نشر نصوصه — اكتب محتوى جديداً دائماً.")
    return " ".join(parts)[:1500]


def _fallback_rules(st: Dict[str, Any]) -> List[str]:
    rules = []
    if st.get("len_median"):
        rules.append(f"اكتب بطول يقارب {st['len_median']} حرفاً")
    rules.append("استخدم الإيموجي كعادة الحساب" if st.get("emoji_rate", 0) >= 0.5
                 else "لا تستخدم الإيموجي" if st.get("emoji_rate", 0) <= 0.1
                 else "استخدم الإيموجي باعتدال")
    if st.get("question_rate", 0) >= 0.2:
        rules.append("اطرح أسئلة تحفّز النقاش كما يفعل الحساب الأصلي")
    if st.get("signature_phrases"):
        rules.append("استخدم عباراته المميّزة بشكل طبيعي: " + "، ".join(st["signature_phrases"][:4]))
    return rules


def persona_from_voice(profile: Dict[str, Any], voice: Dict[str, Any],
                       language: str = "ar") -> Dict[str, Any]:
    a = voice.get("analysis") or {}
    st = voice.get("style") or {}
    p = a.get("persona") or {}
    handle = profile.get("handle", "")
    interests = VP.interests_from_topics(voice.get("topics") or {})
    arabic = sum(1 for t in profile.get("tweets") or [] if re.search(r"[\u0600-\u06FF]", t.get("text", "")))
    lang = "ar" if arabic >= len(profile.get("tweets") or []) / 2 else "en"
    return {
        # الاسم والنبذة لا يُنقلان: هوية الحساب الأصلي ليست ملكاً لنا (انتحال)
        "display_name": "",
        "role": p.get("role") or "صانع محتوى",
        "bio": "",
        "tone": p.get("tone") if p.get("tone") in _TONES else "casual",
        "language": lang or language,
        "dialect": p.get("dialect") or st.get("dialect_guess", ""),
        "values": a.get("values") or [],
        "dos": p.get("dos") or _fallback_rules(st),
        "donts": (p.get("donts") or []) + ["لا تدّعِ أنك صاحب الحساب الأصلي ولا تنسخ نصوصه"],
        "interests": interests,
        "instructions": _instructions(handle, voice),
    }


# ─────────────────────────── fidelity test ───────────────────────────
def _persona_system(persona: Dict[str, Any], voice: Dict[str, Any], kind: str) -> str:
    bits = [f"أنت {persona.get('role')}. اللغة: {persona.get('language')}.",
            persona.get("instructions", "")]
    if persona.get("dialect"):
        bits.append(f"اللهجة الإلزامية: {persona['dialect']}.")
    if persona.get("dos"):
        bits.append("افعل: " + "؛ ".join(persona["dos"]))
    if persona.get("donts"):
        bits.append("لا تفعل: " + "؛ ".join(persona["donts"]))
    bits.append(VP.voice_block(voice, kind))
    return "\n".join(b for b in bits if b)


def _held_out_parent(profile: Dict[str, Any], voice: Dict[str, Any]) -> str:
    used = {p["to"] for p in (voice.get("exemplars") or {}).get("replies") or []}
    for r in profile.get("replies") or []:
        ctx = r.get("in_reply_to") or {}
        if ctx.get("text") and not ctx.get("self") and ctx["text"][:280] not in used:
            return ctx["text"][:280]
    return next(iter(used), "")


def fidelity_test(profile: Dict[str, Any], voice: Dict[str, Any],
                  persona: Dict[str, Any], llm: Any) -> Dict[str, Any]:
    """منشوران ورد بالهوية الجديدة + قياس مطابقتها للأصل. بلا نموذج: لا اختبار."""
    if not (llm and getattr(llm, "available", False)):
        return {}
    pillars = [p["name"] for p in (voice.get("analysis") or {}).get("pillars") or []] or \
              [k for k in (voice.get("topics") or {})][:2] or ["موضوع من اهتماماتك"]
    posts: List[str] = []
    # نفس قواعد الطول/الإيموجي التي يفرضها موجّه الكتابة الحقيقي — وإلا قاس
    # الاختبار نموذجاً بلا قيود وظلم الهوية (قيس: عيّنات أطول من عادته)
    rules = VP.post_rules(voice)
    for topic in pillars[:2]:
        text = llm.complete(
            _persona_system(persona, voice, "post"),
            f"اكتب تغريدة واحدة جاهزة للنشر عن: {topic}.\n{rules['length']}\n{rules['emoji']}\n"
            "اكتب المحتوى فقط.",
            temperature=0.8, max_tokens=300) or ""
        if text.strip():
            posts.append(text.strip().strip('"«»'))
    replies: List[str] = []
    parent = _held_out_parent(profile, voice)
    if parent:
        text = llm.complete(
            _persona_system(persona, voice, "reply"),
            f"اكتب ردّك على هذه التغريدة: «{parent}».\n{VP.reply_length_line(voice, False)}"
            "اكتب الرد فقط.",
            temperature=0.8, max_tokens=150) or ""
        if text.strip():
            replies.append(text.strip().strip('"«»'))
    return {**VP.fidelity(voice, posts, replies),
            "samples": {"posts": posts, "reply": {"to": parent, "text": replies[0] if replies else ""}}}


# ─────────────────────────── entry points ───────────────────────────
def build_cloned_persona(profile: Dict[str, Any], llm: Any, language: str = "ar",
                         test: bool = True) -> Dict[str, Any]:
    if not (profile.get("tweets") or profile.get("replies")):
        raise RuntimeError(
            f"لم أجد أي تغريدات لحساب {profile.get('handle', '')} — تحقّق من صحة "
            "اليوزر، أو أن الحساب محمي/فارغ، أو أن كوكيز حسابك انتهت صلاحيتها.")
    voice = VP.analyze(profile, llm)
    persona = persona_from_voice(profile, voice, language)
    result = {"source": "ai" if voice.get("analysis") else "heuristic",
              "persona": persona, "voice_profile": voice}
    if test:
        try:
            result["fidelity"] = fidelity_test(profile, voice, persona, llm)
        except Exception:  # noqa: BLE001 - الاختبار معلومة إضافية لا شرط
            logger.exception("fidelity test failed")
    return result


def clone_persona(cookie_label: str, handle: str, llm: Any, limit: int = 40,
                  language: str = "ar", lock_timeout: float = 45.0,
                  tab_budget_seconds: float = 30.0, replies_limit: int = 30,
                  test: bool = True) -> Dict[str, Any]:
    """التدفّق الكامل: سحب ← بصمة صوت ← برسونا ← اختبار مطابقة."""
    profile = scrape_profile(cookie_label, handle, limit=limit, replies_limit=replies_limit,
                             lock_timeout=lock_timeout, tab_budget_seconds=tab_budget_seconds)
    result = build_cloned_persona(profile, llm, language=language, test=test)
    result["target"] = {
        "handle": profile.get("handle", f"@{handle}"),
        "display_name": profile.get("display_name", ""),
        "bio": profile.get("bio", ""),
        "followers": profile.get("followers", ""),
        "tweet_count": profile.get("tweet_count", 0),
        "reply_count": profile.get("reply_count", 0),
        "pairs": (result.get("voice_profile") or {}).get("sample", {}).get("pairs", 0),
        "scrape_seconds": profile.get("scrape_seconds"),
    }
    return result


def apply_to_employee(cfg, persona: Dict[str, Any], voice: Dict[str, Any], repo) -> None:
    """احفظ البرسونا والبصمة معاً. الاسم/النبذة الفارغان يُبقيان قيم الموظف الحالية."""
    from ..employee.models import Persona

    current = cfg.persona.to_dict() if cfg.persona else {}
    merged = {**current, **{k: v for k, v in persona.items() if v not in ("", None, [])}}
    cfg.persona = Persona.from_dict(merged)
    cfg.business_rules = {**(cfg.business_rules or {}), VP.STORE_KEY: voice}
    repo.save(cfg)
