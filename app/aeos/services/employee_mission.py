"""
مهمّة موجّهة **حرّة** من الشات — نقاش مفتوح ينتهي بتنفيذ فعلي.

المسارات القديمة كانت ضيّقة: «انشر عن 2» (موضوع مرقّم) أو «تفاعل مع هذا المحتوى»
(محتوى ملصوق). أي طلب طبيعي آخر — «روح للتايم لاين وشوف تغريدة تناسب تخصصك وردّ
عليها»، «تابع حسابين في مجالك»، «اقتبس أقوى تغريدة في الترند» — كان يسقط في ردّ
«خارج النطاق» بلا تنفيذ.

هنا نحوّل الجملة الحرّة إلى **مهمّة مُهيكلة** يفهمها العقل الإدراكي:
``{source, actions, query, count, scope}`` ثم نشغّل دورة موجّهة فورية. الموظف هو
من يختار الهدف بنفسه (لا المستخدم)، وهذا هو الفرق عن التفاعل الموجّه القديم.

التحليل طبقتان: قواعد حتمية (تعمل بلا LLM ولا شبكة) ثم تحسين اختياري بنداء LLM
واحد يستخرج القيد الموضوعي وصياغة التأكيد. الغياب الآمن: بلا LLM تعمل القواعد.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeos.services.employee_mission")

#: أفعال التفاعل التي ينفّذها الموظف على هدف يختاره بنفسه
ENGAGE_ACTIONS = ("reply", "like", "retweet", "quote", "follow")
#: أفعال النشر — تُوجَّه لمسار الموضوع الموجّه (directed_topic) لا للمهمّة
PUBLISH_ACTIONS = ("post", "thread")
#: مصادر الأهداف المتاحة للموظف
SOURCES = ("timeline", "mentions", "trend", "search", "any")

#: بادئة فعل عربية اختيارية: واو العطف/الفاء + حرف المضارعة أو ألف الأمر.
#: تُغطّي «ردّ / تردّ / وترد / يرد» و«اقتبس / تقتبس / وتقتبس» بنمط واحد.
_V = r"(?:[وف]?[أاإتيسن]{0,2})"

_ACTION_PATTERNS = {
    "reply": rf"{_V}ردّ?\b|{_V}علّ?ق\b|تعليق|{_V}جاوب|{_V}جب\b",
    "like": rf"لايك|[إا]عجاب|{_V}عجب|قلب",
    "retweet": rf"ريتويت|رتويت|{_V}عد\s*نشر|[إا]عادة\s*نشر|ريبوست|{_V}عيد\s*نشر",
    "quote": rf"{_V}قتبس|اقتباس|كوت|quote",
    "follow": rf"{_V}تابع|متابعة|فولو|follow",
    # فعل نشر صريح فقط — كلمة «تغريدة» وحدها وصف للهدف لا أمر بالنشر
    "post": rf"{_V}نشر|{_V}غرّ?د\b|{_V}كتب\s*(?:تغريدة|منشور|بوست)",
    "thread": r"ثريد|سلسلة\s*تغريدات|thread",
}

_SOURCE_PATTERNS = {
    "timeline": r"تايم\s*لاين|التايملاين|تايملاين|الخط\s*الزمني|timeline|"
                r"الصفحة\s*الرئيسية|الهوم|home",
    "mentions": r"منشن|المنشنات|منشنات|الإشعارات|الاشعارات|التنبيهات|"
                r"من\s*كلمني|من\s*خاطبني|ذكروني|الردود\s*علي|الردود\s*عليّ",
    "trend": r"ترند|الترندات|ترندات|رائج|الرائج|هاشتاق|الهاشتاق|وسم|الوسم",
    "search": r"ابحث|إبحث|دوّ?ر|دور\s*على|بحث",
}

#: أفعال حركة/استكشاف: «روح شوف»، «افتح»، «تصفّح» — تدلّ على مهمّة لا سؤال
_MOVE_RE = re.compile(
    r"روح|رح\b|اذهب|إذهب|افتح|إفتح|تصفّ?ح|شوف|شف\b|اطّ?لع|اطلع|اقرأ|اقرا|"
    r"دخل\s*على|ادخل"
)

#: طلب/رغبة صريحة — يمنع خطف الجُمل الوصفية («الموظف يرد على الناس تلقائياً»)
_WANT_RE = re.compile(
    r"اب(?:ي|غى)|أب(?:ي|غى)|ابيك|أبيك|ابغاك|أبغاك|بغيت|ودّ?ي|"
    r"سوّ?ي|اسوي|نفّ?ذ|نفذ|جهّ?ز|حضّ?ر|من\s*فضلك|تكفى|يلا|"
    r"^\s*(?:و)?(?:رد|ردّ|تابع|اقتبس|علّق|علق|اعجب|أعجب|لايك|ريتويت|روح|شوف|افتح)"
)

#: نطاق حرّ صريح: المستخدم يسمح بالخروج عن التخصص («أي تغريدة»، «مو لازم تخصصك»)
_FREE_SCOPE_RE = re.compile(
    r"أي\s*تغريدة|اي\s*تغريدة|أي\s*شيء|اي\s*شي|مو\s*لازم\s*تخصص|"
    r"بدون\s*شرط|خارج\s*تخصص"
)

_COUNT_WORDS = {"تغريدتين": 2, "اثنين": 2, "اثنتين": 2, "ثلاث": 3, "ثلاثة": 3,
                "حسابين": 2, "أربع": 4, "اربع": 4, "خمس": 5}


def _detect_actions(msg: str) -> List[str]:
    """الأفعال المطلوبة — مطابقة من **بداية الكلمة** لا داخلها.

    بلا قيد البداية تُلتقط «رد» داخل «تردد» و«نشر» داخل «منشرح»، فينفّذ
    الموظف فعلاً لم يطلبه أحد.
    """
    return [name for name, pattern in _ACTION_PATTERNS.items()
            if re.search(rf"(?<!\w)(?:{pattern})", msg)]


def _detect_source(msg: str) -> str:
    for name, pattern in _SOURCE_PATTERNS.items():
        if re.search(pattern, msg):
            return name
    return "any"


def _detect_count(msg: str) -> int:
    m = re.search(r"\b([1-5])\b", msg)
    if m:
        return int(m.group(1))
    for word, n in _COUNT_WORDS.items():
        if word in msg:
            return n
    return 1


def _rule_parse(message: str) -> Optional[Dict[str, Any]]:
    """تحليل حتمي بلا LLM — يعمل دائماً وهو المرجع عند فشل النموذج."""
    msg = (message or "").strip()
    if not msg:
        return None
    # الأسئلة نقاش لا تنفيذ («هل ترد على المنشنات؟»)
    if msg.endswith(("؟", "?")):
        return None

    # محتوى ملصوق (رابط تغريدة أو نصّ منسوخ متعدّد الأسطر) ليس مهمّة استكشاف:
    # له مساره الخاص (directed_engagement) الذي يبحث عن تلك التغريدة بعينها.
    if re.search(r"https?://", msg):
        return None
    if len([ln for ln in msg.splitlines() if ln.strip()]) >= 3:
        return None

    actions = _detect_actions(msg)
    if not actions:
        return None
    # جوهر المهمّة: المستخدم يقول **من أين** يبحث الموظف (مصدر صريح) أو يأمره
    # بالتحرّك/الاستكشاف. بلا ذلك فالطلب إمّا محتوى ملصوق أو موضوع نشر مباشر.
    source = _detect_source(msg)
    if source == "any" and not _MOVE_RE.search(msg):
        return None
    if not (_WANT_RE.search(msg) or _MOVE_RE.search(msg)):
        return None

    engage = [a for a in actions if a in ENGAGE_ACTIONS]
    publish = [a for a in actions if a in PUBLISH_ACTIONS]
    return {
        "intent": msg[:400],
        "source": source,
        "actions": engage or publish,
        "publish_only": not engage and bool(publish),
        "query": "",
        "count": _detect_count(msg),
        "scope": "free" if _FREE_SCOPE_RE.search(msg) else "specialty",
    }


_LLM_SYSTEM = (
    "أنت موجّه أوامر لموظف ذكي يدير حساب X. المستخدم يخاطبه بحرية بالعامية أو "
    "الفصحى. حوّل الطلب إلى مهمّة قابلة للتنفيذ. أجب JSON فقط بالشكل: "
    '{"is_mission": true/false, "source": "timeline|mentions|trend|search|any", '
    '"actions": ["reply","like","retweet","quote","follow","post","thread"], '
    '"query": "قيد الموضوع إن ذُكر وإلا فراغ", "count": 1, '
    '"scope": "specialty|free", "note": "جملة عربية قصيرة تصف ما ستفعله بصيغة المتكلم"}\n'
    "قواعد: is_mission=false إذا كان الطلب سؤالاً أو نقاشاً أو استعلاماً عن الأداء "
    "(لا تنفيذ). scope=free فقط إذا سمح المستخدم صراحةً بالخروج عن التخصص. "
    "لا تخترع أفعالاً لم يطلبها."
)


def _llm_parse(message: str, cfg, llm) -> Optional[Dict[str, Any]]:
    if llm is None or not getattr(llm, "available", False):
        return None
    persona = getattr(cfg, "persona", None)
    interests = "، ".join((getattr(persona, "interests", None) or [])[:5])
    try:
        data = llm.complete_json(
            system=_LLM_SYSTEM,
            prompt=(
                f"مجال تخصص الموظف: {interests or 'غير محدّد'}.\n"
                f"طلب المستخدم: {message.strip()[:600]}"
            ),
            temperature=0.1,
        ) or {}
    except Exception:  # noqa: BLE001
        logger.exception("mission LLM parse failed")
        return None
    if not isinstance(data, dict) or not data.get("is_mission"):
        return None

    actions = [str(a).strip().lower() for a in (data.get("actions") or [])]
    actions = [a for a in actions if a in ENGAGE_ACTIONS + PUBLISH_ACTIONS]
    if not actions:
        return None
    engage = [a for a in actions if a in ENGAGE_ACTIONS]
    publish = [a for a in actions if a in PUBLISH_ACTIONS]
    source = str(data.get("source") or "any").strip().lower()
    try:
        count = max(1, min(int(data.get("count") or 1), 5))
    except (TypeError, ValueError):
        count = 1
    return {
        "intent": message.strip()[:400],
        "source": source if source in SOURCES else "any",
        "actions": engage or publish,
        "publish_only": not engage and bool(publish),
        "query": str(data.get("query") or "").strip()[:120],
        "count": count,
        "scope": "free" if str(data.get("scope")) == "free" else "specialty",
        "note": str(data.get("note") or "").strip()[:200],
    }


def parse_mission(message: str, cfg, llm=None) -> Optional[Dict[str, Any]]:
    """حوّل رسالة حرّة إلى مهمّة مُهيكلة، أو None إن كانت نقاشاً لا أمراً.

    القواعد الحتمية هي البوابة (فلا نُنفق نداء LLM على كل رسالة دردشة)، ثم
    يحسّن النموذج القيد الموضوعي والصياغة. أي تعارض يُحسم لصالح ما فهمه النموذج
    من الأفعال إذا كان صالحاً — فهو أقدر على العامية.
    """
    base = _rule_parse(message)
    if base is None:
        return None
    refined = _llm_parse(message, cfg, llm)
    if refined:
        # المصدر الحتمي أدقّ عندما يذكر المستخدم كلمة صريحة («تايم لاين»)
        if base["source"] != "any":
            refined["source"] = base["source"]
        return refined
    return base


_ACTION_LABELS = {
    "reply": "الرد", "like": "الإعجاب", "retweet": "إعادة النشر",
    "quote": "الاقتباس", "follow": "المتابعة",
    "post": "النشر", "thread": "نشر ثريد",
}
_SOURCE_LABELS = {
    "timeline": "الخط الزمني", "mentions": "المنشنات والتنبيهات",
    "trend": "الترند والوسوم", "search": "بحث مباشر على X",
    "any": "أفضل المصادر المتاحة",
}


def describe(mission: Dict[str, Any]) -> str:
    """وصف عربي واضح لما سيفعله الموظف — يُعرض في الشات قبل التنفيذ."""
    if mission.get("note"):
        return str(mission["note"])
    acts = "، ".join(_ACTION_LABELS.get(a, a) for a in mission.get("actions") or [])
    src = _SOURCE_LABELS.get(mission.get("source", "any"), "المصادر المتاحة")
    q = mission.get("query") or ""
    scope = ("داخل تخصصي" if mission.get("scope") != "free" else "بلا قيد تخصص")
    parts = [f"أتصفّح {src} وأختار هدفاً مناسباً {scope}"]
    if q:
        parts.append(f"عن «{q}»")
    parts.append(f"ثم أنفّذ: {acts}")
    return "، ".join(parts) + "."


#: القدرات المتوقّعة لكل فعل — للتحقق من أن الأمر وقع فعلاً على X
EXPECTED_CAPABILITY = {
    "reply": "reply_to_user", "like": "like_post", "retweet": "retweet_post",
    "quote": "quote_post", "follow": "follow_user",
    "post": "publish_content", "thread": "publish_thread",
}

#: إعادة المحاولة عند وجود دورة جارية (الأمر لا يُلغى، يُؤجَّل)
_RETRY_ATTEMPTS = 8
_RETRY_WAIT_SEC = 20


def _begin_order(cfg, payload: Dict[str, Any], expected_caps: List[str], label: str) -> str:
    """نقطة استعادة للأمر **قبل** البحث والانتظار: كان الأمر يعيش في خيط ينتظر (حتى 8×20ث) أن
    تفرغ دورة جارية، فإعادة تشغيل الخادم في هذه الدقائق تُضيعه بلا خبر ولا زر استئناف. حين تبدأ
    دورته تتسلّمه نقطة الدورة (RuntimeManager._begin_checkpoint) وتُحذف هذه."""
    from app.services import task_checkpoints as TC

    oid = TC.order_task_id(cfg.id)
    account = getattr(cfg, "account_username", "") or ""
    try:
        view, _ = TC.begin(
            oid, "cycle", getattr(cfg, "user_id", None), employee_id=cfg.id,
            session_id=f"emp_{account}" if account else None, title=f"أمر @{account}: {label}"[:200],
            request={"event_type": "manual", "payload": payload,
                     "order": {"expected_caps": list(expected_caps), "label": label}})
        TC.phase(oid, "research")
        from ..runtime.manager import RuntimeManager

        RuntimeManager._push_task(cfg, view)
    except Exception:  # noqa: BLE001 - الأمر يُنفَّذ ولو تعذّر تسجيل نقطته
        logger.exception("order checkpoint failed for %s", cfg.id)
    return oid


def _close_order(oid: str, status: str, reason: str) -> None:
    from app.services import task_checkpoints as TC

    TC.finish(oid, status, reason)


def dispatch_directed(cfg, payload: Dict[str, Any], expected_caps: List[str],
                      label: str, resume_of: Optional[str] = None) -> None:
    """نفّذ أمر المالك على X فعلاً، ولا تتركه بلا خبر مهما حدث.

    ثلاث ضمانات تفصل «إنشاء تغريدة في الشات» عن «نشرها على X»:

    1. **الموظف موقوف** ⇒ الدورة تُتجاهل بصمت في :class:`RuntimeManager`
       (``status != ACTIVE``). نفحص الحالة أولاً ونخبر المستخدم بدل الوعد الكاذب.
    2. **دورة جارية** ⇒ ``run_cycle`` يعيد ``cycle_already_running`` ويضيع الأمر.
       نعيد المحاولة حتى تفرغ الدورة (لا نلغي أمراً طلبه المالك).
    3. **نُفّذ أم لا؟** لا نكتفي بانتهاء الدورة: نفحص ``results`` بحثاً عن قدرة
       متوقّعة **ناجحة**. النجاح يُترك لسرد الدورة (فيه الرابط)، والفشل/الرفض/
       عدم المحاولة يُبلَّغ صراحةً بسببه.
    """
    from ..bootstrap import get_platform
    from ..employee.models import EmployeeStatus

    from app.services import task_checkpoints as TC

    from ..runtime.manager import RuntimeManager

    platform = get_platform()
    eid = cfg.id
    oid = _begin_order(cfg, payload, expected_caps, label)
    source = RuntimeManager.order_source(oid, resume_of)

    # نشر موجّه بلا رسالة إعلامية بعد (أمر الحساب المثبّت/مهمّة نشر): بحث عميق أولاً.
    # نواه أولاً (توجيه لكل الفريق + مسودة بصوت الموظف)، ثم رسالة media_brief القديمة احتياطاً معلَناً.
    if payload.get("directed_topic") and not payload.get("media_brief"):
        from . import media_brief as MB
        from . import nawah_brief as NB

        if payload.get("nawah_brief") or NB.enabled():
            nb, err = NB.attach(payload, cfg)
            if nb is not None:
                if NB.announce_once(nb, getattr(cfg, "user_id", None)):
                    platform.notify_owner(eid, NB.owner_line(nb), "aeos_cycle_thinking")
                mine = (nb.get("voices") or {}).get(cfg.account_username) or {}
                if mine.get("angle"):
                    platform.notify_owner(eid, f"🎯 زاويتي في هذا التوجيه: {mine['angle']}", "aeos_cycle_thinking")
            elif err:
                platform.notify_owner(
                    eid, f"⚠️ تعذّر توجيه نواه ({err}) — أعدّ رسالة إعلامية بالمسار المعتاد.",
                    "aeos_cycle_thinking")

    if payload.get("directed_topic") and not payload.get("media_brief"):
        from . import media_brief as MB

        brief = MB.attach(payload, cfg, getattr(platform, "llm", None))
        if brief:
            platform.notify_owner(
                eid,
                f"🔎 جهّزت رسالة إعلامية قبل الكتابة: **{brief.get('headline', '')}** — "
                f"{len(brief.get('sources') or [])} مصدراً حديثاً "
                f"({'، '.join(s['site'] for s in (brief.get('sources') or [])[:4])}).",
                "aeos_cycle_thinking",
            )

    # الرسالة الإعلامية مرفقة الآن: الاستئناف يكتب منها بلا بحث جديد
    TC.set_request(oid, {"event_type": "manual", "payload": payload,
                         "order": {"expected_caps": list(expected_caps), "label": label}})
    TC.phase(oid, "queued")

    for attempt in range(_RETRY_ATTEMPTS):
        fresh = platform.repository.get(eid)
        if fresh is None:
            _close_order(oid, TC.CANCELLED, "الموظف لم يعد موجوداً")
            return
        if fresh.status != EmployeeStatus.ACTIVE:
            _close_order(oid, TC.FAILED, f"الموظف {fresh.status.value}")
            platform.notify_owner(
                eid,
                f"⏸️ لم أستطع تنفيذ ({label}) على X لأن الموظف "
                f"**{fresh.status.value}** حالياً. اكتب «شغّل الموظف» ثم أعد "
                "الطلب وسأنفّذه فوراً.",
                "aeos_directed_blocked",
            )
            return

        report = platform.run_cycle_now(eid, "manual", payload, source=source) or {}
        if report.get("skipped") and report.get("reason") == "cycle_already_running":
            if attempt == 0:
                platform.notify_owner(
                    eid,
                    f"⏳ استلمت طلبك ({label}) والموظف في منتصف دورة الآن — "
                    "سأنفّذه تلقائياً بعد انتهائها بلا حاجة لإعادة الطلب.",
                    "aeos_directed_queued",
                )
            time.sleep(_RETRY_WAIT_SEC)
            continue
        if report.get("skipped"):
            _close_order(oid, TC.FAILED, str(report.get("reason") or "")[:300])
            platform.notify_owner(
                eid, f"⚠️ لم تُنفَّذ ({label}): {report.get('reason', 'سبب غير معروف')}.",
                "aeos_directed_blocked",
            )
            return

        # بدأت الدورة: نقطتها تسلّمت الأمر وحذفت نقطة الانتظار
        _report_outcome(platform, eid, report, expected_caps, label)
        return

    _close_order(oid, TC.FAILED, "بقي الموظف مشغولاً بدورة طويلة")
    platform.notify_owner(
        eid,
        f"⚠️ لم أتمكّن من تنفيذ ({label}) لأن الموظف بقي مشغولاً بدورة طويلة. "
        "أعد الطلب بعد قليل.",
        "aeos_directed_blocked",
    )


def _report_outcome(platform, eid: str, report: Dict[str, Any],
                    expected_caps: List[str], label: str) -> None:
    """أبلِغ المالك عندما لا يقع الفعل على X — النجاح يسرده تقرير الدورة."""
    results = [r for r in (report.get("results") or []) if isinstance(r, dict)]
    relevant = [r for r in results if r.get("capability") in expected_caps]
    if any(r.get("success") for r in relevant):
        return  # نُفّذ فعلاً على X؛ الرابط يظهر في سرد الدورة

    if relevant:
        errors = "؛ ".join(str(r.get("error") or "فشل غير موصوف")[:160]
                           for r in relevant[:2])
        platform.notify_owner(
            eid, f"❌ حاولت تنفيذ ({label}) على X لكنه لم ينجح: {errors}",
            "aeos_directed_failed",
        )
        return

    # لم يُقترح/يُختر الإجراء أصلاً: الملاحظة تشرح السبب (لا هدف، أو رفض الحارس)
    note = str(report.get("directed_note") or "").strip()
    guard = report.get("guard_report") or {}
    reasons = ""
    if isinstance(guard, dict):
        rejected = guard.get("rejected") or guard.get("reasons") or {}
        if isinstance(rejected, dict) and rejected:
            reasons = "؛ ".join(str(v)[:120] for v in list(rejected.values())[:2])
    detail = note or reasons or "لم يُختَر أي إجراء مطابق في هذه الدورة."
    platform.notify_owner(
        eid, f"⚠️ لم يقع فعل على X من طلبك ({label}). السبب: {detail}",
        "aeos_directed_failed",
    )


def request_mission(cfg, mission: Dict[str, Any],
                    user_id: Optional[int] = None) -> str:
    """شغّل المهمّة الموجّهة على X في الخلفية وأعد ردّ التأكيد للشات.

    ``force_fresh`` إلزامي: المهمّة تعني «انظر الآن»، فلا يجوز أن تُخدَم من
    ذاكرة الرصد المؤقتة (signal_cache) وتُرجع تايم لاين قديماً.
    """
    payload: Dict[str, Any] = {"force_fresh": True}
    if mission.get("publish_only"):
        # نشر موجّه: المسار الموجود (DailyContentCreator) يجيده أصلاً
        payload["directed_topic"] = mission.get("query") or mission.get("intent", "")
    else:
        payload["directed_mission"] = mission

    expected = [EXPECTED_CAPABILITY[a] for a in (mission.get("actions") or [])
                if a in EXPECTED_CAPABILITY]
    label = describe(mission)

    threading.Thread(
        target=dispatch_directed,
        args=(cfg, payload, expected, label),
        daemon=True,
    ).start()

    account = getattr(cfg, "account_username", "") or "الموظف"
    return (f"🫡 تمام يا مدير! استلمت المهمّة: {label}\n"
            f"الموظف @{account} ينفّذها الآن **فعلياً على X** ويوافيك بالنتيجة "
            "والرابط هنا في الشات. ⏳")


def handle_message(message: str, cfg, llm=None,
                   user_id: Optional[int] = None) -> Optional[str]:
    """نقطة الدخول من الشات: نفّذ إن كانت مهمّة، أو None ليكمل النقاش."""
    mission = parse_mission(message, cfg, llm)
    if not mission:
        return None
    try:
        return request_mission(cfg, mission, user_id)
    except Exception:  # noqa: BLE001
        logger.exception("mission dispatch failed")
        return None
