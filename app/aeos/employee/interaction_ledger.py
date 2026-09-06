"""
Interaction ledger — حلقة التغذية الراجعة المفقودة لقرارات **التفاعل**.

المشكلة التي يحلّها: ``strategy.py`` يقيس أداء المنشورات فقط. أما الرد/الإعجاب/
إعادة النشر/الاقتباس/المتابعة فكانت تُقيَّم بأرقام ثابتة مكتوبة يدوياً داخل
المهارات (``expected_impact=0.68`` للرد، ``0.35`` للإعجاب...). أي أن الموظف
مهما دارت عليه مئات الدورات لا يعرف أبداً: هل ردّ صاحب الحساب؟ هل أثمر التفاعل
مع @فلان شيئاً؟ أي نوع تفاعل يجلب استجابة فعلية في هذا المجال؟ — فلا يتعلّم
قرار التفاعل شيئاً.

هذه الوحدة تسجّل كل تفاعل منفَّذ، ثم **تنسب إليه الإشارة المرتدّة** عندما يظهر
الحساب المستهدف لاحقاً في المنشنات/الردود الواردة، وتحسب من ذلك:

- ``handle_affinity``     — احتمال أن يستجيب هذا الحساب تحديداً (تآلف).
- ``capability_yield``    — نسبة الاستجابة التاريخية لكل نوع تفاعل.
- ``handle_fatigue``      — كم لاحقنا هذا الحساب مؤخراً (منع الإلحاح).

كلها **منعَّمة بـprior** (متوسط بايزي مبسّط) فلا تصادر عيّنةٌ واحدة القرار،
ومصحوبة بعدد العيّنات كي يعطي محرّك القرار مكافأة استكشاف لما لم يُجرَّب.

التخزين في ``runtime_state.flags['interaction_ledger']`` — نفس نهج
``strategy.py`` تماماً: حقل JSON موجود أصلاً، بلا أي هجرة قاعدة بيانات.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

_KEY = "interaction_ledger"

#: أنواع التفاعل **العلنية** التي قد تُثمر استجابة (النشر له سجلّه في
#: ``strategy.py``). الحفظ (bookmark) مستثنى عمداً: غير علني ولا يمكن أن
#: يستجيب له أحد، فإدخاله يخفض معدّل الاستجابة العام زوراً.
INTERACTION_CAPABILITIES = (
    "reply_to_user", "like_post", "retweet_post", "quote_post", "follow_user",
)

#: خريطة الاسم المختصر المستعمل في ``handlers._record_interaction`` → القدرة
ACTION_TO_CAPABILITY = {
    "reply": "reply_to_user",
    "like": "like_post",
    "retweet": "retweet_post",
    "quote": "quote_post",
    "follow": "follow_user",
}

#: نسبة استجابة مفترضة لما لم يُجرَّب (prior). منخفضة عمداً: أغلب التفاعلات
#: على X لا تُثمر ردّاً، فالافتراض المتفائل يجعل كل شيء يبدو ناجحاً.
_PRIOR_RATE = 0.15
#: وزن الـprior بعدد عيّنات وهمية — 4 يعني أن أول نجاح يرفع التقدير بهدوء.
_PRIOR_WEIGHT = 4.0

#: سقوف التخزين (السجل يعيش داخل JSON فلا يجوز أن ينمو بلا حد)
_MAX_ENTRIES = 200
_MAX_HANDLES = 150
#: نافذة نسب الإشارة المرتدّة: منشن بعد 40 دورة لا علاقة له بإعجابنا القديم
_CREDIT_WINDOW_CYCLES = 40


# ─────────────────────────── التخزين ───────────────────────────
def _ledger(config) -> Dict[str, Any]:
    flags = config.runtime_state.flags
    led = flags.get(_KEY)
    if not isinstance(led, dict):
        led = {}
        flags[_KEY] = led
    led.setdefault("seq", 0)
    led.setdefault("entries", {})     # url -> قيد التفاعل
    led.setdefault("handles", {})     # @handle -> عدّادات
    led.setdefault("caps", {})        # capability -> عدّادات
    return led


def normalize_handle(handle: str) -> str:
    """توحيد المعرّف: بلا @، بلا مسافات، أحرف صغيرة، وبلا رابط ملف شخصي."""
    h = str(handle or "").strip()
    if not h:
        return ""
    if "/" in h:                       # https://x.com/user → user
        h = h.rstrip("/").rsplit("/", 1)[-1]
    return h.lstrip("@").strip().lower()


def handle_from_url(url: str) -> str:
    """استخرج المعرّف من رابط تغريدة أو ملف شخصي.

    مهم لأن ``follow_user`` يمرّر ``profile_url`` وقد يأتي بلا ``handle``،
    وبدون استخراجه يضيع القيد من إحصاء الحساب فلا يتعلّم النظام عنه شيئاً.
    """
    u = str(url or "").strip()
    if not u or "x.com" not in u and "twitter.com" not in u:
        return ""
    path = u.split("x.com/")[-1].split("twitter.com/")[-1].strip("/")
    first = path.split("/")[0].split("?")[0]
    if not first or first in ("i", "home", "search", "explore", "notifications"):
        return ""
    return normalize_handle(first)


def _cycle(config) -> int:
    try:
        return int(config.runtime_state.cycles_completed or 0)
    except (TypeError, ValueError):
        return 0


def _trim(led: Dict[str, Any]) -> None:
    entries: Dict[str, Any] = led.get("entries") or {}
    if len(entries) > _MAX_ENTRIES:
        ordered = sorted(entries.items(), key=lambda kv: int(kv[1].get("seq", 0)),
                         reverse=True)
        led["entries"] = dict(ordered[:_MAX_ENTRIES])
    handles: Dict[str, Any] = led.get("handles") or {}
    if len(handles) > _MAX_HANDLES:
        ordered_h = sorted(handles.items(),
                           key=lambda kv: int(kv[1].get("last_cycle", 0)),
                           reverse=True)
        led["handles"] = dict(ordered_h[:_MAX_HANDLES])


# ─────────────────────────── التسجيل ───────────────────────────
def record(config, *, capability: str, url: str = "", handle: str = "",
           text: str = "") -> None:
    """سجّل تفاعلاً **نُفِّذ بنجاح** (يُستدعى من طبقة القدرات لا من المهارات).

    القيد يُفهرس بالرابط لأنه المعرّف الوحيد المستقر للهدف؛ وإن غاب الرابط
    (متابعة بلا رابط مثلاً) نولّد مفتاحاً من المعرّف والتسلسل.
    """
    cap = str(capability or "").strip()
    if cap not in INTERACTION_CAPABILITIES:
        return
    led = _ledger(config)
    led["seq"] = int(led.get("seq", 0)) + 1
    seq = led["seq"]
    h = normalize_handle(handle) or handle_from_url(url)
    cycle = _cycle(config)
    key = (url or "").strip() or f"{cap}:{h or 'unknown'}:{seq}"

    led["entries"][key] = {
        "seq": seq,
        "cap": cap,
        "handle": h,
        "cycle": cycle,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "text": (text or "").strip().replace("\n", " ")[:160],
        "reciprocated": False,
    }
    caps = led["caps"].setdefault(cap, {"count": 0, "reciprocated": 0})
    caps["count"] = int(caps.get("count", 0)) + 1
    if h:
        rec = led["handles"].setdefault(
            h, {"engaged": 0, "reciprocated": 0, "last_cycle": -1, "caps": {}})
        rec["engaged"] = int(rec.get("engaged", 0)) + 1
        rec["last_cycle"] = cycle
        rec["caps"][cap] = int(rec["caps"].get(cap, 0)) + 1
    _trim(led)


def credit_signals(config, mentions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """انسب الإشارات الواردة إلى التفاعلات التي سبقتها — قلب حلقة التعلّم.

    كل منشن/رد وارد من حساب سبق أن تفاعلنا معه **خلال نافذة زمنية معقولة**
    يُحتسب استجابةً لذلك التفاعل: يُعلَّم القيد ويُزاد عدّاد الحساب وعدّاد نوع
    التفاعل. القيد يُحتسب مرة واحدة فقط (``reciprocated``) فلا يضخّم منشنٌ
    واحد النتيجة عبر الدورات.

    يعيد قائمة بما احتُسب حديثاً: ``[{"handle", "cap"}]`` — يعرضها التقرير
    ويُغذّى بها الرسم المعرفي.
    """
    if not mentions:
        return []
    led = _ledger(config)
    entries: Dict[str, Any] = led.get("entries") or {}
    now_cycle = _cycle(config)

    incoming = {normalize_handle(
        m.get("handle") or m.get("author") or m.get("username") or "")
        for m in mentions if isinstance(m, dict)}
    incoming.discard("")
    if not incoming:
        return []

    credited: List[Dict[str, str]] = []
    for entry in entries.values():
        if entry.get("reciprocated"):
            continue
        h = entry.get("handle") or ""
        if h not in incoming:
            continue
        if now_cycle - int(entry.get("cycle", 0)) > _CREDIT_WINDOW_CYCLES:
            continue
        entry["reciprocated"] = True
        entry["credited_cycle"] = now_cycle
        cap = str(entry.get("cap") or "")
        caps = led["caps"].setdefault(cap, {"count": 0, "reciprocated": 0})
        caps["reciprocated"] = int(caps.get("reciprocated", 0)) + 1
        rec = led["handles"].setdefault(
            h, {"engaged": 0, "reciprocated": 0, "last_cycle": -1, "caps": {}})
        rec["reciprocated"] = int(rec.get("reciprocated", 0)) + 1
        credited.append({"handle": h, "cap": cap})
    return credited


# ─────────────────────────── الإشارات المُتعلَّمة ───────────────────────────
def _smoothed(hits: int, total: int, prior: float = _PRIOR_RATE) -> float:
    """نسبة منعَّمة بالـprior — عيّنة واحدة لا تصادر القرار."""
    return (hits + prior * _PRIOR_WEIGHT) / (max(total, 0) + _PRIOR_WEIGHT)


def base_rate(config) -> float:
    """نسبة الاستجابة العامة لهذا الحساب — هي الـprior الطبيعي للمقارنة.

    استعمالها كنقطة صفر يجعل الحسابَ/النوعَ **المجهول محايداً تماماً** في
    التقييم بدل أن يُكافأ أو يُعاقب بلا دليل.
    """
    led = _ledger(config)
    total = sum(int(c.get("count", 0)) for c in (led.get("caps") or {}).values())
    hits = sum(int(c.get("reciprocated", 0)) for c in (led.get("caps") or {}).values())
    if total <= 0:
        return _PRIOR_RATE
    return _smoothed(hits, total)


def capability_stats(config, capability: str) -> Tuple[float, int]:
    """(نسبة الاستجابة المنعَّمة، عدد العيّنات) لهذا النوع من التفاعل."""
    led = _ledger(config)
    rec = (led.get("caps") or {}).get(capability) or {}
    total = int(rec.get("count", 0))
    hits = int(rec.get("reciprocated", 0))
    return _smoothed(hits, total, base_rate(config)), total


def capability_yield(config, capability: str) -> float:
    return capability_stats(config, capability)[0]


def handle_stats(config, handle: str) -> Tuple[float, int]:
    """(تآلف الحساب المنعَّم، عدد مرات تفاعلنا معه)."""
    h = normalize_handle(handle)
    if not h:
        return base_rate(config), 0
    rec = (_ledger(config).get("handles") or {}).get(h) or {}
    total = int(rec.get("engaged", 0))
    hits = int(rec.get("reciprocated", 0))
    return _smoothed(hits, total, base_rate(config)), total


def handle_affinity(config, handle: str) -> float:
    return handle_stats(config, handle)[0]


def handle_fatigue(config, handle: str, window_cycles: int = 8) -> int:
    """كم مرة تفاعلنا مع هذا الحساب خلال آخر ``window_cycles`` دورة.

    الإلحاح على حساب واحد سلوك سبام حتى لو كان كل تفاعل مبرَّراً وحده؛ هذا
    العدّاد يتيح لمحرّك القرار أن يوزّع الاهتمام على حسابات مختلفة.
    """
    h = normalize_handle(handle)
    if not h:
        return 0
    now_cycle = _cycle(config)
    return sum(
        1 for e in (_ledger(config).get("entries") or {}).values()
        if e.get("handle") == h and now_cycle - int(e.get("cycle", 0)) <= window_cycles
    )


def has_evidence(config) -> bool:
    """هل تراكمت عيّنات كافية ليُعتد بإشارات التعلّم في التقييم؟"""
    led = _ledger(config)
    return sum(int(c.get("count", 0)) for c in (led.get("caps") or {}).values()) >= 5


# ─────────────────────────── التقرير ───────────────────────────
def report(config, limit: int = 10) -> Dict[str, Any]:
    """نافذة فحص: ما الذي تعلّمه الموظف عن تفاعله فعلياً."""
    led = _ledger(config)
    caps = led.get("caps") or {}
    handles = led.get("handles") or {}
    total = sum(int(c.get("count", 0)) for c in caps.values())
    hits = sum(int(c.get("reciprocated", 0)) for c in caps.values())

    by_cap = {
        cap: {
            "count": int(v.get("count", 0)),
            "reciprocated": int(v.get("reciprocated", 0)),
            "yield": round(capability_yield(config, cap), 3),
        }
        for cap, v in sorted(caps.items(), key=lambda kv: -int(kv[1].get("count", 0)))
    }
    ranked = sorted(
        handles.items(),
        key=lambda kv: (int(kv[1].get("reciprocated", 0)), int(kv[1].get("engaged", 0))),
        reverse=True,
    )
    return {
        "samples": total,
        "reciprocated": hits,
        "base_rate": round(base_rate(config), 3),
        "has_evidence": has_evidence(config),
        "by_capability": by_cap,
        "top_handles": [
            {"handle": f"@{h}", "engaged": int(v.get("engaged", 0)),
             "reciprocated": int(v.get("reciprocated", 0)),
             "affinity": round(handle_affinity(config, h), 3),
             "fatigue": handle_fatigue(config, h)}
            for h, v in ranked[:limit]
        ],
    }


def summary_line(config) -> str:
    """سطر عربي مختصر يصلح للبثّ في الشات أو حقن البرومبت."""
    rep = report(config, limit=3)
    if not rep["samples"]:
        return ""
    best: Optional[str] = None
    best_yield = -1.0
    for cap, v in (rep["by_capability"] or {}).items():
        if v["count"] >= 3 and v["yield"] > best_yield:
            best, best_yield = cap, v["yield"]
    line = (f"سجلّ التفاعل: {rep['samples']} تفاعلاً، "
            f"{rep['reciprocated']} منها أثمر استجابة "
            f"({rep['base_rate']:.0%} معدّل عام)")
    if best:
        line += f" — الأعلى عائداً: {best} ({best_yield:.0%})"
    return line
