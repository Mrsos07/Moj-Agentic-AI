"""
unslop لكل الموظفين: كشف علامات الكتابة الآلية وإعادة الكتابة مرة واحدة قبل النشر.

المهارة الأصلية (https://github.com/theclaymethod/unslop، MIT) مثبّتة كما هي في
``system/skills/unslop``، لكن ماسحاتها **إنجليزية فقط** (ترفض غير الإنجليزي صراحةً) —
قيس 2026-10-26: نص إنجليزي آلي ← 4 علامات، ونفس النص بالعربية ← 0. والموظفون يكتبون بالعربية،
لذا طبقتان بنفس الفئات:

- **إنجليزي**: ``scripts/banned_phrase_scan.py`` الأصلي حرفياً (``scan_for_violations``).
- **عربي**: جدول أنماط هنا مبني على فئات unslop نفسها (التمهيد، التوكيد الأجوف، «ليس مجرد X بل
  Y»، السؤال الذي يجيب عنه الكاتب، التهويل، الإسناد المبهم، بقايا المساعد الآلي، الخاتمة
  الوعظية، الشرطة الطويلة…). وعلى مبدئها: أنماط سياقية لا حظر كلمات — «شاهد على الحادث» حرفياً
  لا يُعلَّم، «يقف شاهداً على» يُعلَّم.

السياسة (قرار المالك): إعادة كتابة **واحدة** بتصحيح محدد ثم النشر — لا يمنع النشر أبداً.
النص الذي يكتبه المالك بنفسه لا يُمسّ؛ فقط ما يولّده الموظف.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("aeos.employee.unslop")

_SCRIPTS = Path(__file__).resolve().parents[3] / "system" / "skills" / "unslop" / "scripts"
SKILL_SLUG = "unslop_guard"

# ─────────────────────────── تطبيع عربي ───────────────────────────
_TASHKEEL = re.compile(r"[\u064B-\u0652\u0670\u0640]")
_ARABIC = re.compile(r"[\u0600-\u06FF]")


def _norm(text: str) -> str:
    """بلا تشكيل/تطويل، وهمزات الألف ألفاً، والألف المقصورة ياءً، والتاء المربوطة هاءً — لتطابق
    الصيغ المختلفة («الحقيقه» شائعة على X كـ«الحقيقة»)."""
    t = _TASHKEEL.sub("", text or "")
    return t.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"}))


def _mask_quotes(text: str) -> str:
    """الاقتباسات أمثلة أو كلام الغير (unslop يستثنيها أيضاً): تُستبدل بمسافات بنفس الطول."""
    blank = lambda m: " " * len(m.group(0))  # noqa: E731
    for pat in (r"«[^»]*»", r'"[^"\n]*"', r"“[^”]*”"):
        text = re.sub(pat, blank, text)
    return text


# (الفئة، الشدة، النمط، التصحيح) — الأنماط تُطبَّع بنفس _norm قبل الترجمة
_B = r"(?<!\w)(?:[وف])?"      # بداية كلمة مع واو/فاء العطف
_AR_PATTERNS: List[tuple] = [
    ("throat_clearing", "hard",
     _B + r"إليك(?:م)? (?:الأمر|الحقيقة|ما يجب|الخلاصة|السر|القصة|المفاجأة)",
     "احذف التمهيد وابدأ بالمعلومة نفسها."),
    ("throat_clearing", "hard", _B + r"الحقيقة (?:المزعجة|المرة|التي لا يريد)",
     "احذف التمهيد وابدأ بالمعلومة نفسها."),
    ("throat_clearing", "hard",
     _B + r"(?:دعني|دعونا|دعنا) (?:أوضح|أكون واضحا|نتعمق|نغوص|نفكك|نتحدث بصراحة)|"
     + _B + r"(?:لنتعمق|لنغص|لنغوص|لنفكك)(?!\w)",
     "احذف التمهيد وابدأ بالمعلومة نفسها."),
    ("throat_clearing", "hard", _B + r"هنا يكمن (?:السر|الفرق|المفتاح|بيت القصيد)",
     "قل الفكرة مباشرة بدل الإعلان عنها."),
    ("emphasis_crutch", "hard",
     _B + r"نقطة،? (?:على|آخر) السطر|" + _B + r"(?:دع|دعوا) (?:هذا|هذه الفكرة|ذلك) (?:يترسخ|تترسخ|يستقر)|"
     + _B + r"اقرأ(?:ها|وها)? (?:مرة أخرى|مجددا|مرتين)|" + _B + r"لا يمكن المبالغة في|"
     + _B + r"لا تخطئ(?:وا)? الفهم",
     "احذف عبارة التوكيد؛ دع المعلومة تقنع بنفسها."),
    ("significance_inflation", "soft",
     _B + r"(?:يقف|تقف) شاهدا على|" + _B + r"شهادة حية على|" + _B + r"خير شاهد على|"
     + _B + r"لحظة فارقة|" + _B + r"منعطف تاريخي|" + _B + r"نقلة نوعية|" + _B + r"حجر الزاوية|"
     + _B + r"إرث خالد|" + _B + r"نسيج غني|" + _B + r"(?:يغير|تغير|تغيير|سيغير|ستغير|غيرت|غير) قواعد اللعبة|"
     + _B + r"ثورة حقيقية في|" + _B + r"(?:يحمل|تحمل) (?:وعودا|آمالا) كبيرة",
     "استبدل التهويل برقم أو أثر محدد يثبت الأهمية."),
    ("false_agency", "hard",
     _B + r"الأرقام (?:تتحدث|تتكلم) عن نفسها|" + _B + r"الأرقام لا تكذب|"
     + _B + r"(?:البيانات|الأرقام) (?:تروي|تحكي) (?:قصة|حكاية)|"
     + _B + r"(?:ترسم|يرسم) صورة (?:واضحة|قاتمة|مشرقة)",
     "اذكر الرقم واستنتاجك منه صراحة."),
    ("negative_parallelism", "hard",
     _B + r"(?:ليس|ليست|ليسوا|لم يعد|لم تعد) مجرد [^.!؟?\n]{1,60}?[،,]?\s*(?:بل|إنه|إنها)(?!\w)|"
     + _B + r"(?:ليس|ليست) فقط [^.!؟?\n]{1,60}?[،,]?\s*بل(?!\w)|"
     + _B + r"لا (?:يقتصر|تقتصر) [^.!؟?\n]{1,60}?[،,]?\s*بل(?!\w)|"
     # «ليس بروتوكولاً عادياً؛ هو اختبار» / «لم يذهب لتجميل المشهد؛ بل لإقناع» (نُشرا فعلاً
     # 2026-10-26). الفاصل (؛ ،) قبل «بل/هو» هو العلامة: التصحيح الحقيقي «ليس اليوم بل غداً» بلا فاصل
     + _B + r"(?:ليس|ليست|ليسوا|لم يكن|لم تكن) [^.!؟?\n؛;،,]{1,60}[؛;،,]\s*(?:بل|هو|هي|إنه|إنها)(?!\w)|"
     + _B + r"(?:لم|لن) [^.!؟?\n؛;،,]{1,60}[؛;،,]\s*بل(?!\w)",
     "قل ما هو الشيء مباشرة بدل نفي ما ليس هو."),
    ("self_qa", "hard",
     _B + r"(?:لماذا|ليش|ما الذي|ماذا) [^؟?\n]{0,40}[؟?]\s*(?:لأن|ببساطة|الجواب|الإجابة)(?!\w)|"
     + _B + r"(?:السبب|الجواب|النتيجة|الخلاصة|السر)\s*[؟?]",
     "اكتب الجواب مباشرة بلا سؤال تمهيدي تجيب عنه بنفسك."),
    ("vague_attribution", "hard",
     _B + r"(?:يرى|يقول|يؤكد|يعتقد|يحذر|بحسب|حسب|وفق) (?:ال)?(?:خبراء|مختصين|مختصون|محللين|محللون|مراقبين|مراقبون)(?!\w)|"
     + _B + r"(?:تشير|تؤكد|أثبتت|تقول) (?:ال)?(?:دراسات|أبحاث)(?!\w)|"
     + _B + r"(?:دراسات|أبحاث) (?:تشير|تؤكد|أثبتت)(?!\w)",
     "سمِّ المصدر (من قال؟) أو احذف الإسناد — ولا تخترع مصدراً."),
    ("chatbot_artifact", "hard",
     _B + r"آمل أن (?:يكون|تكون) (?:هذا |ذلك |هذه )?(?:مفيد|مفيدا|مفيدة)|" + _B + r"بالتأكيد!|"
     + _B + r"سؤال (?:رائع|ممتاز)|" + _B + r"بصفتي (?:نموذج|ذكاء)|" + _B + r"(?:كنموذج لغوي|كذكاء اصطناعي)|"
     + _B + r"لا تتردد(?:وا)? في|" + _B + r"يسعدني (?:مساعدتك|أن أساعد)|"
     + _B + r"(?:حسب|وفق) (?:آخر )?معلوماتي|" + _B + r"حتى تاريخ معرفتي",
     "احذف عبارة المساعد الآلي؛ أنت شخص يكتب على X."),
    ("reasoning_leak", "hard",
     _B + r"دعني أفكر|" + _B + r"لنفكر خطوة بخطوة|" + _B + r"خطوة بخطوة:|"
     + _B + r"لنحلل (?:هذا|الأمر)|" + _B + r"إليك (?:تحليلي|طريقة تفكيري)",
     "احذف وصف التفكير واكتب النتيجة."),
    ("reader_flattery", "hard",
     _B + r"سواء كنت (?:مبتدئا|خبيرا|محترفا)[^.؟?\n]{0,40}أو|"
     + _B + r"هذا ما (?:يجب|ينبغي) أن (?:تعرفه|تعرفوه)|" + _B + r"(?:يستحق|تستحق) (?:وقتك|القراءة)|"
     + _B + r"إليك ما (?:يجب|ينبغي)",
     "خاطب القارئ بالمعلومة لا بالمجاملة."),
    ("jargon", "soft",
     _B + r"تعظيم الاستفادة|" + _B + r"تحقيق التآزر|" + _B + r"الثمار (?:الدانية|المتدلية)|"
     + _B + r"غوص عميق|" + _B + r"الغوص (?:العميق|في أعماق)|" + _B + r"خارج الصندوق",
     "استبدل المصطلح الإداري بكلمة يومية محددة."),
    ("marketing", "soft",
     _B + r"عالمي المستوى|" + _B + r"بمستوى عالمي|" + _B + r"أحدث ما توصلت إليه|"
     + _B + r"جوهرة (?:مخفية|خفية)|" + _B + r"لا مثيل له|" + _B + r"الأفضل على الإطلاق",
     "استبدل لغة الإعلان بحقيقة قابلة للتحقق."),
]
_AR_COMPILED = [(c, s, re.compile(_norm(p)), fix) for c, s, p, fix in _AR_PATTERNS]
# الخاتمة الوعظية: الجملة الأخيرة فقط («في النهاية، هذا يذكّرنا…»)
_CODA = re.compile(_norm(r"^(?:و)?(?:في النهاية|في نهاية المطاف|في الختام|العبرة|الدرس هنا|تذكر دائما|وتذكر)(?!\w)"))
_SENT_SPLIT = re.compile(r"(?<=[.!؟?…])\s+|\n+")

LABELS = {
    "throat_clearing": "تمهيد فارغ", "emphasis_crutch": "توكيد أجوف",
    "significance_inflation": "تهويل", "false_agency": "إسناد الفعل للأرقام",
    "negative_parallelism": "«ليس مجرد X بل Y»", "self_qa": "سؤال يجيب عنه الكاتب",
    "vague_attribution": "إسناد مبهم", "chatbot_artifact": "عبارة مساعد آلي",
    "reasoning_leak": "تسريب تفكير", "reader_flattery": "مجاملة القارئ",
    "jargon": "مصطلحات إدارية", "marketing": "لغة إعلانية",
    "moralizing_coda": "خاتمة وعظية", "em_dash": "شرطة طويلة (—)",
}


def scan_arabic(text: str) -> List[Dict[str, Any]]:
    masked = _norm(_mask_quotes(text or ""))
    out = []
    for cat, sev, rx, fix in _AR_COMPILED:
        for m in rx.finditer(masked):
            out.append({"category": cat, "severity": sev, "phrase": m.group(0).strip(),
                        "suggestion": fix, "lang": "ar"})
    sentences = [s.strip() for s in _SENT_SPLIT.split(masked) if s.strip()]
    if len(sentences) >= 2 and _CODA.search(sentences[-1]):
        out.append({"category": "moralizing_coda", "severity": "soft", "phrase": sentences[-1][:40],
                    "suggestion": "احذف الخاتمة الوعظية؛ انتهِ عند آخر معلومة أو موقف.", "lang": "ar"})
    dashes = len(re.findall(r"[—–]", masked))
    if dashes:
        # unslop: الافتراضي صفر، واثنتان فأكثر علامة صلبة دائماً
        out.append({"category": "em_dash", "severity": "hard" if dashes >= 2 else "soft",
                    "phrase": "—" * min(dashes, 3),
                    "suggestion": "استبدل الشرطة الطويلة بفاصلة أو نقطة أو جملة جديدة.", "lang": "ar"})
    return out


# ─────────────────────────── الإنجليزي: ماسح unslop الأصلي ───────────────────────────
_upstream_mod = None


def _upstream():
    """``banned_phrase_scan`` الأصلي. يضيف مجلد سكربتاته لـsys.path ليستورد ``_lang``؛ نعيد
    sys.path كما كان بعد التحميل كي لا تحجب سكربتاته (suggest.py، contribute.py…) أي حزمة."""
    global _upstream_mod
    if _upstream_mod is None:
        from app.core.system_loader import load_module

        saved = list(sys.path)
        try:
            _upstream_mod = load_module(_SCRIPTS / "banned_phrase_scan.py",
                                        "moj_system.skills.unslop.banned_phrase_scan")
        finally:
            sys.path[:] = saved
    return _upstream_mod


def scan_english(text: str) -> List[Dict[str, Any]]:
    try:
        found = _upstream().scan_for_violations(text or "")
    except Exception as exc:  # noqa: BLE001 - تحسين للكتابة لا شرط للنشر
        logger.warning("unslop english scan failed: %s", exc)
        return []
    return [{"category": v["category"], "severity": v.get("severity", "hard"),
             "phrase": v["phrase"], "suggestion": v.get("suggestion") or "", "lang": "en"}
            for v in found]


def scan(text: str) -> List[Dict[str, Any]]:
    """كل علامات النص: العربية إن غلبت الحروف العربية، والإنجليزية بماسح unslop إن غلبت اللاتينية."""
    text = text or ""
    ar_words = len(re.findall(r"[\u0600-\u06FF]+", text))
    en_words = len(re.findall(r"[A-Za-z]{2,}", text))
    out: List[Dict[str, Any]] = []
    if ar_words >= 3:
        out += scan_arabic(text)
    if en_words >= 4 and en_words > ar_words:
        out += scan_english(text)
    return out


def score(findings: List[Dict[str, Any]]) -> int:
    return sum(3 if f.get("severity") == "hard" else 1 for f in findings)


def needs_fix(findings: List[Dict[str, Any]]) -> bool:
    """أي علامة صلبة، أو علامتان لينتان فأكثر (اللينة قد تكون أسلوب الشخص الحقيقي)."""
    return any(f.get("severity") == "hard" for f in findings) or len(findings) >= 2


def fix_hint(findings: List[Dict[str, Any]]) -> str:
    """تصحيح محدد للنموذج: العبارات الفعلية التي ظهرت وماذا يفعل بكل منها."""
    seen, lines = set(), []
    for f in findings:
        key = (f["category"], f["phrase"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- «{f['phrase']}» ({LABELS.get(f['category'], f['category'])}): "
                     f"{f.get('suggestion') or 'أعد صياغتها بلغة طبيعية مباشرة.'}")
        if len(lines) >= 6:
            break
    return ("نصّك فيه علامات كتابة آلية (unslop). أعد كتابته بدونها، محافظاً على المعنى نفسه "
            "وكل الأرقام والأسماء والتواريخ، ولا تضف معلومة جديدة:\n" + "\n".join(lines))


_RULES_AR = (
    "- اكتب كإنسان لا كنموذج (مبادئ unslop): بلا تمهيد («إليك الحقيقة»، «دعونا نتعمق»)، "
    "بلا توكيد أجوف («نقطة على السطر»)، بلا «ليس مجرد X بل Y»، بلا سؤال تجيب عنه بنفسك "
    "(«لماذا يهم؟ لأن…»)، بلا تهويل («نقلة نوعية»، «لحظة فارقة»)، بلا إسناد مبهم («يرى الخبراء» "
    "دون اسم)، بلا خاتمة وعظية، وبلا شرطة طويلة (—).")
_RULES_EN = (
    "- Write like a person, not a model (unslop rules): no throat-clearing (\"Here's the thing\"), "
    "no emphasis crutches (\"Let that sink in\"), no \"not just X, it's Y\", no self-answered "
    "questions (\"Why does this matter? Because…\"), no significance inflation, no vague "
    "attribution (\"experts say\"), no chatbot phrases, no moralizing closer, no em dashes.")


def enabled(config) -> bool:
    """مفعّلة للجميع افتراضياً: يطفئها المطوّر من اللوحة (المهارة unslop_guard) أو المالك لموظفه
    (``business_rules.unslop = false``)."""
    try:
        from app.aeos.skills import system_skills

        if not system_skills.is_enabled(SKILL_SLUG):
            return False
    except Exception:  # noqa: BLE001
        pass
    return bool((getattr(config, "business_rules", None) or {}).get("unslop", True))


def prompt_rules(config) -> str:
    """قاعدة الوقاية في موجّه الكتابة (أرخص من إعادة التوليد)، بلغة الموظف."""
    if not enabled(config):
        return ""
    lang = (getattr(getattr(config, "persona", None), "language", "") or "ar").lower()
    return _RULES_EN if lang.startswith("en") else _RULES_AR


def polish(text: str, regenerate: Callable[[str], str], config,
           accept: Optional[Callable[[str], bool]] = None) -> tuple:
    """أعد التوليد مرة واحدة بتصحيح محدد إن ظهرت علامات آلية، واحتفظ بالأفضل.

    يعيد ``(النص، العلامات المتبقية)``. لا يمنع النشر أبداً: أي فشل ← النص الأصلي.
    ``accept``: شرط إضافي للنسخة الجديدة (مثلاً ألا تكرر ردوداً سابقة).
    """
    if not text or not enabled(config):
        return text, []
    found = scan(text)
    if not needs_fix(found):
        return text, found
    try:
        retry = (regenerate(fix_hint(found)) or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("unslop retry failed: %s", exc)
        return text, found
    if not retry:
        return text, found
    again = scan(retry)
    if score(again) < score(found) and (accept is None or accept(retry)):
        logger.info("unslop rewrite: %s → %s",
                    sorted({f["category"] for f in found}), sorted({f["category"] for f in again}))
        return retry, again
    return text, found
