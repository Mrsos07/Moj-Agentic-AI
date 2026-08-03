"""
Persona cloning service — استنساخ أسلوب حساب X معيّن.

Given a target handle, this service:
1. Scrapes the target's profile (bio + original tweets + replies) with the
   employee account's own cookies via ``PlaywrightXMCP.read_user_profile``.
2. Analyzes the writing style, discussion manner and topics — primarily with
   the LLM, falling back to a heuristic statistical analysis when the LLM is
   unavailable.
3. Builds a complete, ready-to-save :class:`Persona` dict that makes the AI
   employee write, reply and discuss exactly like the cloned account.

The result is a *proposal*: the API returns it to the UI where the user
reviews/edits it before saving — nothing is persisted here.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeos.services.persona_clone")

#: أقصى حجم عينة يُرسَل للـLLM (تغريدات + ردود)
_MAX_SAMPLE = 40


# ─────────────────────────── scraping ───────────────────────────
def scrape_profile(cookie_label: str, handle: str, limit: int = 20,
                   headless: bool = True, lock_timeout: float = 45.0,
                   tab_budget_seconds: float = 18.0) -> Dict[str, Any]:
    """Pull the target account's bio/tweets/replies using our saved cookies.

    العملية تفاعلية (المستخدم ينتظر أمام الشاشة)، لذا لها ميزانية زمنية صريحة:
    انتظار محدود لقفل المتصفح، وسقف زمني لتمرير كل تبويب. عشرون تغريدة أصلية
    مع نصف ذلك من الردود عيّنة كافية لتحليل الأسلوب — الزيادة تكلف وقتاً بلا
    تحسّن يُذكر في نتيجة التحليل.
    """
    from ..mcp.base import MCPRequest
    from ..mcp.servers.playwright_x_mcp import PlaywrightXMCP

    server = PlaywrightXMCP()
    res = server.call(MCPRequest(
        employee_id="persona_clone",
        tool="read_user_profile",
        params={"handle": handle, "limit": limit, "headless": headless,
                "lock_timeout": lock_timeout,
                "tab_budget_seconds": tab_budget_seconds},
        context={"cookie_label": cookie_label},
    ))
    if not res.success:
        raise RuntimeError(res.error or "failed to scrape target profile")
    return dict(res.data)


# ─────────────────────────── heuristic analysis ───────────────────────────
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]"
)
_HASHTAG_RE = re.compile(r"#[\w\u0600-\u06FF_]+")
_AR_RE = re.compile(r"[\u0600-\u06FF]")
_STOPWORDS = {
    "في", "من", "على", "الى", "إلى", "عن", "أن", "ان", "لا", "ما", "هذا",
    "هذه", "التي", "الذي", "مع", "كل", "لم", "لن", "قد", "و", "او", "أو",
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is",
    "it", "that", "this", "with", "as", "at", "be", "are", "was",
}


def _style_stats(texts: List[str]) -> Dict[str, Any]:
    """إحصاءات أسلوبية خام: الطول، الإيموجي، الهاشتاقات، اللغة، أبرز الكلمات."""
    if not texts:
        return {}
    lengths = [len(t) for t in texts]
    emoji_ratio = sum(1 for t in texts if _EMOJI_RE.search(t)) / len(texts)
    hashtag_ratio = sum(1 for t in texts if _HASHTAG_RE.search(t)) / len(texts)
    question_ratio = sum(1 for t in texts if "؟" in t or "?" in t) / len(texts)
    arabic_ratio = sum(1 for t in texts if _AR_RE.search(t)) / len(texts)

    words: Counter = Counter()
    for t in texts:
        for w in re.findall(r"[\w\u0600-\u06FF]{3,}", t):
            lw = w.lower()
            if lw not in _STOPWORDS and not lw.isdigit():
                words[lw] += 1
    hashtags: Counter = Counter(h for t in texts for h in _HASHTAG_RE.findall(t))

    return {
        "avg_length": sum(lengths) // len(lengths),
        "uses_emoji": emoji_ratio >= 0.25,
        "uses_hashtags": hashtag_ratio >= 0.25,
        "asks_questions": question_ratio >= 0.2,
        "language": "ar" if arabic_ratio >= 0.5 else "en",
        "top_words": [w for w, _ in words.most_common(12)],
        "top_hashtags": [h for h, _ in hashtags.most_common(6)],
    }


def _fallback_persona(profile: Dict[str, Any], language: str) -> Dict[str, Any]:
    """برسونا احتياطية مبنية على الإحصاءات فقط (عند غياب الـLLM)."""
    tweets = [t.get("text", "") for t in profile.get("tweets", [])]
    replies = [r.get("text", "") for r in profile.get("replies", [])]
    stats = _style_stats(tweets + replies)
    handle = profile.get("handle", "")
    name = profile.get("display_name") or handle

    style_bits = []
    if stats.get("avg_length"):
        style_bits.append(f"اكتب بطول يقارب {stats['avg_length']} حرفاً")
    style_bits.append("استخدم الإيموجي باعتدال" if stats.get("uses_emoji")
                      else "لا تستخدم الإيموجي")
    style_bits.append("استخدم الهاشتاقات المرتبطة" if stats.get("uses_hashtags")
                      else "تجنّب الهاشتاقات")
    if stats.get("asks_questions"):
        style_bits.append("اطرح أسئلة تحفّز النقاش كما يفعل الحساب الأصلي")

    return {
        "display_name": name,
        "role": f"Content creator cloned from {handle}",
        "bio": (profile.get("bio") or "")[:280],
        "tone": "casual",
        "language": stats.get("language", language or "ar"),
        "dialect": "",
        "values": [],
        "dos": style_bits + [
            f"غطِّ مواضيع مثل: {', '.join(stats.get('top_words', [])[:8])}"
            if stats.get("top_words") else "",
        ],
        "donts": ["لا تخرج عن مواضيع واهتمامات الحساب المستنسخ"],
        "interests": [],
        "instructions": (
            f"أنت نسخة بأسلوب الحساب {handle} ({name}). حاكِ أسلوبه في الكتابة "
            "والرد والنقاش كما يظهر في عيّنة تغريداته وردوده، والتزم بمواضيعه "
            "وطريقة طرحه دون نسخ نصوصه حرفياً."
        ),
    }


# ─────────────────────────── LLM analysis ───────────────────────────
def _build_clone_prompt(profile: Dict[str, Any], language: str) -> str:
    tweets = [t.get("text", "") for t in profile.get("tweets", [])][:_MAX_SAMPLE]
    replies = [r.get("text", "") for r in profile.get("replies", [])][:_MAX_SAMPLE]
    stats = _style_stats(tweets + replies)

    sample_tweets = "\n".join(f"- {t[:220]}" for t in tweets[:20]) or "(none)"
    sample_replies = "\n".join(f"- {r[:220]}" for r in replies[:20]) or "(none)"

    return f"""You are an expert social-media style analyst. Study the sample below from the X account {profile.get('handle', '')} ({profile.get('display_name', '')}) and produce a persona that CLONES this account's exact style so an AI employee can imitate it.

ACCOUNT BIO: {profile.get('bio', '') or '(empty)'}
STYLE STATS: {json.dumps(stats, ensure_ascii=False)}

ORIGINAL TWEETS SAMPLE:
{sample_tweets}

REPLIES / DISCUSSION SAMPLE:
{sample_replies}

Analyze deeply: tone (sarcastic/formal/casual/witty...), dialect (Saudi/Egyptian/Gulf/MSA/English...), sentence length and rhythm, emoji & hashtag habits, favourite topics, how they open discussions, how they reply and argue with people, recurring phrases/expressions (do NOT copy texts verbatim — capture the STYLE).

Reply with ONLY a JSON object (no markdown) with these keys:
{{
  "display_name": "...",
  "role": "...",
  "bio": "a bio in the target's own voice ({language}), max 200 chars",
  "tone": "one of: professional|casual|witty|formal|friendly",
  "language": "ar or en (the language the target writes in)",
  "dialect": "the dialect/style variant, e.g. سعودية، مصرية، خليجية، فصحى",
  "values": ["3-5 values the account clearly holds"],
  "dos": ["6-10 precise style rules to write EXACTLY like the target: length, emoji, hashtags, phrasing habits, how to open/close, how to reply in discussions"],
  "donts": ["4-6 things the target never does, to avoid breaking character"],
  "interests": ["choose ONLY from: politics, economy, sports, tech, entertainment, religion, health, travel — the ones matching the account's topics"],
  "instructions": "a rich standing instruction (in {language}, 3-6 sentences) telling the employee it is a clone of {profile.get('handle', '')}: its topics, discussion manner, how it engages and replies, and to always keep this voice"
}}"""


def build_cloned_persona(profile: Dict[str, Any], llm: Any,
                         language: str = "ar") -> Dict[str, Any]:
    """حلّل العينة وابنِ برسونا مستنسخة — LLM أولاً ثم احتياطي إحصائي."""
    if not (profile.get("tweets") or profile.get("replies")):
        raise RuntimeError(
            f"لم أجد أي تغريدات لحساب {profile.get('handle', '')} — تحقّق من صحة "
            "اليوزر، أو أن الحساب محمي/فارغ، أو أن كوكيز حسابك انتهت صلاحيتها."
        )

    if not (llm and getattr(llm, "available", False)):
        return {"source": "heuristic", "persona": _fallback_persona(profile, language)}

    prompt = _build_clone_prompt(profile, language)
    try:
        raw = llm.complete(
            system=("You clone social-media account personas from writing samples. "
                    "Reply with JSON only."),
            prompt=prompt, temperature=0.4, max_tokens=1200,
        ) or ""
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        persona = json.loads(match.group(0)) if match else {}
    except Exception:  # noqa: BLE001
        logger.exception("LLM clone analysis failed — falling back to heuristic")
        persona = {}

    if not persona or not persona.get("instructions"):
        return {"source": "heuristic", "persona": _fallback_persona(profile, language)}

    # تطبيع الحقول لضمان توافقها مع نموذج Persona
    base = _fallback_persona(profile, language)
    for key in ("display_name", "role", "bio", "tone", "language", "dialect",
                "instructions"):
        persona[key] = str(persona.get(key) or base.get(key, ""))
    for key in ("values", "dos", "donts", "interests"):
        v = persona.get(key)
        persona[key] = [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []
    if persona["tone"] not in ("professional", "casual", "witty", "formal", "friendly"):
        persona["tone"] = "casual"
    if persona["language"] not in ("ar", "en"):
        persona["language"] = base.get("language", "ar")

    return {"source": "ai", "persona": persona}


def clone_persona(cookie_label: str, handle: str, llm: Any, limit: int = 20,
                  language: str = "ar", lock_timeout: float = 45.0,
                  tab_budget_seconds: float = 18.0) -> Dict[str, Any]:
    """التدفّق الكامل: سحب البروفايل ← تحليل ← برسونا مستنسخة جاهزة للمراجعة."""
    profile = scrape_profile(cookie_label, handle, limit=limit,
                             lock_timeout=lock_timeout,
                             tab_budget_seconds=tab_budget_seconds)
    result = build_cloned_persona(profile, llm, language=language)
    result["target"] = {
        "handle": profile.get("handle", f"@{handle}"),
        "display_name": profile.get("display_name", ""),
        "bio": profile.get("bio", ""),
        "followers": profile.get("followers", ""),
        "tweet_count": profile.get("tweet_count", 0),
        "reply_count": profile.get("reply_count", 0),
        "scrape_seconds": profile.get("scrape_seconds"),
    }
    return result
