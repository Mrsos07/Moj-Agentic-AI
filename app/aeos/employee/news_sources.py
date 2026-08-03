"""
Free, key-less news sources for grounding real content.

Uses Google News RSS which requires no API key, supports full-text search by the
employee's interests, and is region/locale aware (defaults to Saudi Arabia /
Arabic). Every employee can therefore read *real* recent news and build genuine
content on it — even before any paid trend collector (SerpAPI) is configured.
"""
from __future__ import annotations

from typing import List
from urllib.parse import quote_plus

# Google News RSS endpoints (no API key needed)
_GN_TOP = "https://news.google.com/rss?hl={hl}&gl={gl}&ceid={gl}:{hl}"
_GN_SEARCH = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{hl}"

# Arabic search queries per interest, biased toward Saudi Arabia.
INTEREST_QUERIES = {
    "politics": ["سياسة السعودية", "أخبار سياسية عربية"],
    "economy": ["اقتصاد السعودية", "أسواق المال والاستثمار"],
    "sports": ["رياضة السعودية", "دوري روشن السعودي"],
    "entertainment": ["ترفيه السعودية", "موسم الرياض فعاليات"],
    "art": ["فن وثقافة السعودية", "معارض فنية السعودية"],
}


def saudi_top_feed(hl: str = "ar", gl: str = "SA") -> str:
    """Top-stories feed for a region (default Saudi Arabia / Arabic)."""
    return _GN_TOP.format(hl=hl, gl=gl)


def search_feed(query: str, hl: str = "ar", gl: str = "SA") -> str:
    """Google News search feed for a free-text query."""
    return _GN_SEARCH.format(q=quote_plus(query), hl=hl, gl=gl)


def feeds_for_interests(interests: List[str], hl: str = "ar", gl: str = "SA") -> List[str]:
    """Build a list of feed URLs for the given interests.

    Falls back to the Saudi top-stories feed when the employee has no interests.
    """
    urls: List[str] = []
    for interest in interests or []:
        for q in INTEREST_QUERIES.get(interest, [interest]):
            urls.append(search_feed(q, hl=hl, gl=gl))
    if not urls:
        urls.append(saudi_top_feed(hl=hl, gl=gl))
    return urls
