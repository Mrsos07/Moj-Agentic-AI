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
    # نتائج بحث الاهتمامات المباشر — أهداف مضمونة الصلة بالتخصص، تُجلب عندما
    # يكون التايم لاين/الترند خارج اهتمامات الموظف (وإلا يجوع التفاعل كلياً)
    _add(context.scratch.get("interest_tweets") or [], bonus=3)
    _add(context.scratch.get("hashtag_tweets") or [], bonus=2)
    _add(context.scratch.get("trend_tweets") or [], bonus=2)
    _add(context.scratch.get("timeline") or [], bonus=0)

    pool.sort(key=lambda t: (t.get("_rel", 0), t.get("_score", 0)), reverse=True)
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
        return []

    candidates.sort(
        key=lambda c: (c["_rel"], c["_media"], c["_priority"]),
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
    listing = "\n".join(
        f"{i + 1}. [{c['source']}] {c['title'][:110]}"
        + (f" — {c['summary'][:150]}" if c["summary"] else "")
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


class OnThisDaySkill(ISkill):
    """ينشر أحداثاً تاريخية وقعت «في مثل هذا اليوم» — جوهر حسابات التوثيق.

    حسابات التوثيق التاريخي لا تجد موضوعها في الترندات أو الأخبار (مصادر
    ``_grounded_topic`` كلها آنية)، فكان الحساب إما لا ينشر شيئاً أو ينجرف
    خلف الترندات خارج هويته. هذه المهارة تؤرضن المحتوى بمصدر حقيقي مخصص:
    أحداث اليوم من ويكيبيديا، مع التاريخين الميلادي والهجري، ونافذة أحداث
    تتدوّر عبر منشورات اليوم فلا يتكرر الحدث نفسه.
    """

    name = "on_this_day"
    claim_priority = 45
    uses_capabilities = ["publish_content"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        if not _on_this_day_mode(context):
            return []
        counters = context.config.runtime_state.counters
        posts_today = counters.get("posts_today", 0)
        target = int(context.config.business_rules.get("daily_post_target", 1))
        if posts_today >= target:
            return []

        events = _fetch_on_this_day_events()
        if not events:
            return []  # لا مصدر حقيقي => لا نُلفّق حدثاً من ذاكرة النموذج

        # قصر الأحداث على تخصص الشخصية (حدث سياسي/ديني يتقدم على خبر رياضي
        # عابر): النافذة المتدحرجة فوق قائمة مرتّبة فقط كانت تُسقط منشورَ آخر
        # اليوم على ذيل القائمة (أعاصير/رياضة) فيرفضه الحارس لخروجه عن التخصص
        # وتضيع الدورة. ثم نافذة متدحرجة داخل المتخصص: كل منشور خلال اليوم
        # يعرض أحداثاً مختلفة، وذاكرة النشاط تمنع تكرار ما نُشر فعلاً.
        interests = getattr(context.config.persona, "interests", []) or []
        if interests:
            relevant = [e for e in events if relevance_score(e["text"], interests) >= 1]
            if len(relevant) >= 5:
                events = relevant
            else:
                events = sorted(
                    events,
                    key=lambda e: (relevance_score(e["text"], interests), e["year"]),
                    reverse=True,
                )
        window = 5
        start = (posts_today * window) % max(len(events), 1)
        batch = events[start:start + window] or events[:window]
        listing = "\n".join(f"- سنة {e['year']}: {e['text'][:220]}" for e in batch)

        lang = getattr(context.config.persona, "language", "ar") or "ar"
        # لا تذكر اسم المصدر في الموجز: «(المصدر: ويكيبيديا)» جعل حارس الالتزام
        # يحكم على المصدر بدل الموضوع، ورفضُه تحوّل عبر تقييم الدورة إلى توجيه
        # «تجنب ويكيبيديا» يُحقن في برومبته فيرفض كل موجز تالٍ — حلقة صدى.
        brief = (
            f"تاريخ اليوم: {_today_dates_arabic()}.\n"
            f"أحداث حقيقية موثقة وقعت في مثل هذا اليوم:\n"
            f"{listing}\n\n"
            f"اختر من القائمة أعلاه حصراً (لا تأتِ بحدث من خارجها) الحدث الأهم "
            f"الواقع داخل تخصصك وتجاهل ما سواه، واكتب تغريدة توثيقية واحدة "
            f"باللغة {lang} بأسلوب شخصيتك الرسمي السردي: اذكر سنة الحدث، وصُغ "
            "الحدث بدقة وحيادية كسطر تاريخ موثوق. لا أسئلة، لا نقاش، لا رأي شخصي."
        )
        return [ProposedAction(
            capability="publish_content",
            params={"brief": brief, "media_url": "",
                    "pillar": "في مثل هذا اليوم", "format": "insight"},
            rationale=(f"on-this-day documentation post from {len(events)} real "
                       f"Wikipedia events (posts_today={posts_today}<{target})"),
            expected_impact=0.9, urgency=0.85, risk=0.1,
        )]


class DailyContentCreatorSkill(ISkill):
    """Proposes an original post ONLY when there is a real, grounded topic.

    Never posts generic filler: if there is no trending topic (Google Trends /
    Trend Detector) and no relevant timeline tweet, it proposes nothing. When a
    topic exists it grounds the post on it and attaches the source media.
    """

    name = "daily_content_creator"
    claim_priority = 40
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
                params={"brief": brief, "media_url": "", "owner_directed": True},
                rationale="user-directed topic from chat (explicit owner command)",
                expected_impact=0.95, urgency=0.95, risk=0.1,
            )]

        counters = context.config.runtime_state.counters
        posts_today = counters.get("posts_today", 0)
        target = int(context.config.business_rules.get("daily_post_target", 1))
        if posts_today >= target:
            return []

        # حسابات «في مثل هذا اليوم»: النشر حصراً عبر مهارة on_this_day —
        # الترندات/الأخبار تجرّ الحساب خارج هويته التوثيقية (حدث فعلاً).
        if _on_this_day_mode(context):
            return []

        rules = context.config.business_rules or {}
        require_grounded_topic = bool(rules.get("require_grounded_topic", True))
        enforce_scope = bool(rules.get("enforce_interest_scope", True))

        # تفضيل التفاعل عند وجود فرص قوية = خفض أولوية النشر، لا إلغاؤه
        damping = _engagement_first_damping(context)

        # اختيار تحريري لا آلي: الموظف يراجع المرشّحين كرئيس تحرير ويختار
        # الأهم/الأشوق لجمهور تخصصه مع زاوية معالجة وخُطّاف — لا أول موضوع يطابق الكلمات.
        topic = _editorial_pick(context)
        if require_grounded_topic and not topic:
            return []  # لا موضوع حقيقي => لا ننشر محتوى عاماً
        if not topic:
            return []
        if topic.get("why"):
            context.scratch["editorial_pick"] = {
                "title": topic["title"][:120], "why": topic["why"],
                "angle": topic.get("angle", ""),
            }
            context.emit_progress(
                f"🗞️ **اخترت موضوع المنشور كرئيس تحرير:** «{topic['title'][:90]}» — "
                f"{topic['why']}"
            )

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
            f"ضمن مجال ({focus}) حول موضوع حقيقي: '{topic['title']}'."
        )
        if topic["summary"]:
            brief += f" الخلفية المختصرة: {topic['summary']}"
        if topic.get("angle"):
            brief += f"\nزاوية المعالجة المطلوبة (التزم بها): {topic['angle']}"
        if topic.get("hook"):
            brief += (f"\nخُطّاف مقترح للسطر الأول (حسّنه إن استطعت، لا تضعفه): "
                      f"«{topic['hook']}»")
        brief += (
            "\nقواعد الصنعة: السطر الأول خُطّاف ≤ 12 كلمة (رقم/مفارقة/سؤال جريء)؛ "
            "فكرة واحدة فقط؛ موقف صريح أو معلومة ملموسة لا يعرفها أغلب الجمهور؛ "
            "خاتمة تفتح نقاشاً (رأي يُختلف عليه أو سؤال محدد)؛ لا مقدمات إنشائية "
            "ولا إعادة سرد للخبر كما هو. اكتب كصانع محتوى محترف له شخصية ورأي. "
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
    claim_priority = 90
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
    claim_priority = 5
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
    claim_priority = 10
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

        def _take(n: int, tag: str) -> List[dict]:
            """أهداف مختلفة لكل نوع تفاعل — ومحجوزة عن بقية المهارات أيضاً."""
            return claim_targets(context, relevant, n, owner=f"timeline:{tag}")

        def _impact(t: dict, base: float) -> float:
            """تأثير ديناميكي حسب جودة الهدف: درجة الصلة بالتخصص + وجود
            وسائط — فيُفاضل محرّك القرار بين الأهداف الحقيقية بدل قيم ثابتة."""
            rel = int(t.get("_rel", 0) or 0)
            bonus = 0.04 * min(rel, 4)
            if (t.get("media") or "").strip():
                bonus += 0.05
            return round(min(base + bonus, 0.95), 3)

        # رد حسب البرسونا على الأكثر صلة (أعلى قيمة تفاعلية)
        for t in _take(max_replies, "reply"):
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
        for t in _take(max_retweets, "retweet"):
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
        for t in _take(max_likes, "like"):
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
    claim_priority = 35
    uses_capabilities = ["publish_content"]

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        counters = context.config.runtime_state.counters
        posts_today = counters.get("posts_today", 0)
        target = int(context.config.business_rules.get("daily_post_target", 1))
        if posts_today >= target:
            return []

        # حسابات «في مثل هذا اليوم» لا تطارد الترندات — مهارة on_this_day تتولى النشر
        if _on_this_day_mode(context):
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
    claim_priority = 60
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
        # اقتبس ما يستحق تعليقاً فعلاً: نص كافٍ وصلة عالية بالتخصص.
        # العتبة قاعدة عمل لا رقم مدفون: 80 حرفاً كانت تُلغي الاقتباس عملياً على
        # المحتوى العربي (تغريدات المجال تقع غالباً في 60-80 حرفاً)، فقيست
        # المحاكاة صفر اقتباس في 40 دورة رغم أن القدرة مفعّلة وميزانيتها كاملة.
        min_chars = int(rules.get("min_quote_chars", 60))
        candidates = claim_targets(context, [
            t for t in pool
            if len((t.get("text") or "").strip()) >= min_chars
            and int(t.get("_rel", 0) or 0) >= 1
        ], per_cycle, owner="content_quoter")
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
    claim_priority = 50
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
        eligible: List[dict] = []
        for t in (context.scratch.get("top_posts") or [])[:10]:
            url = (t.get("url") or "").strip()
            text = (t.get("text") or "").strip()
            if not url or url in engaged or len(text) < 60:
                continue
            if _is_ad_or_spam_text(text) or _violates_persona_donts(text, context):
                continue
            if interests and relevance_score(text, interests) < int(
                    rules.get("min_relevance_score", 1)):
                continue
            eligible.append(t)

        actions: List[ProposedAction] = []
        for t in claim_targets(context, eligible, per_cycle, owner="swipe_file"):
            actions.append(ProposedAction(
                capability="bookmark_post",
                params={"tweet_url": (t.get("url") or "").strip(),
                        "original_text": (t.get("text") or "").strip(),
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
    claim_priority = 100
    uses_capabilities = ["reply_to_user", "like_post", "retweet_post"]
    always_on = True

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
                params={"tweet_url": url, "original_text": original, "handle": handle,
                        "owner_directed": True},
                rationale="owner-directed engagement: reply to a specific post",
                expected_impact=0.95, urgency=0.95, risk=0.1,
            ))
        if "like" in wanted:
            actions.append(ProposedAction(
                capability="like_post",
                params={"tweet_url": url, "original_text": original, "handle": handle,
                        "owner_directed": True},
                rationale="owner-directed engagement: like a specific post",
                expected_impact=0.6, urgency=0.85, risk=0.05,
            ))
        if "retweet" in wanted:
            actions.append(ProposedAction(
                capability="retweet_post",
                params={"tweet_url": url, "original_text": original, "handle": handle,
                        "owner_directed": True},
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
                        "_rel": relevance_score(hay, getattr(
                            context.config.persona, "interests", []) or [])})

    out.sort(key=lambda t: (t.get("_match", 0), t.get("_rel", 0)), reverse=True)
    return out


class DirectedMissionSkill(ISkill):
    """Execute a free-form owner mission where the EMPLOYEE picks the target.

    Triggered by ``directed_mission`` in the event payload — produced from open
    chat («روح للتايم لاين وشوف تغريدة تناسب تخصصك وردّ عليها»، «تابع حسابين في
    مجالك»، «اقتبس أقوى تغريدة في الترند»). Unlike ``directed_engager`` (which
    acts on a post the owner pasted), here the owner names only the *source*,
    the *actions* and optionally a topic constraint; the employee scans that
    source, ranks candidates by the constraint then by specialty, and proposes
    the requested actions on DIFFERENT targets (never the same tweet twice).

    Owner authority: actions carry ``owner_directed`` so the persona guard and
    the daily engagement budgets do not silently drop an explicit order. The
    specialty filter still applies unless the owner allowed a free scope.
    Records an Arabic note when the source has no usable target, so the chat
    report explains *why* nothing happened instead of going silent.
    """

    name = "directed_mission"
    claim_priority = 100
    uses_capabilities = ["reply_to_user", "like_post", "retweet_post",
                         "quote_post", "follow_user"]
    always_on = True

    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        mission = (context.event.payload or {}).get("directed_mission")
        if not isinstance(mission, dict):
            return []
        wanted = [a for a in (mission.get("actions") or [])
                  if a in _MISSION_CAPABILITY]
        if not wanted:
            return []

        pool = _mission_candidates(context, mission)
        if not pool and str(mission.get("scope")) != "free":
            # لا هدف داخل التخصص: أعِد المحاولة بلا قيد الموضوع قبل الاستسلام،
            # فالقيد النصّي قد يكون أضيق من محتوى المصدر الفعلي.
            pool = _mission_candidates(
                context, {**mission, "query": ""})
        if not pool:
            context.scratch["directed_mission_note"] = (
                "نفّذت المهمّة لكن لم أجد في المصدر المطلوب هدفاً يصلح للتفاعل "
                "(إمّا لا شيء ضمن تخصصي، أو تفاعلت مسبقاً مع كل ما ظهر)."
            )
            return []

        try:
            count = max(1, min(int(mission.get("count") or 1), 5))
        except (TypeError, ValueError):
            count = 1

        actions: List[ProposedAction] = []
        queue = list(pool)
        engaged = _engaged_urls(context)
        # ترتيب القيمة: رد > اقتباس > إعادة نشر > متابعة > إعجاب (وزن الخوارزمية)
        order = ["reply", "quote", "retweet", "follow", "like"]
        for act in [a for a in order if a in wanted]:
            capability = _MISSION_CAPABILITY[act]
            taken = 0
            while queue and taken < count:
                t = queue.pop(0)
                url = (t.get("url") or "").strip()
                params = {"original_text": (t.get("text") or "")[:400],
                          "handle": t.get("handle", ""),
                          "owner_directed": True}
                if act == "follow":
                    profile = _profile_url_from_handle(t.get("handle", ""))
                    if not profile or profile in engaged:
                        continue
                    params["profile_url"] = profile
                else:
                    if not url:
                        continue
                    params["tweet_url"] = url
                if act == "reply" and mission.get("source") == "mentions":
                    params["brief"] = True
                    params["is_mention_reply"] = True
                taken += 1
                actions.append(ProposedAction(
                    capability=capability, params=params,
                    rationale=f"owner mission: {act} a target the employee "
                              f"picked from {mission.get('source', 'any')}",
                    expected_impact=0.95, urgency=0.95, risk=0.05,
                ))
            # أعِد تعبئة الطابور للفعل التالي كي لا يجوع: الأهداف المختلفة أولاً،
            # وعند نضوبها يُسمح بإعادة استخدام نفس الهدف لفعل مختلف (إعجاب+رد).
            if not queue:
                queue = list(pool)
        return actions


def default_skills() -> List[ISkill]:
    from .custom import CustomSkillRunner

    # الترتيب مهمّ: المهارات تحجز أهدافها من حوض واحد عبر ``claim_targets``،
    # فتحجز أولاً الأفعالُ **النادرة عالية القيمة** (اقتباس: 1-2 يومياً) قبل
    # الأفعال الوفيرة (إعجاب: 12 يومياً) التي تجد بديلاً دائماً. عكس الترتيب
    # كان يعني أن الإعجاب يلتهم أفضل الأهداف ولا يبقى للاقتباس شيء.
    return [
        CustomSkillRunner(),
        OnThisDaySkill(),
        DailyContentCreatorSkill(),
        CommunityEngagerSkill(),
        TrendWatcherSkill(),
        ContentQuoterSkill(),
        SwipeFileSkill(),
        TimelineEngagerSkill(),
        ViralContentCreatorSkill(),
        DirectedEngagementSkill(),
        DirectedMissionSkill(),
    ]
