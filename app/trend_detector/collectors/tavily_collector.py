"""
Tavily Collector — المصدر الوحيد لكاشف الترندات.

حلّ محل أربعة جامعات (Reddit · Google Trends عبر SerpAPI · خادم X API خاص · TikTok) كانت
كلها متوقفة بلا مفاتيح؛ النظام يكتفي بمزوّد واحد (TAVILY_API_KEY) للبحث وللترندات.

الاستعلامات تُبنى من **تخصصات الموظفين الفعلية** (أكثر الاهتمامات تكراراً) + استعلام عام
لأخبار السعودية والخليج، بنطاق «آخر يوم» وموضوع «news». القصة التي تغطيها عدة مصادر تندمج
في المزيل للتكرار فيرتفع تقييمها (``reshares`` = عدد المصادر).

الرصيد: نداء «basic» = رصيد واحد. الافتراضي 4 استعلامات كل 240 دقيقة ≈ 24 رصيداً يومياً.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List
from urllib.parse import urlsplit

from app.trend_detector.collectors.base import BaseCollector

BASE_QUERY = "أهم الأخبار العاجلة اليوم في السعودية والخليج"

#: اهتمامات محفوظة بالإنجليزية في بعض الموظفين — كانت تنتج «آخر أخبار politics»
_AR_INTEREST = {
    "politics": "السياسة", "political": "السياسة", "history": "التاريخ", "religion": "الدين",
    "islam": "الإسلام", "sports": "الرياضة", "sport": "الرياضة", "football": "كرة القدم",
    "soccer": "كرة القدم", "technology": "التقنية", "tech": "التقنية", "ai": "الذكاء الاصطناعي",
    "economy": "الاقتصاد", "business": "الأعمال", "finance": "المال", "markets": "الأسواق",
    "health": "الصحة", "science": "العلوم", "culture": "الثقافة", "entertainment": "الترفيه",
    "education": "التعليم", "energy": "الطاقة", "oil": "النفط", "travel": "السفر",
    "weather": "الطقس", "military": "الشؤون العسكرية", "security": "الأمن",
}
_INDEX_TITLE = re.compile(r"^(آخر|أحدث|أهم)\s+(ال)?أخبار|أخبار\s+google|latest\s+news|breaking\s+news\s*$|"
                          r"^(home|homepage|الرئيسية)\b", re.I)
_SKIP_HOSTS = ("news.google.com", "x.com", "twitter.com", "t.co", "facebook.com", "instagram.com",
               "tiktok.com", "youtube.com", "reddit.com", "threads.net")


def _max_queries() -> int:
    try:
        return max(1, min(int(os.getenv("TREND_TAVILY_MAX_QUERIES", "4")), 10))
    except ValueError:
        return 4


def employee_interests(limit: int) -> List[str]:
    """أكثر اهتمامات الموظفين تكراراً (كل الموظفين، بلا تمييز مالك: الترند عام)."""
    try:
        from app.aeos.employee.repository import SqlEmployeeRepository

        counts: Counter = Counter()
        for cfg in SqlEmployeeRepository().list_all():
            for it in (getattr(cfg.persona, "interests", None) or [])[:6]:
                it = str(it).strip()
                it = _AR_INTEREST.get(it.lower(), it)
                if 2 <= len(it) <= 60:
                    counts[it] += 1
        return [k for k, _ in counts.most_common(limit)]
    except Exception:  # noqa: BLE001 - بلا موظفين/قاعدة: الاستعلام العام يكفي
        return []


def build_queries() -> List[Dict[str, str]]:
    n = _max_queries()
    queries = [{"query": BASE_QUERY, "keyword": "أخبار"}]
    for interest in employee_interests(n - 1):
        # اهتمام بلا ترجمة معروفة ويبقى لاتينياً ← استعلام إنجليزي بدل خليط لغتين
        q = f"latest {interest} news" if interest.isascii() else f"آخر أخبار {interest}"
        queries.append({"query": q, "keyword": interest})
    return queries[:n]


def is_story(url: str, title: str) -> bool:
    """قصة خبرية لا صفحة قسم/رئيسية: «آخر أخبار السعودية» و«أخبار Google» ليست ترنداً."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    if any(host == h or host.endswith("." + h) for h in _SKIP_HOSTS):
        return False
    if _INDEX_TITLE.search(title.strip()):
        return False
    segments = [s for s in parts.path.split("/") if s]
    # صفحة رئيسية أو قسم بمستوى واحد بلا رقم/معرّف (/news ، /saudi-today)
    return not (len(segments) == 0 or (len(segments) == 1 and not re.search(r"\d", segments[0])))


def _published(value: str):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class TavilyCollector(BaseCollector):
    platform = "tavily"

    def is_configured(self) -> bool:
        from app.aeos.mcp.servers.core_mcp import search_provider

        return bool(search_provider()[0])

    async def collect(self) -> List[Dict[str, Any]]:
        from app.aeos.mcp.servers.core_mcp import tavily_search

        if not self.is_configured():
            print("[TavilyCollector] Not configured — add TAVILY_API_KEY to .env")
            return []
        signals: List[Dict[str, Any]] = []
        seen = set()
        for q in build_queries():
            try:
                results = await asyncio.to_thread(
                    tavily_search, q["query"], max_results=8, topic="news", time_range="day")
            except Exception as exc:  # noqa: BLE001 - استعلام فاشل لا يوقف البقية
                status = getattr(getattr(exc, "response", None), "status_code", "")
                print(f"[TavilyCollector] query failed ({type(exc).__name__} {status})")
                continue
            for r in results:
                url = r.get("url") or ""
                title = (r.get("title") or "").strip()
                if not url or not title or url in seen or not is_story(url, title):
                    continue
                seen.add(url)
                domain = (urlsplit(url).hostname or "").removeprefix("www.")
                signals.append(self._make_signal(
                    source_id="tv_" + hashlib.sha1(url.encode()).hexdigest()[:20],
                    title=title,
                    content=(r.get("content") or "")[:1200],
                    url=url,
                    keywords=q["keyword"],
                    author=domain,
                    published_at=_published(r.get("published_date") or "") or datetime.now(timezone.utc),
                    views=int(float(r.get("score") or 0) * 100),   # صلة النتيجة (0–100)
                    reshares=1,                                     # مصدر واحد؛ تُجمع عند الدمج
                    raw_data={"query": q["query"], "score": r.get("score"), "domain": domain},
                ))
        print(f"[TavilyCollector] Collected {len(signals)} signals from {len(build_queries())} queries")
        return signals
