"""
حارس التقييم والتحسين الذاتي — يقيّم مخرجات كل دورة ويصحّح تعليمات الدورة التالية.

المشكلة: الموظف كان ينفّذ دورةً بعد دورة بلا أي حكم على جودة ما أنتجه. الدرس
المستفاد (`LearnPhase`) مجرد سجلّ («نفّذ كذا، نجح كذا») لا نقد، فلا يتحسّن أبداً:
يكرّر نفس الصياغة، ونفس أخطاء نطاق التخصص، ونفس الهاشتاقات الزائدة، ولا يعرف
أن حارس الشخصية يرفض نصف اقتراحاته.

الحل هنا طبقتان لا واحدة:

1. **فحوص حتمية (`findings`)** — أرقام لا آراء: نسبة رفض الحارس، نسبة الفشل،
   طول المنشور وعدد هاشتاقاته، نسبة تشابه موضوعه مع منشوراته السابقة، هل ردّ
   على من خاطبه، هل التزم بخطة المحتوى، واتجاه التفاعل (صاعد/هابط). تعمل بلا
   LLM وتُنتج توجيهات تصحيحية مباشرة.
2. **نقد LLM مُهيكل** — يقرأ الأرقام **والنصوص الفعلية** التي نُشرت، فيحكم على
   الجودة ويكتب توجيهات عملية للدورة القادمة.

التوجيهات تُخزَّن في حالة الموظف وتُحقن تلقائياً في ``persona_prompt`` للدورة
التالية، فتتحكّم بصياغة كل منشور ورد. ومع تكرار المشكلة نفسها ترتفع صرامة
النبرة (``recurring_issues``) حتى تُعالج جذرياً.

مبدأ صارم: التوجيهات **تُهذّب** الأداء ولا تُخالف أبداً تعليمات المستخدم الثابتة
ولا محظورات الشخصية — هي طبقة تحسين فوق الهوية، لا بديلة عنها.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List

from ..employee.persona_templates import normalize_topic_words, topic_overlap_ratio

if TYPE_CHECKING:
    from .context import ExecutionContext

logger = logging.getLogger("aeos.cognition.critique")

_CRITIQUE_KEY = "cycle_critique"
_HISTORY_KEY = "critique_history"
_RECURRING_KEY = "recurring_issues"
_MAX_HISTORY = 12
_MAX_DIRECTIVES = 5
_MAX_AVOID = 4
_MAX_RECURRING_SHOWN = 3
#: عدد مرات ظهور المشكلة قبل أن تُعتبر "متكرّرة" فتُشدَّد نبرتها
_RECURRING_THRESHOLD = 3

_HASHTAG_RE = re.compile(r"#\S+")
_PUBLISH_CAPS = ("publish_content", "publish_thread", "schedule_post")


def _valid_format_ids() -> List[str]:
    from ..employee.strategy import CONTENT_FORMATS
    return list(CONTENT_FORMATS)


def suggested_format(config) -> str:
    """الصيغة التي اقترحها الحارس للدورة القادمة (معرّف صالح أو فراغ)."""
    focus = (config.runtime_state.flags.get(_CRITIQUE_KEY) or {}).get("focus") or {}
    fmt = str(focus.get("format", "")).strip()
    return fmt if fmt in _valid_format_ids() else ""


# ─────────────────────────── الفحوص الحتمية ───────────────────────────
def _published_texts(context: "ExecutionContext") -> List[str]:
    texts: List[str] = []
    for r in context.scratch.get("execution_results", []) or []:
        if not (r.get("success") and r.get("capability") in _PUBLISH_CAPS):
            continue
        out = r.get("output") if isinstance(r.get("output"), dict) else {}
        parts = out.get("texts")
        if isinstance(parts, list) and parts:
            texts.extend(str(p) for p in parts)
        elif out.get("text"):
            texts.append(str(out["text"]))
    return texts


def _engagement_trend(config) -> Dict[str, Any]:
    """اتجاه التفاعل: متوسط أحدث 5 منشورات مقيسة مقابل الـ5 التي قبلها."""
    from ..employee import strategy

    posts = [p for p in (strategy._ledger(config)["posts"] or {}).values()
             if p.get("measured")]
    posts.sort(key=strategy._order_key, reverse=True)
    recent = [int(p.get("engagement", 0) or 0) for p in posts[:5]]
    older = [int(p.get("engagement", 0) or 0) for p in posts[5:10]]
    if not recent or not older:
        return {"direction": "unknown", "recent_avg": None, "previous_avg": None}
    r_avg = sum(recent) / len(recent)
    o_avg = sum(older) / len(older)
    if o_avg <= 0:
        direction = "up" if r_avg > 0 else "flat"
    elif r_avg >= o_avg * 1.15:
        direction = "up"
    elif r_avg <= o_avg * 0.85:
        direction = "down"
    else:
        direction = "flat"
    return {"direction": direction, "recent_avg": round(r_avg, 1),
            "previous_avg": round(o_avg, 1)}


def deterministic_findings(context: "ExecutionContext") -> Dict[str, Any]:
    """أرقام الدورة الحقيقية — أساس أي حكم على الجودة (تعمل بلا LLM)."""
    results = context.scratch.get("execution_results", []) or []
    guard = context.scratch.get("guard_report") or {}
    reviewed = int(guard.get("reviewed", 0) or 0)
    rejected = len(guard.get("rejected") or [])
    successes = sum(1 for r in results if r.get("success"))
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    attempted = len(results) - skipped
    failures = attempted - successes
    texts = _published_texts(context)
    joined = " ".join(texts)

    # تكرار الموضوع: أعلى تشابه بين ما نُشر الآن وما نُشر سابقاً
    repetition = 0.0
    if joined:
        now_words = normalize_topic_words(joined)
        for prev in (context.scratch.get("recent_posts") or [])[:8]:
            repetition = max(repetition,
                             topic_overlap_ratio(now_words, normalize_topic_words(str(prev))))

    mention_list = context.scratch.get("mentions") or []
    mention_urls = {(m.get("url") or "").strip() for m in mention_list if m.get("url")}
    mentions = len(mention_list)
    # «تجاهل من خاطبك» يُقاس على المنشنات **المعلّقة** فقط: قائمة المنشنات تحوي ما
    # رُدّ عليه في دورات سابقة، فكانت المشكلة تُسجَّل كل دورة رغم أنه لا أحد ينتظر
    # رداً (قيس: ignored_mentions تكرّرت 90 مرة لموظف يردّ فعلاً).
    pending = _pending_mentions(context, mention_list)
    mention_replies = sum(
        1 for r in results
        if r.get("success") and r.get("capability") == "reply_to_user"
        and (r.get("target") or "").strip() in mention_urls
    )
    plan = context.scratch.get("content_plan") or {}
    published_caps = [r.get("capability") for r in results
                      if r.get("success") and r.get("capability") in _PUBLISH_CAPS]
    # أجزاء الثريد أقصر بطبيعتها من التغريدة المستقلة (كل جزء فكرة واحدة)، فحدّ
    # "المحتوى الهزيل" يجب أن يختلف — وإلا اتُّهم كل ثريد جيد بالهزال.
    is_thread = "publish_thread" in published_caps or len(texts) > 1
    thin_threshold = 55 if is_thread else 80

    findings: Dict[str, Any] = {
        "actions": len(results),
        "successes": successes,
        "failures": failures,
        "skipped": skipped,
        "failure_rate": round(failures / attempted, 2) if attempted else 0.0,
        "guard_reviewed": reviewed,
        "guard_rejected": rejected,
        "guard_rejection_rate": round(rejected / reviewed, 2) if reviewed else 0.0,
        "guard_reasons": [str(v)[:160] for v in (guard.get("reasons") or {}).values() if v][:4],
        "published_count": len(published_caps),
        "published_capabilities": published_caps,
        "published_chars": len(joined),
        "is_thread": is_thread,
        "shortest_part": min((len(t) for t in texts), default=0),
        "thin_threshold": thin_threshold,
        "hashtags": len(_HASHTAG_RE.findall(joined)),
        "truncated_ending": bool(joined) and joined.rstrip().endswith(("…", "...")),
        "topic_repetition": round(repetition, 2),
        "mentions_seen": mentions,
        "mentions_pending": pending,
        "mention_replies": mention_replies,
        "idle": not results or all(r.get("capability") == "wait" or r.get("status") == "skipped"
                                   for r in results),
        "guard_blocked_reasons": [str(x.get("reason") or "")[:160]
                                  for x in guard.get("rejected_arms") or []][:3],
        "suspended_arms": list(context.scratch.get("suspended_arms") or []),
        "plan_pillar": plan.get("pillar", ""),
        "plan_format": plan.get("format", ""),
        "plan_followed": (not plan or not published_caps
                          or (plan.get("parts", 1) > 1) == ("publish_thread" in published_caps)),
        "emotion_applied": bool(context.scratch.get("emotion_state")),
        "graph_facts_used": len(context.scratch.get("graph_facts") or []),
        "engagement_trend": _engagement_trend(context.config),
    }
    findings["idle_reason"] = _idle_reason(context, findings) if findings["idle"] else ""
    findings["issues"] = _rule_based_issues(findings)
    # مخالفات خوارزمية X المقيسة على النصوص المنشورة فعلاً (روابط في المتن،
    # حشو إيموجي، أسئلة قوالب) — نفس عقد issues فتتحول توجيهات تلقائياً.
    if texts:
        from ..employee import x_algorithm

        findings["issues"].extend(x_algorithm.virality_findings(texts))
    return findings


def _pending_mentions(context: "ExecutionContext", mentions: List[dict]) -> int:
    """منشنات تنتظر رداً فعلاً: لم يُتفاعل معها، ليست سبام، ولا تخالف المحظورات."""
    if not mentions:
        return 0
    from ..skills.builtin import _engaged_urls, _is_ad_or_spam_text, _violates_persona_donts

    engaged = _engaged_urls(context)
    n = 0
    for m in mentions:
        url, text = (m.get("url") or "").strip(), (m.get("text") or "").strip()
        if not url or url in engaged or _is_ad_or_spam_text(text) or _violates_persona_donts(text, context):
            continue
        n += 1
    return n


def _idle_reason(context: "ExecutionContext", f: Dict[str, Any]) -> str:
    """لماذا مرّت الدورة بلا فعل؟ التشخيص يحدّد العلاج، لا «لا تنتظر» لكل حالة.

    - ``guard_blocked``: كان هناك اختيار لكن الحارس رفضه (ورفض البدلاء).
    - ``budget_done``: الحصص اليومية مكتملة — هذا نجاح لا تقصير، ولا يُعاقَب.
    - ``no_opportunity``: لا اقتراح حقيقي أصلاً — مصادر أو تخصص يحتاج توسيعاً.
    """
    if f["guard_rejected"] or f.get("guard_blocked_reasons"):
        return "guard_blocked"
    from ..decision.engine import ENGAGEMENT_BUDGETS

    rules = context.config.business_rules or {}
    counters = context.config.runtime_state.counters
    posts_done = int(counters.get("posts_today", 0)) >= int(rules.get("daily_post_target", 1))
    eng_done = all(int(counters.get(c, 0)) >= int(rules.get(r, d))
                   for c, r, d in ENGAGEMENT_BUDGETS.values() if int(rules.get(r, d)) > 0)
    if posts_done and eng_done:
        return "budget_done"
    return "no_opportunity"


#: قواعد حتمية: (مفتاح المشكلة، شرط، التوجيه التصحيحي)
def _rule_based_issues(f: Dict[str, Any]) -> List[Dict[str, str]]:
    """مشكلات تُكتشف بالأرقام وحدها + توجيه تصحيحي لكل منها."""
    issues: List[Dict[str, str]] = []

    def _add(key: str, problem: str, directive: str) -> None:
        issues.append({"key": key, "problem": problem, "directive": directive})

    if f["guard_rejection_rate"] >= 0.4 and f["guard_reviewed"] >= 2:
        _add("guard_rejections",
             f"حارس الشخصية رفض {f['guard_rejected']} من {f['guard_reviewed']} إجراءات",
             "قبل اقتراح أي منشور أو تفاعل، تأكّد أن موضوعه داخل تخصصك حرفياً "
             "وأنه لا يخالف محظوراتك؛ اترك ما تشكّ فيه.")
    if f["failure_rate"] >= 0.5 and f["actions"] >= 2:
        _add("execution_failures",
             f"فشل {f['failures']} من {f['actions']} إجراءات",
             "فضّل الأهداف المؤكّدة (روابط رأيتها فعلاً) وتجنّب الإجراءات التي "
             "فشلت في هذه الدورة.")
    reason = f.get("idle_reason", "")
    if f["idle"] and reason == "guard_blocked":
        why = (f.get("guard_blocked_reasons") or [""])[0]
        _add("blocked_by_guard",
             "الحارس رفض كل اختياراتك هذه الدورة" + (f": {why[:120]}" if why else ""),
             "لا تقترح هذا النوع من المحتوى مجدداً — اختر ما يطابق نمط حسابك حرفياً"
             + (f" (سبب الرفض: {why[:90]})" if why else "") + ".")
    elif f["idle"] and reason == "no_opportunity":
        _add("idle_cycle", "دورة بلا أي اقتراح صالح ضمن تخصصك",
             "لا تكتفِ بالانتظار: اختر أفضل فرصة متاحة ضمن تخصصك ونفّذها، "
             "ولو كانت تفاعلاً واحداً صغيراً.")
    if f["published_count"] and f["hashtags"] > 1:
        _add("too_many_hashtags", f"استخدمت {f['hashtags']} هاشتاقات",
             "هاشتاق واحد كحد أقصى في المنشور، أو بلا هاشتاق إطلاقاً.")
    if f["published_count"] and f["truncated_ending"]:
        _add("truncated_post", "المنشور انتهى مقطوعاً بعلامة حذف",
             "أنهِ المنشور بجملة تامة داخل حدّ الأحرف؛ لا تتركه مقطوعاً.")
    if f["published_count"] and 0 < f["shortest_part"] < f.get("thin_threshold", 80):
        _add("thin_content",
             f"أقصر جزء منشور كان {f['shortest_part']} حرفاً فقط "
             f"(الحد {f.get('thin_threshold', 80)})",
             "أضف مضموناً حقيقياً: معلومة أو رقم أو موقف واضح، لا جملة عابرة.")
    if f["topic_repetition"] >= 0.5:
        _add("topic_repetition",
             f"تشابه موضوعك مع منشور سابق بنسبة {int(f['topic_repetition'] * 100)}%",
             "غيّر الموضوع أو قدّم زاوية جديدة تماماً؛ لا تُعِد ما نشرته سابقاً "
             "بصياغة أخرى.")
    if f.get("mentions_pending", f["mentions_seen"]) and not f["mention_replies"]:
        _add("ignored_mentions",
             f"{f.get('mentions_pending', f['mentions_seen'])} منشنات تنتظر ردك ولم تُجب أحداً",
             "أجب أولاً على من خاطبك مباشرةً — الردّ على الجمهور أولوية على "
             "النشر الجديد.")
    if not f["plan_followed"]:
        _add("plan_ignored",
             f"خطة الدورة كانت «{f['plan_format']}» ولم تُنفّذ بصيغتها",
             "التزم بصيغة المحتوى المحدّدة في خطة الدورة (ثريد يعني عدة تغريدات "
             "مترابطة، لا تغريدة واحدة).")
    trend = f["engagement_trend"]
    if trend.get("direction") == "down":
        _add("engagement_down",
             f"تفاعلك هابط ({trend['previous_avg']} ← {trend['recent_avg']})",
             "غيّر الصيغة أو المحور: ما تفعله الآن يفقد التفاعل. ابدأ بخطّاف "
             "أقوى وفكرة أكثر تحديداً.")
    if f["published_count"] and not f["graph_facts_used"]:
        _add("memory_unused", "لم تستفد من أي حقيقة من ذاكرتك المعرفية",
             "استند إلى ما تعرفه عن جمهورك وتجاربك السابقة عند اختيار الموضوع "
             "والنبرة.")
    return issues


# ─────────────────────────── نقد LLM ───────────────────────────
def build_critique(context: "ExecutionContext") -> Dict[str, Any]:
    """قيّم الدورة وأنتج توجيهات الدورة القادمة (يعمل بلا LLM أيضاً)."""
    findings = deterministic_findings(context)
    issues = findings["issues"]
    texts = _published_texts(context)

    critique: Dict[str, Any] = {
        "cycle": int(context.config.runtime_state.cycles_completed),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "findings": findings,
        "issue_keys": [i["key"] for i in issues],
        "score": _heuristic_score(findings),
        "verdict": "",
        "strengths": [],
        "weaknesses": [i["problem"] for i in issues][:_MAX_AVOID],
        "directives": [i["directive"] for i in issues][:_MAX_DIRECTIVES],
        "avoid": [],
        "focus": {},
        "source": "rules",
    }

    llm = getattr(context, "llm", None)
    if llm is None or not llm.available:
        critique["verdict"] = (
            "تقييم حتمي بلا نموذج لغوي: "
            + ("لا مشكلات مكتشفة." if not issues
               else "؛ ".join(i["problem"] for i in issues[:3]))
        )
        return critique

    facts_lines = [
        f"- إجراءات: {findings['actions']} (نجح {findings['successes']}، فشل {findings['failures']})",
        f"- رفض الحارس: {findings['guard_rejected']}/{findings['guard_reviewed']}",
        f"- منشورات: {findings['published_count']} | أحرف: {findings['published_chars']} "
        f"| هاشتاقات: {findings['hashtags']}",
        f"- تشابه الموضوع مع السابق: {int(findings['topic_repetition'] * 100)}%",
        f"- منشنات تنتظر رداً: {findings['mentions_pending']} | رددتَ على: {findings['mention_replies']}",
        f"- سبب الخمول (إن وُجد): {findings['idle_reason'] or 'لا خمول'}"
        + (" — اكتمال الحصص ليس تقصيراً" if findings["idle_reason"] == "budget_done" else ""),
        f"- خطة الدورة: {findings['plan_pillar']} / {findings['plan_format']} "
        f"(نُفّذت: {'نعم' if findings['plan_followed'] else 'لا'})",
        f"- اتجاه التفاعل: {findings['engagement_trend']}",
    ]
    if findings["guard_reasons"]:
        facts_lines.append("- أسباب رفض الحارس: " + " | ".join(findings["guard_reasons"]))
    if issues:
        facts_lines.append("- مشكلات كشفها الفحص الحتمي: "
                           + " | ".join(i["problem"] for i in issues))

    published_block = "\n".join(f"  [{i + 1}] {t}" for i, t in enumerate(texts[:5])) \
        or "  (لم يُنشر شيء هذه الدورة)"
    replies_block = "\n".join(
        f"  - {(r.get('output') or {}).get('reply_text', '')[:200]}"
        for r in (context.scratch.get("execution_results") or [])
        if r.get("success") and r.get("capability") == "reply_to_user"
    ) or "  (لا ردود)"

    recurring = _recurring_summary(context.config)
    # هوية نظيفة بدل persona_prompt(): البرومبت الكامل يحتوي توجيهات الدورة
    # السابقة نفسها، فكان النموذج يعيد صياغتها كتوجيهات جديدة إلى الأبد —
    # حلقة صدى جعلت توجيهاً باطلاً (هاشتاق يخالف محظورات الشخصية) يتوارث
    # عبر الدورات رغم زوال مصدره. الحكم يُبنى على الهوية الثابتة وأرقام
    # الدورة فقط.
    p = context.config.persona
    identity = "\n".join(part for part in [
        f"الهوية: {p.display_name or context.config.account_username} — {p.role}. "
        f"اللغة: {p.language}.",
        f"🔒 تعليمات المستخدم الثابتة (المرجع الأعلى): {p.instructions}"
        if p.instructions else "",
        ("التخصص الحصري: " + "، ".join(p.interests))
        if getattr(p, "interests", None) else "",
        ("افعل دائماً: " + "؛ ".join(p.dos)) if p.dos else "",
        ("لا تفعل أبداً: " + "؛ ".join(p.donts)) if p.donts else "",
    ] if part)
    data = llm.complete_json(
        system=(
            "أنت مدير تحرير صارم ومنصف يراجع أداء صانع محتوى على منصة X بعد كل "
            "دورة عمل. حكمك مبني على الأرقام والنصوص الفعلية أمامك، لا على "
            "التخمين. مهمتك: تقييم صادق + توجيهات عملية قابلة للتنفيذ في الدورة "
            "القادمة. أجب JSON فقط بالمفاتيح: "
            '{"score": رقم من 0 إلى 10, "verdict": "حكم موجز بسطر واحد", '
            '"strengths": ["ما أُحسن فعلاً"], "weaknesses": ["الخلل الحقيقي"], '
            '"directives": ["توجيه أمري محدّد للدورة القادمة"], '
            '"avoid": ["ما يجب تجنّبه حرفياً"], '
            '"focus": {"pillar": "محور مقترح", "format": "معرّف صيغة"}}'
            " — و\"format\" يجب أن يكون **أحد هذه المعرّفات حرفياً** وإلا فاتركه فارغاً: "
            + ", ".join(_valid_format_ids())
        ),
        prompt=(
            f"{identity}\n\n"
            f"أرقام الدورة:\n" + "\n".join(facts_lines) + "\n\n"
            f"ما نشره فعلاً:\n{published_block}\n\n"
            f"ردوده:\n{replies_block}\n\n"
            f"{recurring}\n"
            "قيّم جودة المحتوى نفسه (قوة الخطّاف، تحديد الفكرة، القيمة المضافة، "
            "الطبيعية مقابل النبرة الآلية، الالتزام بالتخصص واللهجة)، ثم اكتب "
            "توجيهات للدورة القادمة:\n"
            "- كل توجيه أمرٌ محدّد قابل للتنفيذ (لا نصائح عامة مثل «كن أفضل»).\n"
            "- خمسة توجيهات كحد أقصى، كل واحد أقل من 160 حرفاً.\n"
            "- ممنوع أن يخالف أي توجيه تعليمات المستخدم الثابتة أو محظورات "
            "الشخصية أو نطاق التخصص.\n"
            "- إن كان الأداء جيداً فقل ذلك بوضوح ولا تخترع مشكلات."
        ),
        temperature=0.25,
    ) or {}

    if isinstance(data, dict) and data:
        rule_score = critique["score"]
        critique.update(_clean_llm_critique(data, critique))
        critique["source"] = "llm+rules"
        # درجة النموذج وحدها ضجيج (قيس: 2/10 لدورة نجحت كل أفعالها، ثم 6 ثم 1 ثم 6)،
        # فلا يُبنى عليها منحنى تعلّم. الدرجة النهائية مزيج ثابت تهيمن عليه الأرقام.
        critique["llm_score"], critique["rule_score"] = critique["score"], rule_score
        critique["score"] = round(0.6 * rule_score + 0.4 * critique["llm_score"], 1)
    return critique


def _clean_llm_critique(data: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """نظّف مخرجات النموذج واحفظ توجيهات القواعد الحتمية دائماً (لا تُلغى)."""
    def _lines(key: str, cap: int, limit: int = 160) -> List[str]:
        out = []
        for item in (data.get(key) or []):
            text = re.sub(r"\s+", " ", str(item)).strip()[:limit]
            if text:
                out.append(text)
        return out[:cap]

    try:
        score = float(data.get("score", base["score"]))
    except (TypeError, ValueError):
        score = base["score"]

    # التوجيهات الحتمية أولاً (مبنيّة على أرقام مؤكّدة) ثم توجيهات النموذج
    directives = base["directives"] + [
        d for d in _lines("directives", _MAX_DIRECTIVES)
        if d not in base["directives"]
    ]
    # الصيغة المقترحة تُقبل فقط إن كانت معرّفاً حقيقياً — نصّ حرّ لا يُغيّر شيئاً
    # في الاستراتيجية، فقبولُه يعني بيانات ميتة توهم بأن الحلقة مغلقة.
    focus_raw = data.get("focus") if isinstance(data.get("focus"), dict) else {}
    focus_format = str(focus_raw.get("format", "")).strip()
    if focus_format not in _valid_format_ids():
        focus_format = ""
    focus = {"pillar": focus_raw.get("pillar", ""), "format": focus_format}
    return {
        "score": round(max(0.0, min(score, 10.0)), 1),
        "verdict": re.sub(r"\s+", " ", str(data.get("verdict", ""))).strip()[:220],
        "strengths": _lines("strengths", 3),
        "weaknesses": (base["weaknesses"] + [
            w for w in _lines("weaknesses", _MAX_AVOID) if w not in base["weaknesses"]
        ])[:_MAX_AVOID + 2],
        "directives": directives[:_MAX_DIRECTIVES + 2],
        "avoid": _lines("avoid", _MAX_AVOID),
        "focus": {"pillar": str(focus.get("pillar", ""))[:60],
                  "format": str(focus.get("format", ""))[:40]},
    }


def _heuristic_score(f: Dict[str, Any]) -> float:
    """درجة أساس من الأرقام وحدها (تُستخدم بلا LLM وكقيمة احتياطية)."""
    score = 8.0
    score -= 3.0 * f["guard_rejection_rate"]
    score -= 3.0 * f["failure_rate"]
    score -= 2.0 * min(f["topic_repetition"], 1.0)
    # اكتمال الحصص اليومية ليس تقصيراً: دورة هادئة بعد بلوغ الأهداف لا تُعاقَب
    if f["idle"] and f.get("idle_reason") != "budget_done":
        score -= 3.0
    if f.get("mentions_pending", f["mentions_seen"]) and not f["mention_replies"]:
        score -= 1.5
    if f["published_count"] and f["hashtags"] > 1:
        score -= 0.5
    if f["published_count"] and f["truncated_ending"]:
        score -= 1.0
    if not f["plan_followed"]:
        score -= 1.0
    if f["engagement_trend"].get("direction") == "down":
        score -= 1.0
    elif f["engagement_trend"].get("direction") == "up":
        score += 0.5
    if f["published_count"] and f["successes"] >= 2:
        score += 0.5
    return round(max(0.0, min(score, 10.0)), 1)


# ─────────────────────────── التخزين والتغذية الأمامية ───────────────────
def store_critique(config, critique: Dict[str, Any]) -> None:
    """احفظ التقييم + التاريخ + عدّاد المشكلات المتكرّرة في حالة الموظف."""
    flags = config.runtime_state.flags
    flags[_CRITIQUE_KEY] = critique

    history: List[Dict[str, Any]] = list(flags.get(_HISTORY_KEY) or [])
    history.insert(0, {
        "cycle": critique.get("cycle"),
        "at": critique.get("at"),
        "score": critique.get("score"),
        "issues": critique.get("issue_keys") or [],
    })
    flags[_HISTORY_KEY] = history[:_MAX_HISTORY]

    recurring: Dict[str, int] = dict(flags.get(_RECURRING_KEY) or {})
    current = set(critique.get("issue_keys") or [])
    for key in current:
        recurring[key] = int(recurring.get(key, 0)) + 1
    # المشكلة التي لم تظهر هذه الدورة تتلاشى تدريجياً (تُعالَج فتُنسى)
    for key in list(recurring):
        if key not in current:
            recurring[key] = int(recurring[key]) - 1
            if recurring[key] <= 0:
                recurring.pop(key, None)
    flags[_RECURRING_KEY] = recurring


def _recurring_summary(config) -> str:
    recurring = _recurring_issues(config)
    if not recurring:
        return ""
    lines = [f"- {_ISSUE_LABELS.get(k, k)} (تكرّرت {n} مرات)" for k, n in recurring]
    return "مشكلات متكرّرة في دوراتك السابقة:\n" + "\n".join(lines) + "\n"


def _recurring_issues(config) -> List[tuple]:
    recurring = (config.runtime_state.flags.get(_RECURRING_KEY) or {})
    items = [(k, int(v)) for k, v in recurring.items() if int(v) >= _RECURRING_THRESHOLD]
    items.sort(key=lambda kv: -kv[1])
    return items[:_MAX_RECURRING_SHOWN]


#: أوصاف عربية موجزة للمشكلات (تُستخدم في النبرة المشدّدة والتقارير)
_ISSUE_LABELS = {
    "guard_rejections": "اقتراحات خارج نطاق التخصص يرفضها الحارس",
    "execution_failures": "إجراءات تفشل عند التنفيذ",
    "idle_cycle": "دورات تمرّ بلا إنتاج",
    "blocked_by_guard": "اقتراحات يرفضها الحارس فتضيع الدورة",
    "too_many_hashtags": "هاشتاقات زائدة",
    "truncated_post": "منشورات مقطوعة النهاية",
    "thin_content": "محتوى هزيل قصير",
    "topic_repetition": "تكرار الموضوع نفسه",
    "ignored_mentions": "تجاهل من يخاطبك",
    "plan_ignored": "عدم الالتزام بصيغة خطة المحتوى",
    "engagement_down": "تفاعل هابط",
    "memory_unused": "عدم الاستفادة من الذاكرة المعرفية",
    "external_link_in_post": "روابط خارجية في متن المنشور تخفض وصوله",
    "emoji_overload": "حشو إيموجي يوحي بالسبام",
    "generic_cta": "أسئلة قوالب عامة لا تستدرج ردوداً",
    "question_ending_every_text": "إنهاء كل نص بسؤال (أسلوب آلي)",
    "monotone_openings": "مطالع متطابقة تجعل الأسلوب أحادياً",
}


def directives_block(config) -> str:
    """كتلة التوجيهات التي تُحقن في ``persona_prompt`` للدورة القادمة.

    تُصاغ كأوامر مُلزِمة مع التنصيص على أنها لا تُخالف تعليمات المستخدم — فتبقى
    الهوية هي المرجع الأعلى والتحسين طبقةً فوقها.
    """
    critique = config.runtime_state.flags.get(_CRITIQUE_KEY) or {}
    directives = [d for d in (critique.get("directives") or []) if d]
    avoid = [a for a in (critique.get("avoid") or []) if a]
    recurring = _recurring_issues(config)
    if not (directives or avoid or recurring):
        return ""

    parts = [
        "🔧 تقييم دورتك السابقة وتوجيهات التحسين (التزم بها في هذه الدورة، "
        "دون مخالفة تعليمات المستخدم الثابتة ولا محظوراتك):",
    ]
    score = critique.get("score")
    verdict = str(critique.get("verdict") or "").strip()
    if score is not None:
        parts.append(f"- درجة الدورة السابقة: {score}/10." + (f" {verdict}" if verdict else ""))
    for d in directives[:_MAX_DIRECTIVES]:
        parts.append(f"- {d}")
    if avoid:
        parts.append("- تجنّب حرفياً: " + "؛ ".join(avoid[:_MAX_AVOID]))
    if recurring:
        parts.append(
            "- ⚠️ مشكلات تكرّرت منك ويجب أن تنتهي الآن: "
            + "؛ ".join(f"{_ISSUE_LABELS.get(k, k)} ({n} مرات)" for k, n in recurring)
        )
    return "\n".join(parts)


def critique_report(config) -> Dict[str, Any]:
    """تقرير مقروء للاطلاع (API/الشات): آخر تقييم + الاتجاه + المتكرّر."""
    flags = config.runtime_state.flags
    critique = dict(flags.get(_CRITIQUE_KEY) or {})
    history = list(flags.get(_HISTORY_KEY) or [])
    scores = [h["score"] for h in history if isinstance(h.get("score"), (int, float))]
    trend = "unknown"
    if len(scores) >= 4:
        newer = sum(scores[:2]) / 2
        older = sum(scores[2:4]) / 2
        trend = "up" if newer > older + 0.3 else ("down" if newer < older - 0.3 else "flat")
    return {
        "latest": critique,
        "history": history,
        "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
        "score_trend": trend,
        "recurring_issues": [
            {"key": k, "label": _ISSUE_LABELS.get(k, k), "count": n}
            for k, n in sorted((flags.get(_RECURRING_KEY) or {}).items(),
                               key=lambda kv: -int(kv[1]))
        ],
        "directives_in_effect": directives_block(config),
    }
