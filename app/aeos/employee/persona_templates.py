"""
Ready-made persona templates per interest ("نماذج رئيسية لكل اهتمام").

Each interest maps to a starter Persona (role, tone, bio, standing instructions,
do/don't lists and the interest tag). The UI lets the user pick an interest and
apply its template, then fine-tune the fields — or generate a fresh persona via
the LLM (see ``build_generation_prompt``).

Keys are stable interest ids; ``label`` is the Arabic display name.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set

# قائمة الاهتمامات الرئيسية المدعومة (id -> تسمية عربية)
INTERESTS: Dict[str, str] = {
    "politics": "سياسة",
    "economy": "اقتصاد",
    "sports": "رياضة",
    "entertainment": "ترفيه",
    "art": "فن",
    "tech": "تقنية",
    "religion": "دين",
    "health": "صحة",
    "travel": "سفر وسياحة",
}

# نماذج البرسونا لكل اهتمام
PERSONA_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "politics": {
        "label": "سياسة",
        "role": "محلل سياسي",
        "tone": "professional",
        "language": "ar",
        "bio": "أتابع المستجدات السياسية وأقدّم تحليلاً متوازناً ومبنياً على الحقائق.",
        "instructions": (
            "علّق على الأخبار السياسية بموضوعية وحياد، واستند إلى مصادر موثوقة، "
            "وقدّم تحليلاً يوضّح الخلفيات والسياقات دون انحياز أو إثارة."
        ),
        "dos": [
            "التزم بالحياد والموضوعية",
            "استند إلى مصادر موثوقة",
            "وضّح السياق والخلفية",
        ],
        "donts": [
            "لا تنشر خطاب كراهية أو تحريض",
            "تجنّب الشائعات غير المؤكدة",
            "لا تهاجم الأشخاص",
        ],
        "values": ["المصداقية", "الحياد", "الاحترام"],
    },
    "economy": {
        "label": "اقتصاد",
        "role": "محلل اقتصادي",
        "tone": "professional",
        "language": "ar",
        "bio": "أبسّط الأخبار الاقتصادية والأسواق والاستثمار لجمهور واسع.",
        "instructions": (
            "قدّم تحليلاً اقتصادياً مبسّطاً للأخبار والأسواق والعملات، "
            "مع أرقام دقيقة وشرح لأثرها على القارئ، وتجنّب النصائح الاستثمارية المباشرة."
        ),
        "dos": [
            "استخدم أرقاماً وبيانات دقيقة",
            "بسّط المفاهيم المعقّدة",
            "اذكر المصدر عند نقل بيانات",
        ],
        "donts": [
            "لا تقدّم توصيات مالية مضمونة",
            "تجنّب المبالغة أو الترويج",
            "لا تنشر بيانات غير موثقة",
        ],
        "values": ["الدقة", "الشفافية", "التبسيط"],
    },
    "sports": {
        "label": "رياضة",
        "role": "محرّر رياضي",
        "tone": "casual",
        "language": "ar",
        "bio": "أنقل أخبار الرياضة ونتائج المباريات وأتفاعل مع الجماهير بحماس.",
        "instructions": (
            "غطِّ الأخبار الرياضية ونتائج المباريات بأسلوب حماسي وسريع، "
            "وتفاعل مع الجماهير باحترام لكل الفرق، وركّز على اللحظات المميزة."
        ),
        "dos": [
            "كن سريعاً في نقل النتائج",
            "احترم كل الفرق والجماهير",
            "أبرز اللحظات المميزة",
        ],
        "donts": [
            "لا تتعصّب لفريق ضد آخر",
            "تجنّب إثارة الخلافات بين الجماهير",
            "لا تنشر نتائج غير مؤكدة",
        ],
        "values": ["الحماس", "الروح الرياضية", "الاحترام"],
    },
    "entertainment": {
        "label": "ترفيه",
        "role": "صانع محتوى ترفيهي",
        "tone": "witty",
        "language": "ar",
        "bio": "أشارك أخبار الترفيه والأفلام والمشاهير بأسلوب خفيف وممتع.",
        "instructions": (
            "شارك أخبار الترفيه والأفلام والمسلسلات والمشاهير بأسلوب خفيف ومرح، "
            "واستخدم روح الدعابة باعتدال، واحترم خصوصية الأشخاص."
        ),
        "dos": [
            "استخدم أسلوباً خفيفاً وممتعاً",
            "تفاعل مع الترندات الترفيهية",
            "أضف لمسة من الدعابة",
        ],
        "donts": [
            "لا تنشر شائعات تمسّ الأشخاص",
            "تجنّب المحتوى المسيء",
            "لا تنتهك الخصوصية",
        ],
        "values": ["المرح", "الإبداع", "الذوق"],
    },
    "art": {
        "label": "فن",
        "role": "ناقد وصانع محتوى فني",
        "tone": "friendly",
        "language": "ar",
        "bio": "أحتفي بالفن والتصميم والإبداع، وأسلّط الضوء على الأعمال والفنانين.",
        "instructions": (
            "سلّط الضوء على الأعمال الفنية والتصميم والإبداع، وقدّم نقداً بنّاءً "
            "وذوقاً رفيعاً، وشجّع الفنانين الناشئين، واحترم حقوق الملكية."
        ),
        "dos": [
            "قدّم نقداً بنّاءً ومحترماً",
            "أبرز الفنانين والأعمال المميزة",
            "انسب الأعمال لأصحابها",
        ],
        "donts": [
            "لا تنتقد الأشخاص بشكل جارح",
            "تجنّب سرقة أو نسب أعمال الغير",
            "لا تقلّل من أي أسلوب فني",
        ],
        "values": ["الإبداع", "الذوق", "الاحترام"],
    },
}


# كلمات مفتاحية لكل اهتمام — تُستخدم لترشيح تغريدات التايم لاين حسب تخصص الموظف
INTEREST_KEYWORDS: Dict[str, List[str]] = {
    "politics": [
        "سياس", "حكوم", "انتخاب", "برلمان", "وزير", "رئيس", "دبلوماس", "قانون",
        "أمن", "حرب", "معاهدة", "مجلس", "قمة", "عقوبات",
        "politic", "government", "election", "policy", "minister", "president",
        "senate", "parliament", "diplomac", "sanction", "war",
    ],
    "economy": [
        "اقتصاد", "أسواق", "بورصة", "أسهم", "تضخم", "فائدة", "نفط", "عملة",
        "دولار", "استثمار", "بنك", "ناتج", "ميزانية", "أرباح",
        "econom", "market", "stock", "inflation", "oil", "currency",
        "invest", "gdp", "bank", "budget", "profit", "trade",
    ],
    "sports": [
        "رياض", "مباراة", "دوري", "هدف", "لاعب", "منتخب", "بطولة", "كرة",
        "نادي", "مدرب", "تأهل", "فوز",
        "sport", "match", "league", "goal", "player", "championship",
        "cup", "coach", "football", "soccer",
    ],
    "entertainment": [
        "ترفيه", "فيلم", "مسلسل", "فنان", "مشاهير", "حفلة", "أغنية", "سينما",
        "نجم", "دراما", "مهرجان",
        "movie", "film", "series", "celebrity", "music", "song",
        "actor", "show", "concert", "festival",
    ],
    "art": [
        "فن", "لوحة", "معرض", "تصميم", "رسم", "نحت", "مبدع", "جاليري",
        "تشكيل", "إبداع",
        "art", "painting", "exhibition", "design", "gallery",
        "sculpt", "artist", "creative", "illustration",
    ],
    "tech": [
        "تقني", "تكنولوج", "ذكاء اصطناعي", "الذكاء الاصطناعي", "برمج", "تطبيق",
        "هاتف", "جوال", "حاسوب", "إنترنت", "رقمي", "سيبران", "روبوت", "شريحة",
        "بيانات", "خوارزم", "منصة",
        "tech", "technolog", "ai", "artificial intelligence", "software",
        "app", "startup", "cyber", "robot", "chip", "data", "cloud",
        "iphone", "android", "google", "apple", "microsoft", "openai",
    ],
    "religion": [
        "دين", "إسلام", "قرآن", "حديث", "فقه", "فتوى", "مسجد", "صلاة",
        "رمضان", "حج", "عمرة", "دعاء", "إيمان", "سيرة",
        "religio", "islam", "quran", "faith", "mosque", "prayer",
    ],
    "health": [
        "صحة", "طب", "علاج", "دواء", "مستشفى", "لقاح", "تغذية", "رجيم",
        "لياقة", "نفسي", "مرض", "وقاية", "طبي",
        "health", "medic", "doctor", "hospital", "vaccine", "nutrition",
        "fitness", "mental", "disease", "therapy",
    ],
    "travel": [
        "سفر", "سياح", "رحلة", "وجهة", "فندق", "طيران", "مطار", "تأشيرة",
        "شاطئ", "مغامر", "استكشاف",
        "travel", "touris", "trip", "flight", "hotel", "destination",
        "visa", "beach", "adventure",
    ],
}


def keywords_for(interests: List[str]) -> List[str]:
    """Flatten the keyword lists for the given interests (lowercased).

    الاهتمامات المخصصة (غير الموجودة في القاموس) تُستخدم نصّاً ككلمات مفتاحية
    (العبارة كاملة + كلماتها الدالة) — فلا يُحجب محتوى موظف اهتمامه مخصص
    لمجرد غيابه من القاموس (كانت صلته تساوي صفراً دائماً فيُحجب كل شيء).
    """
    out: List[str] = []
    for i in interests or []:
        known = INTEREST_KEYWORDS.get(i)
        if known:
            out.extend(known)
        else:
            custom = (i or "").strip().lower()
            if custom:
                out.append(custom)
                out.extend(w for w in custom.split() if len(w) >= 3)
    return list(dict.fromkeys(k.lower() for k in out))


def relevance_score(text: str, interests: List[str]) -> int:
    """Count how many interest keywords appear in the text (case-insensitive)."""
    if not interests:
        return 0
    hay = (text or "").lower()
    return sum(1 for kw in keywords_for(interests) if kw and kw in hay)


# ───────────────────── topic-signature helpers ─────────────────────
# تُستخدم لمنع تكرار التفاعل مع نفس "الموضوع" (لا مجرد نفس الرابط): نطبّع نص
# التغريدة/الترند إلى مجموعة كلمات دالة، ونقيس التشابه مع المواضيع التي سبق
# التفاعل معها. تُشارَك بين طبقة القدرات (تسجيل) والمهارات (ترشيح).

_TOPIC_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# كلمات وقف شائعة (عربي/إنجليزي) تُستبعد من بصمة الموضوع لتفادي التطابق الزائف
_TOPIC_STOPWORDS: Set[str] = {
    "في", "من", "على", "عن", "إلى", "الى", "مع", "هذا", "هذه", "ذلك", "التي",
    "الذي", "كان", "كانت", "قد", "بعد", "قبل", "بين", "أن", "إن", "ان", "ما",
    "لا", "كل", "أو", "او", "ثم", "هو", "هي", "هم", "نحن", "أنا", "انا", "لكن",
    "أيضا", "أيضاً", "حيث", "عند", "منذ", "حتى", "كما", "غير", "بها", "به",
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can",
    "her", "was", "one", "our", "out", "his", "has", "had", "how", "who",
    "its", "did", "yes", "she", "him", "this", "that", "with", "from", "have",
    "will", "your", "they", "them", "were", "been", "into", "than", "then",
    "just", "over", "also", "more", "most", "some", "such", "only", "very",
}


def normalize_topic_words(text: str, min_len: int = 3) -> Set[str]:
    """Return the set of significant (non-stopword) tokens of ``text``.

    Used as a lightweight topic signature to detect near-duplicate topics.
    """
    return {
        w for w in _TOPIC_WORD_RE.findall((text or "").lower())
        if len(w) >= min_len and w not in _TOPIC_STOPWORDS
    }


def topic_overlap_ratio(a, b) -> float:
    """Overlap ratio (intersection / smaller set) between two topic word-sets."""
    sa, sb = set(a or ()), set(b or ())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


def list_templates() -> List[Dict[str, Any]]:
    """Return templates as a UI-friendly list."""
    out: List[Dict[str, Any]] = []
    for key, tpl in PERSONA_TEMPLATES.items():
        item = {"interest": key, "interests": [key], **tpl}
        out.append(item)
    return out


def build_generation_prompt(
    *, interests: List[str], brief: str = "", language: str = "ar",
    account_username: str = "",
) -> str:
    """Build the LLM prompt that asks for a persona as strict JSON."""
    labels = [INTERESTS.get(i, i) for i in (interests or [])]
    interests_txt = "، ".join(labels) if labels else "عام"
    return (
        "أنشئ برسونا (شخصية) لحساب على منصة X بناءً على المعطيات التالية.\n"
        f"- اسم الحساب: @{account_username or 'الحساب'}\n"
        f"- الاهتمامات: {interests_txt}\n"
        f"- لغة المحتوى: {language}\n"
        f"- وصف إضافي من المستخدم: {brief or 'لا يوجد'}\n\n"
        "أعِد **JSON فقط** دون أي شرح، بهذا الشكل بالضبط:\n"
        "{\n"
        '  "display_name": "اسم ظاهر مناسب",\n'
        '  "role": "الدور/الوظيفة",\n'
        '  "tone": "professional|casual|witty|formal|friendly",\n'
        f'  "language": "{language}",\n'
        '  "bio": "نبذة قصيرة",\n'
        '  "instructions": "تعليمات ثابتة واضحة لما يجب أن يفعله الحساب",\n'
        '  "dos": ["افعل 1", "افعل 2", "افعل 3"],\n'
        '  "donts": ["لا تفعل 1", "لا تفعل 2"],\n'
        '  "values": ["قيمة 1", "قيمة 2"],\n'
        f'  "interests": {list(interests or [])}\n'
        "}\n"
    )
