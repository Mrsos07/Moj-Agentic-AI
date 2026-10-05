"""
«بصمة الصوت» — هوية مستنسخة عميقة تُستخدم فعلاً في كل منشور ورد.

لماذا؟ الاستنساخ القديم كان يسحب 20 تغريدة و~10 ردود **بلا سياقها**، ويحوّلها
بنداء واحد إلى حقول البرسونا (افعل/لا تفعل/تعليمات) ثم يرمي العيّنة. والأسوأ أن
موجّه الكتابة كان يفرض أسلوب «البيت» فوق أي هوية: نبرة تتناوب كل منشور، و«≤260
حرفاً، إيموجي واحد، هاشتاق واحد»، ومطالع جاهزة — فيتحوّل صاحب الأسلوب الطويل
الغني بالإيموجي إلى قالب عام. الاستنساخ كان شكلياً.

البصمة هنا أربع طبقات، تُحفظ في ``business_rules["voice_profile"]``:

1. **قياسات حتمية** (stylometry): توزيع الطول، الإيموجي وأكثرها، الهاشتاق،
   المنشن، الأسئلة، فواصل الأسطر، المطالع والخواتيم، العبارات المميّزة، مؤشرات
   اللهجة، أسلوب الردود مقارنة بالتغريدات، الثريدات، ساعات النشاط.
2. **توزيع المواضيع** بالتضمينات (نفس مراجع ``topic_intel`` المُعايَرة).
3. **تحليل عميق بالنموذج**: الصوت، المحاور، **دليل الردود** (كيف يرد على
   الموافقة/الخلاف/السؤال/الهجوم/الثناء)، طريقة التفاعل، المعجم، القيم والخطوط
   الحمراء — مبني على **أزواج (التغريدة ← ردّه عليها)** لا ردود معزولة.
4. **أمثلة حقيقية مرجعية** (few-shot) للمنشورات والردود.

والاستهلاك: ``voice_block`` في موجّه النظام، و``post_rules``/``reply_rules``
تستبدل قواعد «البيت» المتعارضة، و``copied_exemplar`` في بوابة الجودة يمنع نسخ
نصوص الحساب الأصلي حرفياً.

**الهوية لا الانتحال**: البصمة تنقل الأسلوب والشخصية فقط؛ لا تنقل اسم الحساب
الأصلي ولا تدّعي أنه هو، ولا تعيد نشر نصوصه.
"""
from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeos.voice_profile")

STORE_KEY = "voice_profile"
VERSION = 1

_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
_HASHTAG_RE = re.compile(r"#[\w\u0600-\u06FF_]+")
_MENTION_RE = re.compile(r"@\w{2,30}")
_URL_RE = re.compile(r"https?://\S+")
_WORD_RE = re.compile(r"[\w\u0600-\u06FF]+")
_NUM_RE = re.compile(r"[\d٠-٩]")

#: مؤشرات لهجة (كلمات كاملة). التداخل بين اللهجات مقصود: نقارن الكثافة لا الوجود.
DIALECT_MARKERS: Dict[str, List[str]] = {
    "سعودية/خليجية": ["وش", "ليش", "كذا", "ابي", "أبي", "ابغى", "أبغى", "زين", "مره", "ترى",
                      "الحين", "اللحين", "شلون", "وايد", "هالشي", "يبيله", "عساكم", "بيض",
                      "ياخي", "اخوي", "أخوي", "ماني", "راح", "يعطيك"],
    "مصرية": ["ايه", "إيه", "ازاي", "إزاي", "كده", "دلوقتي", "بتاع", "اوي", "أوي", "فين",
              "احنا", "إحنا", "عايز", "خالص", "بقى", "ده", "دي"],
    "شامية": ["شو", "هيك", "كتير", "هلق", "منيح", "بدي", "هاد", "هيدا", "لكان"],
    "عراقية": ["شكو", "ماكو", "اكو", "هواية", "شنو", "اريد", "گ"],
    "فصحى": ["سوف", "لقد", "إنّ", "الذي", "التي", "لكنّ", "حيث", "إذ", "ذلك", "أيضاً", "لذا"],
}

_STOP = {
    "في", "من", "على", "الى", "إلى", "عن", "أن", "ان", "لا", "ما", "هذا", "هذه", "التي",
    "الذي", "مع", "كل", "لم", "لن", "قد", "و", "او", "أو", "يا", "هو", "هي", "the", "a",
    "an", "and", "or", "of", "to", "in", "on", "for", "is", "it", "that", "this",
}


# ─────────────────────────── 1) stylometry ───────────────────────────
def _pct(values: List[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def _rate(texts: List[str], pred) -> float:
    return round(sum(1 for t in texts if pred(t)) / len(texts), 3) if texts else 0.0


def _clean(text: str) -> str:
    return _URL_RE.sub("", text or "").strip()


def _words(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def dialect_scores(texts: List[str]) -> Dict[str, float]:
    """كثافة مؤشرات كل لهجة لكل 100 كلمة."""
    words = [w for t in texts for w in _words(t)]
    if not words:
        return {}
    counts = Counter(words)
    return {d: round(100.0 * sum(counts[m] for m in marks) / len(words), 2)
            for d, marks in DIALECT_MARKERS.items()}


def _ngrams(texts: List[str], n: int) -> Counter:
    c: Counter = Counter()
    for t in texts:
        ws = [w for w in _words(t) if not w.isdigit()]
        seen = set()
        for i in range(len(ws) - n + 1):
            g = ws[i:i + n]
            if g[0] in _STOP or g[-1] in _STOP:
                continue
            key = " ".join(g)
            if key not in seen:  # مرة لكل تغريدة: «عبارة مميّزة» = تتكرر عبر التغريدات
                seen.add(key)
                c[key] += 1
    return c


def stylometry(tweets: List[str], replies: List[str],
               times: Optional[List[str]] = None) -> Dict[str, Any]:
    tw = [_clean(t) for t in tweets if _clean(t)]
    rp = [_clean(t) for t in replies if _clean(t)]
    allt = tw + rp
    if not allt:
        return {}
    lens = [len(t) for t in tw] or [len(t) for t in rp]
    emojis = Counter(e for t in allt for e in _EMOJI_RE.findall(t))
    openers = Counter(" ".join(_words(t)[:1]) for t in tw if _words(t))
    closers = Counter(e for t in tw for e in _EMOJI_RE.findall(t[-6:]))
    phrases = [g for g, n in (_ngrams(allt, 3) + _ngrams(allt, 2)).most_common(40)
               if n >= 2][:12]
    dial = dialect_scores(allt)
    hours = Counter()
    for raw in times or []:
        try:
            hours[datetime.fromisoformat(raw.replace("Z", "+00:00")).hour] += 1
        except (ValueError, AttributeError):
            continue
    return {
        "len_median": int(statistics.median(lens)) if lens else 0,
        "len_p25": _pct(lens, 0.25), "len_p75": _pct(lens, 0.75),
        "reply_len_median": int(statistics.median([len(t) for t in rp])) if rp else 0,
        "emoji_rate": _rate(tw or allt, lambda t: bool(_EMOJI_RE.search(t))),
        "reply_emoji_rate": _rate(rp, lambda t: bool(_EMOJI_RE.search(t))),
        "emoji_per_post": round(sum(len(_EMOJI_RE.findall(t)) for t in tw) / len(tw), 2) if tw else 0,
        "top_emojis": [e for e, _ in emojis.most_common(6)],
        "hashtag_rate": _rate(tw, lambda t: bool(_HASHTAG_RE.search(t))),
        "mention_rate": _rate(tw, lambda t: bool(_MENTION_RE.search(t))),
        "question_rate": _rate(tw, lambda t: "؟" in t or "?" in t),
        "exclaim_rate": _rate(tw, lambda t: "!" in t),
        "linebreak_rate": _rate(tw, lambda t: "\n" in t.strip()),
        "number_rate": _rate(tw, lambda t: bool(_NUM_RE.search(t))),
        "openers": [o for o, n in openers.most_common(8) if n >= 2][:6],
        "closing_emojis": [e for e, _ in closers.most_common(3)],
        "signature_phrases": phrases,
        "dialect_scores": dial,
        "dialect_guess": max(dial, key=dial.get) if dial and max(dial.values()) >= 0.6 else "",
        "active_hours_utc": [h for h, _ in hours.most_common(4)],
    }


# ─────────────────────────── 2) topics ───────────────────────────
def topic_distribution(texts: List[str]) -> Dict[str, float]:
    """حصّة كل اهتمام من المحتوى عبر مراجع ``topic_intel`` المُعايَرة (أقرب مرجع)."""
    from . import topic_intel as T

    texts = [t for t in texts if t.strip()][:80]
    if not texts:
        return {}
    labels = list(T.ANCHORS) + list(T.NEGATIVE_ANCHORS)
    vecs = T.embed_texts([*(T.ANCHORS[k] for k in T.ANCHORS),
                          *(T.NEGATIVE_ANCHORS[k] for k in T.NEGATIVE_ANCHORS),
                          *(t[:500] for t in texts)])
    if not vecs or any(v is None for v in vecs):
        return {}
    anchors, items = vecs[:len(labels)], vecs[len(labels):]
    counts: Counter = Counter()
    for v in items:
        sims = [T._cos(a, v) for a in anchors]
        counts[labels[max(range(len(sims)), key=sims.__getitem__)]] += 1
    total = sum(counts.values()) or 1
    return {k: round(n / total, 3) for k, n in counts.most_common()}


def interests_from_topics(dist: Dict[str, float], min_share: float = 0.12) -> List[str]:
    from . import topic_intel as T

    return [k for k, share in dist.items() if k in T.ANCHORS and share >= min_share][:5]


# ─────────────────────────── 3) exemplars ───────────────────────────
def _overlap(a: str, b: str) -> float:
    from .persona_templates import normalize_topic_words, topic_overlap_ratio

    return topic_overlap_ratio(normalize_topic_words(a), normalize_topic_words(b))


def _engagement(t: Dict[str, Any]) -> float:
    return (int(t.get("likes") or 0) + 2 * int(t.get("reposts") or 0)
            + 3 * int(t.get("replies") or 0) + int(t.get("views") or 0) / 200.0)


def select_exemplars(tweets: List[Dict[str, Any]], replies: List[Dict[str, Any]],
                     posts_n: int = 8, replies_n: int = 6) -> Dict[str, Any]:
    """أمثلة مرجعية متنوّعة: الأعلى تفاعلاً أولاً، بلا مواضيع متكررة، وبأطوال مختلفة."""
    posts: List[str] = []
    for t in sorted(tweets, key=_engagement, reverse=True):
        text = _clean(t.get("text", ""))
        if len(text) < 15 or any(_overlap(text, p) >= 0.5 for p in posts):
            continue
        posts.append(text[:400])
        if len(posts) >= posts_n:
            break
    pairs: List[Dict[str, str]] = []
    others = [r for r in replies if r.get("in_reply_to") and not r["in_reply_to"].get("self")]
    for r in sorted(others, key=_engagement, reverse=True):
        text = _clean(r.get("text", ""))
        if len(text) < 2 or any(_overlap(text, p["reply"]) >= 0.6 for p in pairs):
            continue
        pairs.append({"to": r["in_reply_to"]["text"][:280], "reply": text[:280]})
        if len(pairs) >= replies_n:
            break
    return {"posts": posts, "replies": pairs}


# ─────────────────────────── 4) LLM deep analysis ───────────────────────────
_ANALYSIS_SYSTEM = (
    "أنت محلّل أساليب كتابة على X. تستخرج «بصمة صوت» دقيقة من عيّنات حقيقية. "
    "حلّل الأسلوب والشخصية وطريقة التفاعل، لا الوقائع. صِف بحياد مهني بلا أحكام "
    "على مواقف الحساب، وبالعربية فقط (بلا كلمات إنجليزية). أعد JSON فقط."
)

_ANALYSIS_SCHEMA = """{
 "voice_summary": "فقرة من 3-4 جمل تصف صوته كما لو تشرحه لكاتب سيحاكيه",
 "register": "فصحى | عامية | مزيج — مع اللهجة",
 "humor": "لا يمزح | دعابة خفيفة | سخرية | ...",
 "stance": "كيف يطرح رأيه: حاسم/متحفّظ/تحليلي/عاطفي...",
 "rhetorical_devices": ["أدوات بلاغية متكررة: تكرار، مقارنة، سؤال استنكاري، أرقام، أمثال..."],
 "pillars": [{"name": "محور", "share": 0.3, "angle": "زاويته المعتادة فيه"}],
 "formats": ["أشكال منشوراته: خبر عاجل، رأي، سرد، قائمة، ثريد، سؤال..."],
 "reply_playbook": {
   "agree": "كيف يرد على من يوافقه/يمدحه",
   "disagree": "كيف يرد على المخالف",
   "question": "كيف يجيب الأسئلة",
   "attack": "كيف يتعامل مع الهجوم/الاستفزاز",
   "praise": "كيف يرد على الشكر والتحية",
   "general": "سمات عامة لردوده: الطول، الألقاب (اخوي/استاذ)، الدعاء، الإيموجي"
 },
 "interaction": {
   "engages_with": "مع من يتفاعل غالباً (متابعين/شخصيات/خصوم)",
   "initiates": "هل يبدأ النقاش أم يرد فقط",
   "tone_shift": "هل تختلف نبرته في الردود عن منشوراته وكيف"
 },
 "lexicon": {"signature": ["مفردات وعبارات تميّزه"], "avoid": ["ما لا يستخدمه أبداً"]},
 "values": ["3-5 قيم ثابتة يظهرها"],
 "red_lines": ["4-6 أشياء لا يفعلها أبداً (لا تكسر الشخصية)"],
 "persona": {
   "role": "وصف دوره بإيجاز",
   "tone": "professional|casual|witty|formal|friendly",
   "dialect": "اللهجة كما يكتب بها",
   "dos": ["6-10 قواعد كتابة دقيقة وقابلة للتطبيق"],
   "donts": ["4-6 محظورات أسلوبية"]
 }
}"""


def _analysis_prompt(profile: Dict[str, Any], stats: Dict[str, Any],
                     topics: Dict[str, float]) -> str:
    import json

    tweets = profile.get("tweets") or []
    pairs = [r for r in profile.get("replies") or [] if r.get("in_reply_to")]
    loose = [r for r in profile.get("replies") or [] if not r.get("in_reply_to")]
    t_lines = "\n".join(
        f"- {_clean(t.get('text', ''))[:260]}  [❤{t.get('likes', 0)} 👁{t.get('views', 0)}]"
        for t in tweets[:40])
    p_lines = "\n".join(
        f"• على {r['in_reply_to'].get('handle', '')}: «{r['in_reply_to']['text'][:180]}»\n"
        f"  ← ردّه: «{_clean(r.get('text', ''))[:200]}»"
        + ("  (تكملة ثريد له)" if r["in_reply_to"].get("self") else "")
        for r in pairs[:30])
    l_lines = "\n".join(f"- {_clean(r.get('text', ''))[:200]}" for r in loose[:10])
    return (
        f"الحساب: {profile.get('handle', '')} — البايو: {profile.get('bio') or '(فارغ)'}\n"
        f"قياسات حتمية من العيّنة: {json.dumps(stats, ensure_ascii=False)}\n"
        f"توزيع المواضيع المقاس: {json.dumps(topics, ensure_ascii=False)}\n\n"
        f"تغريداته الأصلية ({len(tweets)}) مع تفاعلها:\n{t_lines or '(لا يوجد)'}\n\n"
        f"ردوده **مع التغريدة التي ردّ عليها** ({len(pairs)}):\n{p_lines or '(لا يوجد)'}\n"
        + (f"\nردود بلا سياق:\n{l_lines}\n" if l_lines else "")
        + "\nاستخرج البصمة بهذه البنية حرفياً (قيم عربية، والقياسات أعلاه حقائق لا "
        "تناقضها):\n" + _ANALYSIS_SCHEMA
    )


def _as_list(v: Any, n: int, length: int = 160) -> List[str]:
    return [str(x).strip()[:length] for x in (v if isinstance(v, list) else []) if str(x).strip()][:n]


def normalize_analysis(raw: Dict[str, Any]) -> Dict[str, Any]:
    """تنظيف ما يعيده النموذج — لا نثق بالمخطط: كل قائمة مسقوفة وكل نص مقصوص."""
    raw = raw if isinstance(raw, dict) else {}
    pb = raw.get("reply_playbook") if isinstance(raw.get("reply_playbook"), dict) else {}
    inter = raw.get("interaction") if isinstance(raw.get("interaction"), dict) else {}
    lex = raw.get("lexicon") if isinstance(raw.get("lexicon"), dict) else {}
    pillars = []
    for p in raw.get("pillars") if isinstance(raw.get("pillars"), list) else []:
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            try:
                share = max(0.0, min(float(p.get("share") or 0), 1.0))
            except (TypeError, ValueError):
                share = 0.0
            pillars.append({"name": str(p["name"])[:60], "share": round(share, 2),
                            "angle": str(p.get("angle") or "")[:200]})
    persona = raw.get("persona") if isinstance(raw.get("persona"), dict) else {}
    return {
        "voice_summary": str(raw.get("voice_summary") or "")[:700],
        "register": str(raw.get("register") or "")[:80],
        "humor": str(raw.get("humor") or "")[:80],
        "stance": str(raw.get("stance") or "")[:160],
        "rhetorical_devices": _as_list(raw.get("rhetorical_devices"), 6, 100),
        "pillars": pillars[:6],
        "formats": _as_list(raw.get("formats"), 6, 60),
        "reply_playbook": {k: str(pb.get(k) or "")[:240]
                           for k in ("agree", "disagree", "question", "attack", "praise", "general")
                           if str(pb.get(k) or "").strip()},
        "interaction": {k: str(inter.get(k) or "")[:200]
                        for k in ("engages_with", "initiates", "tone_shift")
                        if str(inter.get(k) or "").strip()},
        "lexicon": {"signature": _as_list(lex.get("signature"), 12, 40),
                    "avoid": _as_list(lex.get("avoid"), 8, 40)},
        "values": _as_list(raw.get("values"), 5, 80),
        "red_lines": _as_list(raw.get("red_lines"), 6, 140),
        "persona": {
            "role": str(persona.get("role") or "")[:120],
            "tone": str(persona.get("tone") or "")[:20],
            "dialect": str(persona.get("dialect") or "")[:40],
            "dos": _as_list(persona.get("dos"), 10, 200),
            "donts": _as_list(persona.get("donts"), 6, 200),
        },
    }


def analyze(profile: Dict[str, Any], llm: Any = None) -> Dict[str, Any]:
    """ابنِ البصمة كاملة من بروفايل مسحوب. بلا نموذج: قياسات + أمثلة فقط."""
    tweets = profile.get("tweets") or []
    replies = profile.get("replies") or []
    tw_text = [t.get("text", "") for t in tweets]
    rp_text = [r.get("text", "") for r in replies]
    stats = stylometry(tw_text, rp_text, [t.get("created_at", "") for t in tweets + replies])
    try:
        # المواضيع من التغريدات الأصلية: الردود أغلبها مجاملات («الله يحفظك») قيس
        # أنها تضخّم «كلام عابر» (34%) و«الدين» في التوزيع وتُفسد الاهتمامات المشتقة
        topics = topic_distribution(tw_text if len(tw_text) >= 5 else tw_text + rp_text)
    except Exception:  # noqa: BLE001
        logger.exception("topic distribution failed")
        topics = {}
    analysis: Dict[str, Any] = {}
    if llm is not None and getattr(llm, "available", False):
        try:
            analysis = normalize_analysis(
                llm.complete_json(_ANALYSIS_SYSTEM, _analysis_prompt(profile, stats, topics)) or {})
        except Exception:  # noqa: BLE001
            logger.exception("voice analysis LLM call failed")
    pairs = [r for r in replies if r.get("in_reply_to")]
    return {
        "version": VERSION,
        "source_handle": profile.get("handle", ""),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample": {"tweets": len(tweets), "replies": len(replies), "pairs": len(pairs),
                   "threads": sum(1 for r in pairs if r["in_reply_to"].get("self"))},
        "style": stats,
        "topics": topics,
        "analysis": analysis,
        "exemplars": select_exemplars(tweets, replies),
    }


# ─────────────────────────── consumption ───────────────────────────
def get(config) -> Dict[str, Any]:
    vp = (getattr(config, "business_rules", None) or {}).get(STORE_KEY)
    return vp if isinstance(vp, dict) and vp.get("version") else {}


def _style_rules(style: Dict[str, Any], kind: str) -> List[str]:
    out = []
    if kind == "reply" and style.get("reply_len_median"):
        out.append(f"طول ردودك المعتاد ≈ {style['reply_len_median']} حرفاً.")
    elif style.get("len_median"):
        out.append(f"طول منشوراتك المعتاد بين {style.get('len_p25')} و{style.get('len_p75')} حرفاً "
                   f"(الوسيط {style['len_median']}).")
    rate = style.get("reply_emoji_rate" if kind == "reply" else "emoji_rate", 0) or 0
    if rate >= 0.5:
        out.append("تستخدم الإيموجي عادةً" + (f" (خاصة {' '.join(style.get('top_emojis', [])[:4])})"
                                            if style.get("top_emojis") else "") + ".")
    elif rate <= 0.1:
        out.append("لا تستخدم الإيموجي تقريباً.")
    if kind != "reply":
        if style.get("hashtag_rate", 0) <= 0.1:
            out.append("نادراً ما تستخدم الهاشتاقات.")
        if style.get("linebreak_rate", 0) >= 0.4:
            out.append("تقسّم منشوراتك على أسطر.")
        if style.get("number_rate", 0) >= 0.4:
            out.append("تستند كثيراً إلى أرقام ووقائع محدّدة.")
        if style.get("openers"):
            out.append("مطالعك المعتادة: " + "، ".join(f"«{o}»" for o in style["openers"][:4]) + ".")
    if style.get("signature_phrases"):
        out.append("عبارات تتكرر عندك: " + "، ".join(f"«{p}»" for p in style["signature_phrases"][:6]) + ".")
    return out


def voice_block(profile: Dict[str, Any], kind: str = "general") -> str:
    """كتلة الموجّه: kind = general | post | reply."""
    if not profile:
        return ""
    a = profile.get("analysis") or {}
    st = profile.get("style") or {}
    lines = ["🎙️ بصمة صوتك (مستخرجة من أسلوب حقيقي — حافظ عليها في كل ما تكتب):"]
    if a.get("voice_summary"):
        lines.append(a["voice_summary"])
    meta = "؛ ".join(x for x in (a.get("register"), a.get("humor"), a.get("stance")) if x)
    if meta:
        lines.append(f"السجل/الدعابة/الموقف: {meta}.")
    if a.get("rhetorical_devices"):
        lines.append("أدواتك البلاغية: " + "، ".join(a["rhetorical_devices"]) + ".")
    lines += _style_rules(st, kind)
    lex = a.get("lexicon") or {}
    if lex.get("signature"):
        lines.append("مفرداتك المميّزة: " + "، ".join(lex["signature"][:10]) + ".")
    if lex.get("avoid"):
        lines.append("لا تستخدم: " + "، ".join(lex["avoid"][:6]) + ".")
    if a.get("red_lines"):
        lines.append("خطوطك الحمراء: " + "؛ ".join(a["red_lines"]) + ".")
    if kind in ("general", "post") and a.get("pillars"):
        lines.append("محاورك: " + "؛ ".join(
            f"{p['name']} ({p['angle']})" if p.get("angle") else p["name"] for p in a["pillars"]) + ".")
    ex = profile.get("exemplars") or {}
    if kind == "post" and ex.get("posts"):
        lines.append("أمثلة حقيقية من أسلوبك — للإيقاع والنبرة فقط؛ **ممنوع نسخ جملها أو "
                     "إعادة وقائعها**، اكتب جديداً تماماً بنفس الروح:")
        lines += [f"  • {p[:280]}" for p in ex["posts"][:5]]
    if kind == "reply":
        pb = a.get("reply_playbook") or {}
        if pb:
            labels = {"agree": "الموافق", "disagree": "المخالف", "question": "السائل",
                      "attack": "المهاجم", "praise": "الشاكر/المحيّي", "general": "عموماً"}
            lines.append("دليل ردودك — اختر ما يناسب نبرة التغريدة:")
            lines += [f"  - مع {labels[k]}: {v}" for k, v in pb.items()]
        if (a.get("interaction") or {}).get("tone_shift"):
            lines.append(f"نبرة ردودك: {a['interaction']['tone_shift']}")
        if ex.get("replies"):
            lines.append("أمثلة حقيقية لطريقة ردّك (للأسلوب فقط، لا تنسخها):")
            lines += [f"  • على «{p['to'][:120]}» ← «{p['reply'][:160]}»" for p in ex["replies"][:4]]
    return "\n".join(lines)


def post_rules(profile: Dict[str, Any]) -> Dict[str, str]:
    """قواعد تستبدل قواعد «البيت» المتعارضة مع الهوية (الطول/الإيموجي/المطلع/النبرة)."""
    st = profile.get("style") or {}
    lo, hi = int(st.get("len_p25") or 60), int(st.get("len_p75") or 220)
    hi = max(min(hi, 270), 40)
    lo = min(max(lo, 15), hi - 10)
    emoji = st.get("emoji_rate", 0) or 0
    em = ("إيموجي بالقدر الذي تستخدمه عادةً" if emoji >= 0.5
          else "بلا إيموجي" if emoji <= 0.1 else "إيموجي واحد كحد أقصى")
    openers = st.get("openers") or []
    return {
        "length": f"- طول المنشور بين {lo} و{hi} حرفاً كما تكتب عادةً، مكتمل المعنى بجملة تامة.",
        "emoji": f"- {em}، وهاشتاق واحد كحد أقصى (أو بدون).",
        "hook": ("- ابدأ كما تبدأ عادةً" + (f" (مثل: {'، '.join(openers[:3])})" if openers else "")
                 + " دون تكرار مطلع منشور سابق حرفياً."),
        "voice": "- نبرتك هي بصمة صوتك الثابتة أعلاه، لا نبرة مختلفة كل مرة.",
    }


def reply_length_line(profile: Dict[str, Any], brief: bool) -> str:
    st = profile.get("style") or {}
    n = int(st.get("reply_len_median") or 0)
    if not n:
        return ""
    cap = min(max(int(n * 1.6), 40), 120 if brief else 270)
    emoji = st.get("reply_emoji_rate", 0) or 0
    em = "بإيموجي كعادتك" if emoji >= 0.5 else "بلا إيموجي" if emoji <= 0.1 else "إيموجي واحد كحد أقصى"
    return f"- بطول ردودك المعتاد (≈{n} حرفاً، وأقل من {cap})، بلا هاشتاقات، {em}.\n"


def min_post_chars(profile: Dict[str, Any], default: int) -> int:
    """بوابة «قصير جداً» تحترم الأسلوب: صاحب التغريدات القصيرة لا يُمنع من النشر."""
    p25 = int((profile.get("style") or {}).get("len_p25") or 0)
    return min(default, max(15, int(p25 * 0.8))) if p25 else default


#: أقل عدد كلمات دالة ليُعتبر التطابق «نسخاً» لا «عبارة مألوفة»
_COPY_MIN_WORDS = 6


def copies_exemplar(text: str, profile: Dict[str, Any], threshold: float = 0.7) -> bool:
    """هل المخرَج نسخة شبه حرفية من نص حقيقي للحساب الأصلي؟ (يُمنع نشره).

    العبارات القصيرة المألوفة («تسلم الله يحفظك اخوي») **أسلوب لا نسخ**: قيس في
    اختبار المطابقة الحي أن رد مجاملة مولَّداً طابق ردود الحساب فاعتُبر منسوخاً —
    وكان سيمنع ردوده المميّزة. لذا لا نحكم إلا على نصّين فيهما ≥6 كلمات دالة.
    """
    from .persona_templates import normalize_topic_words

    ex = profile.get("exemplars") or {}
    refs = list(ex.get("posts") or []) + [p.get("reply", "") for p in ex.get("replies") or []]
    words = normalize_topic_words(text or "")
    if len(words) < _COPY_MIN_WORDS:
        return False
    for r in refs:
        rw = normalize_topic_words(r or "")
        if len(rw) >= _COPY_MIN_WORDS and len(words & rw) / min(len(words), len(rw)) >= threshold:
            return True
    return False


# ─────────────────────────── fidelity ───────────────────────────
def fidelity(profile: Dict[str, Any], posts: List[str], replies: List[str]) -> Dict[str, Any]:
    """درجة مطابقة حتمية بين عيّنات مولَّدة والأسلوب المقاس (0..100) مع التفاصيل."""
    st = profile.get("style") or {}
    checks: Dict[str, bool] = {}
    if posts and st.get("len_median"):
        lo, hi = st.get("len_p25", 0) * 0.6, st.get("len_p75", 280) * 1.4
        checks["length"] = all(lo <= len(p) <= hi for p in posts)
    if posts and st.get("emoji_rate") is not None:
        r = st["emoji_rate"]
        has = [bool(_EMOJI_RE.search(p)) for p in posts]
        checks["emoji"] = all(has) if r >= 0.6 else (not any(has)) if r <= 0.1 else True
    dial = st.get("dialect_guess")
    if dial and dial != "فصحى" and (posts or replies):
        gen = dialect_scores(posts + replies)
        # كل الدرجات صفر ⇒ max يعيد أول مفتاح فيبدو أي نص (حتى الإنجليزي) «خليجياً»
        checks["dialect"] = bool(gen) and gen.get(dial, 0) > 0 and max(gen, key=gen.get) == dial
    if replies and st.get("reply_len_median"):
        checks["reply_length"] = all(len(r) <= max(st["reply_len_median"] * 2.5, 60) for r in replies)
    checks["original"] = not any(copies_exemplar(x, profile) for x in posts + replies)
    score = round(100 * sum(checks.values()) / len(checks)) if checks else 0
    return {"score": score, "checks": checks}
