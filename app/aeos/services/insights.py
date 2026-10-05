"""
Employee Insights service.

Turns an AI Employee's raw per-cycle audit log (``ExecutionLogRecord``) into
human-facing answers for the chat:

- :func:`aggregate_stats`   engagement totals (posts, likes, replies, ...).
- :func:`recent_contents`   the latest things the employee published / replied.
- :func:`build_suggestions` next-step ideas (LLM-grounded, heuristic fallback).
- :func:`answer_question`   free-form discussion grounded in the employee data.

Everything is read-only; it never mutates the employee. The chat layer and the
REST API both call into here so behaviour stays consistent.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.db.database import SessionLocal
from app.db.models import ExecutionLogRecord

logger = logging.getLogger("aeos.insights")

# capability -> human label (Arabic)
_CAP_LABELS = {
    "publish_content": "منشورات",
    "reply_to_user": "ردود",
    "retweet_post": "إعادات نشر",
    "like_post": "إعجابات",
    "follow_user": "متابعات",
    "research_topic": "أبحاث",
    "store_knowledge": "معلومات محفوظة",
}


def _loads(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw) if raw else default
    except Exception:  # noqa: BLE001
        return default


def _iter_results(outcome: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = outcome.get("results")
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    return []


def _fetch_rows(employee_id: str, limit: int = 200) -> List[ExecutionLogRecord]:
    db = SessionLocal()
    try:
        return (
            db.query(ExecutionLogRecord)
            .filter(ExecutionLogRecord.employee_id == employee_id)
            .order_by(ExecutionLogRecord.id.desc())
            .limit(max(1, min(int(limit), 500)))
            .all()
        )
    finally:
        db.close()


def aggregate_stats(employee_id: str, limit: int = 200) -> Dict[str, Any]:
    """Aggregate engagement counters across the employee's recent cycles."""
    rows = _fetch_rows(employee_id, limit)

    counts: Dict[str, int] = {k: 0 for k in _CAP_LABELS}
    action_ok = 0
    action_fail = 0
    cycles = len(rows)
    active_cycles = 0            # cycles that took at least one action
    last_cycle_at: Optional[str] = None
    topics: Dict[str, int] = {}

    for r in rows:
        if last_cycle_at is None and r.created_at:
            last_cycle_at = r.created_at.isoformat()
        outcome = _loads(r.outcome, {})
        if isinstance(outcome, list):
            outcome = {"results": outcome}
        results = _iter_results(outcome)
        if results:
            active_cycles += 1
        for res in results:
            cap = res.get("capability")
            ok = bool(res.get("success"))
            if ok:
                action_ok += 1
            else:
                action_fail += 1
            if cap in counts and ok:
                counts[cap] += 1
        # collect top decision topics/rationale for a rough interest picture
        for d in _loads(r.decision, []) or []:
            if isinstance(d, dict):
                topic = (d.get("topic") or d.get("capability") or "").strip()
                if topic:
                    topics[topic] = topics.get(topic, 0) + 1

    total_actions = action_ok + action_fail
    success_rate = round((action_ok / total_actions) * 100) if total_actions else 0
    top_topics = sorted(topics.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return {
        "employee_id": employee_id,
        "cycles_analyzed": cycles,
        "active_cycles": active_cycles,
        "actions_total": total_actions,
        "actions_ok": action_ok,
        "actions_failed": action_fail,
        "success_rate": success_rate,
        "counts": counts,
        "last_cycle_at": last_cycle_at,
        "top_topics": [{"topic": t, "count": c} for t, c in top_topics],
    }


def recent_contents(employee_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Return the latest published posts / replies with links when available."""
    rows = _fetch_rows(employee_id, 200)
    items: List[Dict[str, Any]] = []
    for r in rows:
        outcome = _loads(r.outcome, {})
        if isinstance(outcome, list):
            outcome = {"results": outcome}
        for res in _iter_results(outcome):
            if not res.get("success"):
                continue
            cap = res.get("capability")
            out = res.get("output") or {}
            if cap == "publish_content":
                items.append({
                    "kind": "post",
                    "text": (out.get("text") or "").strip(),
                    "url": out.get("tweet_url") or out.get("url"),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            elif cap == "reply_to_user":
                items.append({
                    "kind": "reply",
                    "text": (out.get("reply_text") or "").strip(),
                    "url": out.get("tweet_url") or out.get("url"),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                })
            if len(items) >= limit:
                return items
    return items


# ─────────────────────────── formatting ───────────────────────────
def format_stats(cfg, stats: Dict[str, Any]) -> str:
    account = getattr(cfg, "account_username", "") or "الموظف"
    counts = stats["counts"]
    lines = [f"📊 إحصائيات الموظف @{account} (آخر {stats['cycles_analyzed']} دورة):", ""]
    lines.append(f"• الدورات النشطة: {stats['active_cycles']} من {stats['cycles_analyzed']}")
    lines.append(
        f"• إجمالي الإجراءات: {stats['actions_total']} "
        f"(✓ {stats['actions_ok']} / ✗ {stats['actions_failed']}) — نسبة النجاح {stats['success_rate']}%"
    )
    detail = "، ".join(
        f"{label}: {counts.get(cap, 0)}"
        for cap, label in _CAP_LABELS.items() if counts.get(cap, 0)
    )
    lines.append(f"• التفاصيل: {detail or 'لا توجد إجراءات بعد'}")
    if stats.get("last_cycle_at"):
        lines.append(f"• آخر دورة: {stats['last_cycle_at'][:16].replace('T', ' ')}")
    return "\n".join(lines)


def _fmt_dt(value: Any) -> str:
    """صيغة تاريخ مقروءة 'YYYY-MM-DD HH:MM' من ISO string، أو '' إن تعذّر."""
    s = str(value or "")
    return s[:16].replace("T", " ") if len(s) >= 16 else ""


def _format_content_block(items: List[Dict[str, Any]]) -> str:
    """رتّب عنصراً واحداً (تغريدة/رد) في سطر مرقّم مع رابط وتاريخ إن وُجدا."""
    lines: List[str] = []
    for idx, it in enumerate(items, 1):
        text = (it.get("text") or "").replace("\n", " ").strip()[:200] or "(بدون نص)"
        lines.append(f"**{idx}.** «{text}»")
        meta: List[str] = []
        if it.get("url"):
            meta.append(f"🔗 [عرض]({it['url']})")
        dt = _fmt_dt(it.get("created_at"))
        if dt:
            meta.append(f"🕐 {dt}")
        if meta:
            lines.append("   " + " · ".join(meta))
        lines.append("")  # سطر فاصل بين العناصر
    return "\n".join(lines).rstrip()


def format_contents(cfg, items: List[Dict[str, Any]]) -> str:
    account = getattr(cfg, "account_username", "") or "الموظف"
    if not items:
        return f"📝 لم ينشر الموظف @{account} أي محتوى بعد."

    posts = [it for it in items if it.get("kind") == "post"]
    replies = [it for it in items if it.get("kind") == "reply"]

    lines = [
        f"📝 **آخر محتويات الموظف @{account}**",
        f"إجمالي {len(items)} · 🐦 {len(posts)} تغريدة · ↩️ {len(replies)} رد",
    ]
    if posts:
        lines += ["", f"🐦 **التغريدات ({len(posts)})**", "", _format_content_block(posts)]
    if replies:
        lines += ["", f"↩️ **الردود ({len(replies)})**", "", _format_content_block(replies)]
    return "\n".join(lines)


# ─────────────────────────── LLM-backed ───────────────────────────
def _persona_brief(cfg) -> str:
    p = getattr(cfg, "persona", None)
    if not p:
        return ""
    interests = "، ".join(getattr(p, "interests", []) or [])
    return (
        f"الاسم: {getattr(p, 'display_name', '')}؛ الدور: {getattr(p, 'role', '')}؛ "
        f"الأسلوب: {getattr(p, 'tone', '')}؛ اللهجة: {getattr(p, 'dialect', '') or getattr(p, 'language', '')}؛ "
        f"الاهتمامات: {interests or 'غير محددة'}"
    )


def build_suggestions(cfg, stats: Dict[str, Any], llm=None) -> str:
    account = getattr(cfg, "account_username", "") or "الموظف"
    persona = _persona_brief(cfg)
    counts = stats["counts"]

    if llm and getattr(llm, "available", False):
        try:
            prompt = (
                f"أنت الموظف الذكي الذي يدير حساب X: @{account}.\n"
                f"شخصيتك: {persona}.\n"
                f"إحصائيات أدائك الأخيرة: {json.dumps(counts, ensure_ascii=False)}، "
                f"نسبة النجاح {stats['success_rate']}%، دورات نشطة {stats['active_cycles']}/{stats['cycles_analyzed']}.\n"
                "قدّم 3 إلى 5 اقتراحات عملية وموجزة لتحسين المحتوى والتفاعل ضمن تخصصك فقط، "
                "بصيغة نقاط عربية واضحة، دون مقدمات."
            )
            out = llm.complete(
                system="أنت مساعد استراتيجية محتوى موجز وعملي. اكتب بالعربية فقط.",
                prompt=prompt, temperature=0.7, max_tokens=400,
            )
            if out and out.strip():
                return f"💡 اقتراحات الموظف @{account}:\n\n{out.strip()}"
        except Exception:  # noqa: BLE001
            logger.exception("suggestions LLM failed")

    # heuristic fallback
    tips = []
    if counts.get("publish_content", 0) == 0:
        tips.append("- ابدأ بنشر تغريدة تحليلية واحدة ضمن تخصصك لرفع الظهور.")
    if counts.get("reply_to_user", 0) < counts.get("publish_content", 0):
        tips.append("- زد الردود المختصرة على النقاشات النشطة لرفع التفاعل.")
    if stats["success_rate"] < 70 and stats["actions_total"]:
        tips.append("- بعض الإجراءات تفشل؛ تحقّق من صلاحية جلسة الحساب (الكوكيز).")
    if not tips:
        tips.append("- حافظ على وتيرة النشر ونوّع بين التحليل والأخبار والردود ضمن مجالك.")
    return f"💡 اقتراحات الموظف @{account}:\n\n" + "\n".join(tips)


def answer_question(cfg, question: str, llm=None, employee_id: str = "",
                    extra_context: str = "") -> str:
    """Free-form discussion with the employee, grounded in its data.

    ``extra_context`` may carry recently-fetched live data (news/trends/timeline/
    top posts) so the employee can discuss what it just showed the user."""
    account = getattr(cfg, "account_username", "") or "الموظف"
    eid = employee_id or getattr(cfg, "id", "")
    stats = aggregate_stats(eid) if eid else {}
    contents = recent_contents(eid, 5) if eid else []

    if llm and getattr(llm, "available", False):
        try:
            content_lines = "\n".join(
                f"- {(c.get('text') or '')[:120]}" for c in contents
            ) or "لا يوجد محتوى منشور بعد."
            fetched_block = (
                f"\nآخر بيانات جلبتها للتو (استند إليها في النقاش):\n{extra_context[:1500]}\n"
                if (extra_context or "").strip() else ""
            )
            # الذاكرة: كانت كل رسالة تُجاب كأنها الأولى («وضّح أكثر» بلا معنى)، ولا
            # يتذكّر الموظف ما قاله له مالكه سابقاً — الآن سياق المحادثة + ذاكرته الطويلة
            memory_block = ""
            try:
                from ..memory import agent_memory as AM

                short = AM.context_as_text(AM.current_chat_context(question))
                long_ = AM.employee_brief(eid, question) if eid else ""
                memory_block = "\n".join(x for x in (long_, short) if x)
            except Exception:  # noqa: BLE001
                logger.debug("employee chat memory unavailable", exc_info=True)
            prompt = (
                f"أنت الموظف الذكي الذي يدير حساب X: @{account}. تحدّث بصيغة المتكلم.\n"
                f"شخصيتك: {_persona_brief(cfg)}.\n"
                f"ملخص أدائك: {json.dumps(stats.get('counts', {}), ensure_ascii=False)}، "
                f"نسبة نجاح {stats.get('success_rate', 0)}%.\n"
                f"آخر ما نشرته:\n{content_lines}\n"
                f"{fetched_block}\n"
                + (f"{memory_block}\n\n" if memory_block else "")
                + f"رسالة المالك الآن: {question}\n"
                "أجب بإيجاز ووضوح بالعربية، ملتزماً بتخصصك، واعتماداً على بياناتك وسياق "
                "المحادثة أعلاه (إن أشار لشيء سابق فارجع إليه)."
            )
            out = llm.complete(
                system="أنت موظف ذكي يدير حساب تواصل اجتماعي ويناقش مالكه بوضوح وأمانة. اكتب بالعربية.",
                prompt=prompt, temperature=0.6, max_tokens=500,
            )
            if out and out.strip():
                return out.strip()
        except Exception:  # noqa: BLE001
            logger.exception("answer_question LLM failed")

    # fallback: return stats + contents summary
    return format_stats(cfg, stats) + "\n\n" + format_contents(cfg, contents)
