"""
Content strategy brain — يحوّل الموظف من «مطارِد ترندات» إلى صانع محتوى.

المشكلة التي يحلّها: كان الموظف ينشر أي موضوع يطابق كلمات اهتماماته، بصيغة
واحدة، ولا يعرف أبداً أيّ منشور نجح. النتيجة: حساب بلا هوية ولا تطوّر.

هذه الوحدة تضيف ثلاث طبقات يعتمد عليها كل قرار محتوى:

1. **محاور المحتوى (Pillars)** — الأعمدة الثابتة التي يُبنى عليها الحساب
   (تُشتق من اهتمامات الشخصية أو تُحدَّد يدوياً في
   ``business_rules['content_pillars']``). المحاور تتناوب فلا يصير الحساب
   أحادي النغمة.
2. **الصيغ (Formats)** — طريقة العرض: رأي حادّ، شرح عملي، سؤال، قصة، ثريد،
   قائمة، تصحيح مفهوم... لكل صيغة توجيه كتابة خاص وعدد تغريدات.
3. **سجل الأداء (Performance ledger)** — أداء كل منشور فعلياً (إعجابات/إعادات/
   ردود/مشاهدات) مربوطاً بمحوره وصيغته وساعة نشره. الاختيار التالي يُرجَّح بما
   نجح فعلاً، مع إبقاء نسبة استكشاف لما لم يُجرَّب (وإلا تجمّد الحساب على صيغة
   واحدة إلى الأبد).

كل شيء يُخزَّن في حقول JSON موجودة أصلاً (``business_rules`` و
``runtime_state.flags``) — لا تغيير في قاعدة البيانات.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

#: صيغ المحتوى: لكل صيغة توجيه كتابة صريح وعدد أجزاء (parts>1 ⇒ ثريد)
CONTENT_FORMATS: Dict[str, Dict[str, Any]] = {
    "insight": {
        "label": "زبدة/استنتاج",
        "parts": 1,
        "directive": (
            "اكتب استنتاجاً واحداً غير بديهي عن الموضوع: ابدأ بجملة خطّافة قصيرة، "
            "ثم الفكرة التي لا يقولها الجميع، ثم لماذا تهم القارئ عملياً."
        ),
    },
    "hot_take": {
        "label": "رأي صريح",
        "parts": 1,
        "directive": (
            "اكتب موقفاً صريحاً ومحدّداً (لا تُجامل ولا تُعمّم): ما الشائع الخطأ، "
            "وما الصحيح برأيك، وبأي دليل مختصر. اجعله قابلاً للنقاش لا للاستفزاز الفارغ."
        ),
    },
    "howto": {
        "label": "شرح عملي",
        "parts": 1,
        "directive": (
            "اكتب خطوة عملية واحدة قابلة للتطبيق فوراً: المشكلة في جملة، ثم الحل "
            "بخطوات مختصرة جداً، ثم النتيجة المتوقّعة."
        ),
    },
    "question": {
        "label": "سؤال يفتح نقاشاً",
        "parts": 1,
        "directive": (
            "اطرح سؤالاً حقيقياً يستحق النقاش داخل تخصصك: مهّد بسطر يوضّح السياق، "
            "ثم اسأل سؤالاً واحداً محدّداً (لا سؤالاً عاماً مثل «ما رأيكم؟»)."
        ),
    },
    "story": {
        "label": "قصة/موقف",
        "parts": 1,
        "directive": (
            "اروِ موقفاً قصيراً واقعياً أو حالة ملموسة من المجال، ثم استخلص العبرة "
            "في جملة واحدة. بصوت إنساني تماماً بلا وعظ."
        ),
    },
    "myth_bust": {
        "label": "تصحيح مفهوم",
        "parts": 1,
        "directive": (
            "صحّح مفهوماً خاطئاً شائعاً في مجالك: اذكر الاعتقاد الشائع، ثم لماذا هو "
            "غير دقيق، ثم البديل الصحيح — بإيجاز وبلا تعالٍ."
        ),
    },
    "thread": {
        "label": "ثريد تحليلي",
        "parts": 4,
        "directive": (
            "اكتب ثريداً تحليلياً: التغريدة الأولى خطّاف قوي يوعد بقيمة واضحة، ثم "
            "كل تغريدة تالية فكرة واحدة مستقلة تبني على ما قبلها، والأخيرة خلاصة "
            "عملية أو سؤال يفتح النقاش."
        ),
    },
    "listicle": {
        "label": "ثريد قائمة",
        "parts": 5,
        "directive": (
            "اكتب ثريد قائمة مرقّمة: التغريدة الأولى تحدّد الوعد والعدد، ثم كل "
            "تغريدة عنصر واحد بصياغة عملية مختصرة، والأخيرة خلاصة تدفع للحفظ/المشاركة."
        ),
    },
}

#: صيغ افتراضية متاحة إن لم يحدّد المستخدم شيئاً (متوازنة: منها ثريد واحد)
_DEFAULT_FORMATS = ["insight", "hot_take", "howto", "question", "story",
                    "myth_bust", "thread", "listicle"]

#: محاور عامة تُستخدم فقط عندما لا تكون للشخصية اهتمامات محدّدة
_FALLBACK_PILLARS = ["تحليل وآراء", "شرح وتعليم", "أخبار وتحديثات", "تفاعل مع الجمهور"]

_LEDGER_KEY = "post_performance"
_MAX_LEDGER_POSTS = 80
#: متوسط تفاعل مبدئي يُفترض لأي صيغة/محور لم يُجرَّب (Bayesian prior مبسّط)
_PRIOR_ENGAGEMENT = 6.0
_PRIOR_WEIGHT = 2.0


# ─────────────────────────── pillars & formats ───────────────────────────
def content_pillars(config) -> List[str]:
    """محاور المحتوى لهذا الموظف: يدوية إن وُجدت، وإلا من اهتماماته."""
    rules = getattr(config, "business_rules", None) or {}
    manual = [str(p).strip() for p in (rules.get("content_pillars") or []) if str(p).strip()]
    if manual:
        return manual[:8]
    interests = [str(i).strip() for i in (getattr(config.persona, "interests", None) or [])
                 if str(i).strip()]
    return (interests or _FALLBACK_PILLARS)[:8]


def enabled_formats(config) -> List[str]:
    """الصيغ المسموح بها: يدوية إن وُجدت، وإلا الافتراضية (الثريد قابل للإيقاف)."""
    rules = getattr(config, "business_rules", None) or {}
    manual = [str(f).strip() for f in (rules.get("content_formats") or []) if str(f).strip()]
    formats = [f for f in (manual or _DEFAULT_FORMATS) if f in CONTENT_FORMATS]
    if not bool(rules.get("allow_threads", True)):
        formats = [f for f in formats if CONTENT_FORMATS[f]["parts"] <= 1]
    return formats or ["insight"]


def available_formats(config) -> List[str]:
    """الصيغ القابلة للتنفيذ **الآن** — بعد استبعاد ما استُنفدت حصته اليوم.

    خلل مُقاس: الثريد له حصة يومية خاصة (``daily_thread_target``، افتراضياً 1)
    لأنه مكلف. وكانت الخطة تختار «ثريد» بناءً على أدائه، فتقترح المهارة
    ``publish_thread``، فيرفضها المحرّك لاستنفاد الحصة — ولا تُختار صيغة بديلة.
    النتيجة في محاكاة 40 دورة: 5 منشورات فقط بدل 10، وكلها ثريد. الخطة يجب أن
    تعرف الحصص قبل أن تختار، لا أن تكتشفها بالرفض.
    """
    rules = getattr(config, "business_rules", None) or {}
    counters = getattr(config.runtime_state, "counters", None) or {}
    formats = enabled_formats(config)
    try:
        thread_target = int(rules.get("daily_thread_target", 1))
        threads_today = int(counters.get("threads_today", 0))
    except (TypeError, ValueError):
        return formats
    if threads_today >= thread_target:
        singles = [f for f in formats if CONTENT_FORMATS[f]["parts"] <= 1]
        return singles or formats
    return formats


def format_spec(format_id: str) -> Dict[str, Any]:
    return CONTENT_FORMATS.get(format_id) or CONTENT_FORMATS["insight"]


# ─────────────────────────── performance ledger ───────────────────────────
def engagement_score(metrics: Dict[str, Any]) -> int:
    """درجة الأداء الموحّدة لمنشور — المصدر الوحيد لهذه المعادلة.

    التفاعلات هي الإشارة الأقوى (الرد أثقل من الإعجاب لأنه جهد أكبر من القارئ)،
    لكن **المشاهدات تُحسب أيضاً بوزن خفيف**: على حساب حقيقي اختبرناه كانت كل
    المنشورات بصفر إعجاب مع 58–105 مشاهدة؛ لو أهملنا المشاهدات لصارت كل
    الدرجات صفراً فلا يستطيع الموظف التمييز بين ناجح وفاشل ولا يتعلّم شيئاً.
    """
    def _n(key: str) -> int:
        try:
            return int(metrics.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return int(_n("likes") + 2 * _n("reposts") + 3 * _n("replies")
               + 5 * _n("bookmarks") + _n("views") / 50.0)


def _ledger(config) -> Dict[str, Any]:
    flags = config.runtime_state.flags
    ledger = flags.get(_LEDGER_KEY)
    if not isinstance(ledger, dict):
        ledger = {"posts": {}, "last_metrics_read": None, "seq": 0}
        flags[_LEDGER_KEY] = ledger
    ledger.setdefault("posts", {})
    ledger.setdefault("seq", 0)
    return ledger


def _next_seq(ledger: Dict[str, Any]) -> int:
    """ترتيب تسلسلي صريح للمنشورات.

    لا يمكن الاعتماد على ``posted_at`` وحده: عدة منشورات في نفس الثانية تتساوى،
    وترتيبها المستقرّ يجعل «الأحدث» يبدو «الأقدم» فتُطبَّق عقوبة التكرار مقلوبة
    (كان الحساب يتجمّد على محور واحد بسبب هذا بالضبط).
    """
    ledger["seq"] = int(ledger.get("seq", 0)) + 1
    return ledger["seq"]


def _order_key(post: Dict[str, Any]):
    return (int(post.get("seq", 0) or 0), str(post.get("posted_at") or ""))


def record_published(config, *, url: str, text: str, pillar: str, format_id: str,
                     parts: int = 1, has_media: bool = False) -> None:
    """سجّل منشوراً جديداً بمحوره وصيغته — الأساس الذي يُقاس عليه الأداء لاحقاً."""
    key = (url or "").strip() or f"nourl-{datetime.now(timezone.utc).timestamp()}"
    ledger = _ledger(config)
    posts: Dict[str, Any] = ledger["posts"]
    now = datetime.now(timezone.utc)
    posts[key] = {
        "seq": _next_seq(ledger),
        "text": (text or "")[:280],
        "pillar": pillar or "",
        "format": format_id or "",
        "parts": int(parts or 1),
        "has_media": bool(has_media),
        "posted_at": now.isoformat(timespec="seconds"),
        "hour": now.hour,
        "likes": 0, "reposts": 0, "replies": 0, "views": 0, "engagement": 0,
        "measured": False,
    }
    _trim_ledger(ledger)


def update_metrics(config, measured: List[Dict[str, Any]]) -> Dict[str, int]:
    """حدّث أرقام الأداء المقروءة من X داخل السجل.

    ``measured``: عناصر ``{url, likes, reposts, replies, views, engagement, text}``
    كما يعيدها ``playwright_x.read_my_posts``. المنشورات التي لا نعرف محورها
    (نُشرت قبل تشغيل الاستراتيجية أو يدوياً) تُضاف بمحور فارغ حتى لا نفقد
    إشارة الأداء.
    """
    ledger = _ledger(config)
    posts: Dict[str, Any] = ledger["posts"]
    updated = added = 0
    for item in measured or []:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        entry = posts.get(url)
        if entry is None:
            posts[url] = {
                "seq": _next_seq(ledger),
                "text": (item.get("text") or "")[:280],
                "pillar": "", "format": "", "parts": 1,
                "has_media": bool(item.get("has_media")),
                "posted_at": item.get("created_at") or "",
                "hour": _hour_of(item.get("created_at")),
                "measured": True,
            }
            entry = posts[url]
            added += 1
        else:
            updated += 1
        for field in ("likes", "reposts", "replies", "views", "bookmarks"):
            entry[field] = int(item.get(field, 0) or 0)
        # الدرجة تُحسب دائماً من نفس المعادلة (ولو أرسل المصدر قيمة جاهزة)
        entry["engagement"] = engagement_score(entry)
        entry["measured"] = True
        entry["measured_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ledger["last_metrics_read"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _trim_ledger(ledger)
    return {"updated": updated, "added": added,
            "total": len([p for p in posts.values() if p.get("measured")])}


def _hour_of(iso: str) -> int:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).hour
    except (TypeError, ValueError):
        return -1


def _trim_ledger(ledger: Dict[str, Any]) -> None:
    posts: Dict[str, Any] = ledger.get("posts") or {}
    if len(posts) <= _MAX_LEDGER_POSTS:
        return
    ordered = sorted(posts.items(), key=lambda kv: _order_key(kv[1]), reverse=True)
    ledger["posts"] = dict(ordered[:_MAX_LEDGER_POSTS])


def minutes_since_metrics_read(config) -> float:
    """كم دقيقة مضت على آخر قراءة أداء؟ ``inf`` إن لم تُقرأ أبداً."""
    raw = _ledger(config).get("last_metrics_read")
    if not raw:
        return math.inf
    try:
        then = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return math.inf
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 60.0


# ─────────────────────────── learning from performance ───────────────────
def _measured(config) -> List[Dict[str, Any]]:
    return [p for p in (_ledger(config)["posts"] or {}).values()
            if p.get("measured") and int(p.get("engagement", 0) or 0) >= 0]


def _mean_by(config, field: str) -> Dict[str, Tuple[float, int]]:
    """متوسط التفاعل لكل قيمة من قيم ``field`` مع عدد العيّنات."""
    buckets: Dict[str, List[int]] = {}
    for post in _measured(config):
        key = str(post.get(field) or "").strip()
        if not key:
            continue
        buckets.setdefault(key, []).append(int(post.get("engagement", 0) or 0))
    return {k: (sum(v) / len(v), len(v)) for k, v in buckets.items() if v}


def _smoothed(mean_n: Optional[Tuple[float, int]]) -> float:
    """متوسط مُنعَّم بالـprior: عيّنة واحدة ناجحة لا تُصادر القرار للأبد."""
    if not mean_n:
        return _PRIOR_ENGAGEMENT
    mean, n = mean_n
    return (mean * n + _PRIOR_ENGAGEMENT * _PRIOR_WEIGHT) / (n + _PRIOR_WEIGHT)


def performance_report(config) -> Dict[str, Any]:
    """تقرير تعلّم مقروء: أفضل الصيغ والمحاور والساعات وأفضل المنشورات."""
    formats = _mean_by(config, "format")
    pillars = _mean_by(config, "pillar")
    hours: Dict[str, List[int]] = {}
    for post in _measured(config):
        hour = int(post.get("hour", -1) or -1)
        if hour >= 0:
            hours.setdefault(str(hour), []).append(int(post.get("engagement", 0) or 0))
    best_hours = sorted(
        ((int(h), sum(v) / len(v), len(v)) for h, v in hours.items() if v),
        key=lambda x: x[1], reverse=True,
    )
    return {
        "samples": len(_measured(config)),
        "formats": {k: {"avg_engagement": round(v[0], 1), "posts": v[1]}
                    for k, v in sorted(formats.items(), key=lambda kv: -kv[1][0])},
        "pillars": {k: {"avg_engagement": round(v[0], 1), "posts": v[1]}
                    for k, v in sorted(pillars.items(), key=lambda kv: -kv[1][0])},
        "best_hours": [{"hour": h, "avg_engagement": round(a, 1), "posts": n}
                       for h, a, n in best_hours[:5]],
        "top_posts": top_performers(config, 3),
    }


def top_performers(config, limit: int = 3) -> List[Dict[str, Any]]:
    """أفضل المنشورات أداءً — تُعطى للنموذج كمرجع «ما الذي نجح فعلاً»."""
    posts = sorted(_measured(config), key=lambda p: int(p.get("engagement", 0) or 0),
                   reverse=True)
    return [
        {"text": (p.get("text") or "")[:200], "format": p.get("format", ""),
         "pillar": p.get("pillar", ""), "engagement": int(p.get("engagement", 0) or 0),
         "likes": int(p.get("likes", 0) or 0), "views": int(p.get("views", 0) or 0)}
        for p in posts[:limit] if int(p.get("engagement", 0) or 0) > 0
    ]


def _recent_choices(config, limit: int = 6) -> List[Dict[str, str]]:
    """آخر الاختيارات **الأحدث أولاً** (يعتمد على الترتيب التسلسلي لا الثواني)."""
    posts = sorted((_ledger(config)["posts"] or {}).values(),
                   key=_order_key, reverse=True)
    return [{"pillar": str(p.get("pillar") or ""), "format": str(p.get("format") or "")}
            for p in posts[:limit]]


# ─────────────────────────── the decision ────────────────────────────────
def choose_plan(config, prefer_format: str = "") -> Dict[str, Any]:
    """اختر محور المحتوى وصيغته لهذه الدورة.

    ``prefer_format``: صيغة يرشّحها حارس التقييم بناءً على نقد الدورة السابقة —
    تُعطى دفعة معتبرة لكنها لا تُلغي الأداء ولا عقوبة التكرار (حتى لا يتجمّد
    الحساب على اقتراح واحد لو أخطأ الحارس).

    المعادلة: ``score = أداء نسبي × (1 − عقوبة تكرار) + مكافأة استكشاف``

    - **الأداء نسبي لا مطلق (0..1)**: هذه نقطة حاسمة مُقاسة. كان المتوسط يدخل
      المعادلة بمقياسه الخام غير المحدود بينما العقوبة والاستكشاف ثابتان
      صغيران، فثريد واحد حقّق 20 ألف مشاهدة يعطي متوسطاً ≈ 400 مقابل 6 للصيغة
      غير المجرَّبة: حتى بأقصى عقوبة تكرار (×0.2) يبقى 80 مقابل 8 — أي أن
      **نجاحاً واحداً مبكّراً يُجمّد الحساب على تلك الصيغة إلى الأبد**. محاكاة
      40 دورة أعطت 100% ثريد و100% محوراً واحداً. القسمة على الأفضل تُبقي
      الترجيح للأداء لكنها تجعل العقوبة والاستكشاف قادرين على قلب الاختيار.
    - **عقوبة التكرار**: ما استُخدم في آخر المنشورات يُخفَّض تدريجياً.
    - **سقف التتابع الصلب**: لا تتكرر الصيغة/المحور أكثر من
      ``max_same_choice_streak`` مرات متتالية مهما كان أداؤها — ضمانة تنويع لا
      تعتمد على معايرة أوزان.
    - **الاستكشاف**: ما لم يُجرَّب يُدفع للأعلى ليُختبر.
    """
    pillars = content_pillars(config)
    formats = available_formats(config)
    fmt_perf = _mean_by(config, "format")
    pil_perf = _mean_by(config, "pillar")
    recent = _recent_choices(config, limit=max(len(pillars), len(formats), 4))
    rules = getattr(config, "business_rules", None) or {}
    explore = float(rules.get("strategy_exploration", 0.35))
    try:
        max_streak = max(int(rules.get("max_same_choice_streak", 2)), 1)
    except (TypeError, ValueError):
        max_streak = 2

    def _streak(field: str, opt: str) -> int:
        """كم مرة متتالية (الأحدث أولاً) اختير هذا الخيار."""
        n = 0
        for choice in recent:
            if choice.get(field) == opt:
                n += 1
            else:
                break
        return n

    def _pick(options: List[str], perf: Dict[str, Tuple[float, int]], field: str) -> Tuple[str, str]:
        # استبعاد ما تجاوز سقف التتابع (ما لم يكن الخيار الوحيد المتاح)
        allowed = [o for o in options if _streak(field, o) < max_streak] or options
        # الأداء النسبي: الأفضل = 1.0 والبقية كسور منه — مقياس مقيّد يمكن
        # للعقوبة والاستكشاف أن يؤثّرا فيه فعلاً.
        smoothed = {o: _smoothed(perf.get(o)) for o in allowed}
        top = max(smoothed.values()) or 1.0

        best, best_score, why = allowed[0], -math.inf, ""
        for opt in allowed:
            perf_score = smoothed[opt]
            rel = perf_score / top
            samples = perf.get(opt, (0.0, 0))[1]
            # عقوبة تكرار: الأحدث أشد (0.45 ثم 0.22 ثم 0.11 ...)
            penalty = 0.0
            for age, choice in enumerate(recent):
                if choice.get(field) == opt:
                    penalty += 0.45 * (0.5 ** age)
            penalty = min(penalty, 0.8)
            bonus = explore * (1.0 / (1.0 + samples))
            if field == "format" and prefer_format and opt == prefer_format:
                # الدفعة معايَرة على المقياس النسبي (0..1): 0.25 تكفي لترجيح
                # ترشيح الحارس عند التقارب، ولا تكفي لإلغاء أداءٍ مُثبت —
                # القيمة القديمة (0.6 على مقياس خام كبير) صارت بعد التطبيع
                # أكبر من حدّ الأداء نفسه فكانت تفرض ترشيح الحارس دائماً.
                bonus += float(rules.get("critique_focus_boost", 0.25))
            score = rel * (1.0 - penalty) + bonus
            if score > best_score:
                best, best_score = opt, score
                why = (f"أداء متوقّع {perf_score:.1f}"
                       + (f" من {samples} منشور" if samples else " (لم يُجرَّب بعد)")
                       + (f"، خُفّض {penalty:.0%} لتكراره مؤخراً" if penalty else "")
                       + (" (رشّحها حارس التقييم)"
                          if field == "format" and opt == prefer_format else ""))
        skipped = [o for o in options if o not in allowed]
        if skipped:
            why += f"، واستُبعد {'، '.join(skipped)} لتكراره {max_streak} مرات متتالية"
        return best, why

    pillar, pillar_why = _pick(pillars, pil_perf, "pillar")
    format_id, format_why = _pick(formats, fmt_perf, "format")
    spec = format_spec(format_id)

    plan = {
        "pillar": pillar,
        "format": format_id,
        "format_label": spec["label"],
        "parts": int(spec.get("parts", 1)),
        "directive": spec["directive"],
        "why": f"المحور: {pillar_why} | الصيغة ({spec['label']}): {format_why}",
        "top_performers": top_performers(config, 2),
        "best_hours": [h["hour"] for h in performance_report(config)["best_hours"]],
    }
    series = _series_step(config, pillar)
    if series:
        plan["series"] = series
    return plan


def _series_step(config, pillar: str) -> Dict[str, Any]:
    """سلسلة مستمرة (اختيارية): «سلسلة X — الحلقة رقم N».

    تُفعَّل عبر ``business_rules['content_series']`` = اسم السلسلة. الرقم يُخزَّن
    في الأعلام فيستمر عبر الدورات، فيصنع اعتياداً لدى الجمهور.
    """
    rules = getattr(config, "business_rules", None) or {}
    name = str(rules.get("content_series") or "").strip()
    if not name:
        return {}
    every = max(int(rules.get("content_series_every_posts", 3) or 3), 1)
    flags = config.runtime_state.flags
    state = flags.setdefault("content_series_state", {"episode": 0, "posts_since": 999})
    state["posts_since"] = int(state.get("posts_since", 0)) + 1
    if state["posts_since"] < every:
        return {}
    state["posts_since"] = 0
    state["episode"] = int(state.get("episode", 0)) + 1
    return {"name": name, "episode": state["episode"], "pillar": pillar}


def strategy_prompt_block(plan: Dict[str, Any]) -> str:
    """كتلة نصية تُدرَج في البريف — تُترجم الخطة إلى تعليمات كتابة صريحة."""
    if not plan:
        return ""
    lines = [
        f"محور المحتوى لهذه الدورة: {plan.get('pillar', '')}.",
        f"صيغة العرض المطلوبة ({plan.get('format_label', '')}): {plan.get('directive', '')}",
    ]
    if int(plan.get("parts", 1)) > 1:
        lines.append(
            f"عدد تغريدات الثريد: {plan['parts']} كحد أقصى، كل تغريدة أقل من 270 حرفاً "
            "ومستقلة المعنى، بلا ترقيم آلي مثل «1/5» إلا إن كانت الصيغة قائمة."
        )
    series = plan.get("series") or {}
    if series:
        lines.append(
            f"هذا المنشور حلقة من سلسلة «{series['name']}» — الحلقة رقم "
            f"{series['episode']}. اذكر اسم السلسلة ورقم الحلقة بشكل طبيعي في المنشور."
        )
    top = plan.get("top_performers") or []
    if top:
        lines.append("منشوراتك الأنجح سابقاً (اقتبس منها الأسلوب لا الموضوع):")
        for t in top:
            lines.append(f"  - ({t['engagement']} تفاعل) {t['text'][:140]}")
    return "\n".join(lines)
