"""
Trend Detector Configuration
Scoring weights, thresholds, intervals, and API settings
"""
import os
from dotenv import load_dotenv

load_dotenv(override=True)


# =============================================================================
# API Keys (loaded from .env)
# =============================================================================
# المصدر الوحيد للترندات: Tavily (TAVILY_API_KEY، يُقرأ في system/mcps/search).
# أُزيلت جامعات Reddit / Google Trends (SerpAPI) / X API الخاص / TikTok وتحليل Google Vision.

# مزوّد الـ LLM (OpenRouter افتراضياً، OpenAI احتياطاً). لا تُقرأ المفاتيح هنا
# مباشرة: ``app.core.llm_provider.resolve_llm`` هو المصدر الوحيد لاختيار
# المزوّد وعنوانه واسم موديله، وإلا انقسم المنطق على ملفين وتباعدا.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# =============================================================================
# Collector Polling Intervals (in minutes)
# =============================================================================
def _interval(name: str, default: int) -> int:
    try:
        return max(30, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# كل دورة = TREND_TAVILY_MAX_QUERIES رصيداً من Tavily (افتراضياً 4 كل 240 دقيقة ≈ 24 يومياً)
COLLECTOR_INTERVALS = {
    "tavily": _interval("TREND_TAVILY_INTERVAL", 240),
}

# Watchlist re-check interval (minutes)
WATCHLIST_CHECK_INTERVAL = 15

# Max re-checks before EARLY signal expires
WATCHLIST_MAX_CHECKS = 6


# =============================================================================
# Scoring Weights
# Points awarded when a signal exceeds the platform-specific threshold
# =============================================================================
SCORING_WEIGHTS = {
    "views": 3,
    "reshares": 3,
    "likes": 4,
    "comments": 2,
    "unique_authors": 3,
    "cross_platform": 5,      # Signal found on 2+ platforms
    "trending_source": 6,     # Signal from a trending aggregator (e.g. Google Trends)
    "has_media": 1,           # Bonus for signals with media content
}


# =============================================================================
# Platform-Specific Thresholds
# A signal must exceed these to earn points for that metric
# =============================================================================
PLATFORM_THRESHOLDS = {
    # Tavily (أخبار آخر يوم): مصدر ترند بطبيعته (+6). views = صلة النتيجة ×100،
    # reshares = عدد المصادر التي تغطي القصة بعد الدمج. قصة من مصدرين+ أو بصلة ≥0.85 ← HOT (≥8)،
    # ومصدر واحد بصلة عادية ← EARLY (قائمة المراقبة).
    "tavily": {
        "views": 84,
        "reshares": 1,
        "likes": 10**9,
        "comments": 10**9,
        "is_trending_source": True,
    },
}


# =============================================================================
# Validation Thresholds (X Validation stage)
# Used to decide HOT / EARLY / NOT_YET
# =============================================================================
VALIDATION_THRESHOLDS = {
    "hot": {
        "min_score": 8,             # Pre-filter score — X validation is the real gate
        "min_unique_authors": 8,    # At least 8 distinct posters on X
        "min_post_density": 4,      # At least 4 posts in last 4 hours
    },
    "early": {
        "min_score": 6,
        "min_unique_authors": 2,
        "min_post_density": 1,
    },
    # Below "early" thresholds → NOT_YET
}


# =============================================================================
# Classifier Categories
# =============================================================================
CLASSIFIER_CATEGORIES = [
    "سياسي",       # Political
    "اجتماعي",     # Social
    "رياضة",       # Sports
    "ترفيه",       # Entertainment
    "اقتصاد",      # Economy
    "تقنية",       # Technology
    "تاريخي",      # Historical
    "ثقافي",       # Cultural
    "صحة",         # Health
    "أخرى",        # Other
]

SENSITIVITY_LEVELS = ["low", "medium", "high", "critical"]


# =============================================================================
# Deduplication
# =============================================================================
DEDUP_SIMILARITY_THRESHOLD = 0.75  # Cosine similarity threshold for merging
