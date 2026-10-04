"""
تحليل المواضيع: **هل يخص التخصص؟** و**هل هو مهم الآن؟** — لكل ما رصده الموظف.

لماذا؟ كان القرار كله مبنياً على ``relevance_score`` (عدّ كلمات مفتاحية):
- لا يرى المعنى: خبر تقني عن «نموذج GPT» سجّل 0 فحُجب، وصفقة «صندوق
  الاستثمارات» سجّلت 1 فقط.
- لا يرى الأهمية: تغريدة بـ1.4 مليون مشاهدة وأخرى بـ50 مشاهدة متساويتان إن
  تطابقت كلماتهما، والترتيب كان بالكلمات أولاً.

الطبقتان هنا:

1. **الصلة الدلالية** (embeddings): مقارنة النص بجُمل مرجعية (anchor) لكل
   اهتمام من اهتمامات الموظف **مقابل** جُمل مرجعية لما ليس من تخصصه (باقي
   الاهتمامات + كلام يومي + إعلانات). الدرجة = ``الهامش`` = أقرب مرجع إيجابي −
   أقرب مرجع سلبي. لماذا الهامش لا التشابه المطلق؟ قيس: تشابه نص كروي مع وصف
   الموظف (0.174) كان **أعلى** من خبر سياسي حقيقي (0.155) — التشابه المطلق مع
   ملف واحد لا يفصل شيئاً. الهامش صنّف 11/11 عيّنة صحيحاً للموظفَين.
2. **الأهمية**: المشاهدات + التفاعل (الرد أثقل من الإعجاب) × الحداثة (نصف عمر
   12 ساعة)، مطبّعة داخل كل مصدر في الدورة.

بلا مفتاح/شبكة: الطبقة الدلالية تُتخطّى بصمت ويبقى المسار الحتمي (الكلمات على
حدود الكلمة) كما هو — لا توقف أبداً.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Sequence

from .persona_templates import INTERESTS, keywords_for, relevance_score

if TYPE_CHECKING:  # pragma: no cover
    from ..cognition.context import ExecutionContext

logger = logging.getLogger("aeos.topic_intel")

#: جُمل مرجعية مُعايَرة (قيس عليها الهامش) — وصف لنوع المحتوى لا كلمات مفردة
ANCHORS: Dict[str, str] = {
    "politics": "خبر سياسي: الحكومة والوزراء والانتخابات والعلاقات الدولية والحروب والصراعات العسكرية",
    "economy": "خبر اقتصادي: الأسواق والأسهم والاستثمار والشركات والعملات والنفط والميزانية والتضخم",
    "religion": "محتوى ديني: الإسلام والقرآن والحديث والفقه والعبادات والأخلاق والمواعظ",
    "tech": "تقنية: الذكاء الاصطناعي والبرمجيات والتطبيقات والهواتف والشركات التقنية والإنترنت",
    "history": "تاريخ: أحداث تاريخية وحضارات ومعارك قديمة وشخصيات تاريخية وفي مثل هذا اليوم",
    "sports": "رياضة: كرة القدم والمباريات والدوري واللاعبين والأندية والأهداف",
    "entertainment": "ترفيه: المشاهير والفنانين والأفلام والمسلسلات والحفلات والموضة",
    "art": "فن: اللوحات والمعارض والتصميم والرسم والنحت والإبداع الفني",
    "health": "صحة: الطب والعلاج والأدوية والتغذية واللياقة والأمراض والوقاية",
    "travel": "سفر وسياحة: الرحلات والوجهات السياحية والفنادق والطيران والتأشيرات",
}
#: مراجع سلبية دائمة — ما لا يخص أي تخصص
NEGATIVE_ANCHORS: Dict[str, str] = {
    "chatter": "كلام يومي عابر: تحية وسؤال عن الحال ونكتة وصورة شخصية وأمور شخصية",
    "ads": "إعلان تجاري: منتج للبيع وعرض وخصم واطلب الآن وتسوق",
}

#: عتبات الهامش (مُعايَرة على text-embedding-3-small) — قابلة للتجاوز من business_rules
DEFAULT_MARGINS = {"hi": 0.10, "mid": 0.04, "veto": -0.10}

#: المصادر التي تُحلَّل: مفتاح scratch → دالة نص التضمين
_SOURCES: Dict[str, Callable[[dict], str]] = {
    "timeline": lambda t: t.get("text", ""),
    "top_posts": lambda t: t.get("text", ""),
    "interest_tweets": lambda t: t.get("text", ""),
    "hashtag_tweets": lambda t: t.get("text", ""),
    "trend_tweets": lambda t: t.get("text", ""),
    "news": lambda n: f"{n.get('title', '')}. {n.get('summary', '')}",
    "trends": lambda n: f"{n.get('title', '')}. {n.get('summary', '')}",
    "x_trends": lambda t: f"{t.get('name', '')} {t.get('category', '')}",
}
_TWEET_SOURCES = ("timeline", "top_posts", "interest_tweets", "hashtag_tweets", "trend_tweets")

_MAX_ITEMS = 120
_EMBED_TEXT_CHARS = 600


# ─────────────────────────── embeddings ───────────────────────────
class _LRU:
    def __init__(self, cap: int = 5000) -> None:
        self._d: "OrderedDict[str, List[float]]" = OrderedDict()
        self._cap = cap
        self._lock = threading.Lock()

    def get(self, k: str) -> Optional[List[float]]:
        with self._lock:
            v = self._d.get(k)
            if v is not None:
                self._d.move_to_end(k)
            return v

    def put(self, k: str, v: List[float]) -> None:
        with self._lock:
            self._d[k] = v
            self._d.move_to_end(k)
            while len(self._d) > self._cap:
                self._d.popitem(last=False)


_CACHE = _LRU()


def _default_embedder() -> Optional[Callable[[List[str]], List[List[float]]]]:
    if os.getenv("TOPIC_SEMANTIC", "1").strip() in ("0", "false", "no"):
        return None
    from app.core.llm_provider import resolve_llm

    ep = resolve_llm()
    if not ep.available:
        return None
    from openai import OpenAI

    client = OpenAI(**ep.client_kwargs(), timeout=25)
    model = ep.embedding_model

    def embed(texts: List[str]) -> List[List[float]]:
        out = client.embeddings.create(model=model, input=texts)
        return [d.embedding for d in out.data]

    embed.model = model  # type: ignore[attr-defined]
    return embed


#: قابل للاستبدال في الاختبارات (verify_topic_intel.py) — لا شبكة
embedder_factory: Callable[[], Optional[Callable[[List[str]], List[List[float]]]]] = _default_embedder


def embed_texts(texts: Sequence[str]) -> Optional[List[List[float]]]:
    """تضمينات مع ذاكرة مؤقتة؛ نداء شبكة واحد لكل ما ليس في الذاكرة. None عند الفشل."""
    embed = embedder_factory()
    if embed is None:
        return None
    model = getattr(embed, "model", "")
    keys = [hashlib.sha1(f"{model}|{t}".encode("utf-8")).hexdigest() for t in texts]
    missing = [i for i, k in enumerate(keys) if _CACHE.get(k) is None]
    if missing:
        try:
            vecs = embed([texts[i] for i in missing])
        except Exception as exc:  # noqa: BLE001
            logger.warning("embeddings unavailable: %s", exc)
            return None
        for i, v in zip(missing, vecs):
            _CACHE.put(keys[i], v)
    return [_CACHE.get(k) for k in keys]


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


def _anchor_sets(interests: List[str]) -> "tuple[Dict[str, str], Dict[str, str]]":
    """مراجع إيجابية (اهتمامات الموظف) وسلبية (كل ما عداها)."""
    mine = {i for i in interests if i}
    pos: Dict[str, str] = {}
    for i in mine:
        if i in ANCHORS:
            pos[i] = ANCHORS[i]
        else:  # اهتمام مخصّص: وصف من اسمه + كلماته
            label = INTERESTS.get(i, i)
            words = "، ".join(keywords_for([i])[:8])
            pos[i] = f"محتوى عن {label}: {words}"
    neg = {k: v for k, v in ANCHORS.items() if k not in mine}
    neg.update(NEGATIVE_ANCHORS)
    return pos, neg


def semantic_margins(texts: List[str], interests: List[str]) -> Optional[List[float]]:
    """هامش الصلة لكل نص (موجب = ضمن التخصص). None بلا تضمينات."""
    if not texts or not interests:
        return None
    pos, neg = _anchor_sets(interests)
    anchors = list(pos.values()) + list(neg.values())
    vecs = embed_texts(anchors + [t[:_EMBED_TEXT_CHARS] for t in texts])
    if not vecs or any(v is None for v in vecs):
        return None
    pv, nv = vecs[:len(pos)], vecs[len(pos):len(anchors)]
    out = []
    for v in vecs[len(anchors):]:
        out.append(max(_cos(a, v) for a in pv) - max(_cos(a, v) for a in nv))
    return out


# ─────────────────────────── importance ───────────────────────────
def _parse_time(raw: str) -> Optional[datetime]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _recency(raw: str, now: datetime, half_life_h: float = 12.0) -> float:
    dt = _parse_time(raw)
    if dt is None:
        return 0.5
    age_h = max((now - dt).total_seconds() / 3600.0, 0.0)
    return 0.5 ** (min(age_h, 96.0) / half_life_h)


_COUNT_RE = re.compile(r"([\d٠-٩][\d٠-٩.,]*)\s*(K|M|ألف|آلاف|مليون)?", re.IGNORECASE)


def _posts_count(raw: str) -> int:
    m = _COUNT_RE.search((raw or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")))
    if not m:
        return 0
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    unit = (m.group(2) or "").lower()
    return int(n * (1_000 if unit in ("k", "ألف", "آلاف") else 1_000_000 if unit in ("m", "مليون") else 1))


def raw_importance(source: str, item: dict, now: datetime) -> float:
    """أهمية خام قبل التطبيع: انتشار × حداثة."""
    if source in _TWEET_SOURCES:
        n = {k: int(item.get(k) or 0) for k in ("views", "likes", "reposts", "replies")}
        eng = n["likes"] + 2 * n["reposts"] + 3 * n["replies"]
        reach = math.log10(1 + n["views"]) + 1.5 * math.log10(1 + eng)
        return reach * (0.4 + 0.6 * _recency(item.get("created_at", ""), now))
    if source == "x_trends":
        return math.log10(1 + _posts_count(item.get("posts", ""))) + 1.0
    # أخبار/مواضيع رائجة: الحداثة هي الإشارة المتاحة
    return 1.0 + 2.0 * _recency(item.get("published") or item.get("published_at") or "", now, 24.0)


# ─────────────────────────── the phase logic ───────────────────────────
def _margins_cfg(rules: Dict[str, Any]) -> Dict[str, float]:
    out = dict(DEFAULT_MARGINS)
    for k in out:
        try:
            out[k] = float(rules.get(f"semantic_margin_{k}", out[k]))
        except (TypeError, ValueError):
            pass
    return out


def _drop_promoted(context: "ExecutionContext") -> int:
    """البطاقات المموّلة لا تحمل توقيتاً على X (قيس على التايم لاين الحي: التغريدة
    الوحيدة بلا ``created_at`` كانت إعلاناً). نحذفها فقط إن كان أغلب الدفعة مؤرَّخاً
    — وإلا فالأرجح أن المستخرج لم يجد الوقت أصلاً لا أنها إعلانات."""
    dropped = 0
    for key in _TWEET_SOURCES:
        items = context.scratch.get(key) or []
        if len(items) < 3:
            continue
        dated = sum(1 for t in items if t.get("created_at"))
        if dated / len(items) < 0.6:
            continue
        kept = [t for t in items if t.get("created_at")]
        dropped += len(items) - len(kept)
        context.scratch[key] = kept
    return dropped


def analyze(context: "ExecutionContext") -> Dict[str, Any]:
    """حلّل كل ما رُصد هذه الدورة: يضع ``_sem`` (هامش) و``_imp`` (0..1) على كل عنصر."""
    interests = list(getattr(context.config.persona, "interests", []) or [])
    rules = context.config.business_rules or {}
    now = datetime.now(timezone.utc)
    promoted = _drop_promoted(context)

    entries: List[tuple] = []  # (source, item, text)
    for source, text_of in _SOURCES.items():
        for item in (context.scratch.get(source) or [])[:40]:
            if isinstance(item, dict):
                text = re.sub(r"\s+", " ", str(text_of(item) or "")).strip()
                if text:
                    entries.append((source, item, text))
    entries = entries[:_MAX_ITEMS]

    # الأهمية: تطبيع داخل كل مصدر (مشاهدات التايم لاين لا تُقارَن بعدد منشورات ترند)
    by_source: Dict[str, List[tuple]] = {}
    for e in entries:
        by_source.setdefault(e[0], []).append(e)
    for group in by_source.values():
        raws = [raw_importance(s, it, now) for s, it, _ in group]
        top = max(raws) or 1.0
        for (_, it, _), r in zip(group, raws):
            it["_imp"] = round(r / top, 3)

    semantic = bool(rules.get("semantic_relevance", True)) and bool(interests)
    margins = semantic_margins([t for _, _, t in entries], interests) if semantic else None
    index: Dict[str, float] = {}
    if margins is not None:
        for (_, it, text), m in zip(entries, margins):
            it["_sem"] = round(m, 4)
            index[_key(text)] = m

    context.scratch["topic_intel"] = {
        "semantic": margins is not None, "index": index,
        "margins": _margins_cfg(rules), "promoted_dropped": promoted,
    }
    in_scope = [(s, it, t) for (s, it, t) in entries
                if relevance(context, t, interests, it) >= int(rules.get("min_relevance_score", 1))]
    in_scope.sort(key=lambda e: e[1].get("_imp", 0), reverse=True)
    return {
        "analyzed": len(entries),
        "in_scope": len(in_scope),
        "semantic": margins is not None,
        "promoted_dropped": promoted,
        "top": [{"source": s, "text": t[:100], "importance": it.get("_imp", 0),
                 "margin": it.get("_sem")} for s, it, t in in_scope[:3]],
    }


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:80]


def _lookup(context: "ExecutionContext", text: str) -> Optional[float]:
    intel = context.scratch.get("topic_intel") or {}
    index = intel.get("index") or {}
    if not index:
        return None
    hay = re.sub(r"\s+", " ", text or "").strip()
    k = hay[:80]
    if k in index:
        return index[k]
    # نصوص المواقع تُبنى أحياناً بإضافات («النص @الحساب»، «الاسم التصنيف الملخص»)
    for key, m in index.items():
        if len(key) >= 12 and (hay.startswith(key) or key.startswith(hay[:40])):
            return m
    return None


def relevance(context: "ExecutionContext", text: str, interests: List[str],
              item: Optional[dict] = None) -> int:
    """درجة صلة متوافقة مع ``relevance_score`` (عدد صحيح، تُقارن بـ min_relevance_score).

    = max(الكلمات، نقاط الهامش الدلالي)، مع **فيتو**: هامش سلبي واضح يُسقط مطابقة
    كلمة واحدة (هي غالباً جذر عابر: «رئيس» في «الطبق الرئيسي»).
    """
    kw = relevance_score(text, interests)
    if not interests:
        return kw
    m = item.get("_sem") if isinstance(item, dict) and "_sem" in item else _lookup(context, text)
    if m is None:
        return kw
    cfg = (context.scratch.get("topic_intel") or {}).get("margins") or DEFAULT_MARGINS
    if m <= cfg["veto"] and kw <= 1:
        return 0
    sem_pts = 3 if m >= cfg["hi"] else 2 if m >= cfg["mid"] else 1 if m > 0 else 0
    return max(kw, sem_pts)


def importance(item: dict) -> float:
    try:
        return float(item.get("_imp", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def blended_score(rel: int, imp: float, bonus: float = 0.0) -> float:
    """ترتيب واحد: الصلة أولاً (مسقوفة) ثم الأهمية ثم أولوية المصدر.

    كان الترتيب ``(rel, score)`` فيتقدّم أي نص بثلاث كلمات مطابقة على تغريدة
    منتشرة بكلمتين. الآن الأهمية تقلب الترتيب ضمن الصلة المقبولة.
    """
    return 0.55 * min(max(rel, 0), 4) / 4 + 0.35 * imp + 0.10 * min(max(bonus, 0), 5) / 5


def describe_metrics(item: dict) -> str:
    """سطر قصير للموجّهات: «👁 12K · ❤ 300 · منذ 3س»."""
    def k(n: int) -> str:
        return f"{n / 1_000_000:.1f}M" if n >= 1_000_000 else f"{n / 1000:.0f}K" if n >= 1000 else str(n)

    bits = []
    if item.get("views"):
        bits.append(f"👁 {k(int(item['views']))}")
    if item.get("likes"):
        bits.append(f"❤ {k(int(item['likes']))}")
    dt = _parse_time(item.get("created_at") or item.get("published") or "")
    if dt:
        hours = int((datetime.now(timezone.utc) - dt).total_seconds() // 3600)
        bits.append(f"منذ {hours}س" if hours < 48 else f"منذ {hours // 24}ي")
    return " · ".join(bits)
