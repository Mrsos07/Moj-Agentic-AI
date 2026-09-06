"""
Custom per-employee skills — «اصنع مهارة لموظفك من برومبت».

المالك يكتب وصفاً حرّاً لما يريد أن يتقنه موظفه («كل صباح اختر أقوى تغريدة في
التايم لاين عن الذكاء الاصطناعي واقتبسها بتعليق تحليلي، وردّ على أي شخص يسأل
عن الأدوات»)، فيتحوّل الوصف إلى **مواصفة معلنة** (JSON) يفسّرها مشغّل واحد عام
هو :class:`CustomSkillRunner`.

لماذا مواصفة لا كود مولَّد؟
--------------------------
البديل الظاهر «اجعل النموذج يكتب صنف بايثون ونشغّله» يعني تنفيذ كود كتبه نموذج
لغوي داخل عملية الباك-إند التي تملك كوكيز حسابات المستخدمين ومفاتيح الـAPI
وقاعدة البيانات. حقنٌ واحد في البرومبت (وتغريدة في التايم لاين تكفي) يعني
سيطرة كاملة. المواصفة المعلنة تعطي القدرة نفسها عملياً — اختيار مصدر وفلترة
أهداف واقتراح إجراءات وتوجيه كتابة — لكنها:

- **محدودة بقائمة بيضاء**: أي مصدر/إجراء/صيغة خارجها يُحذف عند التطبيع.
- **قابلة للعرض والتعديل**: يراها المالك بالعربية قبل التفعيل ويوقفها بضغطة.
- **لا تتجاوز شيئاً**: المهارة *تقترح* فقط، ثم يمرّ الاقتراح على محرّك القرار
  وحارس الشخصية والميزانيات اليومية كأي مهارة مدمجة. لذلك لا تضع أبداً
  ``owner_directed`` في مقترحاتها: ذاك مخصّص لأمر يكتبه المالك في الشات الآن،
  لا لقاعدة دائمة فعّلها مرة.

مكان التخزين: ``business_rules['custom_skills']`` — و``configure_agent`` يدمج
القواعد (``update``) ولا يستبدلها، فحفظ البرسونا من الواجهة لا يمحو المهارات.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .base import ISkill, ProposedAction

if TYPE_CHECKING:
    from ..cognition.context import ExecutionContext

logger = logging.getLogger("aeos.skills.custom")

STORE_KEY = "custom_skills"
RUNS_FLAG = "custom_skill_runs"

#: الأفعال المسموح بها وقدراتها — القائمة البيضاء الوحيدة. أي فعل خارجها يُحذف.
ACTION_CAPABILITY = {
    "reply": "reply_to_user",
    "like": "like_post",
    "retweet": "retweet_post",
    "quote": "quote_post",
    "follow": "follow_user",
    "bookmark": "bookmark_post",
    "post": "publish_content",
    "thread": "publish_thread",
}

#: أفعال تحتاج هدفاً موجوداً على المنصة (تغريدة/حساب)
TARGET_ACTIONS = ("reply", "like", "retweet", "quote", "follow", "bookmark")
#: أفعال تنتج محتوى جديداً من الحساب نفسه
PUBLISH_ACTIONS = ("post", "thread")

#: مصادر الأهداف → مفاتيح scratch التي يعبّئها طور الرصد
SOURCE_KEYS = {
    "timeline": ("timeline",),
    "mentions": ("mentions",),
    "trend": ("hashtag_tweets", "trend_tweets"),
    "top_posts": ("top_posts",),
    "search": ("interest_tweets", "top_posts"),
    "any": ("top_posts", "interest_tweets", "hashtag_tweets", "trend_tweets",
            "timeline"),
}

_MAX_SKILLS_PER_EMPLOYEE = 12
_MAX_ACTIONS_PER_SKILL = 4
_MAX_COUNT_PER_ACTION = 5


# ─────────────────────────── التطبيع والتحقق ───────────────────────────
def _slug(text: str) -> str:
    """معرّف آمن من نص عربي/إنجليزي (يُستعمل مفتاحاً وعدّاداً)."""
    base = re.sub(r"[^\w]+", "_", str(text or ""), flags=re.UNICODE).strip("_")
    return (base[:40] or "skill").lower()


def _clean_list(value: Any, limit: int = 12, item_len: int = 60) -> List[str]:
    if isinstance(value, str):
        value = re.split(r"[,،\n]", value)
    out: List[str] = []
    for item in (value or []):
        s = str(item).strip()[:item_len]
        if s and s not in out:
            out.append(s)
    return out[:limit]


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def normalize_spec(raw: Dict[str, Any], *,
                   existing_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """حوّل أي قاموس (من النموذج أو من الواجهة) إلى مواصفة صالحة ومحدودة.

    هذه هي بوابة الأمان الوحيدة: كل ما لا تعرفه القوائم البيضاء يُحذف بصمت،
    وكل رقم يُقصّ إلى مداه. لا تعتمد أبداً على «النموذج سيلتزم بالمخطط».
    """
    raw = raw if isinstance(raw, dict) else {}
    label = str(raw.get("label") or raw.get("name") or "مهارة مخصّصة").strip()[:80]

    skill_id = _slug(raw.get("id") or label)
    if not skill_id.startswith("custom_"):
        skill_id = f"custom_{skill_id}"
    taken = set(existing_ids or [])
    if skill_id in taken:
        skill_id = f"{skill_id}_{uuid.uuid4().hex[:4]}"

    trigger = raw.get("trigger") if isinstance(raw.get("trigger"), dict) else {}
    hours = []
    for h in (trigger.get("hours") or []):
        try:
            hour = int(h)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23 and hour not in hours:
            hours.append(hour)

    target = raw.get("target") if isinstance(raw.get("target"), dict) else {}
    source = str(target.get("source") or "any").strip().lower()
    if source not in SOURCE_KEYS:
        source = "any"

    actions: List[Dict[str, Any]] = []
    seen_types = set()
    for item in (raw.get("actions") or []):
        if isinstance(item, str):
            item = {"type": item}
        if not isinstance(item, dict):
            continue
        atype = str(item.get("type") or "").strip().lower()
        if atype not in ACTION_CAPABILITY or atype in seen_types:
            continue
        seen_types.add(atype)
        actions.append({
            "type": atype,
            "count": int(_clamp(item.get("count", 1), 1, _MAX_COUNT_PER_ACTION, 1)),
            "directive": str(item.get("directive") or "").strip()[:400],
        })
        if len(actions) >= _MAX_ACTIONS_PER_SKILL:
            break

    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    fmt = str(content.get("format") or "").strip().lower()
    try:
        from ..employee.strategy import CONTENT_FORMATS
        if fmt not in CONTENT_FORMATS:
            fmt = ""
    except Exception:  # noqa: BLE001
        fmt = ""

    priority = raw.get("priority") if isinstance(raw.get("priority"), dict) else {}

    return {
        "id": skill_id,
        "label": label,
        "description": str(raw.get("description") or "").strip()[:300],
        "prompt": str(raw.get("prompt") or "").strip()[:1000],
        "enabled": bool(raw.get("enabled", True)),
        "trigger": {
            "every_cycles": int(_clamp(trigger.get("every_cycles", 1), 1, 100, 1)),
            "hours": sorted(hours)[:24],
            "max_per_day": int(_clamp(trigger.get("max_per_day", 0), 0, 50, 0)),
            "requires_mentions": bool(trigger.get("requires_mentions", False)),
        },
        "target": {
            "source": source,
            "include": _clean_list(target.get("include")),
            "exclude": _clean_list(target.get("exclude")),
            "handles": [h.lstrip("@").lower()
                        for h in _clean_list(target.get("handles"), limit=20, item_len=30)],
            "min_chars": int(_clamp(target.get("min_chars", 0), 0, 280, 0)),
            "specialty_only": bool(target.get("specialty_only", True)),
        },
        "actions": actions,
        "content": {
            "brief": str(content.get("brief") or "").strip()[:800],
            "format": fmt,
            "pillar": str(content.get("pillar") or "").strip()[:60],
        },
        "priority": {
            "impact": round(_clamp(priority.get("impact", 0.7), 0.05, 0.95, 0.7), 2),
            "urgency": round(_clamp(priority.get("urgency", 0.6), 0.05, 0.95, 0.6), 2),
            "risk": round(_clamp(priority.get("risk", 0.2), 0.0, 0.9, 0.2), 2),
        },
        "created_at": str(raw.get("created_at")
                          or datetime.now(timezone.utc).isoformat(timespec="seconds")),
    }


_ACTION_LABELS = {
    "reply": "الرد", "like": "الإعجاب", "retweet": "إعادة النشر",
    "quote": "الاقتباس مع تعليق", "follow": "المتابعة", "bookmark": "حفظ مرجع",
    "post": "نشر منشور", "thread": "نشر ثريد",
}
_SOURCE_LABELS = {
    "timeline": "الخط الزمني", "mentions": "المنشنات والردود الواردة",
    "trend": "الترند والهاشتاقات", "top_posts": "أهم البوستات",
    "search": "نتائج بحث التخصص", "any": "كل المصادر المتاحة",
}


def describe_spec(spec: Dict[str, Any]) -> str:
    """وصف عربي مفهوم للمالك: متى تعمل، ماذا تفعل، وبأي حدود.

    المالك يجب أن يفهم بالضبط ما وافق عليه قبل أن يعمل الموظف به — «مهارة
    ذكية» غامضة أسوأ من لا مهارة.
    """
    t, tg = spec.get("trigger", {}), spec.get("target", {})
    lines: List[str] = []

    when = []
    every = int(t.get("every_cycles", 1) or 1)
    when.append("كل دورة" if every <= 1 else f"كل {every} دورات")
    if t.get("hours"):
        when.append("في الساعات " + "، ".join(str(h) for h in t["hours"]))
    if t.get("max_per_day"):
        when.append(f"بحد أقصى {t['max_per_day']} مرة يومياً")
    if t.get("requires_mentions"):
        when.append("فقط عند وجود منشنات واردة")
    lines.append("⏱️ متى تعمل: " + "، ".join(when) + ".")

    has_target = any(a["type"] in TARGET_ACTIONS for a in spec.get("actions", []))
    if has_target:
        where = [f"من {_SOURCE_LABELS.get(tg.get('source'), tg.get('source'))}"]
        if tg.get("include"):
            where.append("تحتوي: " + "، ".join(tg["include"]))
        if tg.get("exclude"):
            where.append("وتستبعد: " + "، ".join(tg["exclude"]))
        if tg.get("handles"):
            where.append("من الحسابات: " + "، ".join("@" + h for h in tg["handles"]))
        if tg.get("min_chars"):
            where.append(f"وطولها ≥ {tg['min_chars']} حرفاً")
        where.append("داخل التخصص فقط" if tg.get("specialty_only")
                     else "بلا قيد التخصص")
        lines.append("🎯 كيف تختار الهدف: " + "، ".join(where) + ".")

    acts = "، ".join(
        f"{_ACTION_LABELS.get(a['type'], a['type'])}"
        + (f" ×{a['count']}" if a["count"] > 1 else "")
        for a in spec.get("actions", [])
    )
    lines.append(f"⚡ ماذا تفعل: {acts or 'لا شيء'}.")

    content = spec.get("content", {})
    if content.get("brief"):
        lines.append(f"✍️ توجيه الكتابة: {content['brief'][:200]}")
    lines.append(
        "🛡️ تبقى خاضعة لحارس الشخصية وسقوف الدورة والميزانيات اليومية "
        "كأي مهارة أخرى، ولا تُنفَّذ إلا إن فازت في محرّك القرار."
    )
    return "\n".join(lines)


# ─────────────────────────── التخزين ───────────────────────────
def list_specs(config) -> List[Dict[str, Any]]:
    rules = getattr(config, "business_rules", None) or {}
    return [s for s in (rules.get(STORE_KEY) or []) if isinstance(s, dict)]


def save_spec(config, spec: Dict[str, Any]) -> Dict[str, Any]:
    """أضف مهارة أو حدّث واحدة موجودة بنفس المعرّف."""
    rules = config.business_rules if isinstance(config.business_rules, dict) else {}
    config.business_rules = rules
    current = list_specs(config)
    existing_ids = [s.get("id") for s in current]
    if spec.get("id") in existing_ids:
        clean = normalize_spec(spec)                     # تحديث: المعرّف يبقى
        current = [clean if s.get("id") == clean["id"] else s for s in current]
    else:
        clean = normalize_spec(spec, existing_ids=existing_ids)
        current.append(clean)
    rules[STORE_KEY] = current[-_MAX_SKILLS_PER_EMPLOYEE:]
    return clean


def delete_spec(config, skill_id: str) -> bool:
    rules = config.business_rules if isinstance(config.business_rules, dict) else {}
    config.business_rules = rules
    current = list_specs(config)
    kept = [s for s in current if s.get("id") != skill_id]
    rules[STORE_KEY] = kept
    return len(kept) != len(current)


def set_enabled(config, skill_id: str, enabled: bool) -> bool:
    for spec in list_specs(config):
        if spec.get("id") == skill_id:
            spec["enabled"] = bool(enabled)
            return True
    return False


# ─────────────────────────── التوليد من برومبت ───────────────────────────
_GEN_SYSTEM = (
    "أنت مصمّم مهارات لموظف ذكي يدير حساباً على منصة X. حوّل طلب المالك إلى "
    "**مواصفة مهارة** بصيغة JSON فقط، بلا أي شرح خارج الـJSON.\n"
    "المخطط:\n"
    '{"label": "اسم عربي قصير للمهارة",\n'
    ' "description": "جملة تصف ما تفعله",\n'
    ' "trigger": {"every_cycles": 1, "hours": [], "max_per_day": 0,'
    ' "requires_mentions": false},\n'
    ' "target": {"source": "timeline|mentions|trend|top_posts|search|any",'
    ' "include": ["كلمات يجب أن تظهر"], "exclude": ["كلمات مرفوضة"],'
    ' "handles": [], "min_chars": 0, "specialty_only": true},\n'
    ' "actions": [{"type": "reply|like|retweet|quote|follow|bookmark|post|thread",'
    ' "count": 1, "directive": "توجيه خاص بهذا الفعل"}],\n'
    ' "content": {"brief": "توجيه كتابة المحتوى", "format": "", "pillar": ""},\n'
    ' "priority": {"impact": 0.7, "urgency": 0.6, "risk": 0.2}}\n\n"'
    "قواعد إلزامية:\n"
    "1) لا تخترع أنواع أفعال أو مصادر خارج القوائم أعلاه.\n"
    "2) اختر أقل عدد أفعال يحقّق الطلب فعلاً (الجودة لا الكثرة).\n"
    "3) ``include``/``exclude`` كلمات مفتاحية قصيرة تُطابق نص التغريدة، لا جملاً.\n"
    "4) إن طلب المالك النشر فاكتب ``content.brief`` توجيهاً حرفياً للكاتب: "
    "الزاوية، البنية، ما يُتجنّب — لا إعادة صياغة للطلب.\n"
    "5) ``risk`` أعلى للأفعال العلنية الجريئة (اقتباس/رد) وأقل للإعجاب/الحفظ."
)


def generate_spec(prompt: str, config, llm=None) -> Dict[str, Any]:
    """حوّل برومبت المالك إلى مواصفة مهارة (نموذج لغوي + احتياطي حتمي).

    الاحتياطي الحتمي ليس ترفاً: بلا مفتاح/عند فشل الشبكة يجب أن تُنشأ مهارة
    معقولة بدل رسالة خطأ — وهو أيضاً ما تختبره سكربتات التحقق بلا شبكة.
    """
    text = str(prompt or "").strip()
    if not text:
        raise ValueError("prompt is required")

    data: Dict[str, Any] = {}
    if llm is not None and getattr(llm, "available", False):
        persona = getattr(config, "persona", None)
        interests = "، ".join(getattr(persona, "interests", None) or []) or "عام"
        try:
            data = llm.complete_json(
                system=_GEN_SYSTEM,
                prompt=(
                    f"تخصص الحساب: {interests}\n"
                    f"دور الحساب: {getattr(persona, 'role', '') or 'صانع محتوى'}\n"
                    f"لغة الكتابة: {getattr(persona, 'language', 'ar') or 'ar'}\n\n"
                    f"طلب المالك:\n«{text}»\n\n"
                    "صمّم المهارة الأنسب لتنفيذ هذا الطلب تلقائياً كل دورة."
                ),
                temperature=0.2,
            ) or {}
        except Exception as exc:  # noqa: BLE001 - التوليد لا يفشل نهائياً
            logger.warning("custom skill generation failed, using fallback: %s", exc)
            data = {}

    if not isinstance(data, dict) or not data.get("actions"):
        data = _fallback_spec(text, data if isinstance(data, dict) else {})

    data["prompt"] = text
    return normalize_spec(data, existing_ids=[s.get("id") for s in list_specs(config)])


#: أفعال عربية/إنجليزية → نوع الفعل (تحمّل السوابق: «وتردّ»، «يقتبس»)
_ACTION_PATTERNS = {
    "reply": r"(?<!\w)(?:و|ف|ث)?[ايتنس]?ر[دّد]|رد(?:ود)?|reply|respond",
    "quote": r"(?<!\w)(?:و|ف)?[ايتن]?قتبس|اقتباس|quote",
    "retweet": r"(?<!\w)(?:و|ف)?[ايتن]?عيد\s*نشر|ريتويت|رتويت|retweet|repost",
    "like": r"(?<!\w)(?:و|ف)?[ايتن]?عجب|إعجاب|اعجاب|لايك|like",
    "follow": r"(?<!\w)(?:و|ف)?[ايتن]?تابع|متابعة|follow",
    "bookmark": r"احفظ|حفظ|مرجع|bookmark",
    "thread": r"ثريد|سلسلة\s*تغريدات|thread",
    "post": r"(?<!\w)(?:و|ف)?[ايتن]?نشر|منشور|تغريدة|غرّد|غرد|post|tweet",
}
_SOURCE_PATTERNS = {
    "mentions": r"منشن|المنشنات|الإشعارات|الاشعارات|التنبيهات|من\s*كلمني|الردود\s*علي",
    "timeline": r"تايم\s*لاين|التايملاين|الخط\s*الزمني|timeline|الرئيسية",
    "trend": r"ترند|الترندات|هاشتاق|وسم|رائج",
    "top_posts": r"أهم\s*البوستات|اهم\s*البوستات|أفضل\s*المنشورات|top\s*posts",
    "search": r"ابحث|بحث|search",
}


def _fallback_spec(text: str, partial: Dict[str, Any]) -> Dict[str, Any]:
    """مواصفة معقولة بلا نموذج لغوي — قراءة حتمية لنيّة الطلب."""
    low = text.lower()
    actions = [
        {"type": name, "count": 1}
        for name, pattern in _ACTION_PATTERNS.items()
        if re.search(pattern, low, re.IGNORECASE)
    ]
    # «انشر ثريد» يذكر الاثنين: الثريد أدقّ فيُلغي المنشور المفرد
    if any(a["type"] == "thread" for a in actions):
        actions = [a for a in actions if a["type"] != "post"]
    if not actions:
        actions = [{"type": "reply", "count": 1}]

    source = "any"
    for name, pattern in _SOURCE_PATTERNS.items():
        if re.search(pattern, low, re.IGNORECASE):
            source = name
            break

    spec = {
        "label": partial.get("label") or f"مهارة: {text[:40]}",
        "description": partial.get("description") or text[:200],
        "target": {"source": source},
        "actions": actions,
    }
    if any(a["type"] in PUBLISH_ACTIONS for a in actions):
        spec["content"] = {"brief": text[:600]}
    return spec


# ─────────────────────────── المشغّل ───────────────────────────
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def runs_today(config, skill_id: str) -> int:
    rec = (config.runtime_state.flags.get(RUNS_FLAG) or {}).get(skill_id) or {}
    return int(rec.get("count", 0)) if rec.get("date") == _today() else 0


def note_run(config, skill_id: str) -> None:
    """سجّل تنفيذاً ناجحاً لمهارة مخصّصة (يُستدعى من طور التنفيذ).

    العدّ يجري عند **التنفيذ** لا عند الاقتراح: الاقتراح قد يسقط في محرّك القرار
    أو يرفضه الحارس، وعدّه حينها يستهلك حصة المهارة اليومية بلا أي فعل.
    """
    flags = config.runtime_state.flags
    runs = flags.setdefault(RUNS_FLAG, {})
    rec = runs.get(skill_id) or {}
    if rec.get("date") != _today():
        rec = {"date": _today(), "count": 0}
    rec["count"] = int(rec.get("count", 0)) + 1
    runs[skill_id] = rec


def _local_hour(config) -> int:
    tz_name = getattr(getattr(config, "scheduler", None), "timezone", "") or ""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name)).hour
    except Exception:  # noqa: BLE001 - منطقة غير معروفة ⇒ توقيت الخادم
        return datetime.now().hour


def should_run(config, spec: Dict[str, Any], context: "ExecutionContext") -> str:
    """"" إن كانت المهارة مستحقّة الآن، وإلا سبب عربي مختصر للتخطّي."""
    if not spec.get("enabled", True):
        return "موقوفة"
    trigger = spec.get("trigger") or {}
    every = int(trigger.get("every_cycles", 1) or 1)
    if every > 1 and int(config.runtime_state.cycles_completed or 0) % every:
        return f"دورها كل {every} دورات"
    hours = trigger.get("hours") or []
    if hours and _local_hour(config) not in hours:
        return "خارج ساعات تشغيلها"
    max_day = int(trigger.get("max_per_day", 0) or 0)
    if max_day and runs_today(config, spec["id"]) >= max_day:
        return f"بلغت حدّها اليومي ({max_day})"
    if trigger.get("requires_mentions") and not (context.scratch.get("mentions") or []):
        return "لا منشنات واردة"
    return ""


def _matches(text: str, spec_target: Dict[str, Any]) -> bool:
    low = (text or "").lower()
    if len(low.strip()) < int(spec_target.get("min_chars", 0) or 0):
        return False
    include = [w.lower() for w in (spec_target.get("include") or [])]
    if include and not any(w in low for w in include):
        return False
    exclude = [w.lower() for w in (spec_target.get("exclude") or [])]
    if exclude and any(w in low for w in exclude):
        return False
    return True


class CustomSkillRunner(ISkill):
    """يشغّل كل المهارات المخصّصة لهذا الموظف (مهارة واحدة تفسّر مواصفات كثيرة).

    ``always_on`` لأن المهارات المخصّصة تُخزَّن في قواعد العمل لا في
    ``installed_skills``: الموظف المُنشأ قبل وجود الميزة يملك قائمة مهارات
    قديمة في قاعدة البيانات، فربط التشغيل بها يعني مهارةً أنشأها المالك ولا
    تعمل أبداً بلا سبب ظاهر.
    """

    name = "custom_skills"
    uses_capabilities = list(dict.fromkeys(ACTION_CAPABILITY.values()))
    always_on = True
    #: مهارة صمّمها المالك بنفسه: تحجز هدفها قبل المهارات التلقائية العامة
    #: (وبعد واجب الردّ على المنشنات وأوامر الشات المباشرة).
    claim_priority = 80

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        specs = list_specs(context.config)
        if not specs:
            return []
        out: List[ProposedAction] = []
        for spec in specs:
            skip = should_run(context.config, spec, context)
            if skip:
                logger.debug("custom skill %s skipped: %s", spec.get("id"), skip)
                continue
            try:
                out.extend(self._propose_one(context, spec))
            except Exception:  # noqa: BLE001 - مهارة معطوبة لا تُسقط الدورة
                logger.exception("custom skill %s failed to propose", spec.get("id"))
        return out

    # ---- الأهداف ----
    @staticmethod
    def _candidates(context: "ExecutionContext", spec: Dict[str, Any]) -> List[dict]:
        from ..employee.persona_templates import relevance_score
        from .builtin import (
            _engaged_urls, _is_ad_or_spam_text, _is_specialty_match,
            _topic_recently_engaged, _violates_persona_donts,
        )

        tg = spec.get("target") or {}
        source = tg.get("source", "any")
        # المنشنات مصدر مقصود لذاته: من خاطبنا يستحق رداً ولو بلا كلمات التخصص
        specialty = bool(tg.get("specialty_only", True)) and source != "mentions"
        handles = set(tg.get("handles") or [])
        engaged = _engaged_urls(context)
        interests = getattr(context.config.persona, "interests", []) or []

        pool: List[dict] = []
        seen: set = set()
        for key in SOURCE_KEYS.get(source, SOURCE_KEYS["any"]):
            for item in (context.scratch.get(key) or []):
                if not isinstance(item, dict):
                    continue
                url = (item.get("url") or "").strip()
                text = (item.get("text") or "").strip()
                handle = (item.get("handle") or "").strip().lstrip("@").lower()
                if not url or url in seen or url in engaged:
                    continue
                hay = f"{text} {handle}".strip()
                # الفلاتر الثابتة تسبق فلاتر المالك دائماً: لا سبام، ولا مخالفة
                # لمحظورات الشخصية، ولا إعادة فتح موضوع سبق التفاعل معه.
                if _is_ad_or_spam_text(hay) or _violates_persona_donts(hay, context):
                    continue
                if handles and handle not in handles:
                    continue
                if not _matches(text, tg):
                    continue
                if specialty and not _is_specialty_match(context, hay):
                    continue
                if _topic_recently_engaged(context, text):
                    continue
                seen.add(url)
                pool.append({**item, "_rel": relevance_score(hay, interests)
                             if interests else 0})
        pool.sort(key=lambda t: t.get("_rel", 0), reverse=True)
        return pool

    # ---- بناء المقترحات ----
    def _propose_one(self, context: "ExecutionContext",
                     spec: Dict[str, Any]) -> List[ProposedAction]:
        from .builtin import _profile_url_from_handle, _strategy_brief_suffix

        prio = spec.get("priority") or {}
        impact = float(prio.get("impact", 0.7))
        urgency = float(prio.get("urgency", 0.6))
        risk = float(prio.get("risk", 0.2))
        label = spec.get("label") or spec["id"]

        actions: List[ProposedAction] = []
        needs_target = any(a["type"] in TARGET_ACTIONS
                           for a in (spec.get("actions") or []))
        pool = self._candidates(context, spec) if needs_target else []
        if needs_target and not pool:
            context.scratch.setdefault("custom_skill_notes", []).append(
                f"«{label}»: لم أجد في المصدر المطلوب هدفاً مطابقاً لشروطها.")

        queue = list(pool)
        for action in (spec.get("actions") or []):
            atype = action["type"]
            capability = ACTION_CAPABILITY[atype]
            directive = action.get("directive") or ""

            if atype in PUBLISH_ACTIONS:
                brief = self._publish_brief(context, spec, directive)
                if not brief:
                    continue
                params: Dict[str, Any] = {
                    "brief": brief + _strategy_brief_suffix(context),
                    "media_url": "",
                    "pillar": (spec.get("content") or {}).get("pillar", ""),
                    "format": (spec.get("content") or {}).get("format", ""),
                    "_skill": spec["id"],
                }
                if atype == "thread":
                    params["parts"] = 4
                actions.append(ProposedAction(
                    capability=capability, params=params,
                    rationale=f"custom skill «{label}»: {atype}",
                    expected_impact=impact, urgency=urgency, risk=risk,
                ))
                continue

            taken = 0
            while queue and taken < int(action.get("count", 1)):
                target = queue.pop(0)
                params = {
                    "original_text": (target.get("text") or "")[:400],
                    "handle": target.get("handle", ""),
                    "_skill": spec["id"],
                }
                if atype == "follow":
                    profile = _profile_url_from_handle(target.get("handle", ""))
                    if not profile:
                        continue
                    params["profile_url"] = profile
                else:
                    params["tweet_url"] = target.get("url", "")
                if directive:
                    params["brief"] = directive
                if atype == "reply" and (spec.get("target") or {}).get("source") == "mentions":
                    params["is_mention_reply"] = True
                taken += 1
                actions.append(ProposedAction(
                    capability=capability, params=params,
                    rationale=f"custom skill «{label}»: {atype} "
                              f"{target.get('handle', '')}".strip(),
                    expected_impact=impact, urgency=urgency, risk=risk,
                ))
            # أعد تعبئة الطابور للفعل التالي كي لا يجوع (إعجاب + رد على أهداف مختلفة)
            if not queue:
                queue = list(pool)
        return actions

    @staticmethod
    def _publish_brief(context: "ExecutionContext", spec: Dict[str, Any],
                       directive: str) -> str:
        """موجز الكتابة لمهارة نشر — مؤرضن بموضوع حقيقي متى توفّر.

        لا نطلب من النموذج «اكتب شيئاً» في الفراغ: إن وُجد موضوع حقيقي في
        مصادر هذه الدورة نمرّره كخلفية، وإلا نكتفي بتوجيه المالك (بعض المهارات
        لا تحتاج مصدراً: تأمّل، سؤال أسبوعي، تذكير...).
        """
        from .builtin import _grounded_topic

        persona = context.config.persona
        lang = getattr(persona, "language", "ar") or "ar"
        dialect = getattr(persona, "dialect", "") or ""
        content = spec.get("content") or {}
        brief_parts = [
            f"اكتب منشور X باللغة {lang}"
            + (f" وبلهجة {dialect}" if dialect else "")
            + " بأسلوب شخصيتك."
        ]
        owner_brief = (content.get("brief") or directive
                       or spec.get("prompt") or "").strip()
        if owner_brief:
            brief_parts.append(f"تعليمات المهارة التي وضعها صاحب الحساب: {owner_brief}")
        topic = _grounded_topic(context)
        if topic and topic.get("title"):
            brief_parts.append(
                f"موضوع حقيقي من مصادر اليوم يمكن البناء عليه: '{topic['title']}'"
                + (f" — {topic['summary'][:200]}" if topic.get("summary") else "")
            )
        brief_parts.append(
            "قواعد الصنعة: خُطّاف أول ≤ 12 كلمة، فكرة واحدة، موقف صريح أو معلومة "
            "ملموسة، خاتمة تفتح نقاشاً، بلا مقدمات إنشائية."
        )
        return "\n".join(brief_parts)
