"""
التعلّم الذاتي من الدورات: **سلوك يتغيّر بالتجربة، لا نصائح تتراكم في الموجّه**.

ما قِيس على البيانات الحية قبل الكتابة:
- @liketoday5: **13 دورة من آخر 25 بلا أي فعل**. السبب في كل مرة واحد: مهارة الاقتباس
  تقترح اقتباس محتوى سياسي معاصر، والحارس يرفضه (ثقة 0.97–0.99) لأنه خارج النمط
  التاريخي. والتقييم يكتب كل دورة «لا تكتفِ بالانتظار» — توجيه لا يمسّ السبب، فتكرّر
  الرفض نفسه عشر مرات ولم يتغيّر شيء. التعلّم كان نصاً في الموجّه فقط، والمهارات
  والمحرّك لا تقرأ الموجّه.
- الدروس المخزّنة صيغ متعددة للفكرة نفسها (5 صيغ لـ«انشر عن اليمن بعنوان صادم»)،
  بلا قياس لأي منها: هل نفع؟
- الدرجة ضجيج: 2/10 لدورة نجحت كل أفعالها، و6 ثم 1 ثم 6 — لا منحنى يمكن التعلّم منه.

ما هنا:
1. **سجل تجربة لكل «ذراع»** (نوع الفعل × المهارة التي اقترحته): قُبل ونُفّذ / فشل /
   رفضه الحارس — مع آخر أسباب الرفض. ويُضاف عائد متأخر: تفاعل المنشورات الحقيقي.
2. **قيمة متعلَّمة** (متوسط بيتا مُرجَّح بالثقة) يضيفها محرّك القرار لدرجة كل اقتراح:
   الذراع الذي يُرفض يهبط، والذي ينجح ويُثمر يصعد.
3. **تعليق مؤقت بتراجع أُسّي**: ذراع تفاعلي رُفض 3 مرات متتالية يُعلَّق 3 دورات، ثم
   6 ثم 12 (تجربة واحدة بعد كل تعليق). منشورات الموظف الأساسية لا تُعلَّق أبداً — تُخفض فقط.
4. **منحنى تعلّم**: متوسط متحرك للدرجات واتجاهه — هل يتحسّن الموظف فعلاً؟
5. **فاعلية الدروس**: كل مشكلة لها درس؛ إن اختفت 3 دورات فالدرس «نافع»، وإن تكرّرت
   5 مرات بعده فهو «غير نافع» ويُصعَّد بدل تكراره.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from .context import ExecutionContext

logger = logging.getLogger("aeos.cognition.learning")

KEY = "learning"
#: أفعال يجوز تعليقها مؤقتاً (تفاعلات مع محتوى الغير). النشر الأصلي هو هوية الموظف:
#: يُخفض ولا يُعلَّق، وإلا قد يتوقف حساب توثيق عن مهمته الأساسية بسبب رفض عابر.
SUSPENDABLE = {"quote_post", "reply_to_user", "retweet_post", "like_post", "follow_user",
               "bookmark_post"}
_REJECT_STREAK = 3
_BASE_COOLDOWN = 3
_MAX_COOLDOWN = 24
_CONF_SAMPLES = 5.0
_SCORE_WINDOW = 30
_EMA_ALPHA = 0.3


# ─────────────────────────── state ───────────────────────────
def state(config) -> Dict[str, Any]:
    flags = config.runtime_state.flags
    st = flags.get(KEY)
    if not isinstance(st, dict):
        st = flags[KEY] = {}
    st.setdefault("arms", {})
    st.setdefault("lessons", {})
    st.setdefault("scores", [])
    return st


def _cycle(config) -> int:
    return int(config.runtime_state.cycles_completed or 0)


def arm_of(action) -> str:
    """الذراع = نوع الفعل | مصدره (المهارة). المهارة المخصّصة تُميَّز بمعرّفها."""
    params = getattr(action, "params", None) or {}
    src = getattr(action, "source", "") or ""
    if params.get("_skill"):
        src = f"custom:{params['_skill']}"
    return f"{action.capability}|{src or '-'}"


def _arm(st: Dict[str, Any], arm: str) -> Dict[str, Any]:
    rec = st["arms"].get(arm)
    if not isinstance(rec, dict):
        rec = st["arms"][arm] = {"ok": 0, "fail": 0, "rej": 0, "streak_rej": 0, "strikes": 0,
                                 "suppressed_until": 0, "last_cycle": 0, "reasons": []}
    return rec


# ─────────────────────────── recording ───────────────────────────
def record(config, arm: str, outcome: str, reason: str = "") -> Dict[str, Any]:
    """سجّل نتيجة ذراع: ``ok`` | ``fail`` | ``rejected``. يعيد سجل الذراع بعد التحديث."""
    st = state(config)
    rec = _arm(st, arm)
    cycle = _cycle(config)
    rec["last_cycle"] = cycle
    if outcome == "ok":
        rec["ok"] += 1
        rec["streak_rej"] = 0
        rec["strikes"] = max(0, int(rec["strikes"]) - 1)   # النجاح يمحو الإنذارات تدريجياً
    elif outcome == "fail":
        rec["fail"] += 1
    elif outcome == "rejected":
        rec["rej"] += 1
        rec["streak_rej"] += 1
        if reason:
            rec["reasons"] = ([re.sub(r"\s+", " ", reason)[:180]] + list(rec.get("reasons") or []))[:3]
        cap = arm.split("|", 1)[0]
        if cap in SUSPENDABLE and rec["streak_rej"] >= _REJECT_STREAK:
            rec["strikes"] = int(rec["strikes"]) + 1
            rec["suppressed_until"] = cycle + min(_BASE_COOLDOWN * 2 ** (rec["strikes"] - 1),
                                                  _MAX_COOLDOWN)
            rec["streak_rej"] = 0
    return rec


def record_cycle(context: "ExecutionContext") -> Dict[str, Any]:
    """اجمع تجربة الدورة: ما رفضه الحارس + نتائج التنفيذ. يعيد ملخّصاً للدرس والتقرير."""
    cfg = context.config
    guard = context.scratch.get("guard_report") or {}
    summary = {"ok": [], "fail": [], "rejected": [], "suspended": []}
    for item in guard.get("rejected_arms") or []:
        arm, reason = item.get("arm", ""), item.get("reason", "")
        if not arm:
            continue
        before = _arm(state(cfg), arm)["suppressed_until"]
        rec = record(cfg, arm, "rejected", reason)
        summary["rejected"].append({"arm": arm, "reason": reason[:120]})
        if rec["suppressed_until"] > before:
            summary["suspended"].append({"arm": arm, "until": rec["suppressed_until"],
                                         "reasons": rec["reasons"]})
    for r in context.scratch.get("execution_results") or []:
        arm = r.get("arm")
        if not arm or r.get("status") == "skipped" or r.get("capability") == "wait":
            continue
        outcome = "ok" if r.get("success") else "fail"
        record(cfg, arm, outcome)
        summary[outcome].append(arm)
    return summary


# ─────────────────────────── policy ───────────────────────────
def _engagement_bonus(config, arm: str) -> Tuple[float, int]:
    """عائد متأخر: متوسط تفاعل منشورات هذا الذراع المقيسة ÷ متوسط كل المنشورات − 1."""
    from ..employee import strategy

    posts = [p for p in (strategy._ledger(config)["posts"] or {}).values() if p.get("measured")]
    if len(posts) < 4:
        return 0.0, 0
    mine = [int(p.get("engagement", 0) or 0) for p in posts if p.get("arm") == arm]
    if not mine:
        return 0.0, 0
    overall = sum(int(p.get("engagement", 0) or 0) for p in posts) / len(posts)
    if overall <= 0:
        return 0.0, len(mine)
    return max(-1.0, min(1.0, (sum(mine) / len(mine)) / overall - 1.0)), len(mine)


def value(config, arm: str) -> Tuple[float, Dict[str, Any]]:
    """قيمة الذراع المتعلَّمة في [-0.5, 0.5] تقريباً، مُرجَّحة بعدد العيّنات."""
    rec = state(config)["arms"].get(arm)
    if not rec:
        return 0.0, {}
    pos = float(rec["ok"])
    neg = float(rec["rej"]) + 0.5 * float(rec["fail"])
    n = pos + neg
    if n <= 0:
        return 0.0, {}
    mean = (pos + 1.0) / (n + 2.0) - 0.5
    conf = min(1.0, n / _CONF_SAMPLES)
    bonus, bn = _engagement_bonus(config, arm)
    v = mean * conf + 0.25 * bonus * min(1.0, bn / 3.0)
    return round(v, 4), {"n": int(n), "ok": int(rec["ok"]), "rej": int(rec["rej"]),
                         "fail": int(rec["fail"]), "engagement_bonus": round(bonus, 2)}


def is_suppressed(config, arm: str) -> bool:
    rec = state(config)["arms"].get(arm)
    return bool(rec) and _cycle(config) < int(rec.get("suppressed_until", 0) or 0)


def adjustment(config, action) -> Tuple[Optional[float], str]:
    """(التعديل على درجة الاقتراح أو None إن كان الذراع معلّقاً، ملاحظة للتفسير)."""
    params = action.params or {}
    if params.get("owner_directed") or action.capability == "wait":
        return 0.0, ""
    arm = arm_of(action)
    if is_suppressed(config, arm):
        return None, f"learned:suspended({arm})"
    v, meta = value(config, arm)
    if not meta:
        return 0.0, ""
    w = float((config.business_rules or {}).get("learning_weight", 0.4))
    delta = round(w * v, 3)
    return delta, (f"learned={delta:+.2f}({meta['ok']}✓/{meta['rej']}✗)" if abs(delta) >= 0.01 else "")


# ─────────────────────────── learning curve ───────────────────────────
def record_score(config, score: float) -> Dict[str, Any]:
    st = state(config)
    scores = (list(st["scores"]) + [round(float(score), 1)])[-_SCORE_WINDOW:]
    st["scores"] = scores
    ema = scores[0]
    for s in scores[1:]:
        ema = _EMA_ALPHA * s + (1 - _EMA_ALPHA) * ema
    st["ema"] = round(ema, 2)
    return curve(config)


def curve(config) -> Dict[str, Any]:
    scores = list(state(config)["scores"])
    out = {"cycles": len(scores), "ema": state(config).get("ema"), "trend": "unknown",
           "recent_avg": None, "previous_avg": None}
    if len(scores) >= 6:
        recent, prev = scores[-5:], scores[-10:-5] or scores[:-5]
        r, p = sum(recent) / len(recent), sum(prev) / len(prev)
        out.update(recent_avg=round(r, 2), previous_avg=round(p, 2),
                   trend="up" if r > p + 0.5 else "down" if r < p - 0.5 else "flat")
    return out


# ─────────────────────────── lesson efficacy ───────────────────────────
_EFFECTIVE_AFTER = 3     # دورات نظيفة متتالية بعد الدرس
_INEFFECTIVE_AFTER = 5   # مرات ظهور المشكلة بعد الدرس


def track_lessons(config, issues: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """حدّث فاعلية درس كل مشكلة. يعيد المشكلات التي صار درسها «غير نافع» (للتصعيد)."""
    st = state(config)
    lessons: Dict[str, Any] = st["lessons"]
    cycle = _cycle(config)
    present = {i["key"]: i for i in issues}
    escalate = []
    for key, issue in present.items():
        rec = lessons.get(key)
        if rec is None:
            lessons[key] = {"text": issue.get("directive", "")[:200], "since": cycle,
                            "seen_after": 0, "clean_streak": 0, "status": "active"}
            continue
        rec["seen_after"] = int(rec.get("seen_after", 0)) + 1
        rec["clean_streak"] = 0
        if rec.get("status") == "effective":       # عادت المشكلة: الدرس لم يعد كافياً
            rec["status"], rec["seen_after"] = "active", 1
        if rec["seen_after"] >= _INEFFECTIVE_AFTER and rec.get("status") != "ineffective":
            rec["status"] = "ineffective"
            escalate.append({"key": key, "lesson": rec["text"], "seen_after": rec["seen_after"]})
    for key, rec in lessons.items():
        if key in present:
            continue
        rec["clean_streak"] = int(rec.get("clean_streak", 0)) + 1
        if rec["clean_streak"] >= _EFFECTIVE_AFTER:
            rec["status"] = "effective"
    return escalate


# ─────────────────────────── lessons consolidation ───────────────────────────
def _words(text: str) -> set:
    from ..employee.persona_templates import normalize_topic_words

    return set(normalize_topic_words(text))


def near_duplicate(text: str, existing: List[Dict[str, Any]], threshold: float = 0.5) -> Optional[Dict[str, Any]]:
    """درس مكرّر بصياغة أخرى؟ (احتواء الكلمات المطبّعة ≥ العتبة)."""
    a = _words(text)
    if len(a) < 3:
        return None
    best, best_r = None, 0.0
    for m in existing:
        b = _words(m.get("content", ""))
        if len(b) < 3:
            continue
        r = len(a & b) / min(len(a), len(b))
        if r > best_r:
            best, best_r = m, r
    return best if best_r >= threshold else None


# ─────────────────────────── report ───────────────────────────
def report(config) -> Dict[str, Any]:
    st = state(config)
    cycle = _cycle(config)
    arms = []
    for arm, rec in st["arms"].items():
        v, meta = value(config, arm)
        arms.append({"arm": arm, "value": v, **meta,
                     "suspended": cycle < int(rec.get("suppressed_until", 0) or 0),
                     "suspended_until": int(rec.get("suppressed_until", 0) or 0),
                     "last_reasons": rec.get("reasons") or []})
    arms.sort(key=lambda a: a["value"])
    return {
        "cycle": cycle,
        "curve": curve(config),
        "arms": arms,
        "suspended": [a for a in arms if a["suspended"]],
        "lessons": [{"issue": k, **v} for k, v in st["lessons"].items()],
    }


def summary_line(config) -> str:
    """سطر للشات/المنسّق: المنحنى + ما تعلّمه (معلّق/صاعد)."""
    rep = report(config)
    c = rep["curve"]
    arrow = {"up": "↑ يتحسّن", "down": "↓ يتراجع", "flat": "→ مستقر"}.get(c["trend"], "")
    bits = []
    if c.get("ema") is not None:
        bits.append(f"منحنى الأداء {c['ema']}/10 {arrow}".strip())
    if rep["suspended"]:
        bits.append("أوقفت مؤقتاً: " + "، ".join(a["arm"] for a in rep["suspended"][:3]))
    best = [a for a in rep["arms"] if a["value"] > 0.05][-2:]
    if best:
        bits.append("الأنجح: " + "، ".join(a["arm"] for a in best))
    eff = [l["issue"] for l in rep["lessons"] if l.get("status") == "effective"]
    if eff:
        bits.append(f"دروس نفعت: {len(eff)}")
    return " · ".join(bits)
