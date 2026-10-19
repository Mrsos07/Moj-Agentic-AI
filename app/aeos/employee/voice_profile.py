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

الإصدار 2 (عمق زمني): الإصدار الأول بُني من آخر 40 تغريدة و30 رداً، أي من أسبوع أو
اثنين، فصارت **مواضيع المرحلة** («الوعد صنعاء»، «غارات جوية») معجماً و«عبارات مميّزة»
تُحقن في كل رد، وطول الرد = وسيط مجاملات قصيرة (24 حرفاً)، فانهارت ردود الموظف إلى قالب
واحد («… بالميدان لا بالبيان باذن الله»). الآن:
- العيّنة **موزّعة زمنياً** (حدّ لكل يوم: يوم واحد فيه 136 مجاملة لا يبتلع العيّنة)،
  ومن فترات أقدم بالبحث، و≥100 رد.
- **العبارة المميّزة** = ما يتكرر عبر أسابيع مختلفة؛ وما تركّز في فترة قصيرة «موضوع مرحلة».
- **أنواع الردود** (مجاملة/موافقة/اعتراض/سؤال/دعابة/تعليق بمضمون) بنسبها وأطوالها، و
  **سياسة الرد** حسب نوع ما يُردّ عليه، و**بنك أمثلة** يُختار منه ما يناسب كل رد.
"""
from __future__ import annotations

import logging
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("aeos.voice_profile")

STORE_KEY = "voice_profile"
VERSION = 2
#: أقل عدد ردود يُبنى منه التحليل حين يتوفر (طلب المالك: لا يقل عن 100)
MIN_ANALYZED_REPLIES = 100

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


# ─────────────────────────── 0) depth: time & kinds ───────────────────────────
def item_day(item: Dict[str, Any]) -> str:
    return str(item.get("created_at") or "")[:10]


def _week(day: str) -> str:
    try:
        y, w, _ = datetime.fromisoformat(day).isocalendar()
        return f"{y}-{w:02d}"
    except (TypeError, ValueError):
        return ""


def stratify(items: Sequence[Dict[str, Any]], n: int, per_day: int = 6,
             fill: bool = False) -> List[Dict[str, Any]]:
    """حتى ``n`` عنصراً موزّعة على الأيام بالتناوب، وبحد ``per_day`` لكل يوم.

    قيس على @halgawi: 136 رداً من 881 في يوم واحد (يوم تهانٍ) — «آخر N» كانت ستجعل
    الشخصية كلها مجاملات ذلك اليوم. العناصر بلا تاريخ تُعامل كيوم واحد.
    ``fill``: الحد مرن — إن لم يكتمل ``n`` نكمل جولات التناوب (يبقى التوزيع أعدل ما يمكن)
    بدل أن تنقص العيّنة عن الحد الأدنى المطلوب (≥100 رد).
    """
    by_day: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        by_day[item_day(it)].append(it)
    days = sorted(by_day, reverse=True)
    out: List[Dict[str, Any]] = []
    rounds = max((len(v) for v in by_day.values()), default=0) if fill else max(1, per_day)
    for r in range(rounds):
        for d in days:
            if r < len(by_day[d]):
                out.append(by_day[d][r])
                if len(out) >= n:
                    return out
    return out


def coverage(items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    days = sorted({item_day(i) for i in items if item_day(i)})
    return {"from": days[0] if days else "", "to": days[-1] if days else "", "days": len(days),
            "weeks": len({_week(d) for d in days if _week(d)})}


#: أنواع الردود — ما يميّز «كيف» يرد الحساب لا «عمّ»
REPLY_KINDS = {
    "courtesy": "مجاملة/دعاء/شكر", "agree": "موافقة", "disagree": "اعتراض/تصحيح",
    "question": "سؤال/استيضاح", "humor": "دعابة/تهكّم", "reaction": "تعليق قصير جداً",
    "substantive": "تعليق بمضمون (معلومة/رأي)",
}
#: أنواع ما يُردّ عليه. «مناسبة اجتماعية» منفصلة عن «خبر/رأي»: بدونها كانت التعازي
#: والتهاني تُحسب ردوداً على «محتوى» فتصير سياسة الرد على الأخبار 55% دعاءً ومجاملة.
PARENT_KINDS = {"praise": "ثناء أو تحية موجّهة له", "question": "سؤال أو كلام موجّه له",
                "attack": "هجوم أو استفزاز", "social": "مناسبة اجتماعية (عزاء/تهنئة/تحية)",
                "content": "خبر أو رأي عام"}
_SOCIAL_RE = re.compile(
    r"ذمة\s*الله|إنا\s*لله|انا\s*لله|توفي|توفى|وفاة|انتقل\s*إلى\s*رحمة|انتقل\s*الى\s*رحمة|عزاء|"
    r"زواج|زفاف|عقد\s*قران|مولود|رزقت|رزقني|تخرج|تخرّج|ترقية|مبارك|مبروك|صباح\s*الخير|"
    r"مساء\s*الخير|جمعة\s*مباركة|عيد|كل\s*عام|يسعدني|سعدت|أسعدني|اسعدني|تشرفت|تشرّفت|"
    r"شكراً\s*ل|شكرا\s*ل|فخور|افتخر|أفتخر|نهنئ|اهنئ|أهنئ", re.I)

_COURTESY_RE = re.compile(
    r"الله\s*يحفظ|يحفظك|تسلم|يسلمك|يسلمو|العافي|بيض\s*الله|يرفع\s*قدر|شكر|مشكور|ممنون|ممتن|"
    r"الله\s*يحيي|حيّ?اك|يسعد|مبرو?ك|مبارك|آمين|امين|يا\s*رب|يارب|أحسن\s*الله|احسن\s*الله|"
    r"عظم\s*الله|رحم(?:ه|ها)?\s*الله|جزاك|عساك|عساكم|ما\s*قصرت|ماقصرت|تستاهل|بارك\s*الله|"
    r"الله\s*يبارك|صباح\s*(?:الخير|النور)|مساء\s*(?:الخير|النور)|كل\s*عام|\b(?:يا)?هلا\b|"
    r"أهلاً|أهلا|اهلا|\bthanks?\b", re.I)
_LAUGH_RE = re.compile(r"ه{3,}|😂|🤣|😅|خخخ")
_AGREE_RE = re.compile(r"^(?:نعم|ايوه|إيه|صحيح|صح\b|بالضبط|أكيد|اكيد|فعلاً|فعلا|صدقت|كلامك|"
                       r"اتفق|أتفق|معك\s*حق|تمام|بلا\s*شك)", re.I)
_DISAGREE_RE = re.compile(r"^(?:لا\b|لا،|لأ|غير\s*صحيح|مو\s*صحيح|مب\s*صحيح|ليس\s*صحيح|بالعكس|"
                          r"كذب|خطأ|غلط|أبداً|ابدا|ابداً|مستحيل|هذا\s*غير)", re.I)
_ATTACK_RE = re.compile(r"كذاب|كذبت|مرتزق|عميل|جاهل|تافه|خاين|خائن|منافق|اسكت|انقلع|فاشل|"
                        r"حقير|ذباب|مطبل|طبال|مأجور|جبان", re.I)
_YOU_RE = re.compile(r"(?:^|\s)(?:انت|أنت|إنت|عندك|رأيك|رايك|حسابك|كلامك|تغريدتك|منك|عليك|"
                     r"يعطيك|يحفظك|حبيبي|ياخي|يا\s*أخي|استاذ|أستاذ)(?=\s|$|[؟?!.,،])")


def reply_kind(text: str) -> str:
    t = _clean(text)
    n = len(_words(t))
    if not n:
        return "reaction"
    question = "؟" in t or "?" in t
    if _LAUGH_RE.search(t) and n <= 8:
        return "humor"
    # التعزية والتهنئة الطويلة مجاملة رغم طولها: كثافة الصيغ (≥2) لا عدد الكلمات
    courtesy = len(_COURTESY_RE.findall(t))
    if courtesy and not question and (n <= 14 or courtesy >= 2):
        return "courtesy"
    if question:
        return "question"
    if _DISAGREE_RE.search(t):
        return "disagree"
    if _AGREE_RE.search(t):
        return "agree"
    return "reaction" if n <= 3 else "substantive"


def parent_kind(text: str, names: Sequence[str] = ()) -> str:
    t = _clean(text)
    if not t:
        return "content"
    addressed = any(nm and nm in t for nm in names) or bool(_YOU_RE.search(t))
    if _ATTACK_RE.search(t):
        return "attack"
    if _COURTESY_RE.search(t) and (addressed or len(_words(t)) <= 12):
        return "praise"
    # كلام موجّه له بالاسم أو بالخطاب («ياحسين هات سنابك») حوارٌ لا «خبر عام»
    if addressed or "؟" in t and len(_words(t)) <= 12:
        return "question"
    if _SOCIAL_RE.search(t):
        return "social"
    return "content"


def _shares(counter: Counter) -> Dict[str, float]:
    total = sum(counter.values()) or 1
    return {k: round(v / total, 3) for k, v in counter.most_common()}


def reply_kind_stats(replies: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """لكل نوع رد: حصته ووسيط طوله والربع الأعلى — الطول يتبع النوع لا وسيطاً واحداً."""
    by: Dict[str, List[int]] = defaultdict(list)
    for r in replies:
        t = _clean(r.get("text", ""))
        if t:
            by[reply_kind(t)].append(len(t))
    total = sum(len(v) for v in by.values()) or 1
    return {k: {"share": round(len(v) / total, 3), "n": len(v),
                "len_median": int(statistics.median(v)), "len_p75": _pct(v, 0.75)}
            for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))}


def reply_policy(pairs: Sequence[Dict[str, Any]], names: Sequence[str] = ()) -> Dict[str, Dict[str, Any]]:
    """ماذا يفعل الحساب فعلاً حسب نوع ما يُردّ عليه: {نوع الأصل: {n, نوع الرد: حصة}}."""
    by: Dict[str, Counter] = defaultdict(Counter)
    for r in pairs:
        ctx = r.get("in_reply_to") or {}
        if ctx.get("self") or not ctx.get("text"):
            continue
        by[parent_kind(ctx["text"], names)][reply_kind(r.get("text", ""))] += 1
    return {pk: {"n": sum(c.values()), "kinds": _shares(c)} for pk, c in by.items()}


_AR_NORM = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ـ": None})
#: التشكيل علامات مركّبة لا تُعدّ حروفاً في ``\w``: «تُسجَّل» كانت تتقطّع إلى «ت/سج/ل»
_TASHKEEL_RE = re.compile(r"[\u064B-\u065F\u0670]")


def norm_ar(text: str) -> str:
    """توحيد إملائي خفيف: «أحسن/احسن» و«بإذن/باذن» و«تُسجَّل/تسجل» واحدة لا اثنتان."""
    return _TASHKEEL_RE.sub("", (text or "")).translate(_AR_NORM)


def _gram_index(items: Sequence[Dict[str, Any]]) -> Tuple[Dict[str, List[Tuple[int, str]]], set]:
    """عبارة (2-3 كلمات) → [(رقم النص، أسبوعه)] مرة لكل نص، ومجموعة الأسابيع كلها."""
    grams: Dict[str, List[Tuple[int, str]]] = defaultdict(list)
    weeks_all = set()
    stop = {norm_ar(s) for s in _STOP}
    for idx, it in enumerate(items):
        wk = _week(item_day(it))
        if wk:
            weeks_all.add(wk)
        seen = set()
        ws = [w for w in _words(norm_ar(_clean(it.get("text", "")))) if not w.isdigit()]
        for n in (2, 3):
            for i in range(len(ws) - n + 1):
                g = ws[i:i + n]
                if g[0] in stop or g[-1] in stop:
                    continue
                key = " ".join(g)
                if key not in seen:
                    seen.add(key)
                    grams[key].append((idx, wk))
    return grams, weeks_all


def phrase_spread(items: Sequence[Dict[str, Any]], min_count: int = 3,
                  topical: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, List[str]]:
    """العبارة المميّزة تتكرر عبر أسابيع مختلفة؛ ما تركّز في فترة قصيرة «موضوع مرحلة».

    ``topical``: نصوص المضمون (التغريدات والتعليقات) التي تُستخرج منها مواضيع المرحلة —
    صيغ المجاملة («تسلم الله يحفظك») تتكدّس في أيام التهاني فتبدو «مرحلية» وهي أسلوب.
    بلا انتشار زمني كافٍ (أقل من 3 أسابيع) لا يمكن التمييز: نعيد الترتيب القديم بالتكرار.
    """
    grams, weeks_all = _gram_index(items)
    total = len(weeks_all)
    if total < 3:
        ranked = sorted(((k, len(v)) for k, v in grams.items() if len(v) >= 2), key=lambda kv: -kv[1])
        return {"signature": [k for k, _ in ranked[:12]], "era": []}
    need = max(3, math.ceil(0.2 * total))
    sig = []
    for k, occ in grams.items():
        spread = Counter(w for _, w in occ if w)
        if len(occ) >= min_count and len(spread) >= need and spread.most_common(1)[0][1] / len(occ) <= 0.5:
            sig.append((k, len(spread), len(occ), {i for i, _ in occ}))
    sig.sort(key=lambda x: (-x[1], -x[2]))
    # صيغة واحدة طويلة (التعزية) تولّد ست عبارات متداخلة: نُسقط ما يتداخل نصاً أو يظهر
    # في النصوص نفسها غالباً (Jaccard ≥ 0.6) ونُبقي الأوسع انتشاراً
    chosen: List[Tuple[str, set]] = []
    for k, _, _, ids in sig:
        if not any(k in c or c in k or len(ids & cid) / min(len(ids), len(cid)) >= 0.6
                   for c, cid in chosen):
            chosen.append((k, ids))
    era = []
    t_grams, _ = _gram_index(topical if topical is not None else items)
    for k, occ in t_grams.items():
        spread = Counter(w for _, w in occ if w)
        if (len(occ) >= min_count and not _COURTESY_RE.search(k)
                and (len(spread) <= 2 or spread.most_common(1)[0][1] / len(occ) > 0.6)):
            era.append((k, len(occ)))
    era.sort(key=lambda x: -x[1])
    return {"signature": [k for k, _ in chosen[:12]], "era": [k for k, _ in era[:20]]}


def phrase_kind_rates(phrases: Sequence[str], replies: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """كم يستعمل الحساب كل عبارة مميّزة داخل كل نوع رد — «الله يحفظك» طبيعية في المجاملة
    ونادرة في التعليق بمضمون؛ حقنها في كل رد هو ما صنع القالب."""
    by: Dict[str, List[str]] = defaultdict(list)
    for r in replies:
        t = _clean(r.get("text", ""))
        if t:
            by[reply_kind(t)].append(" ".join(_words(norm_ar(t))))
    out: Dict[str, Dict[str, float]] = {}
    for p in phrases:
        rates = {k: round(sum(1 for t in texts if p in t) / len(texts), 2) for k, texts in by.items() if texts}
        if any(rates.values()):
            out[p] = {k: v for k, v in rates.items() if v}
    return out


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
                     posts_n: int = 16, replies_n: int = 48,
                     names: Sequence[str] = ()) -> Dict[str, Any]:
    """بنك أمثلة متنوّع زمنياً ونوعياً — يُختار منه عند كل رد ما يناسبه (لا الأمثلة نفسها دائماً).

    المنشورات: الأعلى تفاعلاً داخل كل شهر بالتناوب بين الشهور، بلا تكرار موضوعي.
    الردود: حصة لكل نوع رد بقدر حصته الحقيقية (2 على الأقل لكل نوع موجود)، أيام مختلفة.
    """
    by_month: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for t in tweets:
        by_month[item_day(t)[:7]].append(t)
    for m in by_month:
        by_month[m].sort(key=_engagement, reverse=True)
    posts: List[str] = []
    for r in range(max((len(v) for v in by_month.values()), default=0)):
        # الجولة r: الأفضل r في كل شهر، مرتّبة بالتفاعل (الأقوى أولاً)
        for t in sorted((v[r] for v in by_month.values() if r < len(v)), key=_engagement, reverse=True):
            text = _clean(t.get("text", ""))
            if len(posts) < posts_n and len(text) >= 15 and not any(_overlap(text, p) >= 0.5 for p in posts):
                posts.append(text[:400])
    others = [r for r in replies if r.get("in_reply_to") and not r["in_reply_to"].get("self")
              and _clean(r.get("text", ""))]
    stats = reply_kind_stats(others)
    quota = {k: max(2, round(v["share"] * replies_n)) for k, v in stats.items()}
    pairs: List[Dict[str, str]] = []
    used_days: Counter = Counter()
    for r in sorted(stratify(others, len(others), per_day=3), key=lambda x: used_days[item_day(x)]):
        text = _clean(r.get("text", ""))
        kind = reply_kind(text)
        if quota.get(kind, 0) <= 0 or len(pairs) >= replies_n:
            continue
        if any(p["kind"] == kind and _overlap(text, p["reply"]) >= 0.6 for p in pairs):
            continue
        quota[kind] -= 1
        used_days[item_day(r)] += 1
        pairs.append({"to": r["in_reply_to"]["text"][:280], "reply": text[:280], "kind": kind,
                      "parent_kind": parent_kind(r["in_reply_to"]["text"], names), "date": item_day(r)})
    return {"posts": posts, "replies": pairs}


# ─────────────────────────── 4) LLM deep analysis ───────────────────────────
_ANALYSIS_SYSTEM = (
    "أنت محلّل أساليب كتابة على X. تستخرج «بصمة صوت» دقيقة من عيّنات حقيقية موزّعة على "
    "أشهر. حلّل الأسلوب والشخصية وطريقة التفاعل، لا الوقائع. افصل بصرامة بين **ثوابت "
    "الهوية** (ما يتكرر عبر فترات مختلفة) و**مواضيع المرحلة** (أسماء أحداث وأماكن وجهات "
    "تركّزت في فترة قصيرة): الأخيرة ليست معجماً ولا عبارات مميّزة ولا قواعد كتابة. صِف "
    "بحياد مهني بلا أحكام على مواقف الحساب، وبالعربية فقط. أعد JSON فقط."
)

_ANALYSIS_SCHEMA = """{
 "voice_summary": "فقرة من 3-4 جمل تصف صوته الثابت عبر الفترات كما لو تشرحه لكاتب سيحاكيه",
 "identity_core": ["3-6 ثوابت في الهوية تظهر في فترات مختلفة: زوايا نظر ومواقف وطريقة تفكير، لا أحداث"],
 "era_topics": ["مواضيع وأحداث تركّزت في الفترة الأخيرة فقط (للعلم؛ لا تُقلَّد كأسلوب)"],
 "reply_kinds": {
   "courtesy": "متى يجامل/يدعو/يشكر ولمن، وكيف يصوغها، وكم يختلف لفظها",
   "agree": "كيف يوافق: هل يضيف معلومة أم يكتفي",
   "disagree": "كيف يعترض أو يصحّح",
   "question": "متى يسأل وكيف",
   "humor": "هل يمزح أو يتهكم وكيف",
   "substantive": "كيف يعلّق بمضمون على خبر أو رأي: الطول، البناء، المصادر، النبرة"
 },
 "variety": "كيف تتنوّع ردوده فعلاً (المطالع والأطوال والخواتيم)، وما الذي لا يلتزم به كقالب",
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
 "lexicon": {"signature": ["تعابير أسلوبية تتكرر عبر فترات مختلفة (لا أسماء أحداث أو أماكن)"], "avoid": ["ما لا يستخدمه أبداً"]},
 "values": ["3-5 قيم ثابتة يظهرها"],
 "red_lines": ["4-6 أشياء لا يفعلها أبداً (لا تكسر الشخصية)"],
 "persona": {
   "role": "وصف دوره بإيجاز",
   "tone": "professional|casual|witty|formal|friendly",
   "dialect": "اللهجة كما يكتب بها",
   "dos": ["6-10 قواعد كتابة دقيقة وقابلة للتطبيق، بلا عبارات ختام ثابتة وبلا مواضيع مرحلة"],
   "donts": ["4-6 محظورات أسلوبية"]
 }
}"""

#: سقوف ما يدخل موجّه التحليل (يكفي للتمثيل ويبقى ضمن ميزانية الرموز)
_PROMPT_TWEETS, _PROMPT_PAIRS, _PROMPT_LOOSE = 60, 110, 30


def _analysis_prompt(profile: Dict[str, Any], stats: Dict[str, Any],
                     topics: Dict[str, float]) -> str:
    import json

    tweets = stratify(profile.get("tweets") or [], _PROMPT_TWEETS, per_day=3)
    pairs_all = [r for r in profile.get("replies") or [] if r.get("in_reply_to")]
    pairs = stratify(pairs_all, _PROMPT_PAIRS, per_day=4)
    loose = stratify([r for r in profile.get("replies") or [] if not r.get("in_reply_to")],
                     _PROMPT_LOOSE, per_day=3)
    t_lines = "\n".join(
        f"- [{item_day(t)}] {_clean(t.get('text', ''))[:240]}  [❤{t.get('likes', 0)}]" for t in tweets)
    by_kind: Dict[str, List[str]] = defaultdict(list)
    for r in pairs:
        text = _clean(r.get("text", ""))
        kind = "thread" if r["in_reply_to"].get("self") else reply_kind(text)
        by_kind[kind].append(f"• [{item_day(r)}] على {r['in_reply_to'].get('handle', '')}: "
                             f"«{r['in_reply_to']['text'][:150]}»\n  ← ردّه: «{text[:200]}»")
    p_blocks = "\n".join(
        f"\n— {REPLY_KINDS.get(k, 'تكملة ثريد له')} ({len(v)}):\n" + "\n".join(v)
        for k, v in sorted(by_kind.items(), key=lambda kv: -len(kv[1])))
    l_lines = "\n".join(f"- [{item_day(r)}] {_clean(r.get('text', ''))[:200]}" for r in loose)
    cov = stats.get("coverage") or {}
    return (
        f"الحساب: {profile.get('handle', '')} — البايو: {profile.get('bio') or '(فارغ)'}\n"
        f"العيّنة تغطي {cov.get('days', '?')} يوماً من {cov.get('from', '?')} إلى {cov.get('to', '?')} "
        f"({len(profile.get('tweets') or [])} تغريدة و{len(profile.get('replies') or [])} رداً؛ "
        "المعروض أدناه عيّنة موزّعة زمنياً).\n"
        f"قياسات حتمية: {json.dumps({k: v for k, v in stats.items() if k != 'coverage'}, ensure_ascii=False)}\n"
        f"توزيع المواضيع المقاس: {json.dumps(topics, ensure_ascii=False)}\n\n"
        f"تغريداته الأصلية مع تاريخها ({len(tweets)}):\n{t_lines or '(لا يوجد)'}\n\n"
        f"ردوده **مع التغريدة التي ردّ عليها**، مجمّعة حسب النوع ({len(pairs)}):{p_blocks or ' (لا يوجد)'}\n"
        + (f"\nردود بلا سياق ظاهر:\n{l_lines}\n" if l_lines else "")
        + "\nاستخرج البصمة بهذه البنية حرفياً (قيم عربية، والقياسات أعلاه حقائق لا تناقضها؛ "
        "«عبارات مرحلة» في القياسات ليست معجماً):\n" + _ANALYSIS_SCHEMA
    )


def _as_list(v: Any, n: int, length: int = 160) -> List[str]:
    return [str(x).strip()[:length] for x in (v if isinstance(v, list) else []) if str(x).strip()][:n]


#: قاعدة تعمّم صيغة على كل الردود («اجعل المجاملة معياراً ثابتاً في الردود»، «اختم دائماً
#: بالدعاء») — تحوّل عادةً مشروطة بنوع الرد إلى قالب في كل رد. نوع الرد وصيغه تحددها
#: reply_plan من النسب الحقيقية، فلا مكان لهذه القواعد في البرسونا.
_BLANKET_FORMULA_RE = re.compile(
    r"(?:ثابت|دائم|دوماً|دوما|كل\s*(?:رد|الردود|تغريد|منشور)|جميع\s*الردود|في\s*الردود)"
    r".{0,40}(?:مجامل|دعاء|تعزي|اختم|خاتم|ختام|باذن|بإذن|يحفظك)|"
    r"(?:مجامل|دعاء|تعزي|اختم|خاتم|ختام|باذن|بإذن|يحفظك).{0,40}(?:ثابت|دائم|دوماً|دوما|كل\s*(?:رد|تغريد|منشور)|جميع\s*الردود)")


def normalize_analysis(raw: Dict[str, Any], era: Sequence[str] = ()) -> Dict[str, Any]:
    """تنظيف ما يعيده النموذج — لا نثق بالمخطط: كل قائمة مسقوفة وكل نص مقصوص.

    ``era``: عبارات مرحلة مقيسة زمنياً — تُسقط من المعجم حتى لو وضعها النموذج فيه.
    """
    raw = raw if isinstance(raw, dict) else {}
    pb = raw.get("reply_playbook") if isinstance(raw.get("reply_playbook"), dict) else {}
    rk = raw.get("reply_kinds") if isinstance(raw.get("reply_kinds"), dict) else {}
    inter = raw.get("interaction") if isinstance(raw.get("interaction"), dict) else {}
    lex = raw.get("lexicon") if isinstance(raw.get("lexicon"), dict) else {}
    era_words = {w for e in era for w in _words(norm_ar(e)) if len(w) >= 3 and w not in _STOP}

    def _not_era(entry: str) -> bool:
        ws = [w for w in _words(norm_ar(entry)) if len(w) >= 3 and w not in _STOP]
        return not ws or sum(1 for w in ws if w in era_words) < max(1, len(ws) / 2)
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
        "identity_core": _as_list(raw.get("identity_core"), 6, 200),
        "era_topics": _as_list(raw.get("era_topics"), 8, 80),
        "reply_kinds": {k: str(rk.get(k) or "")[:240] for k in REPLY_KINDS if str(rk.get(k) or "").strip()},
        "variety": str(raw.get("variety") or "")[:300],
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
        "lexicon": {"signature": [s for s in _as_list(lex.get("signature"), 12, 40) if _not_era(s)],
                    "avoid": _as_list(lex.get("avoid"), 8, 40)},
        "values": _as_list(raw.get("values"), 5, 80),
        "red_lines": _as_list(raw.get("red_lines"), 6, 140),
        "persona": {
            "role": str(persona.get("role") or "")[:120],
            "tone": str(persona.get("tone") or "")[:20],
            "dialect": str(persona.get("dialect") or "")[:40],
            "dos": [d for d in _as_list(persona.get("dos"), 10, 200) if not _BLANKET_FORMULA_RE.search(d)],
            "donts": _as_list(persona.get("donts"), 6, 200),
        },
    }


def analyze(profile: Dict[str, Any], llm: Any = None) -> Dict[str, Any]:
    """ابنِ البصمة كاملة من بروفايل مسحوب. بلا نموذج: قياسات + أمثلة فقط."""
    tweets_all = profile.get("tweets") or []
    replies_all = profile.get("replies") or []
    # كل القياسات من عيّنة موزّعة زمنياً: دفعة يوم واحد لا تحدّد الشخصية
    tweets = stratify(tweets_all, 120, per_day=4)
    replies = stratify(replies_all, 200, per_day=8)
    if len(replies) < MIN_ANALYZED_REPLIES:
        # الحد الأدنى للعمق أهم من عدالة التوزيع المطلقة: نكمل بالتناوب نفسه
        replies = stratify(replies_all, MIN_ANALYZED_REPLIES, fill=True)
    names = _names(profile)
    tw_text = [t.get("text", "") for t in tweets]
    rp_text = [r.get("text", "") for r in replies]
    stats = stylometry(tw_text, rp_text, [t.get("created_at", "") for t in tweets + replies])
    spread = phrase_spread(tweets + replies, topical=tweets + [
        r for r in replies if reply_kind(r.get("text", "")) not in ("courtesy", "reaction")])
    era_words = {w for e in spread["era"] for w in _words(e)}
    stats.update({
        "signature_phrases": spread["signature"],
        "era_phrases": spread["era"],
        # مطلع من مواضيع المرحلة («غارات»، «الحوثي») ليس عادة أسلوبية
        "openers": [o for o in stats.get("openers") or [] if norm_ar(o) not in era_words],
        "reply_kinds": reply_kind_stats(replies),
        "reply_policy": reply_policy(replies, names),
        "phrase_rates": phrase_kind_rates(spread["signature"], replies),
        "coverage": coverage(tweets_all + replies_all),
    })
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
            from ..core.llm import _safe_json

            # موجّه أكبر (عيّنة موزّعة + أنواع الردود) ومخرَج أغنى: سقف رموز أعلى من الافتراضي
            raw = llm.complete(_ANALYSIS_SYSTEM + "\nRespond ONLY with valid minified JSON.",
                               _analysis_prompt(profile, stats, topics), temperature=0.3,
                               max_tokens=3500)
            analysis = normalize_analysis(_safe_json(raw) if raw else {}, era=spread["era"])
        except Exception:  # noqa: BLE001
            logger.exception("voice analysis LLM call failed")
    pairs = [r for r in replies_all if r.get("in_reply_to")]
    return {
        "version": VERSION,
        "source_handle": profile.get("handle", ""),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sample": {"tweets": len(tweets_all), "replies": len(replies_all), "pairs": len(pairs),
                   "threads": sum(1 for r in pairs if r["in_reply_to"].get("self")),
                   "analyzed_tweets": len(tweets), "analyzed_replies": len(replies),
                   "coverage": stats["coverage"]},
        "style": stats,
        "topics": topics,
        "analysis": analysis,
        "exemplars": select_exemplars(tweets, replies, names=names),
    }


def _names(profile: Dict[str, Any]) -> List[str]:
    """ما يُنادى به الحساب (لمعرفة أن التغريدة موجّهة إليه): أول كلمة من اسمه + يوزره."""
    out = _words(profile.get("display_name") or "")[:1] + [(profile.get("handle") or "").lstrip("@")]
    return [n for n in out if len(n) >= 3]


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
    if style.get("signature_phrases") and kind != "reply":
        # بلا نسب الاستعمال كانت «عبارات تتكرر عندك» أمراً ضمنياً بحشرها في كل نص
        out.append("عبارات تظهر عندك في سياقاتها فقط (لا تحشرها في كل نص): "
                   + "، ".join(f"«{p}»" for p in style["signature_phrases"][:5]) + ".")
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
    if a.get("identity_core"):
        lines.append("ثوابت هويتك: " + "؛ ".join(a["identity_core"]) + ".")
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
    era = a.get("era_topics") or (profile.get("style") or {}).get("era_phrases") or []
    if era:
        lines.append("مواضيع مرحلة مرّت في الحساب الأصلي — ليست عبارات جاهزة لك؛ لا تذكرها "
                     "إلا إذا كان الموضوع نفسه عنها: " + "، ".join(era[:6]) + ".")
    ex = profile.get("exemplars") or {}
    if kind == "post" and ex.get("posts"):
        lines.append("أمثلة حقيقية من أسلوبك — للإيقاع والنبرة فقط؛ **ممنوع نسخ جملها أو "
                     "إعادة وقائعها**، اكتب جديداً تماماً بنفس الروح:")
        lines += [f"  • {p[:280]}" for p in ex["posts"][:5]]
    if kind == "reply" and a.get("reply_kinds"):
        # الإصدار 2: نوع الرد وطوله وأمثلته تُختار لكل رد في reply_plan؛ هنا الوصف العام فقط
        if a.get("variety"):
            lines.append(f"تنوّع ردودك الحقيقي: {a['variety']}")
    elif kind == "reply":
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


def reply_length_line(profile: Dict[str, Any], brief: bool, kind: str = "") -> str:
    """طول الرد من القياس — ومن **نوعه** إن عُرف: وسيط واحد لكل الردود (24 حرفاً، أغلبها
    مجاملات) كان يحشر التعليق على خبر في شعار من خمس كلمات."""
    st = profile.get("style") or {}
    ks = (st.get("reply_kinds") or {}).get(kind) or {}
    n = int(ks.get("len_median") or st.get("reply_len_median") or 0)
    if not n:
        return ""
    top = int(ks.get("len_p75") or 0)
    cap = min(max(int(n * 1.6), top + 10, 40), 120 if brief else 270)
    emoji = st.get("reply_emoji_rate", 0) or 0
    em = "بإيموجي كعادتك" if emoji >= 0.5 else "بلا إيموجي" if emoji <= 0.1 else "إيموجي واحد كحد أقصى"
    return f"- بطول ردودك المعتاد (≈{n} حرفاً، وأقل من {cap})، بلا هاشتاقات، {em}.\n"


# ─────────────────────────── replies: plan & repetition ───────────────────────────
#: كم رداً سابقاً للموظف يُفحص تكراره
RECENT_REPLIES_KEEP = 30


def overused_phrases(recent: Sequence[str], keep: int = 8) -> List[str]:
    """قوالب استهلكها الموظف مؤخراً: عبارة في ≥3 من آخر 20 رداً أو ≥2 من آخر 5، أو خاتمة
    (آخر كلمتين) تكررت ≥3 مرات في آخر 10. هذا ما يجعل «بالميدان لا بالبيان» ممنوعة الآن."""
    last20 = [norm_ar(_clean(r)) for r in list(recent)[-20:] if r]
    stop = {norm_ar(s) for s in _STOP}
    counts20: Counter = Counter()
    counts5: Counter = Counter()
    for i, t in enumerate(last20):
        ws = _words(t)
        grams = set()
        for n in (2, 3):
            for j in range(len(ws) - n + 1):
                g = ws[j:j + n]
                if g[0] not in stop and g[-1] not in stop:
                    grams.add(" ".join(g))
        counts20.update(grams)
        if i >= len(last20) - 5:
            counts5.update(grams)
    picked = [g for g, c in counts20.most_common() if c >= 3] + [g for g, c in counts5.items() if c >= 2]
    endings = Counter(" ".join(_words(t)[-2:]) for t in last20[-10:] if len(_words(t)) >= 2)
    picked += [e for e, c in endings.items() if c >= 3]
    # لازمة بكلمة واحدة («الضجيج» في 3 من 10 ردود — قيس حيّاً) تكشف الآلية مثل العبارة
    words10 = Counter(w for t in last20[-10:] for w in _content_words(t) if len(w) >= 4)
    picked += [w for w, c in words10.most_common() if c >= 3]
    out: List[str] = []
    for g in picked:
        if not any(g in o or o in g for o in out):
            out.append(g)
    return out[:keep]


def banned_for(banned: Sequence[str], original: str) -> List[str]:
    """ما يُمنع في هذا الرد بالذات: كلمة موجودة في التغريدة نفسها («الحوثي» في خبر عن الحوثي)
    ذِكرها طبيعي وليس لازمة."""
    own = {norm_ar(w) for w in _words(original or "")}
    return [b for b in banned if " " in b or b not in own]


#: ألفاظ المجاملة والدعاء والألقاب: أسلوب مشترك بين ردود الحساب لا «تكرار مضمون» —
#: قيس: «بيض الله وجهك، التوضيح مهم» رُفض لأنه يشارك «بيض الله وجهك، أين المصدر؟» ثلاث كلمات.
_FORMULA_WORDS = {norm_ar(w) for w in (
    "بيض", "الله", "وجهك", "يحفظك", "يحفظ", "تسلم", "يعطيك", "العافية", "العافيه", "يرفع", "قدرك",
    "الغالي", "استاذ", "أستاذ", "اخوي", "أخوي", "اخي", "أخي", "مشكور", "شكرا", "شكراً", "جزاك",
    "خير", "ماشاء", "باذن", "بإذن", "يارب", "آمين", "امين", "حياك", "الحبيب", "يالغالي")}


def _content_words(text: str) -> set:
    from .persona_templates import normalize_topic_words

    return {w for w in normalize_topic_words(text) if w not in _FORMULA_WORDS}


def reply_repetition(text: str, recent: Sequence[str], banned: Sequence[str] = ()) -> List[str]:
    """عيوب تكرار في رد مولَّد: نسخة شبه حرفية من رد حديث، أو قالب مستهلك."""
    from .persona_templates import topic_overlap_ratio

    issues: List[str] = []
    t = norm_ar(_clean(text))
    words = _content_words(t)
    recent = [norm_ar(_clean(r)) for r in list(recent)[-RECENT_REPLIES_KEEP:] if r]
    for i, r in enumerate(reversed(recent)):
        if len(words) >= 3:
            if topic_overlap_ratio(words, _content_words(r)) >= 0.6:
                issues.append("near_duplicate")
                break
        elif i < 3 and " ".join(_words(t)) == " ".join(_words(r)):
            # مجاملة قصيرة متطابقة مقبولة (يكتبها صاحب الأسلوب كثيراً) لكن ليس في ردود متتالية
            issues.append("near_duplicate")
            break
    flat = " ".join(_words(t))
    tokens = set(_words(t))
    if any(b and ((" " in b and b in flat) or (" " not in b and b in tokens)) for b in banned):
        issues.append("overused_phrase")
    return issues


#: صياغة خبرية (حساب أخبار لا شخص): مخاطبته بـ«أستاذ» أو «أخوي» غير واقعية
_NEWS_RE = re.compile(r"(?:^|\s)(?:عاجل|متحدث|المتحدث|أعلن|اعلن|أعلنت|أكد|أكدت|بيان|وكالة|مراسل|"
                      r"مصادر|رئيس\s+[^\s]+\s*:|وزارة|الخارجية|التحالف\s*:|هاتفي)|"
                      # سطر أول ينتهي بنقطتين: صياغة خبر منقول («الرئيس التركي لولي العهد هاتفيًا:»)
                      r"\A[^\n]{4,90}[:：]\s*(?:\n|$)", re.I)


def _pick_kind(dist: Dict[str, float], recent_kinds: Sequence[str], rng: random.Random) -> str:
    """نوع الرد بنسب الحساب الحقيقية، مع تخفيف ما تكرر في آخر ردّين (تنوّع لا قالب)."""
    weights = {}
    for k, share in dist.items():
        if k not in REPLY_KINDS or share <= 0:
            continue
        w = float(share)
        for prev in list(recent_kinds)[-2:]:
            if prev == k:
                w *= 0.5
        weights[k] = w
    if not weights:
        return "substantive"
    total = sum(weights.values())
    x = rng.random() * total
    for k, w in weights.items():
        x -= w
        if x <= 0:
            return k
    return next(iter(weights))


def pick_exemplars(profile: Dict[str, Any], kind: str, pk: str, original: str,
                   rng: random.Random, n: int = 4) -> List[Dict[str, str]]:
    """أمثلة من البنك تناسب هذا الرد: النوع نفسه أولاً، ثم نوع الأصل، ثم قرب الموضوع —
    مع تدوير عشوائي حتى لا يرى النموذج الأمثلة الأربعة نفسها في كل رد."""
    bank = (profile.get("exemplars") or {}).get("replies") or []

    def score(p: Dict[str, str]) -> float:
        return ((2.0 if p.get("kind") == kind else 0.0) + (0.7 if p.get("parent_kind") == pk else 0.0)
                + _overlap(original, p.get("to", "")) + rng.random() * 0.6)

    ranked = sorted(bank, key=score, reverse=True)
    out: List[Dict[str, str]] = []
    for p in ranked:
        if not any(_overlap(p["reply"], q["reply"]) >= 0.6 for q in out):
            out.append(p)
        if len(out) >= n:
            break
    return out


def reply_plan(profile: Dict[str, Any], original: str, recent: Sequence[str] = (),
               recent_kinds: Sequence[str] = (), seed: str = "", brief: bool = False,
               names: Sequence[str] = ()) -> Dict[str, Any]:
    """خطة رد واحد مأخوذة من سلوك الحساب الحقيقي: نوع الرد المناسب لما يُردّ عليه، طوله،
    أمثلة مطابقة، وما يُمنع تكراره الآن. ``{}`` لبصمة الإصدار الأول (لا أنواع مقاسة)."""
    st = profile.get("style") or {}
    kinds = st.get("reply_kinds") or {}
    banned = banned_for(overused_phrases(recent), original)
    if not kinds:
        return {"kind": "", "banned": banned, "length_line": reply_length_line(profile, brief),
                "block": voice_block(profile, "reply")}
    rng = random.Random(seed or original)
    pk = parent_kind(original, names)
    policy = (st.get("reply_policy") or {}).get(pk) or {}
    overall = {k: v.get("share", 0) for k, v in kinds.items()}
    dist = policy.get("kinds") or overall
    if policy and policy.get("n", 0) < 8:
        # عيّنة صغيرة لهذا النوع من الأصل: نمزجها بالتوزيع العام بدل الوثوق بها وحدها
        dist = {k: 0.5 * dist.get(k, 0) + 0.5 * overall.get(k, 0) for k in set(dist) | set(overall)}
    kind = _pick_kind(dist, recent_kinds, rng)
    a = profile.get("analysis") or {}
    lines = [f"🎙️ طريقة ردّك هنا (من {(profile.get('sample') or {}).get('replies', '?')} رداً حقيقياً "
             f"عبر {(st.get('coverage') or {}).get('days', '?')} يوماً):",
             f"- التغريدة من نوع «{PARENT_KINDS.get(pk, pk)}»، والحساب يرد على مثلها غالباً بـ: "
             + "، ".join(f"{REPLY_KINDS.get(k, k)} {round(v * 100)}%"
                         for k, v in sorted(dist.items(), key=lambda kv: -kv[1])[:4] if v) + ".",
             f"- نوع ردّك هذه المرة: **{REPLY_KINDS.get(kind, kind)}**"
             + (f" — {a['reply_kinds'][kind]}" if (a.get("reply_kinds") or {}).get(kind) else "") + "."]
    if kind == "disagree":
        lines.append("- اعتراضك على الاستنتاج أو الصياغة أو التوقيت فقط؛ لا تنفِ الخبر بمعلومة من عندك.")
    if pk == "content" and _NEWS_RE.search(original or ""):
        lines.append("- التغريدة خبر من حساب إخباري: لا تخاطب صاحبها بلقب أو اسم (أستاذ/أخوي/الغالي)؛ "
                     "علّق على الخبر نفسه.")
    rates = st.get("phrase_rates") or {}
    natural = [p for p, r in rates.items() if r.get(kind, 0) >= 0.15 and p not in banned]
    rare = [p for p, r in rates.items() if r.get(kind, 0) < 0.15]
    if natural:
        lines.append("صيغ طبيعية في هذا النوع (اختر صياغتك ولا تلتزم بها): " + "، ".join(natural[:4]) + ".")
    if rare:
        lines.append("صيغ لا يستعملها الحساب في هذا النوع من الردود: " + "، ".join(rare[:5]) + ".")
    ex = pick_exemplars(profile, kind, pk, original, rng)
    if ex:
        lines.append("أمثلة حقيقية مشابهة (للطريقة فقط، لا تنسخ ولا تكرر ألفاظها):")
        lines += [f"  • على «{p['to'][:110]}» ← «{p['reply'][:150]}»" for p in ex]
    if banned:
        lines.append("❌ قوالب كررتها في ردودك الأخيرة — ممنوعة هذه المرة: " + "، ".join(f"«{b}»" for b in banned) + ".")
    return {"kind": kind, "parent_kind": pk, "banned": banned,
            "length_line": reply_length_line(profile, brief, kind), "block": "\n".join(lines)}


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
