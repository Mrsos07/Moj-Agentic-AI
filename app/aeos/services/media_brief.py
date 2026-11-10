"""
الموجز الإعلامي — بحث عميق مرة واحدة ثم رسالة إعلامية يكتب الموظفون منها، لا نشر أعمى.

قبله: ``order_publish(employee, topic)`` كان يرسل نص الموضوع كما هو، فيبحث كل موظف وحده بحثاً
سطحياً (4 مقتطفات × 320 حرفاً، عمق basic) ويكتب منفرداً — ثلاثة موظفين = ثلاثة بحوث ضحلة
بمحتوى متشابه أو متناقض، وأرقام قد لا تكون في أي مصدر.

الآن عند توجيه المنسّق بموضوع:
1. **بحث عميق** في Tavily (``advanced`` + أخبار آخر يوم، ثم أسبوع إن قلّت النتائج) مع ملخّص المزوّد.
2. **رسالة إعلامية** يصوغها LLM من المصادر فقط: العنوان، الخلاصة، آخر التطورات بالترتيب
   الزمني، الحقائق والأرقام (كلٌّ بمصدره)، التصريحات، الخلفية، **زوايا تناول مقترحة**، وما
   **لا يجوز ادّعاؤه** (غير مؤكَّد/شائعات).
3. **توزيع**: كل موظف يتلقّى الرسالة نفسها مع **زاوية مختلفة** تناسب تخصصه، فيقتبس منها
   ويصوغ بأسلوبه — لا يخترع حقائق خارجها، ولا يكرّر زميله.

الرسالة تُخزَّن مؤقتاً (30 دقيقة) لكل مستخدم/موضوع: توجيه 3 موظفين بالموضوع نفسه = بحث واحد.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

logger = logging.getLogger("aeos.media_brief")

_TTL = 30 * 60
_cache: Dict[tuple, tuple] = {}
_assigned: Dict[tuple, List[str]] = {}
_lock = threading.Lock()

_SYSTEM = (
    "أنت محرّر أخبار أول في غرفة أخبار عربية خليجية. تُعدّ «رسالة إعلامية» داخلية لفريق كُتّاب "
    "على X، من المصادر المرفقة فقط. القواعد:\n"
    "- كل حقيقة أو رقم أو تصريح يجب أن يكون موجوداً في المصادر، مع رقم المصدر [n].\n"
    "- رتّب التطورات من الأحدث إلى الأقدم، واذكر التوقيت إن ورد.\n"
    "- افصل المؤكَّد عن غير المؤكَّد: ما لم تؤكده مصادر موثوقة يوضع في do_not_claim.\n"
    "- لا رأي ولا تحليل في الحقائق؛ الرأي مكانه «الزوايا المقترحة» فقط.\n"
    "- الزوايا: 3 إلى 5 زوايا تناول مختلفة فعلاً (أمنية، سياسية، اقتصادية، إنسانية، تاريخية…)، "
    "كل زاوية بجملة واحدة تصلح منطلقاً لمنشور.\n"
    "أجب JSON مضغوطاً فقط."
)


def _norm(topic: str) -> str:
    return re.sub(r"\s+", " ", topic or "").strip().lower()[:200]


def _google_news_research(query: str) -> Dict[str, Any]:
    """بحث أخبار بلا مفتاح: Google News RSS (آخر يومين ثم أسبوع). المحتوى عنوان + مقتطف فقط،
    فالرسالة أقصر من Tavily لكنها من مصادر حقيقية مؤرّخة."""
    import html as _html
    import xml.etree.ElementTree as ET

    import requests

    from app.aeos.employee.news_sources import search_feed

    results: List[Dict[str, Any]] = []
    for when in ("2d", "7d"):
        try:
            resp = requests.get(search_feed(f"{query} when:{when}"), timeout=20,
                                headers={"User-Agent": "Mozilla/5.0"})
            root = ET.fromstring(resp.content)
        except Exception as exc:  # noqa: BLE001
            logger.info("google news research failed: %s", exc)
            continue
        seen = {r["url"] for r in results}
        for item in root.iter("item"):
            url = (item.findtext("link") or "").strip()
            if not url or url in seen:
                continue
            src = item.find("source")
            desc = re.sub(r"<[^>]+>", " ", _html.unescape(item.findtext("description") or ""))
            results.append({"url": url, "title": (item.findtext("title") or "").strip(),
                            "published_date": item.findtext("pubDate") or "",
                            "site": (urlsplit(src.get("url", "")).hostname or "") if src is not None else "",
                            "content": re.sub(r"\s+", " ", desc).strip()})
        if len(results) >= 6:
            break
    if not results:
        raise RuntimeError("البحث غير مُهيّأ (TAVILY_API_KEY) ولم يُعِد Google News نتائج")
    return {"answer": "", "results": results[:10]}


def _research(query: str) -> Dict[str, Any]:
    from app.aeos.mcp.servers.core_mcp import search_provider, tavily_search

    if not search_provider()[0]:
        # قيس 2026-11-08: لا TAVILY_API_KEY في .env وخادم Tavily MCP يطلب OAuth ⇒ كان كل توجيه
        # (order_publish) يُسند بلا بحث إطلاقاً. بديل مجاني بلا مفتاح: بحث Google News (RSS).
        return _google_news_research(query)
    data = tavily_search(query, max_results=8, topic="news", time_range="day", depth="advanced",
                         include_answer=True, full=True, timeout=45)
    results = [r for r in data.get("results") or [] if r.get("url")]
    if len(results) < 4:   # خبر أقل حداثة: وسّع النطاق لأسبوع
        wider = tavily_search(query, max_results=8, topic="news", time_range="week", depth="advanced",
                              include_answer=True, full=True, timeout=45)
        seen = {r["url"] for r in results}
        results += [r for r in wider.get("results") or [] if r.get("url") and r["url"] not in seen]
        data["answer"] = data.get("answer") or wider.get("answer")
    return {"answer": data.get("answer") or "", "results": results[:10]}


def build(topic: str, *, user_id: Optional[int], llm=None, query: str = "") -> Dict[str, Any]:
    """بحث عميق + رسالة إعلامية لموضوع. يعيد dict (مع ``ok``) — من الذاكرة المؤقتة إن وُجد."""
    key = (user_id or 0, _norm(topic))
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < _TTL:
            return hit[1]
    from app.aeos.employee.research import clean_query

    q = clean_query(query or topic)
    research = _research(q)
    sources = [{"n": i + 1, "title": (r.get("title") or "")[:140], "url": r["url"],
                "site": (r.get("site") or urlsplit(r["url"]).hostname or "").removeprefix("www."),
                "date": r.get("published_date") or "", "content": (r.get("content") or "")[:900]}
               for i, r in enumerate(research["results"])]
    if not sources:
        raise RuntimeError(f"لم يجد البحث العميق مصادر حديثة عن «{q}»")
    brief: Dict[str, Any] = {}
    if llm is not None and getattr(llm, "available", False):
        src_text = "\n\n".join(f"[{s['n']}] {s['site']} — {s['title']} ({s['date']})\n{s['content']}"
                               for s in sources)
        brief = llm.complete_json(
            _SYSTEM,
            f"الموضوع الذي وجّه به المالك: «{topic[:300]}»\n"
            + (f"ملخّص محرك البحث: {research['answer'][:800]}\n" if research["answer"] else "")
            + f"\nالمصادر:\n{src_text}\n\n"
            "أعد JSON بالمفاتيح: headline (عنوان دقيق)، summary (3 جمل)، latest (قائمة {when, what, src})، "
            "facts (قائمة {fact, src})، numbers (قائمة {value, meaning, src})، statements (قائمة "
            "{who, quote, src})، background (جملتان)، angles (قائمة 3-5 نصوص)، do_not_claim (قائمة)، "
            "hashtags (0-2 وسوم عربية متداولة فعلاً إن ظهرت في المصادر).",
            temperature=0.2,
        ) or {}
    if not brief.get("headline"):
        # بلا LLM أو فشل: موجز حتمي من المصادر نفسها (أفضل من نص الموضوع وحده)
        brief = {"headline": sources[0]["title"], "summary": research["answer"][:500],
                 "facts": [{"fact": s["content"][:220], "src": s["n"]} for s in sources[:5]],
                 "angles": [], "latest": [], "numbers": [], "statements": [], "do_not_claim": []}
    brief.update(ok=True, topic=topic, query=q, created_at=time.time(),
                 sources=[{k: s[k] for k in ("n", "title", "url", "site", "date")} for s in sources])
    with _lock:
        _cache[key] = (time.time(), brief)
    return brief


def assign_angle(brief: Dict[str, Any], user_id: Optional[int], employee: str) -> int:
    """زاوية مختلفة لكل موظف يُوجَّه بالموضوع نفسه (بالترتيب، ثم دوران)."""
    key = (user_id or 0, _norm(brief.get("topic", "")))
    with _lock:
        names = _assigned.setdefault(key, [])
        if employee not in names:
            names.append(employee)
        return names.index(employee)


def _txt(value: Any) -> str:
    """حقل نصي من النموذج قد يأتي قائمة جمل — كان يظهر في الرسالة «['…', '…']»."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v).strip() for v in value if v)
    return str(value or "").strip()


def _src(value: Any) -> str:
    """رقم المصدر بصيغة واحدة [n] (النموذج يعيده 1 أو "[1]" أو "1,2" — كان يظهر [[1]])."""
    nums = re.findall(r"\d+", str(value or ""))
    return " " + "".join(f"[{n}]" for n in dict.fromkeys(nums)) if nums else ""


def _when(value: Any) -> str:
    """«Wed, 14 Oct 2026 06:00:00 GMT» ← «14/10 06:00». يعيد النص كما هو إن لم يكن تاريخاً."""
    text = str(value or "").strip()
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        return dt.strftime("%d/%m %H:%M") + (" GMT" if dt.utcoffset() is not None and not dt.utcoffset() else "")
    except (TypeError, ValueError, IndexError):
        return text[:30]


def _list(items, fmt) -> List[str]:
    out = []
    for it in items or []:
        try:
            line = fmt(it) if isinstance(it, dict) else str(it)
        except Exception:  # noqa: BLE001 - حقل ناقص من النموذج لا يُسقط الرسالة
            line = str(it)
        if line and line.strip():
            out.append(line.strip())
    return out


def for_employee(brief: Dict[str, Any], angle_index: int = 0, angle_text: str = "") -> str:
    """الرسالة الإعلامية بصيغة يكتب منها الموظف (تُحقن في بريف النشر).
    ``angle_text``: زاوية مفصّلة على تخصص الموظف (خطة الحملة) تتقدّم على زوايا الرسالة العامة."""
    angles = [_txt(a) for a in brief.get("angles") or [] if a]
    angle = angle_text.strip() or (angles[angle_index % len(angles)] if angles else "")
    blocks = [f"📰 الرسالة الإعلامية — {_txt(brief.get('headline'))}"]
    if brief.get("summary"):
        blocks.append(f"الخلاصة: {_txt(brief['summary'])}")
    sections = [
        ("آخر التطورات (الأحدث أولاً)", _list(brief.get("latest"), lambda x: f"{_when(x.get('when'))} — {_txt(x.get('what'))}{_src(x.get('src'))}")),
        ("الحقائق المؤكَّدة", _list(brief.get("facts"), lambda x: f"{_txt(x.get('fact'))}{_src(x.get('src'))}")),
        ("الأرقام", _list(brief.get("numbers"), lambda x: f"{_txt(x.get('value'))} — {_txt(x.get('meaning'))}{_src(x.get('src'))}")),
        ("التصريحات", _list(brief.get("statements"), lambda x: f"{_txt(x.get('who'))}: «{_txt(x.get('quote'))}»{_src(x.get('src'))}")),
    ]
    for title, lines in sections:
        if lines:
            blocks.append(title + ":\n" + "\n".join(f"- {line}" for line in lines[:7]))
    if brief.get("background"):
        blocks.append(f"الخلفية: {_txt(brief['background'])}")
    if brief.get("do_not_claim"):
        blocks.append("لا تدّعِ هذه (غير مؤكَّدة): " + "؛ ".join(str(x) for x in brief["do_not_claim"][:5]))
    srcs = "؛ ".join(f"[{s['n']}] {s['site']}" for s in brief.get("sources") or [])
    if srcs:
        blocks.append(f"المصادر: {srcs}")
    instructions = (
        "\n\nطريقة الكتابة من الرسالة:\n"
        "- اكتب منشورك أنت بأسلوب شخصيتك ولهجتك، مقتبساً الحقائق والأرقام والتصريحات **كما وردت** أعلاه.\n"
        "- لا تضف أي حقيقة أو رقم ليس في الرسالة، ولا تقدّم ما في «لا تدّعِ» كأنه مؤكَّد.\n"
        + (f"- زاويتك أنت (زملاؤك يكتبون من زوايا أخرى، فلا تكرّرهم): {angle}\n" if angle else
           "- اختر زاوية واحدة واضحة من تخصصك.\n")
        + "- اختر أقوى معلومة واحدة أو اثنتين فقط — المنشور ليس تلخيصاً لكل الرسالة.\n"
        "- يمكنك الإشارة للمصدر باسمه (مثلاً: بحسب العربية) عند رقم أو تصريح حساس."
    )
    if brief.get("hashtags"):
        instructions += f"\n- وسوم متداولة يمكن استخدام واحد منها إن ناسب: {' '.join(brief['hashtags'][:2])}"
    return "\n\n".join(blocks) + instructions


def research_items(brief: Dict[str, Any]) -> List[Dict[str, Any]]:
    """الرسالة بصيغة نتائج البحث (للمخرج البصري وسجل الدورة) — بلا بحث جديد."""
    items = [{"source": s.get("site", ""), "title": s.get("title", ""), "url": s.get("url", ""),
              "snippet": ""} for s in brief.get("sources") or []][:4]
    if items and brief.get("summary"):
        items[0]["snippet"] = _txt(brief["summary"])[:300]
    return items


def attach(payload: Dict[str, Any], cfg, llm=None) -> Optional[Dict[str, Any]]:
    """ألحق الرسالة الإعلامية بحمولة نشر موجّه لم تُجهَّز بعد (أمر الحساب المثبّت، مهمّة نشر).
    يعيد الموجز أو None (لا موضوع / سبق تجهيزه / البحث معطّل أو فشل — يستمر بالمسار المعتاد)."""
    topic = str(payload.get("directed_topic") or "").strip()
    if not topic or payload.get("media_brief") or payload.get("deep_research") is False:
        return None
    try:
        brief = build(topic, user_id=getattr(cfg, "user_id", None), llm=llm)
    except Exception as exc:  # noqa: BLE001
        logger.info("media brief skipped for %s: %s", getattr(cfg, "account_username", "?"), exc)
        return None
    angle = assign_angle(brief, getattr(cfg, "user_id", None), cfg.account_username)
    payload["media_brief"] = for_employee(brief, angle)
    payload["media_brief_items"] = research_items(brief)
    return brief


def owner_summary(brief: Dict[str, Any]) -> Dict[str, Any]:
    """ما يعرضه المنسّق للمالك عن الرسالة (مختصراً)."""
    return {"headline": _txt(brief.get("headline")), "summary": _txt(brief.get("summary")),
            "latest": _list(brief.get("latest"), lambda x: f"{_when(x.get('when'))} — {_txt(x.get('what'))}")[:3],
            "facts": len(brief.get("facts") or []), "statements": len(brief.get("statements") or []),
            "angles": [_txt(a) for a in brief.get("angles") or []],
            "sources": [s["site"] for s in brief.get("sources") or []][:8]}
