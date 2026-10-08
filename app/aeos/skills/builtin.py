"""
Built-in skills.

Each skill inspects the execution context (goals, memory, event, counters) and
proposes candidate actions. The Planner/Decision Engine then selects which to
execute this cycle. Skills never execute actions themselves — separation of
concerns between *proposing* and *deciding/executing*.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, List

from ..employee import topic_intel
from ..employee.persona_templates import (
    normalize_topic_words,
    relevance_score,
    topic_overlap_ratio,
)
from .base import ISkill, ProposedAction

if TYPE_CHECKING:
    from ..cognition.context import ExecutionContext


def _rel(context: "ExecutionContext", text: str, interests: List[str],
         item: "dict | None" = None) -> int:
    """صلة بالتخصص: الكلمات + الهامش الدلالي من ``TopicIntelPhase`` إن توفّر."""
    return topic_intel.relevance(context, text, interests, item)


def _profile_url_from_handle(handle: str) -> str:
    """Turn '@user' (or 'user') into a full X profile URL."""
    h = (handle or "").strip().lstrip("@").strip()
    return f"https://x.com/{h}" if h else ""


def _best_tweet_media(url: str) -> str:
    """Normalize an X media URL to a good quality, download-friendly form.

    pbs.twimg.com media often carries ``name=small``/``360x360`` — bump it to
    ``name=large`` for a full-resolution image.
    """
    u = (url or "").strip()
    if not u:
        return ""
    if "pbs.twimg.com" in u and "name=" in u:
        u = re.sub(r"name=[^&]+", "name=large", u)
    return u


def _relevant_timeline(context: "ExecutionContext") -> List[dict]:
    """Timeline tweets ranked by relevance to the employee's interests.

    If the persona has interests, only tweets matching those interests are kept
    (so a political persona engages with political content). If no interests are
    set, all timeline tweets are considered (generic engagement).
    """
    tweets = context.scratch.get("timeline") or []
    interests = getattr(context.config.persona, "interests", []) or []
    if not interests:
        return list(tweets)
    scored = []
    for t in tweets:
        score = _rel(context, f"{t.get('text', '')} {t.get('handle', '')}", interests, t)
        if score > 0:
            scored.append((topic_intel.blended_score(score, topic_intel.importance(t)), t))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [t for _, t in scored]


def _engaged_urls(context: "ExecutionContext") -> set:
    """URLs (tweets/profiles) the employee already engaged with — never repeat."""
    return set(context.config.runtime_state.flags.get("engaged_urls", []) or [])


def _engaged_topic_sets(context: "ExecutionContext") -> List[set]:
    """Word-signatures of topics the employee already engaged with recently."""
    raw = context.config.runtime_state.flags.get("engaged_topics", []) or []
    return [set(x) for x in raw if x]


def _topic_recently_engaged(context: "ExecutionContext", text: str,
                            threshold: float = 0.6) -> bool:
    """True if ``text`` covers a topic already engaged with (avoid repetition).

    Compares the text's word signature to stored engaged-topic signatures using
    an overlap ratio; a high overlap means "same subject, different link".
    """
    words = normalize_topic_words(text)
    if len(words) < 3:
        return False
    for prev in _engaged_topic_sets(context):
        if topic_overlap_ratio(words, prev) >= threshold:
            return True
    return False


_AD_SPAM_PATTERNS = [
    r"\bpromoted\b", r"\bsponsored\b", r"\bad\b", r"\bads\b",
    r"\bgiveaway\b", r"\bairdrop\b", r"\bfollow\s+me\b",
    r"\bdiscount\b", r"\bpromo\b", r"\bbuy\s+now\b",
    r"إعلان", r"ممول", r"خصم", r"اشتر", r"رابط\s*الشراء",
    r"سجل\s*الآن", r"اربح", r"مسابقة\s*سحب", r"ايردروب",
]


def _is_ad_or_spam_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(re.search(p, t, re.IGNORECASE) for p in _AD_SPAM_PATTERNS)


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _persona_donts(context: "ExecutionContext") -> List[str]:
    """Deterministic blocklist phrases from the persona's ``donts``.

    Keeps only meaningful tokens/phrases so the fast filter can reject any
    engagement target or content topic that conflicts with the persona.
    """
    persona = context.config.persona
    phrases: List[str] = []
    for d in (getattr(persona, "donts", []) or []):
        s = (d or "").strip().lower()
        if len(s) >= 3:
            phrases.append(s)
    return phrases


def _violates_persona_donts(text: str, context: "ExecutionContext") -> bool:
    """True if the text clearly violates a persona ``dont`` phrase/keyword.

    Matches a whole dont phrase, or (for multi-word donts) a strong overlap of
    its significant words, so short topical keywords in donts are honored.
    """
    t = (text or "").strip().lower()
    if not t:
        return False
    for phrase in _persona_donts(context):
        if phrase in t:
            return True
        words = [w for w in _WORD_RE.findall(phrase) if len(w) >= 3]
        if words and sum(1 for w in words if w in t) >= max(1, len(words) // 2 + 1):
            return True
    return False


def _is_specialty_match(context: "ExecutionContext", text: str) -> bool:
    """Strict specialty gate: only allow engagement inside persona interests."""
    rules = context.config.business_rules or {}
    interests = getattr(context.config.persona, "interests", []) or []
    enforce_scope = bool(rules.get("enforce_interest_scope", True))
    min_rel = int(rules.get("min_relevance_score", 1))

    if _is_ad_or_spam_text(text):
        return False
    # التزام البرسونا: لا تفاعل مع ما يخالف تعليمات "لا تفعل"
    if _violates_persona_donts(text, context):
        return False
    if not interests:
        return not _is_ad_or_spam_text(text)
    if not enforce_scope:
        return not _is_ad_or_spam_text(text)

    cleaned = (text or "").strip()
    if not cleaned:
        return False
    return _rel(context, cleaned, interests) >= min_rel


def _engagement_pool(context: "ExecutionContext") -> List[dict]:
    """Unified pool of tweets to engage with, best first (strict specialty).

    - Never engage with ads/spam.
    - When interests exist and enforce_scope=True, require explicit relevance
      from tweet text itself (not only source bonus).
    - Keep trend tweets prioritized, but not exempt from specialty checks.
    """
    engaged = _engaged_urls(context)
    interests = getattr(context.config.persona, "interests", []) or []
    rules = context.config.business_rules or {}
    enforce_scope = bool(rules.get("enforce_interest_scope", True))
    min_rel = int(rules.get("min_relevance_score", 1))

    pool: List[dict] = []
    seen = set()

    def _add(tweets: List[dict], bonus: int) -> None:
        for t in tweets or []:
            url = (t.get("url") or "").strip()
            text = (t.get("text") or "").strip()
            handle = (t.get("handle") or "").strip()
            hay = f"{text} {handle}".strip()

            if not url or url in seen or url in engaged:
                continue
            if _is_ad_or_spam_text(hay):
                continue
            # التزام البرسونا: استبعد أي هدف يخالف تعليمات "لا تفعل"
            if _violates_persona_donts(hay, context):
                continue
            # منع التكرار على مستوى الموضوع (لا الرابط فقط)
            if _topic_recently_engaged(context, text):
                continue

            rel = _rel(context, hay, interests, t) if interests else 0

            # specialty gate: if interests are set and scope enforced, require strict match
            if interests and enforce_scope and rel < min_rel:
                continue

            seen.add(url)
            # الترتيب: الصلة (مسقوفة) + الأهمية (انتشار × حداثة) + أولوية المصدر
            score = topic_intel.blended_score(rel, topic_intel.importance(t), bonus)
            pool.append({**t, "_score": score, "_rel": rel})

    # أهم البوستات (Top Posts) أولاً — الأكثر أداءً في المجال، ثم تغريدات
    # الهاشتاق الترند المطابق للتخصص (مشاركة داخل النقاش الرائج)، ثم ترند X،
    # ثم التايم لاين
    _add(context.scratch.get("top_posts") or [], bonus=3)
    # نتائج بحث الاهتمامات المباشر — أهداف مضمونة الصلة بالتخصص، تُجلب عندما
    # يكون التايم لاين/الترند خارج اهتمامات الموظف (وإلا يجوع التفاعل كلياً)
    _add(context.scratch.get("interest_tweets") or [], bonus=3)
    _add(context.scratch.get("hashtag_tweets") or [], bonus=2)
    _add(context.scratch.get("trend_tweets") or [], bonus=2)
    _add(context.scratch.get("timeline") or [], bonus=0)

    pool.sort(key=lambda t: t.get("_score", 0), reverse=True)
    return pool


def claim_targets(context: "ExecutionContext", items: List[dict],
                  count: int, owner: str = "") -> List[dict]:
    """احجز أهدافاً **لم تحجزها مهارة أخرى في هذه الدورة**.

    خلل بنيوي مُقاس: كل المهارات تقرأ ``_engagement_pool`` نفسه وترتّبه بنفس
    المعيار، فتلتقط جميعها التغريدة الأولى ذاتها. ثم يُبقي منعُ تكرار الهدف
    (``SkillBasedPlanner._limit_per_target``) إجراءً صاخباً واحداً عليها،
    فتُسقَط دائماً المهارةُ الأدنى أولويةً — قاست المحاكاة **صفر اقتباس في 40
    دورة** رغم أن القدرة مفعّلة وميزانيتها كاملة تماماً.

    الحجز يجعل كل مهارة تعمل على موضوع مختلف، فيصير تنوّع الدورة تنوّعَ
    **مواضيع** لا تنوّع أفعال على الموضوع نفسه. الحجز داخل الدورة فقط
    (``context.scratch``) ولا يترك أثراً دائماً.

    ``owner`` (اسم المهارة/الفعل) يجعل الحجز **ثابت النتيجة**: استدعاء
    ``propose()`` مرتين في الدورة نفسها يعيد الأهداف ذاتها لا أهدافاً جديدة.
    بدونه يصير الاقتراح ذا أثر جانبي: أي استدعاء ثانٍ (فحص، إعادة محاولة،
    مسار بديل عند رفض الحارس) يجد الحوض محجوزاً فيعيد لا شيء.
    """
    store: Dict[str, List[str]] = context.scratch.setdefault("_claims", {})
    claimed: set = context.scratch.setdefault("_claimed_targets", set())
    pool = {(i.get("url") or "").strip(): i for i in (items or [])}
    if owner and owner in store:
        return [pool[u] for u in store[owner] if u in pool]

    out: List[dict] = []
    for item in items or []:
        url = (item.get("url") or "").strip()
        if not url or url in claimed:
            continue
        claimed.add(url)
        out.append(item)
        if len(out) >= max(count, 0):
            break
    if owner:
        store[owner] = [(i.get("url") or "").strip() for i in out]
    return out


def _has_strong_engagement_candidates(context: "ExecutionContext") -> bool:
    """True when there are enough in-scope timeline/trend items to engage now."""
    rules = context.config.business_rules or {}
    threshold = int(rules.get("strong_engagement_threshold", 2))
    return len(_engagement_pool(context)[:threshold]) >= threshold


def _engagement_first_damping(context: "ExecutionContext") -> float:
    """معامل خفض أولوية النشر عند وجود فرص تفاعل قوية — **لا إلغاؤه**.

    كان الكود يُرجع ``[]`` فيمتنع الموظف عن النشر كلياً كلما وُجد مرشّحان
    للتفاعل، فيمرّ اليوم كله بلا منشور واحد (صانع محتوى لا ينشر!). الآن نخفض
    الأولوية فقط، ويبقى القرار لمحرّك المنفعة الذي قد ينفّذ النشر والتفاعل معاً
    في الدورة نفسها (``max_actions_per_cycle``).
    """
    if not _has_strong_engagement_candidates(context):
        return 1.0
    rules = context.config.business_rules or {}
    counters = context.config.runtime_state.counters
    # إن لم يُنشر شيء اليوم فالنشر واجب: لا خفض تقريباً
    if int(counters.get("posts_today", 0)) == 0:
        return float(rules.get("engagement_first_damping_empty_day", 0.95))
    return float(rules.get("engagement_first_damping", 0.8))


def _content_plan(context: "ExecutionContext") -> dict:
    """خطة المحتوى التي أعدّها ``StrategyPhase`` (محور + صيغة + سلسلة)."""
    plan = context.scratch.get("content_plan")
    return plan if isinstance(plan, dict) else {}


def _strategy_brief_suffix(context: "ExecutionContext") -> str:
    """كتلة الاستراتيجية التي تُضاف إلى أي بريف محتوى (محور/صيغة/سلسلة/الأنجح)."""
    block = context.scratch.get("strategy_block") or ""
    return f"\n\n[استراتيجية الحساب]\n{block}" if block else ""


#: ترويسات أقسام X التي تتنكّر كأسماء ترندات («الاقتصاد و الأعمال متداول»)
_CATEGORY_LABEL_RE = re.compile(
    r"(متداول|متداوَل|يتداول|الأكثر\s*تداول|trending|الترند\s*في|رائج|\bLIVE\b)",
    re.IGNORECASE,
)


def _is_weak_topic(title: str, summary: str) -> bool:
    """موضوع لا يصلح أساساً لمنشور: ترويسة قسم، أو عنوان بلا مضمون.

    كان الموظف يبني منشوراً على «الاقتصاد و الأعمال متداول» (ترويسة قسم في X)
    فيرفضه حارس الشخصية ويُهدر الدورة. نمنع ذلك من المصدر: العنوان الذي يبدو
    تسمية قسم، أو القصير جداً بلا خلفية كافية، لا يصير موضوعاً.
    """
    t = (title or "").strip()
    s = (summary or "").strip()
    if not t:
        return True
    if _CATEGORY_LABEL_RE.search(t):
        return True
    # عنوان قصير (كلمتان أو أقل) بلا خلفية حقيقية لا يكفي لبناء منشور
    if len(t.split()) <= 2 and len(s) < 80:
        return True
    return False


def _grounded_candidates(context: "ExecutionContext") -> List[dict]:
    """ALL real grounded topic candidates within specialty, best first.

    الفصل بين «جمع المرشّحين» و«الاختيار» مقصود: الترتيب الحتمي هنا مجرد
    فرز أولي، والاختيار النهائي يرجع لطبقة «رئيس التحرير» (:func:`_editorial_pick`)
    التي تزن الأهمية والإثارة لا مجرد تطابق الكلمات.
    """
    interests = getattr(context.config.persona, "interests", []) or []
    rules = context.config.business_rules or {}
    enforce_scope = bool(rules.get("enforce_interest_scope", True))
    min_rel = int(rules.get("min_relevance_score", 1))

    candidates: List[dict] = []

    # أهم البوستات (Top Posts) — أقوى مصدر إلهام لبناء منشور أصلي ضمن التخصص
    for t in (context.scratch.get("top_posts") or [])[:8]:
        text = (t.get("text") or "").strip().replace("\n", " ")
        if not text or _is_ad_or_spam_text(text):
            continue
        rel = _rel(context, text, interests, t) if interests else 1
        if interests and enforce_scope and rel < min_rel:
            continue
        media = _best_tweet_media((t.get("media") or "").strip())
        candidates.append({
            "source": "top_post",
            "title": text[:120],
            "summary": text[:280],
            "media_url": media,
            "_rel": rel,
            "_imp": topic_intel.importance(t),
            "_metrics": topic_intel.describe_metrics(t),
            "_media": 1 if media else 0,
            "_priority": 5,
        })

    # الهاشتاق الترند المطابق للتخصص — منشور يتضمّن الهاشتاق نفسه للمشاركة
    # في نقاش الترند الحالي (أعلى وصول وانطباعات)
    matched_ht = context.scratch.get("matched_hashtag") or {}
    ht_name = (matched_ht.get("name") or "").strip()
    if ht_name:
        ht_tweets = context.scratch.get("hashtag_tweets") or []
        ht_media = ""
        for tt in ht_tweets:
            m = _best_tweet_media((tt.get("media") or "").strip())
            if m:
                ht_media = m
                break
        samples = [
            (t.get("text") or "").strip().replace("\n", " ")[:140]
            for t in ht_tweets[:4]
        ]
        summary = " | ".join(s for s in samples if s)
        hay = f"{ht_name} {matched_ht.get('category', '')} {summary}".strip()
        rel = _rel(context, hay, interests) if interests else 1
        if not _is_ad_or_spam_text(hay) and (not interests or not enforce_scope or rel >= min_rel):
            candidates.append({
                "source": "x_hashtag",
                "title": ht_name,
                "summary": summary[:400],
                "media_url": ht_media,
                "hashtag": ht_name,
                "_rel": rel,
                "_imp": max([topic_intel.importance(t) for t in ht_tweets[:4]] or [0.0]),
                "_media": 1 if ht_media else 0,
                "_priority": 5,
            })

    x_trends = context.scratch.get("x_trends") or []
    if x_trends:
        trend_tweets = context.scratch.get("trend_tweets") or []
        # صورة حقيقية من تغريدات الترند (وسائط X الأصلية) لإرفاقها بالمنشور
        trend_media = ""
        for tt in trend_tweets:
            m = _best_tweet_media((tt.get("media") or "").strip())
            if m:
                trend_media = m
                break
        for idx, tr in enumerate(x_trends[:5]):
            name = (tr.get("name") or "").strip()
            cat = (tr.get("category") or "").strip()
            if not name:
                continue
            samples = [
                (t.get("text") or "").strip().replace("\n", " ")[:140]
                for t in trend_tweets[:4]
            ]
            summary = " | ".join(s for s in samples if s)
            hay = f"{name} {cat} {summary}".strip()
            rel = _rel(context, hay, interests, tr) if interests else 1
            if _is_ad_or_spam_text(hay):
                continue
            if interests and enforce_scope and rel < min_rel:
                continue
            # تغريدات الترند تخص الترند الأعلى فقط، لذا نرفق الصورة له
            media = trend_media if idx == 0 else ""
            candidates.append({
                "source": "x_trend",
                "title": name,
                "summary": summary[:400],
                "media_url": media,
                "_rel": rel,
                "_imp": topic_intel.importance(tr),
                "_metrics": (tr.get("posts") or "").strip(),
                "_media": 1 if media else 0,
                "_priority": 4,
            })

    for t in (context.scratch.get("trends") or [])[:10]:
        title = (t.get("title") or "").strip()
        summary = (t.get("summary") or "").strip()
        media = (t.get("media_url") or "").strip()
        if not (title or summary):
            continue
        hay = f"{title} {summary} {t.get('keywords','')} {t.get('category','')}".strip()
        rel = _rel(context, hay, interests, t) if interests else 1
        if _is_ad_or_spam_text(hay):
            continue
        if interests and enforce_scope and rel < min_rel:
            continue
        candidates.append({
            "source": "trend",
            "title": title,
            "summary": summary[:400],
            "media_url": media,
            "_rel": rel,
            "_imp": topic_intel.importance(t),
            "_media": 1 if media else 0,
            "_priority": 3,
        })

    for n in (context.scratch.get("news") or [])[:10]:
        title = (n.get("title") or "").strip()
        summary = (n.get("summary") or "").strip()
        media = (n.get("media_url") or "").strip()
        if not (title or summary):
            continue
        hay = f"{title} {summary}".strip()
        rel = _rel(context, hay, interests, n) if interests else 1
        if _is_ad_or_spam_text(hay):
            continue
        if interests and enforce_scope and rel < min_rel:
            continue
        candidates.append({
            "source": "news",
            "title": title,
            "summary": summary[:400],
            "media_url": media,
            "_rel": rel,
            "_imp": topic_intel.importance(n),
            "_metrics": topic_intel.describe_metrics(n),
            "_media": 1 if media else 0,
            "_priority": 2,
        })

    relevant = _relevant_timeline(context)
    for t in relevant[:5]:
        text = (t.get("text") or "").strip().replace("\n", " ")
        if not text or _is_ad_or_spam_text(text):
            continue
        rel = _rel(context, text, interests, t) if interests else 1
        if interests and enforce_scope and rel < min_rel:
            continue
        # صورة حقيقية من التغريدة نفسها إن وُجدت
        media = _best_tweet_media((t.get("media") or "").strip())
        candidates.append({
            "source": "timeline",
            "title": text[:120],
            "summary": text[:280],
            "media_url": media,
            "_rel": rel,
            "_imp": topic_intel.importance(t),
            "_metrics": topic_intel.describe_metrics(t),
            "_media": 1 if media else 0,
            "_priority": 1,
        })

    # التزام البرسونا + منع التكرار: استبعد ما يخالف "لا تفعل" أو موضوعاً سبق التفاعل معه
    owned = context.scratch.get("trend_owned")
    if owned:
        from ..employee.trend_intel import norm_name

        candidates = [c for c in candidates if norm_name(c.get("hashtag") or c["title"]) != owned]
    candidates = [
        c for c in candidates
        if not _violates_persona_donts(f"{c['title']} {c['summary']}", context)
        and not _topic_recently_engaged(context, f"{c['title']} {c['summary']}")
        and not _is_weak_topic(c["title"], c["summary"])
    ]
    if not candidates:
        return []

    # الصلة + الأهمية + أولوية المصدر، والوسائط ترجيح خفيف (كانت الترتيب الثاني
    # بعد الكلمات مباشرة فتغلب صورةٌ على خبر أهم بلا صورة)
    candidates.sort(
        key=lambda c: topic_intel.blended_score(
            c["_rel"], c.get("_imp", 0.0), c["_priority"]) + 0.03 * c["_media"],
        reverse=True
    )
    return candidates


def _format_candidate(c: dict) -> dict:
    return {
        "source": c["source"],
        "title": c["title"],
        "summary": c["summary"],
        "media_url": c["media_url"],
        "hashtag": c.get("hashtag", ""),
    }


def _grounded_topic(context: "ExecutionContext") -> "dict | None":
    """Return the BEST real grounded topic within specialty, preferring media."""
    candidates = _grounded_candidates(context)
    return _format_candidate(candidates[0]) if candidates else None


def _editorial_pick(context: "ExecutionContext") -> "dict | None":
    """طبقة «رئيس التحرير»: اختيار الموضوع الأهم/الأشوق + زاوية معالجة.

    الفرز الحتمي يقيس تطابق الكلمات فقط، فيفوز الموضوع الأكثر تكراراً لكلمات
    التخصص لا الأكثر أهميةً أو إثارةً للجمهور. هنا يراجع الموظف المرشّحين كمحرر
    محترف: يختار الأجدر بالنشر الآن، ويحدّد زاوية معالجة مميزة وخُطّافاً
    مقترحاً قبل الكتابة. بلا LLM أو عند أي فشل: الأفضل حتمياً (لا توقف أبداً).

    يعيد الموضوع المُختار مع مفاتيح إضافية: ``angle``، ``hook``، ``why``.
    """
    candidates = _grounded_candidates(context)
    if not candidates:
        return None
    best = _format_candidate(candidates[0])
    llm = getattr(context, "llm", None)
    if len(candidates) == 1 or llm is None or not getattr(llm, "available", False):
        return best

    persona = context.config.persona
    interests = "، ".join(getattr(persona, "interests", []) or [])
    role = getattr(persona, "role", "") or ""
    recent = [p[:90] for p in (context.scratch.get("recent_posts") or [])[:5]]
    shortlist = candidates[:8]
    # المرشّحون مرتّبون بالصلة+الأهمية، ورئيس التحرير يرى مؤشرات الانتشار والحداثة
    # نفسها — كان يرى العناوين فقط فيختار «الأشوق» بلا أي دليل على ما يهمّ الجمهور.
    listing = "\n".join(
        f"{i + 1}. [{c['source']}] {c['title'][:110]}"
        + (f" — {c['summary'][:150]}" if c["summary"] else "")
        + (f" ({c['_metrics']})" if c.get("_metrics") else "")
        + f" [أهمية {c.get('_imp', 0):.0%}]"
        for i, c in enumerate(shortlist)
    )
    prompt = (
        f"دور الحساب: {role or 'صانع محتوى'} | التخصص: {interests or 'عام'}\n"
        + (f"آخر منشورات الحساب (لا تكرر مواضيعها): {recent}\n" if recent else "")
        + "\nالمواضيع المرشّحة الآن (كلها حقيقية من مصادر حيّة):\n" + listing +
        "\n\nاختر الموضوع الواحد الأجدر بالنشر الآن بمعيار رئيس التحرير: الأهمية "
        "الفعلية لجمهور التخصص، الجِدّة، قابلية إثارة نقاش — لا مجرد تطابق الكلمات. "
        "ثم حدّد زاوية معالجة مميزة (لا إعادة سرد الخبر) وخُطّافاً مقترحاً للسطر "
        "الأول."
    )
    try:
        out = llm.complete_json(
            system=(
                "أنت رئيس تحرير محترف لحساب X متخصص. "
                'أعد JSON فقط: {"choice": رقم, "angle": "زاوية المعالجة", '
                '"hook": "السطر الأول المقترح", "why": "لماذا هذا الموضوع بجملة"}'
            ),
            prompt=prompt,
        ) or {}
        idx = int(out.get("choice", 0)) - 1
        if not (0 <= idx < len(shortlist)):
            return best
        picked = _format_candidate(shortlist[idx])
        picked["angle"] = str(out.get("angle") or "").strip()[:200]
        picked["hook"] = str(out.get("hook") or "").strip()[:140]
        picked["why"] = str(out.get("why") or "").strip()[:200]
        return picked
    except Exception:  # noqa: BLE001
        return best


# ─────────────────── «في مثل هذا اليوم» — محتوى تاريخي يومي ───────────────────
def _on_this_day_mode(context: "ExecutionContext") -> bool:
    """هل هذا حساب «في مثل هذا اليوم»؟ قاعدة عمل صريحة أو تُستنتج من الشخصية.

    الاستنتاج من نصوص الشخصية يجعل الوضع يعمل فور تحسين البرسونا بدون أي
    إعداد إضافي — وقاعدة ``on_this_day_mode`` تتيح فرضه/تعطيله صراحةً.
    """
    rules = context.config.business_rules or {}
    if "on_this_day_mode" in rules:
        return bool(rules["on_this_day_mode"])
    p = context.config.persona
    hay = " ".join([
        getattr(p, "display_name", "") or "",
        getattr(p, "bio", "") or "",
        getattr(p, "instructions", "") or "",
    ])
    return "مثل هذا اليوم" in hay or "on this day" in hay.lower()


#: كاش أحداث اليوم لكل يوم (المصدر لا يتغير خلال اليوم فلا نعيد الجلب كل دورة)
_OTD_CACHE: dict = {"date": "", "events": []}


def _fetch_on_this_day_events() -> List[dict]:
    """أحداث «في مثل هذا اليوم» من ويكيبيديا العربية (fallback إنجليزي).

    مصدر موثوق ومجاني وبلا مفاتيح: Wikimedia REST ``feed/onthisday/events``.
    يُرجع [{"year": int, "text": str}] مرتبة من الأحدث، مع كاش يومي.
    """
    import datetime

    import requests

    today = datetime.date.today()
    key = f"{today.month:02d}-{today.day:02d}"
    if _OTD_CACHE["date"] == key and _OTD_CACHE["events"]:
        return _OTD_CACHE["events"]

    events: List[dict] = []
    for lang in ("ar", "en"):
        try:
            r = requests.get(
                f"https://{lang}.wikipedia.org/api/rest_v1/feed/onthisday/events/"
                f"{today.month:02d}/{today.day:02d}",
                timeout=20, headers={"User-Agent": "MojAgenticAI/1.0"},
            )
            if r.status_code != 200:
                continue
            for e in r.json().get("events", []):
                text = (e.get("text") or "").strip()
                year = e.get("year")
                if text and year:
                    events.append({"year": int(year), "text": text})
            if events:
                break
        except Exception:  # noqa: BLE001
            continue

    events.sort(key=lambda e: e["year"], reverse=True)
    if events:
        _OTD_CACHE.update({"date": key, "events": events})
    return events


def _today_dates_arabic() -> str:
    """تاريخ اليوم بالميلادي والهجري بالعربية — عمود أسلوب حساب التوثيق."""
    import datetime

    months = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
              "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
    t = datetime.date.today()
    greg = f"{t.day} {months[t.month - 1]} {t.year}م"
    try:
        from hijridate import Gregorian

        h = Gregorian(t.year, t.month, t.day).to_hijri()
        return f"{greg} — {h.day} {h.month_name('ar')} {h.year}هـ"
    except Exception:  # noqa: BLE001
        return greg


def _budget_left(context: "ExecutionContext", capability: str) -> bool:
    """نفس ميزانيات محرّك القرار — نختار فعلاً سيُسمح به بدل اقتراح ما سيُرفض."""
    from ..decision.engine import ENGAGEMENT_BUDGETS

    rules = context.config.business_rules or {}
    counters = context.config.runtime_state.counters
    if capability == "publish_content":
        posts = int(counters.get("posts_today", 0))
        return posts < int(rules.get("daily_post_target", 1)) and \
            posts < int(rules.get("max_posts_per_day", 999))
    counter, rule, default = ENGAGEMENT_BUDGETS[capability]
    return int(counters.get(counter, 0)) < int(rules.get(rule, default))


#: مفتاح سقف الدورة لكل فعل مشاركة (0 = عطّله المالك)
CYCLE_LIMIT_KEYS = {"quote_post": "max_quotes_per_cycle", "reply_to_user": "max_replies_per_cycle"}


#: خرائط المصدر → مفاتيح scratch التي عبّأها طور الرصد
_MISSION_SOURCES = {
    "timeline": ("timeline",),
    "mentions": ("mentions",),
    "trend": ("hashtag_tweets", "trend_tweets", "top_posts"),
    "search": ("interest_tweets", "top_posts"),
    "any": ("top_posts", "interest_tweets", "hashtag_tweets", "trend_tweets",
            "timeline"),
}

#: القدرة المنفّذة لكل فعل في المهمّة
_MISSION_CAPABILITY = {
    "reply": "reply_to_user", "like": "like_post", "retweet": "retweet_post",
    "quote": "quote_post", "follow": "follow_user",
}


def _mission_candidates(context: "ExecutionContext",
                        mission: dict) -> List[dict]:
    """أهداف المهمّة مرتّبة: الأنسب للقيد الموضوعي ثم للتخصص.

    نبني البركة من المصدر الذي طلبه المستخدم تحديداً (تايم لاين/منشنات/ترند)
    ولا نخلطها بغيره، وإلا صار «روح للتايم لاين» يتفاعل مع نتائج بحث. الفلاتر
    الثابتة تبقى دائماً: لا تكرار لهدف سابق، ولا إعلانات/سبام، ولا ما يخالف
    محظورات الشخصية — حتى في النطاق الحرّ.
    """
    source = str(mission.get("source") or "any")
    keys = _MISSION_SOURCES.get(source, _MISSION_SOURCES["any"])
    # من خاطبنا يستحق رداً دائماً: فلتر التخصص لا يُطبّق على المنشنات
    # («شكراً» / «وين المصدر؟» لا تحمل كلمات التخصص فتُقصى زوراً).
    specialty_only = str(mission.get("scope")) != "free" and source != "mentions"
    query_words = normalize_topic_words(str(mission.get("query") or ""))
    engaged = _engaged_urls(context)

    out: List[dict] = []
    seen: set = set()
    for key in keys:
        for t in context.scratch.get(key) or []:
            if not isinstance(t, dict):
                continue
            url = (t.get("url") or "").strip()
            text = (t.get("text") or "").strip()
            handle = (t.get("handle") or "").strip()
            if not url or url in seen or url in engaged:
                continue
            hay = f"{text} {handle}".strip()
            if _is_ad_or_spam_text(hay) or _violates_persona_donts(hay, context):
                continue
            if specialty_only and not _is_specialty_match(context, hay):
                continue
            match = (topic_overlap_ratio(normalize_topic_words(text), query_words)
                     if query_words else 0.0)
            if query_words and match <= 0:
                continue
            seen.add(url)
            out.append({**t, "_match": round(match, 3),
                        "_rel": _rel(context, hay, getattr(
                            context.config.persona, "interests", []) or [], t)})

    out.sort(key=lambda t: (t.get("_match", 0), t.get("_rel", 0)), reverse=True)
    return out


# ─────────────── المهارات نفسها: في المجلد الرئيسي system/skills ───────────────
#: الأسماء القديمة (``from app.aeos.skills.builtin import OnThisDaySkill``) تبقى صالحة:
#: تُحمَّل كسولاً من ``system/skills/<name>/skill.py`` (PEP 562) — فلا استيراد دائري.
def __getattr__(name: str):
    from .system_skills import employee_skill_class

    cls = employee_skill_class(name)
    if cls is None:
        raise AttributeError(name)
    return cls


def default_skills() -> List[ISkill]:
    """مشغّل المهارات المخصّصة + كل مهارات الموظفين في system/skills بترتيبها (``order``)."""
    from .custom import CustomSkillRunner
    from .system_skills import employee_skill_instances

    # الترتيب مهمّ: المهارات تحجز أهدافها من حوض واحد عبر ``claim_targets``،
    # فتحجز أولاً الأفعالُ **النادرة عالية القيمة** (اقتباس: 1-2 يومياً) قبل
    # الأفعال الوفيرة (إعجاب: 12 يومياً) التي تجد بديلاً دائماً. عكس الترتيب
    # كان يعني أن الإعجاب يلتهم أفضل الأهداف ولا يبقى للاقتباس شيء.
    return [CustomSkillRunner(), *employee_skill_instances()]
