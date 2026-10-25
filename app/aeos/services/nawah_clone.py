"""
بصمة الصوت عبر خادم «نواه» (``system/mcps/nawah_clone``) — الطبقة التي تكلّمه من بايثون.

ما الذي تغيّر (2026-10-25): كان ``voice_profile.analyze`` يطلب من نموذج النظام تحليلاً حراً
للهوية (``_ANALYSIS_SCHEMA``). الآن **البصمة الأسلوبية** تُبنى بأداة ``create_clone`` في نواه:
قياسات حتمية + قراءة النموذج (اللهجة، السجل، دليل أسلوب، عبارات يكرّرها، ما لا يفعله أبداً)،
وتُحوَّل هنا إلى شكل ``analysis`` الذي تستهلكه كتل الموجّه والبرسونا، فلا يتغيّر مسار الكتابة.
القياسات العربية الخاصة بالمشروع (أنواع الردود، سياسة الرد، الأمثلة، عبارات المرحلة) تبقى كما هي:
نواه تحلّل المنشورات، والمشروع يحلّل الردود.

الحدود المقصودة:
- الخادم **داخلي**: نطاقه للمنسّق والموظفين ``false`` في ``mcp.json``؛ لا تصل إليه أداة محادثة.
- مفتاح النموذج وعنوانه من :func:`resolve_llm` (نقطة **النظام** دائماً، كالتضمينات والصور) تُحقن
  وقت الاتصال عبر ``external_mcp.register_env_provider`` — لا أسرار محفوظة للخادم، ويُفرَّغ
  ``GEMINI_API_KEY`` حتى لا يتكلم الطفل مع Google مباشرة أبداً.
- الموديل: ``NAWAH_MODEL`` (افتراضياً ``google/gemini-3.7-flash`` على OpenRouter، مؤهَّل بالمزوّد).
- ملف لكل (حساب الموظف، الحساب المستنسخ): ``data/nawah/clones/<label>__<handle>.json`` — لا
  مكتبة مشتركة بين المستخدمين، والمصدر الحقيقي للبصمة هو ``business_rules.voice_profile``
  كما كان؛ الملف ذاكرة لنواه فقط (score/write لاحقاً).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("aeos.services.nawah_clone")

SLUG = "nawah_clone"
NAME = "ext_" + SLUG
_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = Path(os.getenv("NAWAH_DATA_DIR") or _ROOT / "data" / "nawah")
#: مؤهَّل بالمزوّد: الاسم المجرّد 404 على OpenRouter (والخطأ يصل عربياً من الخادم لا صمتاً)
DEFAULT_MODEL = "google/gemini-3.7-flash"
DEFAULT_FALLBACK = "google/gemini-3.6-flash"
#: أقل عدد نصوص تقبله نواه لبناء بصمة منشورات
MIN_TEXTS = 5
MAX_TEXTS = 400
CALL_TIMEOUT = 240.0
_URL_RE = re.compile(r"https?://\S+")


# ─────────────────────────── env / availability ───────────────────────────
def runtime_env() -> Dict[str, str]:
    """بيئة الطفل: نقطة النظام من resolve_llm() + Tavily + مجلد البيانات. تُحسب عند كل اتصال."""
    from app.core.llm_provider import resolve_llm

    ep = resolve_llm()
    openrouter = ep.provider == "openrouter"
    model = (os.getenv("NAWAH_MODEL") or "").strip() or (DEFAULT_MODEL if openrouter else ep.model)
    fallback = (os.getenv("NAWAH_FALLBACK_MODEL") or "").strip() or (DEFAULT_FALLBACK if openrouter else ep.model)
    env = {
        "NAWAH_LLM_API_KEY": ep.api_key or "",
        "NAWAH_LLM_BASE_URL": ep.base_url or "https://api.openai.com/v1",
        "NAWAH_LLM_MODEL": model,
        "NAWAH_LLM_FALLBACK_MODEL": fallback,
        "TAVILY_API_KEY": (os.getenv("TAVILY_API_KEY") or "").strip(),
        "NAWAH_DATA_DIR": str(DATA_DIR),
        # الطفل يرث بيئة العملية: نفرّغ مفتاح Google حتى لا يبقى أي مسار إلى Gemini المباشر
        "GEMINI_API_KEY": "",
    }
    site, app = os.getenv("OPENROUTER_SITE_URL", "").strip(), os.getenv("OPENROUTER_APP_NAME", "").strip()
    if site:
        env["OPENROUTER_SITE_URL"] = site
    if app:
        env["OPENROUTER_APP_NAME"] = app
    return env


def install() -> None:
    """يُستدعى عند الإقلاع قبل تسجيل الخوادم الخارجية: يربط بيئة الخادم بـresolve_llm()."""
    from ..mcp.servers import external_mcp as X

    X.register_env_provider(SLUG, runtime_env)


def _server():
    from . import tools_service as TS

    reg = TS.registry()
    return reg.get(NAME) if reg.has(NAME) else None


def status() -> Dict[str, Any]:
    """لماذا لا تعمل نواه الآن (إن كانت لا تعمل) — بالعربية، للمالك."""
    if not shutil.which("node"):
        return {"ok": False, "reason": "Node.js غير مثبّت على الخادم (خادم نواه يعمل بـnode)"}
    if not (_ROOT / "system" / "mcps" / SLUG / "dist" / "clone-server.mjs").exists():
        return {"ok": False, "reason": "ملف نواه المجمّع غير موجود: شغّل npm run build:clone في system/mcps/nawah_clone"}
    try:
        srv = _server()
    except Exception as exc:  # noqa: BLE001 - قبل اكتمال الإقلاع
        return {"ok": False, "reason": f"سجل الخوادم غير جاهز: {exc}"}
    if srv is None:
        return {"ok": False, "reason": "خادم nawah_clone غير مسجّل أو معطّل في system/mcps"}
    if not runtime_env().get("NAWAH_LLM_API_KEY"):
        return {"ok": False, "reason": "لا مفتاح نموذج للنظام (OPENROUTER_API_KEY)"}
    return {"ok": True, "reason": ""}


def enabled() -> bool:
    return bool(status()["ok"])


# ─────────────────────────── building the fingerprint ───────────────────────────
def clone_id(cookie_label: str, handle: str) -> str:
    """معرّف ملف ثابت لكل (حساب الموظف، الحساب المستنسخ) — إعادة الاستنساخ تستبدل الملف نفسه."""
    clean = lambda s: re.sub(r"[^\w-]+", "-", (s or "").strip().lstrip("@"))[:40].strip("-") or "x"  # noqa: E731
    return f"{clean(cookie_label)}__{clean(handle)}"


def _texts(profile: Dict[str, Any]) -> List[str]:
    """المنشورات الأصلية أولاً (البصمة بصمة منشورات)؛ الردود تكمّل فقط إن قلّت المنشورات."""
    def clean(t: str) -> str:
        return _URL_RE.sub("", t or "").strip()

    tweets = [clean(t.get("text", "")) for t in profile.get("tweets") or []]
    out = [t for t in tweets if t]
    if len(out) < 20:
        out += [clean(r.get("text", "")) for r in profile.get("replies") or [] if clean(r.get("text", ""))]
    return out[:MAX_TEXTS]


def build_fingerprint(profile: Dict[str, Any], cookie_label: str,
                      timeout: float = CALL_TIMEOUT) -> Dict[str, Any]:
    """نداء ``create_clone`` واحد يعيد البصمة كاملة. يرفع RuntimeError برسالة عربية عند الفشل."""
    st = status()
    if not st["ok"]:
        raise RuntimeError(st["reason"])
    texts = _texts(profile)
    if len(texts) < MIN_TEXTS:
        raise RuntimeError(f"نواه تحتاج {MIN_TEXTS} منشورات على الأقل (وجدنا {len(texts)})")
    handle = (profile.get("handle") or "").lstrip("@")
    srv = _server()
    from ..mcp.servers.external_mcp import result_text

    res = srv.client.call_tool("create_clone", {
        "name": handle or cookie_label, "handle": handle, "kind": "posts",
        "texts": texts, "id": clone_id(cookie_label, handle),
    }, timeout=timeout)
    if res.get("isError"):
        raise RuntimeError(result_text(res)[:300] or "نواه أعادت خطأ بلا تفاصيل")
    sc = res.get("structuredContent") or {}
    fp = sc.get("fingerprint") or {}
    if not isinstance(fp.get("data"), dict) or not fp["data"].get("style_guide"):
        raise RuntimeError("نواه لم تُعد بصمة صالحة (لا style_guide)")
    return {"id": sc.get("id") or clone_id(cookie_label, handle), "fingerprint": fp,
            "kept": int(sc.get("kept") or 0), "dropped": sc.get("dropped") or {},
            "texts": len(texts)}


_TONE_HINTS = (
    ("witty", ("ساخر", "تهكم", "فكاه", "مرح", "دعاب")),
    ("formal", ("رسمي", "رصين", "تحرير", "إخباري", "اخباري", "أكاديم", "فصحى")),
    ("friendly", ("ودود", "حميم", "دافئ", "قريب")),
    ("professional", ("مهني", "تقني", "تحليل", "خبير")),
    ("casual", ("عامّي", "عامي", "مباشر", "تلقائي", "محكي")),
)


def _tone(register: str, dialect: str) -> str:
    text = f"{register} {dialect}"
    for tone, keys in _TONE_HINTS:
        if any(k in text for k in keys):
            return tone
    return ""


def _strs(v: Any, n: int, length: int = 200) -> List[str]:
    return [str(x).replace("\n", " ").strip()[:length] for x in (v if isinstance(v, list) else []) if str(x).strip()][:n]


def to_analysis(fingerprint: Dict[str, Any]) -> Dict[str, Any]:
    """بصمة نواه ← شكل ``analysis`` الذي تقرؤه ``voice_block`` و``persona_from_voice``.

    ما لا تنتجه نواه (محاور، قيم، خطوط حمراء، دليل ردود) يُترك فارغاً عمداً — لا نخترعه؛
    اهتمامات الموظف تأتي من توزيع المواضيع المقاس، وأنواع ردوده من سياسة الرد المقاسة.
    """
    d = fingerprint.get("data") if isinstance(fingerprint.get("data"), dict) else {}
    one, desc = str(d.get("one_line") or "").strip(), str(d.get("description") or "").strip()
    register, dialect = str(d.get("register") or "")[:80], str(d.get("dialect") or "")[:40]
    style_guide = _strs(d.get("style_guide"), 4, 300)
    never = _strs(d.get("never_uses"), 6, 120)
    notes = {k: str(d.get(k) or "").strip()[:200]
             for k in ("openings_note", "punctuation_note", "emoji_note", "hashtag_note", "numerals_note")
             if str(d.get(k) or "").strip() and "لا ينطبق" not in str(d.get(k))}
    return {
        "voice_summary": " ".join(x for x in (one, desc) if x)[:700],
        "identity_core": [], "era_topics": [], "reply_kinds": {}, "variety": "",
        "register": register, "humor": "", "stance": "", "rhetorical_devices": [],
        "pillars": [], "formats": [], "reply_playbook": {}, "interaction": {},
        "lexicon": {"signature": _strs(d.get("signature_phrases"), 12, 60), "avoid": []},
        "values": [], "red_lines": [],
        "persona": {"role": "", "tone": _tone(register, dialect), "dialect": dialect,
                    "dos": style_guide, "donts": never},
        # حقول نواه الإضافية — تقرؤها voice_block والواجهة
        "style_guide": style_guide, "style_notes": notes, "never_uses": never,
        "dialect_family": str(d.get("dialect_family") or "")[:30],
    }


def _summary(fingerprint: Dict[str, Any], built: Dict[str, Any]) -> Dict[str, Any]:
    st = fingerprint.get("stats") if isinstance(fingerprint.get("stats"), dict) else {}
    keep = ("count", "medianLines", "avgWordsPerLine", "hashtagRate", "emojiRate", "questionRate",
            "numerals", "fusedNegationRate", "dialectMarkers")
    return {"id": built["id"], "model": fingerprint.get("model", ""),
            "version": fingerprint.get("version", 1), "source_count": fingerprint.get("source_count", 0),
            "texts_sent": built.get("texts", 0), "dropped": built.get("dropped") or {},
            "stats": {k: st[k] for k in keep if k in st}}


def builder(cookie_label: str) -> Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]:
    """الدالة التي تمرَّر إلى ``voice_profile.analyze(nawah=...)``: تعيد {analysis, nawah}."""
    def build(profile: Dict[str, Any], _stats: Dict[str, Any]) -> Dict[str, Any]:
        built = build_fingerprint(profile, cookie_label)
        return {"analysis": to_analysis(built["fingerprint"]), "nawah": _summary(built["fingerprint"], built)}
    return build


# ─────────────────────────── scoring ───────────────────────────
def score(clone_ref: str, text: str, timeout: float = 60.0) -> Optional[int]:
    """درجة تطابق نص مع البصمة (0..100) بقياس نواه الحتمي — None إن تعذّر."""
    if not (clone_ref and text.strip()):
        return None
    try:
        srv = _server()
        if srv is None:
            return None
        res = srv.client.call_tool("score", {"clone": clone_ref, "text": text, "format": "post"}, timeout=timeout)
        if res.get("isError"):
            return None
        val = (res.get("structuredContent") or {}).get("score")
        return int(val) if isinstance(val, (int, float)) else None
    except Exception:  # noqa: BLE001 - الدرجة معلومة إضافية لا شرط
        logger.exception("nawah score failed")
        return None
