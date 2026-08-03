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

from ..employee.persona_templates import (
    normalize_topic_words,
    relevance_score,
    topic_overlap_ratio,
)
from .base import ISkill, ProposedAction

if TYPE_CHECKING:
    from ..cognition.context import ExecutionContext


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
        score = relevance_score(f"{t.get('text', '')} {t.get('handle', '')}", interests)
        if score > 0:
            scored.append((score, t))
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
    return relevance_score(cleaned, interests) >= min_rel


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

            rel = relevance_score(hay, interests) if interests else 0

            # specialty gate: if interests are set and scope enforced, require strict match
            if interests and enforce_scope and rel < min_rel:
                continue

            seen.add(url)
            # ranking: relevance first, then source priority bonus
            score = (rel * 10) + bonus
            pool.append({**t, "_score": score, "_rel": rel})

    # أهم البوستات (Top Posts) أولاً — الأكثر أداءً في المجال، ثم تغريدات
    # الهاشتاق الترند المطابق للتخصص (مشاركة داخل النقاش الرائج)، ثم ترند X،
    # ثم التايم لاين
    _add(context.scratch.get("top_posts") or [], bonus=3)
    _add(context.scratch.get("hashtag_tweets") or [], bonus=2)
    _add(context.scratch.get("trend_tweets") or [], bonus=2)
    _add(context.scratch.get("timeline") or [], bonus=0)

    pool.sort(key=lambda t: (t.get("_rel", 0), t.get("_score", 0)), reverse=True)
    return pool


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


def _grounded_topic(context: "ExecutionContext") -> "dict | None":
    """Return the BEST real grounded topic within specialty, preferring media."""
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
        rel = relevance_score(text, interests) if interests else 1
        if interests and enforce_scope and rel < min_rel:
            continue
        media = _best_tweet_media((t.get("media") or "").strip())
        candidates.append({
            "source": "top_post",
            "title": text[:120],
            "summary": text[:280],
            "media_url": media,
            "_rel": rel,
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
        rel = relevance_score(hay, interests) if interests else 1
        if not _is_ad_or_spam_text(hay) and (not interests or not enforce_scope or rel >= min_rel):
            candidates.append({
                "source": "x_hashtag",
                "title": ht_name,
                "summary": summary[:400],
                "media_url": ht_media,
                "hashtag": ht_name,
                "_rel": rel,
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
            rel = relevance_score(hay, interests) if interests else 1
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
        rel = relevance_score(hay, interests) if interests else 1
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
        rel = relevance_score(hay, interests) if interests else 1
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
            "_media": 1 if media else 0,
            "_priority": 2,
        })

    relevant = _relevant_timeline(context)
    for t in relevant[:5]:
        text = (t.get("text") or "").strip().replace("\n", " ")
        if not text or _is_ad_or_spam_text(text):
            continue
        rel = relevance_score(text, interests) if interests else 1
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
            "_media": 1 if media else 0,
            "_priority": 1,
        })

    # التزام البرسونا + منع التكرار: استبعد ما يخالف "لا تفعل" أو موضوعاً سبق التفاعل معه
    candidates = [
        c for c in candidates
        if not _violates_persona_donts(f"{c['title']} {c['summary']}", context)
        and not _topic_recently_engaged(context, f"{c['title']} {c['summary']}")
        and not _is_weak_topic(c["title"], c["summary"])
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda c: (c["_rel"], c["_media"], c["_priority"]),
        reverse=True
    )
    best = candidates[0]
    return {
        "source": best["source"],
        "title": best["title"],
        "summary": best["summary"],
        "media_url": best["media_url"],
        "hashtag": best.get("hashtag", ""),
    }


class DailyContentCreatorSkill(ISkill):
    """Proposes an original post ONLY when there is a real, grounded topic.

    Never posts generic filler: if there is no trending topic (Google Trends /
    Trend Detector) and no relevant timeline tweet, it proposes nothing. When a
    topic exists it grounds the post on it and attaches the source media.
    """

    name = "daily_content_creator"
    uses_capabilities = ["publish_content"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        # ── توجيه مباشر من المستخدم عبر الشات («انشر عن …» / «تكلم عن …») ──
        # أمر صريح من صاحب الحساب: ينفَّذ فوراً بأسلوب الشخصية، ويتجاوز حدود
        # النشر اليومي وفحص نطاق الاهتمامات (المستخدم هو المرجع الأعلى).
        directed = str((context.event.payload or {}).get("directed_topic") or "").strip()
        if directed:
            lang = getattr(context.config.persona, "language", "ar") or "ar"
            dialect = getattr(context.config.persona, "dialect", "") or ""
            dialect_part = (' وبلهجة ' + dialect) if dialect else ''
            brief = (
                "أرسل لك صاحب الحساب الطلب التالي مباشرةً — وقد يتضمّن خبراً أو "
                "عنواناً منسوخاً بالإضافة إلى كلام توجيه:\n"
                f"«{directed}»\n\n"
                "أولاً استخرج الموضوع/الخبر الجوهري من هذا الطلب (تجاهل كلمات "
                "التوجيه مثل: انشر، جهّز، ابيك، محتوى قوي). ثم ابحث فيه واكتب "
                f"منشور X أصلياً قوياً بصوت إنساني طبيعي باللغة {lang}{dialect_part} "
                "بأسلوب شخصيتك: ابدأ بـHook قوي، ثم زبدة الموضوع وموقف/Insight "
                "واضح يدفع للتفاعل. لا تنسخ العنوان حرفياً، وقدّم زاوية وتحليلاً "
                "يضيف قيمة حقيقية."
            )
            if directed.startswith("#"):
                brief += f" مهم: ضمّن الوسم {directed} كما هو حرفياً في المنشور."
            return [ProposedAction(
                capability="publish_content",
                params={"brief": brief, "media_url": ""},
                rationale="user-directed topic from chat (explicit owner command)",
                expected_impact=0.95, urgency=0.95, risk=0.1,
            )]

        counters = context.config.runtime_state.counters
        posts_today = counters.get("posts_today", 0)
        target = int(context.config.business_rules.get("daily_post_target", 1))
        if posts_today >= target:
            return []

        rules = context.config.business_rules or {}
        require_grounded_topic = bool(rules.get("require_grounded_topic", True))
        enforce_scope = bool(rules.get("enforce_interest_scope", True))

        # تفضيل التفاعل عند وجود فرص قوية = خفض أولوية النشر، لا إلغاؤه
        damping = _engagement_first_damping(context)

        topic = _grounded_topic(context)
        if require_grounded_topic and not topic:
            return []  # لا موضوع حقيقي => لا ننشر محتوى عاماً
        if not topic:
            return []

        interests = getattr(context.config.persona, "interests", []) or []
        if enforce_scope and interests:
            hay = f"{topic.get('title','')} {topic.get('summary','')}"
            min_rel = int(rules.get("min_relevance_score", 1))
            if relevance_score(hay, interests) < min_rel:
                return []
        focus = "، ".join(interests) if interests else (
            context.top_goal_description() or "مجال اهتمام الجمهور")
        lang = getattr(context.config.persona, "language", "ar") or "ar"
        dialect = getattr(context.config.persona, "dialect", "") or ""
        plan = _content_plan(context)

        brief = (
            f"اكتب منشور X أصلي بصوت إنساني طبيعي باللغة {lang}"
            f"{(' وبلهجة ' + dialect) if dialect else ''} "
            f"ضمن مجال ({focus}) حول موضوع حقيقي: '{topic['title']}'. "
            "ابدأ بـHook قوي قصير، ثم فكرة واحدة واضحة وموقف/Insight عملي قابل للنقاش."
        )
        if topic["summary"]:
            brief += f" الخلفية المختصرة: {topic['summary']}"
        brief += (
            " تجنب الصياغة العامة والإنشائية، واكتب كصانع محتوى محترف. "
            "إن وُجدت ميديا مرتبطة استخدمها لدعم المنشور."
        )
        # مشاركة في هاشتاق الترند: ضمّن الهاشتاق نصّاً ليظهر المنشور داخل نقاشه
        if topic.get("hashtag"):
            brief += (
                f" مهم جداً: ضمّن الهاشتاق {topic['hashtag']} كما هو حرفياً في نهاية "
                "المنشور للمشاركة في الترند الحالي."
            )
        brief += _strategy_brief_suffix(context)

        # الصيغة تحدّد القدرة: ثريد (parts>1) أو تغريدة مفردة
        parts = int(plan.get("parts", 1) or 1)
        capability = "publish_thread" if parts > 1 else "publish_content"
        params = {
            "brief": brief,
            "media_url": topic["media_url"],
            "pillar": plan.get("pillar", ""),
            "format": plan.get("format", ""),
        }
        if parts > 1:
            params["parts"] = parts
        return [ProposedAction(
            capability=capability,
            params=params,
            rationale=(f"grounded {plan.get('format', 'post')} from {topic['source']} "
                       f"on pillar '{plan.get('pillar', '')}' "
                       f"(posts_today={posts_today}<{target})"),
            expected_impact=round(min(0.78 * damping + (0.06 if parts > 1 else 0), 0.95), 3),
            urgency=round(0.6 * damping, 3), risk=0.2,
        )]


class CommunityEngagerSkill(ISkill):
    """Reacts to mentions/replies to the account.

    Two sources:
    1. An event-delivered mention/reply (webhook/notification wake).
    2. The Mentions tab read during Observe (``scratch['mentions']``) — the
       people who replied to / mentioned the account. The employee replies back
       **briefly**, **one reply per incoming reply**, and **never twice** to the
       same one (permanent de-duplication via ``engaged_urls``).
    """

    name = "community_engager"
    uses_capabilities = ["reply_to_user", "like_post", "read_notifications"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        payload = context.event.payload or {}
        actions: List[ProposedAction] = []
        tweet_url = payload.get("tweet_url")
        if tweet_url and context.event.type.value in ("mention", "reply", "notification"):
            original_text = payload.get("text", "") or ""
            if _is_specialty_match(context, original_text):
                actions.append(ProposedAction(
                    capability="reply_to_user",
                    params={"tweet_url": tweet_url, "original_text": original_text,
                            "brief": True, "is_mention_reply": True},
                    rationale="incoming mention/reply is within employee specialty",
                    expected_impact=0.8, urgency=0.9, risk=0.3,
                ))
                actions.append(ProposedAction(
                    capability="like_post",
                    params={"tweet_url": tweet_url},
                    rationale="acknowledge in-scope engagement", expected_impact=0.3,
                    urgency=0.4, risk=0.05,
                ))

        # الردود/المنشنات المقروءة من تبويب التنبيهات هذه الدورة: ردّ مختصر واحد
        # لكل ردّ، دون تكرار الردّ على نفس الردّ أبداً.
        actions.extend(self._reply_to_mentions(context))
        return actions

    @staticmethod
    def _reply_to_mentions(context: "ExecutionContext") -> List[ProposedAction]:
        mentions = context.scratch.get("mentions") or []
        if not mentions:
            return []

        engaged = _engaged_urls(context)
        rules = context.config.business_rules or {}
        # حد أقصى للردود على التنبيهات في الدورة الواحدة (افتراضي 3)
        cap = int(rules.get("max_reply_notifications_per_cycle",
                            rules.get("max_replies_per_cycle", 3)))

        out: List[ProposedAction] = []
        for m in mentions:
            if len(out) >= cap:
                break
            url = (m.get("url") or "").strip()
            text = (m.get("text") or "").strip()
            # ردّ واحد لكل ردّ بشكل دائم: تخطَّ ما سبق التفاعل معه
            if not url or url in engaged:
                continue
            # لا ننجرّ خلف السبام/الإعلانات أو ما يخالف محظورات الشخصية
            if _is_ad_or_spam_text(text) or _violates_persona_donts(text, context):
                continue
            out.append(ProposedAction(
                capability="reply_to_user",
                params={"tweet_url": url, "original_text": text, "brief": True,
                        "is_mention_reply": True},
                rationale="reply back briefly to someone who replied to us (one reply, no repeats)",
                expected_impact=0.85, urgency=0.95, risk=0.2,
            ))
        return out


class TrendWatcherSkill(ISkill):
    """Proposes researching a source URL provided via a news event."""

    name = "trend_watcher"
    uses_capabilities = ["research_topic", "store_knowledge"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        payload = context.event.payload or {}
        url = payload.get("url") or payload.get("link")
        if url:
            return [ProposedAction(
                capability="research_topic",
                params={"url": url, "topic": payload.get("title", "")},
                rationale="news/trend source available for research",
                expected_impact=0.5, urgency=0.5, risk=0.1,
            )]
        return []


class TimelineEngagerSkill(ISkill):
    """Interest-aware engagement with the authenticated home timeline.

    Filters the timeline (read during Observe) down to tweets that match the
    employee's persona interests (e.g. a political persona engages only with
    political content/accounts), then proposes like / retweet / reply / follow
    on the most relevant ones. Reply text is left empty so the reply capability
    generates it from the persona/instructions. Requires the ``browse_timeline``
    capability (playwright_x MCP) to have populated the timeline scratch.
    """

    name = "timeline_engager"
    uses_capabilities = [
        "browse_timeline", "like_post", "retweet_post", "reply_to_user", "follow_user",
    ]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        relevant = _engagement_pool(context)
        if not relevant:
            return []

        rules = context.config.business_rules or {}
        max_likes = int(rules.get("max_likes_per_cycle", 3))
        max_replies = int(rules.get("max_replies_per_cycle", 1))
        max_retweets = int(rules.get("max_retweets_per_cycle", 1))
        max_follows = int(rules.get("max_follows_per_cycle", 1))

        actions: List[ProposedAction] = []
        engaged = _engaged_urls(context)
        queue = list(relevant)  # نوزّع تغريدات مختلفة على أنواع تفاعل مختلفة

        def _take(n: int) -> List[dict]:
            taken, rest = queue[:n], queue[n:]
            queue.clear()
            queue.extend(rest)
            return taken

        def _impact(t: dict, base: float) -> float:
            """تأثير ديناميكي حسب جودة الهدف: درجة الصلة بالتخصص + وجود
            وسائط — فيُفاضل محرّك القرار بين الأهداف الحقيقية بدل قيم ثابتة."""
            rel = int(t.get("_rel", 0) or 0)
            bonus = 0.04 * min(rel, 4)
            if (t.get("media") or "").strip():
                bonus += 0.05
            return round(min(base + bonus, 0.95), 3)

        # رد حسب البرسونا على الأكثر صلة (أعلى قيمة تفاعلية)
        for t in _take(max_replies):
            url = t.get("url")
            if not url:
                continue
            actions.append(ProposedAction(
                capability="reply_to_user",
                params={"tweet_url": url, "original_text": t.get("text", ""),
                        "handle": t.get("handle", "")},
                rationale="reply to a relevant tweet in the employee's specialty",
                expected_impact=_impact(t, 0.68), urgency=0.6, risk=0.3,
            ))

        # إعادة نشر (رتويت) لتغريدة مختلفة
        for t in _take(max_retweets):
            url = t.get("url")
            if not url:
                continue
            actions.append(ProposedAction(
                capability="retweet_post",
                params={"tweet_url": url, "original_text": t.get("text", ""),
                        "handle": t.get("handle", "")},
                rationale="amplify highly-relevant content in the employee's field",
                expected_impact=_impact(t, 0.55), urgency=0.5, risk=0.2,
            ))

        # إعجاب بتغريدات أخرى ذات صلة
        for t in _take(max_likes):
            url = t.get("url")
            if not url:
                continue
            actions.append(ProposedAction(
                capability="like_post",
                params={"tweet_url": url, "original_text": t.get("text", ""),
                        "handle": t.get("handle", "")},
                rationale=f"like relevant tweet by {t.get('handle','')}",
                expected_impact=_impact(t, 0.35), urgency=0.45, risk=0.05,
            ))

        # متابعة أصحاب أبرز التغريدات ذات الصلة (توجّههم يوافق الموظف)
        follows = 0
        for t in relevant:
            if follows >= max_follows:
                break
            profile_url = _profile_url_from_handle(t.get("handle", ""))
            if not profile_url or profile_url in engaged:
                continue
            follows += 1
            actions.append(ProposedAction(
                capability="follow_user",
                params={"profile_url": profile_url, "handle": t.get("handle", ""),
                        "original_text": t.get("text", "")},
                rationale=f"follow relevant account {t.get('handle','')} in the field",
                expected_impact=_impact(t, 0.45), urgency=0.4, risk=0.25,
            ))

        return actions


class ViralContentCreatorSkill(ISkill):
    """Creates original, timely 'viral' content about trending topics the employee
    saw on its timeline within its field of interest.

    Picks the most relevant recent topic from the timeline and asks the content
    capability to write an engaging, shareable original post about it (grounded
    in the persona). Gated by the daily post target to avoid spamming.
    """

    name = "viral_content_creator"
    uses_capabilities = ["publish_content"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        counters = context.config.runtime_state.counters
        posts_today = counters.get("posts_today", 0)
        target = int(context.config.business_rules.get("daily_post_target", 1))
        if posts_today >= target:
            return []

        rules = context.config.business_rules or {}
        require_grounded_topic = bool(rules.get("require_grounded_topic", True))
        enforce_scope = bool(rules.get("enforce_interest_scope", True))

        # تفضيل التفاعل = خفض أولوية النشر لا إلغاؤه (انظر _engagement_first_damping)
        damping = _engagement_first_damping(context)

        topic = _grounded_topic(context)
        if require_grounded_topic and not topic:
            return []  # بلا موضوع رائج/حقيقي لا ننشر أي شيء
        if not topic:
            return []

        interests = getattr(context.config.persona, "interests", []) or []
        if enforce_scope and interests:
            hay = f"{topic.get('title','')} {topic.get('summary','')}"
            min_rel = int(rules.get("min_relevance_score", 1))
            if relevance_score(hay, interests) < min_rel:
                return []

        lang = getattr(context.config.persona, "language", "ar") or "ar"
        dialect = getattr(context.config.persona, "dialect", "") or ""
        brief = (
            "اكتب منشوراً أصلياً قصيراً وقوياً قابلاً للانتشار على X "
            f"باللغة {lang}{(' وبلهجة ' + dialect) if dialect else ''} "
            f"ضمن اختصاصك فقط حول: '{topic['title']}'. "
            "ابدأ بـHook لافت، ثم موقف واضح أو Insight ذكي يدفع للتفاعل."
        )
        if topic["summary"]:
            brief += f" الخلفية المختصرة: {topic['summary']}"
        brief += (
            " اجعل النبرة بشرية واقعية ومباشرة، وتجنب العموميات والنسخ الإخباري. "
            "استخدم الميديا المرتبطة بالموضوع إن توفرت."
        )
        # مشاركة في هاشتاق الترند: ضمّن الهاشتاق نصّاً ليظهر المنشور داخل نقاشه
        if topic.get("hashtag"):
            brief += (
                f" مهم جداً: ضمّن الهاشتاق {topic['hashtag']} كما هو حرفياً في نهاية "
                "المنشور للمشاركة في الترند الحالي."
            )
        brief += _strategy_brief_suffix(context)

        plan = _content_plan(context)
        parts = int(plan.get("parts", 1) or 1)
        capability = "publish_thread" if parts > 1 else "publish_content"
        params = {
            "brief": brief, "media_url": topic["media_url"],
            "pillar": plan.get("pillar", ""), "format": plan.get("format", ""),
        }
        if parts > 1:
            params["parts"] = parts
        return [ProposedAction(
            capability=capability,
            params=params,
            rationale=f"viral content grounded in {topic['source']} topic (with media={bool(topic['media_url'])})",
            expected_impact=round(min(0.85 * damping, 0.95), 3),
            urgency=round(0.65 * damping, 3), risk=0.25,
        )]


class ContentQuoterSkill(ISkill):
    """Quote-tweet the strongest in-field post with the employee's own take.

    الاقتباس أقوى من إعادة النشر الصامتة: يبني صوت الحساب وموقفه بدل نقل كلام
    غيره، ويجذب جمهور الحساب الأصلي إلى النقاش. نختار أفضل عنصر في مجمّع
    التفاعل (أعلى صلة، ومنشورات الأداء العالي أولاً) ونطلب تعليقاً يضيف زاوية.
    """

    name = "content_quoter"
    uses_capabilities = ["quote_post"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        rules = context.config.business_rules or {}
        if int(rules.get("daily_quotes_target", 2)) <= 0:
            return []
        per_cycle = int(rules.get("max_quotes_per_cycle", 1))
        if per_cycle <= 0:
            return []
        pool = _engagement_pool(context)
        if not pool:
            return []
        # اقتبس ما يستحق تعليقاً فعلاً: نص كافٍ وصلة عالية بالتخصص
        candidates = [
            t for t in pool
            if len((t.get("text") or "").strip()) >= 80 and int(t.get("_rel", 0) or 0) >= 1
        ][:per_cycle]
        plan = _content_plan(context)
        return [ProposedAction(
            capability="quote_post",
            params={
                "tweet_url": t.get("url", ""),
                "original_text": t.get("text", ""),
                "handle": t.get("handle", ""),
                "pillar": plan.get("pillar", ""),
            },
            rationale=(f"quote {t.get('handle', '')} to add our own angle "
                       f"(rel={t.get('_rel', 0)})"),
            expected_impact=round(min(0.66 + 0.03 * int(t.get("_rel", 0) or 0), 0.9), 3),
            urgency=0.5, risk=0.25,
        ) for t in candidates]


class SwipeFileSkill(ISkill):
    """Bookmark exceptional in-field posts as raw material for future content.

    كل صانع محتوى محترف يملك «ملف إلهام»: يحفظ الطرح القوي ليبني عليه لاحقاً.
    نحفظ فقط ما جاء من «أهم البوستات» (أداء عالٍ فعلاً) وضمن التخصص.
    """

    name = "swipe_file"
    uses_capabilities = ["bookmark_post"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        rules = context.config.business_rules or {}
        if int(rules.get("daily_bookmarks_target", 4)) <= 0:
            return []
        per_cycle = int(rules.get("max_bookmarks_per_cycle", 1))
        if per_cycle <= 0:
            return []
        engaged = _engaged_urls(context)
        interests = getattr(context.config.persona, "interests", []) or []
        actions: List[ProposedAction] = []
        for t in (context.scratch.get("top_posts") or [])[:10]:
            if len(actions) >= per_cycle:
                break
            url = (t.get("url") or "").strip()
            text = (t.get("text") or "").strip()
            if not url or url in engaged or len(text) < 60:
                continue
            if _is_ad_or_spam_text(text) or _violates_persona_donts(text, context):
                continue
            if interests and relevance_score(text, interests) < int(
                    rules.get("min_relevance_score", 1)):
                continue
            actions.append(ProposedAction(
                capability="bookmark_post",
                params={"tweet_url": url, "original_text": text,
                        "handle": t.get("handle", "")},
                rationale="save a high-performing in-field post as future raw material",
                expected_impact=0.3, urgency=0.25, risk=0.02,
            ))
        return actions


class DirectedEngagementSkill(ISkill):
    """Engage with a SPECIFIC post the account owner pasted in chat.

    Triggered by an owner directive («تفاعل مع هذا المحتوى …» / «رد على …» /
    «اعمل ريتويت …») delivered via the ``directed_engagement`` event payload.
    The owner is the top authority, so this bypasses the specialty scope (the
    guard exempts directed engagement). If a tweet URL is given it engages
    directly; otherwise it live-searches X for the pasted text/handle, picks the
    best-matching tweet, and proposes reply / like / retweet as requested.
    Proposes nothing (and records a chat note) when the target cannot be found.
    """

    name = "directed_engager"
    uses_capabilities = ["reply_to_user", "like_post", "retweet_post"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        eng = (context.event.payload or {}).get("directed_engagement")
        if not eng:
            return []
        content = str(eng.get("text") or "").strip()
        handle = str(eng.get("handle") or "").strip()
        url = str(eng.get("url") or "").strip()
        wanted = set(eng.get("actions") or ["reply", "like"])

        if not url:
            url, content, handle = self._resolve_tweet(context, content, handle)
        if not url:
            context.scratch["directed_engagement_note"] = (
                "لم أعثر على التغريدة المطابقة للمحتوى المطلوب على X، فتعذّر التفاعل معها."
            )
            return []

        original = content[:400]
        actions: List[ProposedAction] = []
        # الرد أولاً (أعلى قيمة)، ثم الإعجاب، ثم إعادة النشر إن طُلبت
        if "reply" in wanted:
            actions.append(ProposedAction(
                capability="reply_to_user",
                params={"tweet_url": url, "original_text": original, "handle": handle},
                rationale="owner-directed engagement: reply to a specific post",
                expected_impact=0.95, urgency=0.95, risk=0.1,
            ))
        if "like" in wanted:
            actions.append(ProposedAction(
                capability="like_post",
                params={"tweet_url": url, "original_text": original, "handle": handle},
                rationale="owner-directed engagement: like a specific post",
                expected_impact=0.6, urgency=0.85, risk=0.05,
            ))
        if "retweet" in wanted:
            actions.append(ProposedAction(
                capability="retweet_post",
                params={"tweet_url": url, "original_text": original, "handle": handle},
                rationale="owner-directed engagement: retweet a specific post",
                expected_impact=0.7, urgency=0.85, risk=0.15,
            ))
        return actions

    @staticmethod
    def _resolve_tweet(context: "ExecutionContext", text: str, handle: str):
        """Locate the pasted content on X via live search.

        Returns ``(url, text, handle)`` — url is empty when no match is found.
        """
        from ..mcp.base import MCPRequest

        if "playwright_x" not in (context.config.mcp_servers or []):
            return "", text, handle

        # نص التغريدة: إن كان المحتوى بصيغة «اسم: نص» خذ ما بعد أول نقطتين
        body = text
        if ":" in body or "：" in body:
            body = re.split(r"[:：]", body, 1)[-1].strip()
        body = body.strip("*«»\"'“” ").strip()

        h = handle.lstrip("@").strip()
        words = [w for w in re.split(r"\s+", body) if len(w) > 1]
        query = " ".join(words[:8]).strip()
        if not query and h:
            query = f"from:{h}"
        if not query:
            return "", text, handle

        sr = context.mcp.call(MCPRequest(
            employee_id=context.config.id, tool="search_tweets",
            params={"cookie_label": context.config.cookie_label,
                    "query": query, "limit": 12},
            context={"server": "playwright_x",
                     "cookie_label": context.config.cookie_label},
        ))
        tweets = sr.data.get("tweets", []) if sr.success else []
        if not tweets:
            return "", text, handle

        body_words = normalize_topic_words(body)

        def _match(t: dict) -> float:
            score = topic_overlap_ratio(normalize_topic_words(t.get("text", "")), body_words)
            if h and h.lower() in (t.get("handle", "") or "").lower():
                score += 0.5
            return score

        best = max(tweets, key=_match)
        if _match(best) <= 0:
            return "", text, handle
        return (
            (best.get("url", "") or "").strip(),
            (best.get("text") or text),
            (best.get("handle") or handle),
        )


def default_skills() -> List[ISkill]:
    return [
        DailyContentCreatorSkill(),
        CommunityEngagerSkill(),
        TrendWatcherSkill(),
        TimelineEngagerSkill(),
        ViralContentCreatorSkill(),
        ContentQuoterSkill(),
        SwipeFileSkill(),
        DirectedEngagementSkill(),
    ]
