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
import threading
import time
import uuid
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from ..employee import voice_profile as VP

logger = logging.getLogger("aeos.services.persona_clone")

_TONES = ("professional", "casual", "witty", "formal", "friendly")

#: عيّنة الاستنساخ (قرار المالك 2026-11-01): **70 منشوراً أصلياً فقط** من ملف الحساب — بلا تبويب
#: الردود وبلا بحث في فترات أقدم. البصمة تُبنى من التغريدات وحدها (نواه create_clone)، والردود
#: لا تدخل لا في القياس ولا في الأمثلة. (النسخة السابقة «العميقة»: 100 تغريدة + 400 رد + 4 فترات
#: تاريخية، ~2-4 دقائق سحب؛ الآن سحب تبويب واحد.)
SAMPLE = {"limit": 70, "replies_limit": 0, "tab_budget_seconds": 45.0}
#: اسم قديم أبقيناه لمن يستورده
DEEP = {**SAMPLE, "replies_budget_seconds": 0.0, "history_budget_seconds": 0.0, "history_per_window": 0}
#: فترات البحث في التاريخ: (بداية قبل كم يوم، طول الفترة بالأيام) — لم تعد تُستعمل افتراضياً
HISTORY_OFFSETS = ((75, 20), (165, 20), (255, 20), (350, 20))


def history_windows(today: Optional[date] = None) -> List[List[str]]:
    today = today or date.today()
    return [[(today - timedelta(days=start)).isoformat(),
             (today - timedelta(days=start - span)).isoformat()] for start, span in HISTORY_OFFSETS]


# ─────────────────────────── scraping ───────────────────────────
def scrape_profile(cookie_label: str, handle: str, limit: int = SAMPLE["limit"],
                   replies_limit: int = SAMPLE["replies_limit"], headless: bool = True,
                   lock_timeout: float = 45.0,
                   tab_budget_seconds: float = SAMPLE["tab_budget_seconds"],
                   deep: bool = False) -> Dict[str, Any]:
    """اسحب منشورات الحساب الأصلية (حتى ``limit``). ``deep=True`` يعيد الوضع القديم (ردود + تاريخ)."""
    from ..mcp.base import MCPRequest
    from ..mcp.servers.playwright_x_mcp import PlaywrightXMCP

    limit = max(5, min(int(limit), SAMPLE["limit"])) if not deep else int(limit)
    params = {"handle": handle, "limit": limit, "replies_limit": replies_limit if deep else 0,
              "headless": headless, "lock_timeout": lock_timeout,
              "tab_budget_seconds": tab_budget_seconds, "include_replies": bool(deep)}
    if deep:
        params.update({"replies_budget_seconds": 120.0, "history_windows": history_windows(),
                       "history_budget_seconds": 10.0, "history_per_window": 40})
    res = PlaywrightXMCP().call(MCPRequest(
        employee_id="persona_clone", tool="read_user_profile",
        params=params, context={"cookie_label": cookie_label},
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
    if a.get("identity_core"):
        parts.append("ثوابتك: " + "؛ ".join(a["identity_core"]) + ".")
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
        # نفس خطة الرد التي يستعملها الموظف فعلاً (النوع والطول والأمثلة المطابقة)
        plan = VP.reply_plan(voice, parent, seed="fidelity")
        text = llm.complete(
            _persona_system(persona, voice, "reply") + "\n" + plan["block"],
            f"اكتب ردّك على هذه التغريدة: «{parent}».\n{plan['length_line']}"
            "اكتب الرد فقط.",
            temperature=0.8, max_tokens=200) or ""
        if text.strip():
            replies.append(text.strip().strip('"«»'))
    out = {**VP.fidelity(voice, posts, replies),
           "samples": {"posts": posts, "reply": {"to": parent, "text": replies[0] if replies else ""}}}
    # درجة نواه الحتمية للمنشورات المولَّدة مقابل البصمة نفسها (إن بُنيت بنواه)
    nawah_id = (voice.get("nawah") or {}).get("id")
    if nawah_id and posts:
        from . import nawah_clone as NC

        scores = [s for s in (NC.score(nawah_id, p) for p in posts) if s is not None]
        if scores:
            out["nawah_score"] = round(sum(scores) / len(scores))
    return out


# ─────────────────────────── entry points ───────────────────────────
def build_cloned_persona(profile: Dict[str, Any], llm: Any, language: str = "ar",
                         test: bool = True, cookie_label: str = "") -> Dict[str, Any]:
    if not (profile.get("tweets") or profile.get("replies")):
        raise RuntimeError(
            f"لم أجد أي تغريدات لحساب {profile.get('handle', '')} — تحقّق من صحة "
            "اليوزر، أو أن الحساب محمي/فارغ، أو أن كوكيز حسابك انتهت صلاحيتها.")
    # البصمة الأسلوبية من خادم نواه (المسار الأساسي)؛ المحلّل القديم احتياط معلَن فقط
    from . import nawah_clone as NC

    nawah = NC.builder(cookie_label) if cookie_label and NC.enabled() else None
    voice = VP.analyze(profile, llm, nawah=nawah)
    persona = persona_from_voice(profile, voice, language)
    result = {"source": voice.get("source") or ("ai" if voice.get("analysis") else "heuristic"),
              "persona": persona, "voice_profile": voice}
    if voice.get("nawah_error"):
        result["nawah_error"] = voice["nawah_error"]
    elif nawah is None and cookie_label:
        result["nawah_error"] = NC.status()["reason"]
    if test:
        try:
            result["fidelity"] = fidelity_test(profile, voice, persona, llm)
        except Exception:  # noqa: BLE001 - الاختبار معلومة إضافية لا شرط
            logger.exception("fidelity test failed")
    return result


def clone_persona(cookie_label: str, handle: str, llm: Any, limit: int = SAMPLE["limit"],
                  language: str = "ar", lock_timeout: float = 45.0,
                  tab_budget_seconds: float = SAMPLE["tab_budget_seconds"],
                  replies_limit: int = SAMPLE["replies_limit"], test: bool = True,
                  deep: bool = False,
                  progress: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """التدفّق الكامل: سحب ← بصمة صوت ← برسونا ← اختبار مطابقة."""
    step = progress or (lambda _msg: None)
    step(f"سحب آخر {min(int(limit), SAMPLE['limit'])} منشوراً أصلياً من @{handle} (بلا ردود)…")
    profile = scrape_profile(cookie_label, handle, limit=limit, replies_limit=replies_limit,
                             lock_timeout=lock_timeout, tab_budget_seconds=tab_budget_seconds,
                             deep=deep)
    cov = profile.get("coverage") or {}
    step(f"سُحب {profile.get('tweet_count', 0)} منشوراً أصلياً من {cov.get('from', '?')} إلى "
         f"{cov.get('to', '?')} — بناء بصمة الصوت عبر نواه ثم اختبار المطابقة…")
    result = build_cloned_persona(profile, llm, language=language, test=test, cookie_label=cookie_label)
    sample = (result.get("voice_profile") or {}).get("sample", {})
    result["target"] = {
        "handle": profile.get("handle", f"@{handle}"),
        "display_name": profile.get("display_name", ""),
        "bio": profile.get("bio", ""),
        "followers": profile.get("followers", ""),
        "tweet_count": profile.get("tweet_count", 0),
        "reply_count": profile.get("reply_count", 0),
        "pairs": sample.get("pairs", 0),
        "analyzed_replies": sample.get("analyzed_replies", 0),
        "coverage": cov,
        "scrape_seconds": profile.get("scrape_seconds"),
    }
    return result


def _owner_lines(instructions: str) -> List[str]:
    """توجيهات علّمها المالك للموظف (``_handle_teach`` يضيفها أسطراً تبدأ بـ«- »)."""
    return [ln for ln in (instructions or "").splitlines() if ln.strip().startswith("- ")]


def apply_to_employee(cfg, persona: Dict[str, Any], voice: Dict[str, Any], repo) -> None:
    """احفظ البرسونا والبصمة معاً. الاسم/النبذة الفارغان يُبقيان قيم الموظف الحالية.

    إعادة الاستنساخ تستبدل وصف الهوية، لكن **توجيهات المالك** المضافة بعده تبقى: كانت
    تُمسح مع التعليمات القديمة دون أن يعلم المالك.
    """
    from ..employee.models import Persona

    current = cfg.persona.to_dict() if cfg.persona else {}
    merged = {**current, **{k: v for k, v in persona.items() if v not in ("", None, [])}}
    kept = [ln for ln in _owner_lines(current.get("instructions", ""))
            if ln not in (merged.get("instructions") or "")]
    if kept:
        merged["instructions"] = ((merged.get("instructions") or "").rstrip() + "\n" + "\n".join(kept)).strip()
    cfg.persona = Persona.from_dict(merged)
    cfg.business_rules = {**(cfg.business_rules or {}), VP.STORE_KEY: voice}
    repo.save(cfg)


# ─────────────────────────── background jobs ───────────────────────────
#: الاستنساخ العميق يستغرق دقائق: طلب HTTP متزامن كان يصطدم بمهلة الواجهة (240-300 ث)
_JOBS: Dict[str, Dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_JOB_TTL = 3600


def start_job(owner_id: Any, run: Callable[[Callable[[str], None]], Dict[str, Any]]) -> str:
    """شغّل ``run(progress)`` في الخلفية وأعد معرّف المهمة. النتيجة/الخطأ يُقرآن بـ``get_job``."""
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "owner": owner_id, "status": "running", "progress": "بدأ الاستنساخ…",
           "started": time.time(), "result": None, "error": ""}
    with _JOBS_LOCK:
        for k in [k for k, v in _JOBS.items() if time.time() - v["started"] > _JOB_TTL]:
            _JOBS.pop(k, None)
        _JOBS[job_id] = job

    def progress(msg: str) -> None:
        job["progress"] = msg

    def worker() -> None:
        try:
            job["result"] = run(progress)
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - يُعرض للمالك كما هو
            logger.exception("persona clone job %s failed", job_id)
            job["error"] = str(exc) or exc.__class__.__name__
            job["status"] = "error"

    threading.Thread(target=worker, daemon=True, name=f"clone-{job_id}").start()
    return job_id


def get_job(job_id: str, owner_id: Any) -> Optional[Dict[str, Any]]:
    """حالة المهمة لمالكها فقط (مستخدم آخر لا يرى نتيجة استنساخ غيره)."""
    job = _JOBS.get(job_id)
    if not job or job["owner"] != owner_id:
        return None
    return {k: job[k] for k in ("id", "status", "progress", "result", "error")} | {
        "elapsed": round(time.time() - job["started"])}
