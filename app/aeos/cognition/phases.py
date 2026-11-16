"""
Cognitive phases.

The employee's "thinking" is decomposed into small, single-responsibility phases,
each implementing :class:`ICognitivePhase`. The :class:`CognitiveCycle` runs them
in order, threading state through the shared ``ExecutionContext.scratch``.

Full sequence:
    Observe -> Analyze -> Think -> Prioritize -> Plan -> Decide
    -> Execute -> Evaluate -> Learn -> Save

("Sleep"/destroy is handled by the RuntimeManager, not a phase, since it tears
down the runtime itself.)
"""
from __future__ import annotations

import abc
import hashlib
import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..capabilities.base import CapabilityRequest
from ..decision.engine import ENGAGEMENT_BUDGETS, PUBLISHING_CAPABILITIES
from ..memory.base import MemoryLayerType
from .signal_cache import (
    SNAPSHOT_SCRATCH_KEYS, load_snapshot, restore_snapshot, save_snapshot,
    snapshot_has_signals,
)

if TYPE_CHECKING:
    from .context import ExecutionContext

logger = logging.getLogger("aeos.cognition")


class ICognitivePhase(abc.ABC):
    name: str

    @abc.abstractmethod
    def run(self, context: "ExecutionContext") -> None: ...


class ObservePhase(ICognitivePhase):
    """Gather signals: the event, recent memory, current counters/state."""

    name = "observe"

    def run(self, context: "ExecutionContext") -> None:
        recall = context.memory.recall_all(context.config.id, limit=5)
        summary_lines: List[str] = []
        for layer, records in recall.items():
            if layer == "reflection":
                continue  # الدروس الآلية («executed X») ضجيج — الدروس الحقيقية في البريف أدناه
            for r in records[:3]:
                summary_lines.append(f"[{layer}] {r.content[:160]}")
        # الذاكرة طويلة المدى: ما قاله المالك عن هذا الموظف + دروس التقييم. تُحسب مرة
        # واحدة للدورة (persona_prompt يُستدعى مرات كثيرة) وتُقرأ من scratch.
        try:
            from ..memory import agent_memory as AM

            brief = AM.employee_brief(context.config.id)
        except Exception:  # noqa: BLE001
            logger.debug("employee memory brief failed", exc_info=True)
            brief = ""
        context.scratch["memory_brief"] = brief
        if brief:
            summary_lines.insert(0, brief)
        context.scratch["recent_memory_summary"] = "\n".join(summary_lines)

        # راجع سجلّ تفاعلاتك المنظّم قبل أن تقرّر: ماذا نشرتَ ورددتَ وأعجبتَ
        # وأعدتَ نشره وتابعتَ — كي تعرف ما شاركت فيه سابقاً ولا تفتح النقاش
        # نفسه ولا تكرّر الموضوع/الصياغة، وتقدّم دوماً زاوية جديدة.
        posts = context.memory.recall(
            context.config.id, MemoryLayerType.LONG_TERM,
            query="Published post", limit=8,
        )
        recent_posts = [
            r.content.split(":", 1)[-1].strip()
            for r in posts if "Published post" in r.content
        ]

        # تفاعلات مهيكلة (رد/إعجاب/إعادة نشر/متابعة) من ذاكرة المحادثة
        interactions = context.memory.recall(
            context.config.id, MemoryLayerType.CONVERSATION, limit=30,
        )
        by_action: Dict[str, List[str]] = {}
        for r in interactions:
            act = (r.metadata or {}).get("action")
            if act:
                by_action.setdefault(act, []).append(r.content)

        recent_replies = [
            c.split("—", 1)[-1].strip() for c in by_action.get("reply", [])
        ]
        context.scratch["recent_posts"] = recent_posts
        context.scratch["recent_replies"] = recent_replies
        context.scratch["interaction_history"] = by_action

        activity_lines: List[str] = []
        if recent_posts:
            activity_lines.append("منشوراتك الأخيرة (لا تكرّر موضوعها ولا صياغتها، وقدّم زاوية جديدة):")
            activity_lines += [f"- {t[:160]}" for t in recent_posts[:6]]
        _history_titles = {
            "reply": "ردودك الأخيرة (لا تكرّر نفس النقاش ولا الصياغة):",
            "quote": "اقتبستها مع تعليق مؤخراً (لا تقتبس الموضوع نفسه):",
            "retweet": "أعدت نشرها مؤخراً (لا تعِد نشر الموضوع نفسه):",
            "like": "أعجبتَ بها مؤخراً (تجنّب تكرار نفس الموضوع):",
            "follow": "حسابات تابعتها مؤخراً (لا تتابعها من جديد):",
        }
        for act, title in _history_titles.items():
            items = by_action.get(act) or []
            if items:
                activity_lines.append(title)
                activity_lines += [f"- {c[:120]}" for c in items[:4]]
        context.scratch["recent_activity_summary"] = "\n".join(activity_lines)
        observations = {
            "event_type": context.event.type.value,
            "event_payload_keys": list((context.event.payload or {}).keys()),
            "counters": dict(context.config.runtime_state.counters),
            "cycles_completed": context.config.runtime_state.cycles_completed,
        }

        account = getattr(context.config, "account_username", "") or "الموظف الذكي"

        def _source(icon: str, label: str, count: int | None,
                    detail: str = "", error: str = "") -> None:
            """بثّ سطر تقدّم مباشر لكل مصدر يفحصه الموظف — شفافية كاملة للدورة."""
            if error:
                context.emit_progress(
                    f"{icon} **{label}:** تعذّر الوصول ({str(error)[:80]})",
                    intent="aeos_cycle_observing",
                )
                return
            if count:
                line = f"{icon} **{label}:** {count}"
                if detail:
                    line += f" — {detail}"
            else:
                line = f"{icon} **{label}:** لا جديد الآن"
            context.emit_progress(line, intent="aeos_cycle_observing")

        # ── ذاكرة رصد الإشارات: أعِد استخدام آخر مسح (تايم لاين/ترندات/أهم
        # البوستات/أخبار) ما دام حديثاً بدل إعادة المسح الكامل كل دورة.
        rules = context.config.business_rules or {}
        cache_ttl = int(rules.get("observation_cache_minutes", 60))
        force_fresh = bool((context.event.payload or {}).get("force_fresh"))
        if cache_ttl > 0 and not force_fresh:
            snap, age_min = load_snapshot(context.workspace)
            if snap and age_min < cache_ttl and snapshot_has_signals(snap):
                restore_snapshot(snap, context.scratch, observations)
                observations["signals_cache_age_min"] = round(age_min, 1)
                try:                       # ترندات التخصص كل دورة حتى مع ذاكرة الرصد
                    from ..employee import tool_use

                    st = tool_use.specialty_trends(context)
                    if st:
                        observations["specialty_trends"] = [t["topic"] for t in st]
                except Exception as exc:  # noqa: BLE001
                    logger.warning("specialty trends failed: %s", exc)
                context.scratch["observations"] = observations
                context.emit_progress(
                    f"🗂️ **@{account} يستخدم ذاكرة الرصد المخزّنة** — آخر مسح "
                    f"للمصادر قبل {int(age_min)} دقيقة (الصلاحية {cache_ttl} دقيقة)، "
                    "فينتقل مباشرة للتحليل واتخاذ القرار دون إعادة المسح.",
                    intent="aeos_cycle_observing",
                )
                return

        # رسالة استهلالية: يعلن الموظف أنه سيمرّ على كل أدواته، لا الترند فقط
        context.emit_progress(
            f"🔎 **@{account} بدأ دورة جديدة — يفحص جميع مصادره بالتسلسل:**\n"
            "الخط الزمني ← ترندات X ← أهم البوستات ← أخبار التخصص ← "
            "المواضيع الرائجة ← أخبار قوقل ← المنشنات.",
            intent="aeos_cycle_observing",
        )

        # تصفّح التايم لاين بالكوكيز إن كان الموظف مزوّداً بخادم playwright_x
        if "playwright_x" in (context.config.mcp_servers or []):
            try:
                result = context.capabilities.execute(
                    CapabilityRequest(name="browse_timeline", params={"limit": 10}),
                    context,
                )
                if result.success:
                    tweets = result.output.get("tweets", []) if result.output else []
                    context.scratch["timeline"] = tweets
                    observations["timeline_count"] = len(tweets)
                    observations["timeline_preview"] = [
                        {"handle": t.get("handle", ""), "text": (t.get("text", "") or "")[:120]}
                        for t in tweets[:5]
                    ]
                    _source("📰", "الخط الزمني", len(tweets), "تغريدة رصدها")
                else:
                    observations["timeline_error"] = result.error
                    _source("📰", "الخط الزمني", None, error=result.error)
            except Exception as exc:  # noqa: BLE001
                logger.warning("timeline browse failed: %s", exc)
                observations["timeline_error"] = str(exc)
                _source("📰", "الخط الزمني", None, error=str(exc))

            # تصفّح ترندات X الخاصة بحساب الموظف + تغريدات أهم ترند يخص تخصصه
            try:
                xt = context.capabilities.execute(
                    CapabilityRequest(name="browse_x_trends", params={"limit": 10}),
                    context,
                )
                if xt.success:
                    x_trends = xt.output.get("trends", []) if xt.output else []
                    observations["x_trends_count"] = len(x_trends)
                    observations["x_trends_preview"] = [
                        t.get("name", "") for t in x_trends[:5]
                    ]
                    observations["x_top_trend"] = xt.output.get("top_trend", "")
                    observations["trend_tweets_count"] = len(
                        context.scratch.get("trend_tweets") or [])
                    # الهاشتاقات الترند + الهاشتاق المطابق لتخصص الحساب (للمشاركة فيه)
                    observations["trend_hashtags"] = [
                        h.get("name", "")
                        for h in (context.scratch.get("trend_hashtags") or [])[:5]
                    ]
                    observations["matched_hashtag"] = (
                        context.scratch.get("matched_hashtag") or {}
                    ).get("name", "")
                    observations["hashtag_tweets_count"] = len(
                        context.scratch.get("hashtag_tweets") or [])
                    _top = xt.output.get("top_trend", "")
                    _source("📈", "ترندات X", len(x_trends),
                            f"أبرزها: {str(_top)[:50]}" if _top else "")
                else:
                    observations["x_trends_error"] = xt.error
                    _source("📈", "ترندات X", None, error=xt.error)
            except Exception as exc:  # noqa: BLE001
                logger.warning("x trends browse failed: %s", exc)
                observations["x_trends_error"] = str(exc)
                _source("📈", "ترندات X", None, error=str(exc))

            # تصفّح "أهم البوستات" (Creator Inspiration → Top Posts): الأكثر أداءً
            # التي يقترحها X لهذا الحساب, ليتفاعل معها الموظف ضمن تخصصه.
            try:
                tp = context.capabilities.execute(
                    CapabilityRequest(name="browse_top_posts", params={"limit": 12}),
                    context,
                )
                if tp.success:
                    top_posts = tp.output.get("tweets", []) if tp.output else []
                    observations["top_posts_count"] = len(top_posts)
                    observations["top_posts_preview"] = [
                        {"handle": t.get("handle", ""), "text": (t.get("text", "") or "")[:120]}
                        for t in top_posts[:5]
                    ]
                    _source("⭐", "أهم البوستات", len(top_posts), "منشور عالي الأداء")
                else:
                    observations["top_posts_error"] = tp.error
                    _source("⭐", "أهم البوستات", None, error=tp.error)
            except Exception as exc:  # noqa: BLE001
                logger.warning("top posts browse failed: %s", exc)
                observations["top_posts_error"] = str(exc)
                _source("⭐", "أهم البوستات", None, error=str(exc))

            # ترندات التخصص من أدوات MCP (trendsmcp…) — كل دورة، قبل البحث في X لأنه يبحث بها
            try:
                from ..employee import tool_use

                st = tool_use.specialty_trends(context)
                if st:
                    observations["specialty_trends"] = [t["topic"] for t in st]
                    _source("📈", "ترندات التخصص (" + "، ".join(t["topic"][:40] for t in st) + ")",
                            len(st), "موضوعاً")
            except Exception as exc:  # noqa: BLE001 - تحسين لا شرط للدورة
                logger.warning("specialty trends failed: %s", exc)

            # تصفّح أحدث أخبار التخصص عبر Playwright (بحث حي على X) — بديل خادم
            # الأخبار: نكتفي بـPlaywright لجلب آخر ما يخص مجال الموظف.
            if context.capabilities.has("browse_specialty_news"):
                try:
                    sn = context.capabilities.execute(
                        CapabilityRequest(name="browse_specialty_news", params={"limit": 12}),
                        context,
                    )
                    if sn.success:
                        news_items = sn.output.get("news", []) if sn.output else []
                        observations["specialty_news_count"] = len(news_items)
                        observations["specialty_news_preview"] = [
                            (n.get("title", "") or "")[:100] for n in news_items[:5]
                        ]
                        _source("🗞️", "أخبار التخصص (عبر X)", len(news_items), "خبراً حديثاً")
                    else:
                        observations["specialty_news_error"] = sn.error
                        _source("🗞️", "أخبار التخصص (عبر X)", None, error=sn.error)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("specialty news browse failed: %s", exc)
                    observations["specialty_news_error"] = str(exc)
                    _source("🗞️", "أخبار التخصص (عبر X)", None, error=str(exc))

            # اقرأ التنبيهات (الردود/المنشنات الموجّهة للحساب) ليردّ عليها الموظف
            # باختصار لاحقاً — ردّ واحد لكل ردّ ودون تكرار (إزالة تكرار دائمة).
            if context.capabilities.has("read_notifications"):
                try:
                    nt = context.capabilities.execute(
                        CapabilityRequest(name="read_notifications", params={"limit": 15}),
                        context,
                    )
                    if nt.success:
                        mentions = nt.output.get("tweets", []) if nt.output else []
                        observations["mentions_count"] = len(mentions)
                        observations["mentions_preview"] = [
                            {"handle": m.get("handle", ""), "text": (m.get("text", "") or "")[:120]}
                            for m in mentions[:5]
                        ]
                        _source("💬", "المنشنات والردود", len(mentions), "موجّهة لحسابه")
                        self._credit_interactions(context, mentions)
                    else:
                        observations["mentions_error"] = nt.error
                        _source("💬", "المنشنات والردود", None, error=nt.error)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("notifications read failed: %s", exc)
                    observations["mentions_error"] = str(exc)
                    _source("💬", "المنشنات والردود", None, error=str(exc))

            # ── بحث الاهتمامات المباشر: أداة الملاذ عندما لا يقدّم التايم لاين
            # والترندات أهدافاً ضمن التخصص. حساب جديد/تايم لاين عام يعني صفر
            # أهداف داخل الاهتمامات، فيرفض الحارس كل مقترح وتمضي الدورات بلا
            # أي فعل (شوهد مع liketoday5: فن/سفر/ترفيه وسط تايم لاين كروي).
            # نبحث في X مباشرةً بكلمات اهتمامات الموظف فنملأ حوض التفاعل
            # بأهداف مضمونة الصلة.
            try:
                from ..employee.news_sources import INTEREST_QUERIES
                from ..employee.persona_templates import relevance_score

                interests = getattr(context.config.persona, "interests", []) or []
                _pool = (context.scratch.get("timeline") or []) + \
                        (context.scratch.get("trend_tweets") or []) + \
                        (context.scratch.get("top_posts") or [])
                in_scope = sum(
                    1 for t in _pool
                    if relevance_score(str(t.get("text", "")), interests) >= 1
                )
                if interests and in_scope < 3:
                    from ..mcp.base import MCPRequest

                    found: List[dict] = []
                    for interest in interests[:2]:
                        query = (INTEREST_QUERIES.get(interest) or [str(interest)])[0]
                        sr = context.mcp.call(MCPRequest(
                            employee_id=context.config.id, tool="search_tweets",
                            params={"cookie_label": context.config.cookie_label,
                                    "query": query, "limit": 8},
                            context={"server": "playwright_x",
                                     "cookie_label": context.config.cookie_label},
                        ))
                        if sr.success:
                            found += [t for t in (sr.data.get("tweets") or [])
                                      if t not in found]
                        if len(found) >= 8:
                            break
                    if found:
                        context.scratch["interest_tweets"] = found
                        observations["interest_tweets_count"] = len(found)
                        _source("🎯", "بحث الاهتمامات", len(found),
                                "تغريدة ضمن التخصص (بحث مباشر)")
            except Exception as exc:  # noqa: BLE001
                logger.warning("interest search failed: %s", exc)

        # اجلب المواضيع الرائجة الحقيقية (قوقل ترند / كاشف الترند) لتأسيس المحتوى عليها
        if context.capabilities.has("fetch_trends"):
            try:
                tr = context.capabilities.execute(
                    CapabilityRequest(name="fetch_trends", params={"limit": 10}),
                    context,
                )
                if tr.success:
                    trends = tr.output.get("trends", []) if tr.output else []
                    context.scratch["trends"] = trends
                    observations["trends_count"] = len(trends)
                    observations["trends_preview"] = [
                        {"title": (t.get("title", "") or "")[:120],
                         "category": t.get("category", ""),
                         "has_media": bool(t.get("media_url"))}
                        for t in trends[:5]
                    ]
                    _source("🌐", "المواضيع الرائجة", len(trends), "موضوعاً رائجاً")
                else:
                    observations["trends_error"] = tr.error
                    _source("🌐", "المواضيع الرائجة", None, error=tr.error)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fetch trends failed: %s", exc)
                observations["trends_error"] = str(exc)
                _source("🌐", "المواضيع الرائجة", None, error=str(exc))

        # اقرأ أهم الأخبار الحقيقية عبر خادم news (قوقل نيوز) حسب اهتمام الموظف
        if "news" in (context.config.mcp_servers or []) and context.capabilities.has("fetch_news"):
            try:
                nw = context.capabilities.execute(
                    CapabilityRequest(name="fetch_news", params={"limit": 10}),
                    context,
                )
                if nw.success:
                    news = nw.output.get("news", []) if nw.output else []
                    # لا تدُس على أخبار التخصص التي جُمعت عبر Playwright؛ ادمجها
                    existing = context.scratch.get("news") or []
                    merged = existing + [n for n in news if n not in existing]
                    context.scratch["news"] = merged
                    observations["news_count"] = len(news)
                    observations["news_preview"] = [
                        {"title": (n.get("title", "") or "")[:120],
                         "source": n.get("source", ""),
                         "has_media": bool(n.get("media_url"))}
                        for n in news[:5]
                    ]
                    _source("📻", "أخبار قوقل", len(news), "خبراً")
                else:
                    observations["news_error"] = nw.error
                    _source("📻", "أخبار قوقل", None, error=nw.error)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fetch news failed: %s", exc)
                observations["news_error"] = str(exc)
                _source("📻", "أخبار قوقل", None, error=str(exc))

        # أدوات MCP المفعّلة (ترندات حية…) حسب خطة استخدام كل خادم — مرة كل دورة
        try:
            from ..employee import tool_use

            signals = context.scratch.get("mcp_signals")
            if signals is None:
                signals = tool_use.observe(context)
            if signals:
                observations["mcp_signals_count"] = len(signals)
                observations["mcp_signals_preview"] = [f"{s['source']}: {s['text'][:100]}" for s in signals]
                _source("🧰", "أدوات MCP (" + "، ".join(dict.fromkeys(s["source"] for s in signals)) + ")",
                        len(signals), "إشارة")
        except Exception as exc:  # noqa: BLE001 - الأدوات تحسين لا شرط للدورة
            logger.warning("mcp observe failed: %s", exc)

        context.scratch["observations"] = observations

        # احفظ نتيجة المسح الحي كلقطة تعاد قراءتها في الدورات القادمة (ضمن الصلاحية)
        if cache_ttl > 0 and any(context.scratch.get(k) for k in SNAPSHOT_SCRATCH_KEYS):
            save_snapshot(context.workspace, context.scratch, observations)

        # خلاصة موحّدة لكل ما جُمِع هذه الدورة — تؤكد أنه مرّ على جميع الأدوات
        summary_bits: List[str] = []
        if observations.get("timeline_count"):
            summary_bits.append(f"{observations['timeline_count']} من الخط الزمني")
        if observations.get("x_trends_count"):
            summary_bits.append(f"{observations['x_trends_count']} ترند X")
        if observations.get("top_posts_count"):
            summary_bits.append(f"{observations['top_posts_count']} بوست مميّز")
        if observations.get("specialty_news_count"):
            summary_bits.append(f"{observations['specialty_news_count']} خبر تخصّص")
        if observations.get("trends_count"):
            summary_bits.append(f"{observations['trends_count']} موضوع رائج")
        if observations.get("news_count"):
            summary_bits.append(f"{observations['news_count']} خبر قوقل")
        if observations.get("mentions_count"):
            summary_bits.append(f"{observations['mentions_count']} منشن/رد")
        if summary_bits:
            context.emit_progress(
                f"✅ **@{account} أنهى جمع المعلومات من كل مصادره:** "
                + "، ".join(summary_bits) + ". ينتقل الآن للتحليل واتخاذ القرار.",
                intent="aeos_cycle_observing",
            )

    @staticmethod
    def _credit_interactions(context: "ExecutionContext", mentions: List[Dict]) -> None:
        """انسب المنشنات الواردة إلى التفاعلات التي سبقتها (حلقة تعلّم التفاعل).

        هذه هي اللحظة الوحيدة التي يمكن فيها معرفة أن تفاعلاً ما «أثمر»: حسابٌ
        تفاعلنا معه سابقاً يخاطبنا الآن. بدونها يبقى قرار التفاعل مبنياً على
        أرقام ثابتة مكتوبة يدوياً ولا يتعلّم شيئاً مهما دارت الدورات.
        """
        from ..employee import interaction_ledger

        try:
            credited = interaction_ledger.credit_signals(context.config, mentions)
        except Exception:  # noqa: BLE001 - التعلّم لا يُسقط الرصد أبداً
            logger.exception("interaction credit failed")
            return
        if not credited:
            return
        context.scratch["interaction_credits"] = credited
        names = "، ".join(
            dict.fromkeys(f"@{c['handle']}" for c in credited if c.get("handle")))
        context.emit_progress(
            f"🔁 **تفاعل أثمر:** {names} تجاوب مع تفاعلٍ سابق منّي "
            f"({len(credited)} إشارة) — سأرجّح هذا النوع من الأهداف.",
            intent="aeos_cycle_observing",
        )


class TopicIntelPhase(ICognitivePhase):
    """حلّل ما رُصد: الصلة الدلالية بالتخصص + الأهمية (انتشار × حداثة).

    يضع ``_sem``/``_imp`` على عناصر scratch نفسها فتستهلكها المهارات مباشرة
    (حوض التفاعل، مرشّحو المحتوى، رئيس التحرير). نداء تضمين واحد للدورة، وأي
    فشل يُبقي المسار الحتمي (الكلمات) كما هو.
    """

    name = "topic_intel"

    def run(self, context: "ExecutionContext") -> None:
        from ..employee import topic_intel

        try:
            report = topic_intel.analyze(context)
        except Exception:  # noqa: BLE001 - التحليل تحسين، لا شرط للدورة
            logger.exception("topic intel failed")
            return
        context.scratch["topic_intel_report"] = report
        if not report.get("analyzed"):
            return
        account = getattr(context.config, "account_username", "") or "الموظف الذكي"
        line = (f"🧭 **@{account} حلّل {report['analyzed']} عنصراً:** "
                f"{report['in_scope']} ضمن تخصصه"
                + ("" if report.get("semantic") else " (بالكلمات فقط — التحليل الدلالي غير متاح)"))
        if report.get("promoted_dropped"):
            line += f"، واستبعد {report['promoted_dropped']} إعلاناً مموّلاً"
        top = report.get("top") or []
        if top:
            line += "\nالأهم الآن:\n" + "\n".join(
                f"• {t['text'][:90]} _(أهمية {t['importance']:.0%})_" for t in top)
        context.emit_progress(line, intent="aeos_cycle_thinking")


class PerformancePhase(ICognitivePhase):
    """Read the account's OWN posts performance — the missing feedback loop.

    كان الموظف ينشر في الفراغ: لا يعرف أيّ منشور نجح. هذا الطور يقرأ أرقام
    منشوراته الحقيقية من صفحته (إعجابات/إعادات/ردود/مشاهدات) ويحدّث سجل الأداء
    المربوط بالمحور والصيغة والساعة، فيصير كل قرار محتوى قادم مبنياً على دليل.

    القراءة تُشغّل متصفحاً، لذا تجري بوتيرة مقنّنة (``metrics_read_every_minutes``،
    افتراضياً كل 180 دقيقة) وتفشل بأمان: أي خلل يترك السجل كما هو.
    """

    name = "performance"

    def run(self, context: "ExecutionContext") -> None:
        from ..employee import strategy

        report = strategy.performance_report(context.config)
        context.scratch["performance_report"] = report

        rules = context.config.business_rules or {}
        every = int(rules.get("metrics_read_every_minutes", 180))
        if every <= 0 or not context.capabilities.has("read_own_metrics"):
            return
        if "playwright_x" not in (context.config.mcp_servers or []):
            return
        elapsed = strategy.minutes_since_metrics_read(context.config)
        if elapsed < every:
            return

        account = getattr(context.config, "account_username", "") or "الموظف الذكي"
        try:
            result = context.capabilities.execute(
                CapabilityRequest(name="read_own_metrics",
                                  params={"limit": int(rules.get("metrics_posts_limit", 15))}),
                context,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("reading own metrics failed: %s", exc)
            return
        if not result.success:
            logger.info("own metrics unavailable: %s", result.error)
            return

        report = (result.output or {}).get("report") or strategy.performance_report(context.config)
        context.scratch["performance_report"] = report
        best_fmt = next(iter(report.get("formats") or {}), "")
        summary = f"📊 **@{account} قرأ أداء منشوراته:** {report.get('samples', 0)} منشوراً مقيساً"
        if best_fmt:
            avg = report["formats"][best_fmt]["avg_engagement"]
            summary += f" — أنجح صيغة: {best_fmt} (متوسط تفاعل {avg})"
        if report.get("best_hours"):
            summary += f"، أفضل ساعة نشر: {report['best_hours'][0]['hour']}:00 UTC"
        context.emit_progress(summary, intent="aeos_cycle_observing")


class StrategyPhase(ICognitivePhase):
    """Decide THIS cycle's content pillar + format, driven by real performance.

    يحوّل الموظف من «مطارد ترندات» إلى صانع محتوى له هوية: يختار محور المحتوى
    وصيغة العرض (رأي/شرح/سؤال/قصة/ثريد/قائمة/تصحيح مفهوم) بترجيح ما نجح فعلاً،
    مع عقوبة تكرار تُدوّر المحاور والصيغ، ونسبة استكشاف تجرّب ما لم يُجرَّب.
    النتيجة توضع في ``scratch['content_plan']`` فتستهلكها مهارات المحتوى.
    """

    name = "strategy"

    def run(self, context: "ExecutionContext") -> None:
        from ..employee import strategy

        rules = context.config.business_rules or {}
        if not bool(rules.get("content_strategy_enabled", True)):
            context.scratch["content_plan"] = None
            return
        # الصيغة التي رشّحها حارس التقييم بعد نقد الدورة السابقة (إن كانت صالحة)
        from .critique import suggested_format

        try:
            plan = strategy.choose_plan(
                context.config, prefer_format=suggested_format(context.config))
        except Exception as exc:  # noqa: BLE001
            logger.warning("content strategy failed: %s", exc)
            context.scratch["content_plan"] = None
            return

        context.scratch["content_plan"] = plan
        context.scratch["strategy_block"] = strategy.strategy_prompt_block(plan)
        account = getattr(context.config, "account_username", "") or "الموظف الذكي"
        line = (f"🎯 **خطة محتوى @{account} لهذه الدورة:** محور «{plan['pillar']}» "
                f"بصيغة «{plan['format_label']}»")
        if int(plan.get("parts", 1)) > 1:
            line += f" ({plan['parts']} تغريدات)"
        series = plan.get("series") or {}
        if series:
            line += f" — حلقة {series['episode']} من سلسلة «{series['name']}»"
        context.emit_progress(f"{line}.\n_{plan.get('why', '')}_",
                              intent="aeos_cycle_thinking")


class AnalyzePhase(ICognitivePhase):
    """Interpret observations into a situational understanding."""

    name = "analyze"

    def run(self, context: "ExecutionContext") -> None:
        obs = context.scratch.get("observations", {})
        event = context.event
        analysis: Dict[str, Any] = {
            "requires_response": event.type.value in ("mention", "reply", "notification"),
            "is_scheduled_wake": event.type.value in ("scheduler", "time_trigger"),
            "has_source_url": bool((event.payload or {}).get("url") or (event.payload or {}).get("link")),
        }
        context.scratch["analysis"] = analysis


class ThinkPhase(ICognitivePhase):
    """Optional deliberation via LLM to form intent (grounded in persona/goals)."""

    name = "think"

    def run(self, context: "ExecutionContext") -> None:
        account = getattr(context.config, "account_username", "") or "الموظف الذكي"
        if not (context.llm and context.llm.available):
            context.scratch["thought"] = "heuristic mode (no LLM)"
            return
        thought = context.llm.complete(
            system=context.persona_prompt(),
            prompt=(
                "In 2-3 sentences, reason about what you should focus on this cycle.\n"
                f"{context.planning_prompt()}"
            ),
            temperature=0.5, max_tokens=200,
        )
        thought = thought or "no explicit thought"
        context.scratch["thought"] = thought
        # بثّ التفكير لحظياً إلى شات المستخدم (تفاعل مباشر أثناء الدورة)
        context.emit_progress(
            f"💭 **@{account} يفكّر الآن:**\n{thought}",
            intent="aeos_cycle_thinking",
        )


class PrioritizePhase(ICognitivePhase):
    """Order goals so the planner focuses on what matters now."""

    name = "prioritize"

    def run(self, context: "ExecutionContext") -> None:
        goals = sorted(context.config.goals, key=lambda g: (g.priority, -g.progress))
        context.scratch["prioritized_goals"] = [g.to_dict() for g in goals]


class PlanPhase(ICognitivePhase):
    """Produce candidate actions using the planner."""

    name = "plan"

    def __init__(self, planner) -> None:
        self._planner = planner

    def run(self, context: "ExecutionContext") -> None:
        proposals = self._planner.plan(context)
        context.scratch["proposals"] = proposals
        context.scratch["plan_preview"] = [
            {"capability": p.capability, "rationale": p.rationale} for p in proposals
        ]


class DecidePhase(ICognitivePhase):
    """Select which action(s) to execute using the decision engine."""

    name = "decide"

    def __init__(self, decision_engine) -> None:
        self._engine = decision_engine

    def run(self, context: "ExecutionContext") -> None:
        proposals = context.scratch.get("proposals", [])
        decisions = self._engine.decide(proposals, context)
        context.scratch["decisions"] = decisions
        context.scratch["decision_log"] = deepcopy([d.to_dict() for d in decisions])
        context.scratch["initial_decisions"] = deepcopy(context.scratch["decision_log"])


def _announce_execution_plan(context, plan) -> None:
    # بثّ خطة العمل التي اتُّخذت هذه الدورة (شفافية احترافية للمستخدم)
    account = getattr(context.config, "account_username", "") or "الموظف الذكي"
    _labels = {
        "publish_content": "نشر منشور أصلي",
        "publish_thread": "نشر ثريد (سلسلة تغريدات)",
        "quote_post": "اقتباس مع تعليق",
        "schedule_post": "جدولة منشور لوقت أفضل",
        "reply_to_user": "الرد على تغريدة",
        "retweet_post": "إعادة نشر",
        "like_post": "إعجاب",
        "follow_user": "متابعة حساب",
        "unfollow_user": "إلغاء متابعة",
        "bookmark_post": "حفظ مرجع للإلهام",
        "delete_own_post": "حذف منشور خاطئ",
        "read_profile": "قراءة ملف حساب",
        "read_own_metrics": "قراءة أداء منشوراته",
        "generate_image": "توليد صورة للمحتوى",
        "research_topic": "بحث معمّق في موضوع",
        "store_knowledge": "حفظ معرفة",
        "wait": "الانتظار دون إجراء",
    }
    guard = context.scratch.get("guard_report") or {}
    if guard.get("rejected"):
        reasons = "؛ ".join(dict.fromkeys(
            str(r) for r in (guard.get("reasons") or {}).values() if r))
        context.emit_progress(
            f"استبعدت {len(guard['rejected'])} من الاختيارات الأولية بعد المراجعة: "
            f"{reasons or 'لا تتوافق مع الشخصية أو النشاط السابق'}."
            + (" اخترت بديلاً اجتاز المراجعة." if guard.get("substituted") else ""),
            intent="aeos_cycle_plan_review",
        )
    acts = [entry["capability"] for entry in plan if entry["capability"] != "wait"]
    if acts:
        from collections import Counter
        counts = Counter(acts)
        plan_line = "، ".join(
            f"{_labels.get(a, a)} (×{n})" if n > 1 else _labels.get(a, a)
            for a, n in counts.items()
        )
        context.emit_progress(
            f"**@{account} قرّر خطة هذه الدورة بعد المراجعة:** {plan_line}.",
            intent="aeos_cycle_deciding",
        )
    else:
        context.emit_progress(
            f"@{account}: لا يوجد إجراء قابل للتنفيذ في هذه الدورة بعد المراجعة.",
            intent="aeos_cycle_deciding",
        )


#: تسميات عربية واضحة لأنواع الإجراءات — تجعل مراجعة الحارس أدق وأوضح
_GUARD_CAP_LABELS = {
    "reply_to_user": "رد علني على تغريدة",
    "retweet_post": "إعادة نشر (تبنٍّ للمحتوى)",
    "like_post": "إعجاب",
    "follow_user": "متابعة حساب",
    "publish_content": "نشر منشور أصلي",
    "publish_thread": "نشر ثريد (سلسلة تغريدات)",
    "quote_post": "اقتباس مع تعليق (تبنّي المحتوى + موقف علني)",
    "schedule_post": "جدولة منشور أصلي",
    "unfollow_user": "إلغاء متابعة حساب",
    "bookmark_post": "حفظ مرجع (غير علني)",
    "delete_own_post": "حذف منشور من حسابه",
}


def _action_guard_text(action) -> str:
    """Human-readable description of what an action would do, for guarding."""
    p = action.params or {}
    cap = action.capability
    target = (p.get("original_text") or p.get("brief") or p.get("text")
              or p.get("handle") or p.get("profile_url") or p.get("tweet_url") or "")
    target = str(target).replace("\n", " ").strip()[:280]
    return f"{cap} -> {target}" if target else cap


def _action_guard_entry(idx: int, decision) -> str:
    """وصف مُهيكل وكامل للإجراء (النوع/الهدف/النص/المبرر) ليحكم الحارس بدقة."""
    a = decision.action
    p = a.params or {}
    label = _GUARD_CAP_LABELS.get(a.capability, a.capability)
    text = str(p.get("original_text") or p.get("brief") or p.get("text") or "")
    text = text.replace("\n", " ").strip()[:400]
    handle = str(p.get("handle") or p.get("profile_url") or "").strip()
    rationale = str(getattr(a, "rationale", "") or "").strip()[:160]
    parts = [f"[{idx}] النوع: {label}"]
    if handle:
        parts.append(f"الحساب المستهدف: {handle}")
    if text:
        # النشر يُعرض بموجز تكليف (تعليمات توليد + مادة خام) — النص النهائي
        # يُصاغ لاحقاً وفق الشخصية، فلا يصح الحكم عليه كأنّه المنشور النهائي.
        if a.capability in PUBLISHING_CAPABILITIES and p.get("brief"):
            parts.append(
                f"موجز التكليف (تعليمات توليد وليس نص المنشور النهائي): «{text}»")
        else:
            parts.append(f"النص/المحتوى: «{text}»")
    if rationale:
        parts.append(f"مبرر الاقتراح: {rationale}")
    directive = p.get("skill_directive")
    if isinstance(directive, str) and directive.strip():
        parts.append(f"توجيه المهارة المراد تطبيقه (لا يتجاوز تعليمات الشخصية): {directive[:1200]}")
    return " | ".join(parts)


class PersonaGuardPhase(ICognitivePhase):
    """Two-layer per-cycle guard binding EVERY interaction to persona+instructions.

    Layer 1 (deterministic — always runs, even without LLM): drops actions whose
    target text is ads/spam, violates the persona's donts, or re-opens a topic
    the employee already engaged with (memory-grounded repetition check).

    Layer 2 (LLM — one call): audits the remaining actions against an explicit
    criteria checklist (specialty scope, tone/dialect, dos/donts, values,
    repetition vs recent activity, reputational risk) and returns a per-action
    verdict with confidence + reason. Only rejections at/above the confidence
    threshold are enforced — precise, no over-blocking. Fail-open on LLM errors.
    """

    name = "guard"

    #: only public-facing interactions are guarded; internal/no-op actions skip
    _GUARDED = {"reply_to_user", "retweet_post", "quote_post", "like_post",
                "follow_user", "unfollow_user", "publish_content",
                "publish_thread", "schedule_post"}

    def run(self, context: "ExecutionContext") -> None:
        decisions = context.scratch.get("decisions", []) or []
        if not decisions:
            return

        # أمر مباشر من صاحب الحساب («انشر عن …» / «تفاعل مع …») يتجاوز الحراسة —
        # المستخدم هو المرجع الأعلى: النشر الموجّه يتجاوز حارس النشر، والتفاعل
        # الموجّه يتجاوز حارس التفاعل (رد/إعجاب/إعادة نشر/متابعة). أما الدورات
        # التلقائية فتبقى بقية إجراءاتها خاضعة للحارس بالكامل.
        payload = context.event.payload or {}
        directed = bool(payload.get("directed_topic"))
        directed_engage = bool(payload.get("directed_engagement"))
        _engage_caps = {"reply_to_user", "like_post", "retweet_post", "quote_post",
                        "follow_user"}
        guarded_idx = [
            i for i, d in enumerate(decisions)
            if d.action.capability in self._GUARDED
            and not (directed and d.action.capability in PUBLISHING_CAPABILITIES)
            and not (directed_engage and d.action.capability in _engage_caps)
            # مهمّة موجّهة حرّة: الهدف اختاره الموظف لكن الأمر أمرُ المالك، وقد
            # مرّ الهدف أصلاً بفلتر التخصص والمحظورات في المهارة نفسها.
            and not (d.action.params or {}).get("owner_directed")
        ]
        if not guarded_idx:
            return

        reasons: dict = {}

        # ── الطبقة 1: فحص حتمي صارم (يعمل حتى بلا LLM) ──
        hard_reject = self._deterministic_screen(context, decisions, guarded_idx, reasons)
        llm_candidates = [i for i in guarded_idx if i not in hard_reject]

        # ── الطبقة 2: تدقيق LLM مُهيكل بحكم/ثقة/سبب لكل إجراء ──
        llm_reject: set = set()
        if llm_candidates and context.llm and context.llm.available:
            llm_reject = self._llm_audit(context, decisions, llm_candidates, reasons)

        reject = hard_reject | llm_reject
        if not reject:
            context.scratch["guard_report"] = {
                "reviewed": len(guarded_idx), "rejected": [], "reasons": {},
            }
            return

        kept = [d for i, d in enumerate(decisions) if i not in reject]
        removed = [_action_guard_text(decisions[i].action) for i in sorted(reject)]

        # جولة الإنقاذ: إن لم يبقَ أي إجراء حقيقي بعد الرفض، جرّب البدلاء
        # الذين قيّمهم محرّك القرار (الأعلى منفعةً أولاً) عبر نفس طبقتَي
        # الحراسة. بدونها: max_actions=1 + رفض واحد = دورة كاملة بلا أي فعل
        # رغم وجود مقترحات صالحة (شوهد مع liketoday5 ثلاث دورات متتالية).
        from . import learning

        substituted: List[str] = []
        alt_rejected: List[Dict[str, str]] = []
        if not any(d.action.capability != "wait" for d in kept):
            rejected_sigs = {
                (decisions[i].action.capability,
                 str((decisions[i].action.params or {}).get("tweet_url")
                     or (decisions[i].action.params or {}).get("profile_url")
                     or (decisions[i].action.params or {}).get("handle") or ""))
                for i in reject
            }
            alternates = list(context.scratch.get("decision_alternates") or [])
            attempts = 0
            for cand in alternates:
                if attempts >= 4:   # سقف نداءات التدقيق — لا نحرق الدورة كلها
                    break
                sig = (cand.action.capability,
                       str((cand.action.params or {}).get("tweet_url")
                           or (cand.action.params or {}).get("profile_url")
                           or (cand.action.params or {}).get("handle") or ""))
                if sig in rejected_sigs:
                    continue
                attempts += 1
                cand_reasons: dict = {}
                if self._deterministic_screen(context, [cand], [0], cand_reasons) or (
                        context.llm and context.llm.available
                        and self._llm_audit(context, [cand], [0], cand_reasons)):
                    rejected_sigs.add(sig)
                    alt_rejected.append({"arm": learning.arm_of(cand.action),
                                         "reason": str(cand_reasons.get(0, ""))})
                    continue
                cand.explanation += " | بديل بعد رفض الحارس للاختيار الأول"
                kept.append(cand)
                substituted.append(_action_guard_text(cand.action))
                break

        context.scratch["decisions"] = kept
        context.scratch["decision_log"] = [d.to_dict() for d in kept]
        context.scratch["guard_report"] = {
            "reviewed": len(guarded_idx),
            "rejected": removed,
            "reasons": {str(i): reasons.get(i, "") for i in sorted(reject)},
            "substituted": substituted,
            # تغذية التعلّم: أي ذراع (فعل × مهارة) رُفض ولماذا — بدونها يتكرّر الرفض نفسه
            "rejected_arms": [{"arm": learning.arm_of(decisions[i].action),
                               "reason": str(reasons.get(i, ""))} for i in sorted(reject)]
            + alt_rejected,
        }
        logger.info("persona guard rejected %d/%d actions: %s (reasons=%s, substituted=%s)",
                    len(reject), len(guarded_idx), removed, reasons, substituted)

    # ─────────────── layer 1: deterministic ───────────────
    @staticmethod
    def _deterministic_screen(context: "ExecutionContext", decisions,
                              guarded_idx, reasons: dict) -> set:
        """رفض قاطع للسبام/مخالفة المحظورات/تكرار موضوع سبق التفاعل معه."""
        from ..skills.builtin import (
            _is_ad_or_spam_text, _topic_recently_engaged, _violates_persona_donts,
        )

        rejected: set = set()
        for i in guarded_idx:
            a = decisions[i].action
            p = a.params or {}
            text = str(p.get("original_text") or p.get("text") or "").strip()
            brief = str(p.get("brief") or "").strip() if isinstance(p.get("brief"), str) else ""
            hay = f"{text} {p.get('handle', '')}".strip()
            try:
                if hay and _is_ad_or_spam_text(hay):
                    rejected.add(i)
                    reasons[i] = "فحص حتمي: الهدف إعلان/سبام"
                    continue
                for candidate in (hay, brief):
                    if candidate and _violates_persona_donts(candidate, context):
                        rejected.add(i)
                        reasons[i] = "فحص حتمي: يخالف محظورات الشخصية (لا تفعل)"
                        break
                if i in rejected:
                    continue
                # منع إعادة فتح نفس النقاش/الموضوع (الرد وإعادة النشر فقط —
                # الإعجاب/المتابعة لا يفتحان نقاشاً، والنشر يُفحص عبر الـbrief)
                if a.capability in ("reply_to_user", "retweet_post", "quote_post") and text \
                        and _topic_recently_engaged(context, text):
                    rejected.add(i)
                    reasons[i] = "فحص حتمي: موضوع سبق التفاعل معه (لا تكرار)"
            except Exception:  # noqa: BLE001 - screening must never break the cycle
                logger.exception("deterministic guard screen failed for action %d", i)
        return rejected

    # ─────────────── layer 2: LLM audit ───────────────
    def _llm_audit(self, context: "ExecutionContext", decisions,
                   candidates, reasons: dict) -> set:
        rules = context.config.business_rules or {}
        min_conf = float(rules.get("guard_min_confidence", 0.6))
        interests = getattr(context.config.persona, "interests", []) or []

        graph_facts = context.scratch.get("graph_handle_facts") or {}
        lines = []
        for i in candidates:
            entry = _action_guard_entry(i, decisions[i])
            # ما يعرفه الرسم المعرفي عن هذا الحساب تحديداً — يجعل الحكم مبنياً
            # على تاريخ حقيقي معه لا على نص التغريدة وحده.
            p = decisions[i].action.params or {}
            handle = str(p.get("handle") or p.get("profile_url") or "")
            key = handle.rstrip("/").rsplit("/", 1)[-1].lstrip("@").strip().lower()
            known = graph_facts.get(key) or []
            if known:
                entry += " | من ذاكرتك عنه: " + "؛ ".join(str(f)[:100] for f in known[:2])
            lines.append(entry)
        recent = (context.scratch.get("recent_activity_summary") or "").strip()
        recent_block = (
            f"\n\nسجلّ نشاط الموظف الأخير (لكشف التكرار):\n{recent[:1200]}"
            if recent else ""
        )
        prompt = (
            "أنت حارس التزام صارم ودقيق. شخصية الموظف وتعليماته في رسالة النظام أعلاه. "
            "راجع الإجراءات المقترحة التالية واحداً واحداً:\n"
            + "\n".join(lines)
            + recent_block
            + "\n\nدقّق كل إجراء مقابل هذه المعايير حصراً:\n"
            f"1) التخصص: هل المحتوى/الهدف داخل مجال الموظف ({'، '.join(interests) or 'حسب الشخصية'})؟ "
            "إجراء خارج التخصص كلياً = رفض.\n"
            "2) المحظورات والقيم: هل يخالف أي بند من (لا تفعل) أو قيم الموظف؟ = رفض.\n"
            "3) النبرة واللغة: هل يناقض نبرة الشخصية أو لغتها/لهجتها؟ = رفض.\n"
            "4) التكرار: هل يعيد فتح نقاش/موضوع ظاهر في سجل النشاط الأخير؟ = رفض.\n"
            "5) السمعة: هل فيه إساءة، تحيز خطير، معلومة غير مؤكدة تُنسب كحقيقة، "
            "أو تبنٍّ لمحتوى مسيء عبر رتويت/إعجاب؟ = رفض.\n"
            "ملاحظة: الرتويت والإعجاب تبنٍّ علني للمحتوى المستهدف نفسه — طبّق المعايير "
            "على النص المستهدف. لا ترفض لمجرد الحذر: ارفض فقط عند مخالفة واضحة.\n"
            "ملاحظة مهمة: إجراءات النشر تُعرض بـ«موجز تكليف» (تعليمات توليد ومادة خام "
            "قد تكون مقتطعة) — المنشور النهائي سيُصاغ لاحقاً وفق الشخصية ويمر بفحص جودة "
            "مستقل. احكم على موضوع التكليف ونطاقه فقط، ولا ترفضه بسبب الصياغة أو "
            "الاكتمال أو انقطاع النص.\n\n"
            "أعِد حصراً JSON بالشكل:\n"
            '{"verdicts": [{"id": رقم الإجراء, "verdict": "approve" أو "reject", '
            '"confidence": رقم من 0 إلى 1, "reason": "سبب مختصر بالعربية"}]}'
        )
        try:
            raw = context.llm.complete(
                system=context.persona_prompt(),
                prompt=prompt, temperature=0.0, max_tokens=600,
            ) or ""
        except Exception as exc:  # noqa: BLE001 - fail-open, never block the fleet
            logger.warning("persona guard LLM call failed (fail-open): %s", exc)
            return set()

        return self._parse_verdicts(raw, valid=set(candidates),
                                    min_conf=min_conf, reasons=reasons)

    @staticmethod
    def _parse_verdicts(raw: str, valid: set, min_conf: float,
                        reasons: dict) -> set:
        """Parse per-action verdicts; supports legacy {"reject": [...]} too."""
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return set()
        try:
            data = json.loads(m.group(0))
        except Exception:  # noqa: BLE001 - unparseable => fail-open
            return set()
        if not isinstance(data, dict):
            return set()

        rejected: set = set()
        for v in data.get("verdicts", []) or []:
            if not isinstance(v, dict):
                continue
            try:
                idx = int(v.get("id"))
                conf = float(v.get("confidence", 1.0))
            except (TypeError, ValueError):
                continue
            if idx not in valid:
                continue
            if str(v.get("verdict", "")).strip().lower() == "reject" and conf >= min_conf:
                rejected.add(idx)
                reason = str(v.get("reason", "") or "").strip()[:200]
                reasons[idx] = f"تدقيق LLM (ثقة {conf:.2f}): {reason}"

        # توافق رجعي مع الصيغة القديمة {"reject": [أرقام], "reasons": {...}}
        legacy_reasons = data.get("reasons", {}) if isinstance(data.get("reasons"), dict) else {}
        for i in data.get("reject", []) or []:
            try:
                idx = int(i)
            except (TypeError, ValueError):
                continue
            if idx in valid and idx not in rejected:
                rejected.add(idx)
                reasons.setdefault(
                    idx,
                    f"تدقيق LLM: {str(legacy_reasons.get(str(idx), '') or '').strip()[:200]}",
                )
        return rejected


def resume_key(capability: str, target: str) -> str:
    """مفتاح فعل على X يمنع تكراره عند استئناف دورة مقطوعة. النشر مفتاحه واحد لأن
    المحتوى يُولَّد من جديد عند الاستئناف فلا يطابق نصُّه نصَّ المنشور الأول."""
    if capability in PUBLISHING_CAPABILITIES:
        return "publish"
    if capability == "wait":
        return ""
    return f"{capability}:{target}" if target else capability


def count_success(config, capability: str, skill_id: Optional[str] = None) -> None:
    """استهلاك الميزانيات اليومية لفعل نجح (posts_today + ميزانيات التفاعل + حصة المهارة).
    يُستدعى أيضاً عند الاستئناف لأفعال نجحت قبل الانقطاع: الدورة المقطوعة لم تصل للحفظ."""
    if skill_id:
        from ..skills.custom import note_run
        note_run(config, str(skill_id))
    c = config.runtime_state.counters
    if capability in PUBLISHING_CAPABILITIES:
        c["posts_today"] = c.get("posts_today", 0) + 1
        if capability == "publish_thread":
            c["threads_today"] = c.get("threads_today", 0) + 1
    elif capability in ENGAGEMENT_BUDGETS:
        key = ENGAGEMENT_BUDGETS[capability][0]
        c[key] = c.get(key, 0) + 1


def _action_checkpoint(key: str, action, entry: Dict[str, Any], skill_id, output) -> Dict[str, Any]:
    """ما يكفي لإعادة بناء سجلات التعلّم عند الاستئناف (الدورة المقطوعة لا تصل للحفظ).
    لا يرمي أبداً: استثناء هنا كان سيُحسب فشلاً لفعل نجح فعلاً على X."""
    info: Dict[str, Any] = {"key": key, "capability": action.capability,
                            "target": entry.get("target") or "", "skill": skill_id}
    try:
        out = output if isinstance(output, dict) else {}
        p = action.params or {}
        try:
            parts = int(out.get("parts") or 1)
        except (TypeError, ValueError):
            parts = 1
        info.update(
            arm=str(entry.get("arm") or ""),
            url=str(out.get("tweet_url") or out.get("url") or "")[:300],
            handle=str(p.get("handle") or "")[:80],
            text=str(out.get("text") or out.get("reply_text") or p.get("original_text") or "")[:280],
            pillar=str(p.get("pillar") or ""), format=str(p.get("format") or ""),
            parts=parts, has_media=bool(out.get("media_url") or out.get("has_media")))
    except Exception:  # noqa: BLE001
        logger.debug("action checkpoint details skipped", exc_info=True)
    return info


class ExecutePhase(ICognitivePhase):
    """Execute the chosen actions through the capability layer."""

    name = "execute"

    def run(self, context: "ExecutionContext") -> None:
        from ..decision.engine import WeightedUtilityDecisionEngine
        from . import learning

        decisions = deepcopy(context.scratch.get("decisions", []))
        plan = []
        for i, decision in enumerate(decisions):
            entry = deepcopy(decision.to_dict())
            params = entry["params"] or {}
            entry["arm"] = learning.arm_of(decision.action)
            entry["action_id"] = f"{context.cycle_id or 'cycle'}:{i + 1}"
            entry["target"] = str(params.get("tweet_url") or params.get("profile_url")
                                  or params.get("handle") or "")
            plan.append(entry)
        context.scratch["execution_plan"] = deepcopy(plan)
        context.scratch["decision_log"] = deepcopy(plan)
        blocked_phases = set(context.scratch.get("phase_errors") or []) & {"plan", "decide", "guard"}
        if blocked_phases:
            context.emit_progress(
                "توقفت قبل التنفيذ لتعطّل اعتماد الخطة في مرحلة: "
                + "، ".join(sorted(blocked_phases)), intent="aeos_cycle_plan_review")
        else:
            _announce_execution_plan(context, plan)
        results = []
        policy = WeightedUtilityDecisionEngine()
        done_before = set(context.scratch.get("resume_skip") or ())
        for decision, entry in zip(decisions, plan):
            action = decision.action
            outcome = {"action_id": entry["action_id"], "capability": action.capability,
                       "target": entry["target"], "arm": entry["arm"]}
            key = resume_key(action.capability, entry["target"])
            context.scratch["active_action"] = deepcopy(entry)
            try:
                if key and key in done_before:
                    reason = "نُفّذ قبل انقطاع الدورة السابقة — لن أكرّره"
                    results.append({**outcome, "success": False, "status": "skipped", "error": reason})
                    context.emit_progress(f"تخطّيت {action.capability}: {reason}.",
                                          intent="aeos_cycle_action_result")
                    continue
                skill_skip = ""
                skill_id = (action.params or {}).get("_skill")
                if skill_id:
                    from ..skills import custom as CS
                    spec = next((s for s in CS.list_specs(context.config) if s.get("id") == skill_id), None)
                    skill_skip = (CS.should_run(context.config, spec, context) if spec else
                                  "المهارة ليست مثبتة لهذا الموظف")
                if blocked_phases or skill_skip or not policy._allowed(action, context):
                    reason = ("لم تكتمل مراجعة الخطة" if blocked_phases else skill_skip or
                              "لم يعد الإجراء مسموحاً وفق الحدود أو الميزانية الحالية")
                    results.append({**outcome, "success": False, "status": "skipped", "error": reason})
                    context.emit_progress(f"لم أنفّذ {action.capability}: {reason}.",
                                          intent="aeos_cycle_action_result")
                    continue
                result = context.capabilities.execute(
                    CapabilityRequest(name=action.capability, params=deepcopy(action.params)),
                    context,
                )
                results.append({
                    **outcome,
                    "status": "succeeded" if result.success else "failed",
                    "success": result.success,
                    "output": result.output,
                    "error": result.error,
                })
                if not result.success:
                    context.emit_progress(
                        f"تعذّر تنفيذ {action.capability}: {result.error or 'لم يتأكد نجاح الأداة'}.",
                        intent="aeos_cycle_action_result")
                # maintain counters (e.g. posts_today + ميزانيات التفاعل اليومية)
                if result.success:
                    # حصة المهارة المخصّصة تُستهلك عند التنفيذ لا عند الاقتراح
                    skill_id = (action.params or {}).get("_skill")
                    count_success(context.config, action.capability, skill_id)
                    if key:
                        context.checkpoint("action", **_action_checkpoint(
                            key, action, entry, skill_id, result.output))
            except Exception as exc:  # noqa: BLE001
                logger.exception("capability execution failed")
                results.append({
                    **outcome, "success": False, "status": "failed", "error": str(exc),
                })
                context.emit_progress(
                    f"تعذّر تنفيذ {action.capability}: {str(exc)[:200]}.",
                    intent="aeos_cycle_action_result")
            finally:
                context.scratch.pop("active_action", None)
        context.scratch["execution_results"] = results
        # سجّل أنواع الإجراءات المنفّذة بنجاح هذه الدورة، ليتجنّب محرّك القرار
        # تكرار نفس النوع في الدورة التالية (تنويع فعلي عبر الدورات).
        done_types = sorted({
            r["capability"] for r in results
            if r.get("success") and r.get("capability") != "wait"
        })
        context.config.runtime_state.flags["last_action_types"] = done_types
        # سجل متدحرج لأنواع إجراءات آخر 6 دورات (الأحدث أولاً) — يغذّي عقوبة
        # التكرار المتدرّجة في محرّك القرار فلا يتكرر نفس النوع دورةً بعد دورة.
        if done_types:
            hist = list(context.config.runtime_state.flags.get("action_type_history") or [])
            hist.insert(0, done_types)
            context.config.runtime_state.flags["action_type_history"] = hist[:6]


class EvaluatePhase(ICognitivePhase):
    """Judge outcomes and update goal progress."""

    name = "evaluate"

    def run(self, context: "ExecutionContext") -> None:
        results = context.scratch.get("execution_results", [])
        successes = sum(1 for r in results if r.get("success"))
        skipped = sum(1 for r in results if r.get("status") == "skipped")
        failures = len(results) - successes - skipped
        context.scratch["evaluation"] = {
            "actions": len(results), "successes": successes, "failures": failures,
            "skipped": skipped,
        }
        # naive goal progress bump on success
        if successes:
            for g in context.config.goals:
                if g.status.value in ("pending", "in_progress"):
                    g.progress = min(1.0, g.progress + 0.02 * successes)


class LearnPhase(ICognitivePhase):
    """Reflect and persist lessons into reflection memory."""

    name = "learn"

    def run(self, context: "ExecutionContext") -> None:
        from . import learning

        evaluation = context.scratch.get("evaluation", {})
        results = context.scratch.get("execution_results", [])
        # التجربة أولاً: كل ذراع (فعل × مهارة) قُبل/فشل/رُفض يُحدّث سياسة القرار للدورة التالية
        try:
            exp = learning.record_cycle(context)
        except Exception:  # noqa: BLE001 - التعلّم لا يُسقط الدورة
            logger.exception("experience recording failed")
            exp = {"ok": [], "fail": [], "rejected": [], "suspended": []}
        context.scratch["experience"] = exp
        # درس مقروء من التجربة (كان قالباً ثابتاً «executed X(succeeded)» لا يُتعلَّم منه)
        bits = []
        if exp["ok"]:
            bits.append("نجح: " + "، ".join(dict.fromkeys(exp["ok"])))
        if exp["fail"]:
            bits.append("فشل: " + "، ".join(dict.fromkeys(exp["fail"])))
        if exp["rejected"]:
            bits.append("رفض الحارس: " + "؛ ".join(
                f"{r['arm']} ({r['reason'][:70]})" for r in exp["rejected"][:3]))
        if exp["suspended"]:
            bits.append("أوقفت مؤقتاً: " + "، ".join(
                f"{s['arm']} حتى الدورة {s['until']}" for s in exp["suspended"]))
        lesson = (f"دورة {context.event.type.value}: " + " · ".join(bits) if bits else
                  f"Cycle via {context.event.type.value}: chose to wait; no actions taken.")
        # لا نكرّر الدرس نفسه: قيس 100 سجل «chose to wait» لموظف واحد تُغرق الذاكرة
        last = context.memory.recall(context.config.id, MemoryLayerType.REFLECTION, limit=1)
        if not (last and last[0].content == lesson):
            context.memory.learn(context.config.id, lesson, importance=1 if not results else 2)
        context.scratch["lesson"] = lesson
        # احتفاظ محدود بذاكرة الدورة (كل 25 دورة) — النمو بلا حد يبطئ الاسترجاع
        if context.config.runtime_state.cycles_completed % 25 == 0:
            try:
                from ..memory import agent_memory as AM

                AM.prune_employee_memory(context.config.id)
            except Exception:  # noqa: BLE001
                logger.debug("memory pruning failed", exc_info=True)


class CritiquePhase(ICognitivePhase):
    """حارس التقييم: يحكم على مخرجات هذه الدورة ويصحّح تعليمات الدورة القادمة.

    يجري بعد ``LearnPhase`` (فالنتائج والدرس جاهزان) وقبل ``GraphMemoryPhase``
    (فتُغذّى الذاكرة المعرفية بالتقييم أيضاً). يجمع أرقام الدورة الحقيقية
    (رفض الحارس، الفشل، التكرار، الهاشتاقات، الردّ على الجمهور، اتجاه التفاعل)
    ثم يُصدر تقييماً بدرجة وتوجيهات أمرية تُحقن تلقائياً في ``persona_prompt``
    للدورة التالية. المشكلة التي تتكرّر ترتفع صرامتها حتى تُعالَج.
    """

    name = "critique"

    def run(self, context: "ExecutionContext") -> None:
        from . import critique as C

        try:
            result = C.build_critique(context)
        except Exception as exc:  # noqa: BLE001 - التقييم لا يُسقط الدورة أبداً
            logger.exception("critique failed: %s", exc)
            return
        from . import learning

        # فاعلية الدروس: مشكلة اختفت بعد درسها = درس نافع؛ تكرّرت رغمه = غير نافع ويُصعَّد
        escalated = learning.track_lessons(context.config, result["findings"].get("issues") or [])
        for esc in escalated:
            result.setdefault("directives", []).insert(
                0, f"⚠️ الدرس «{esc['lesson'][:90]}» لم ينفع بعد {esc['seen_after']} دورات — "
                   "غيّر النهج كلياً لا الصياغة.")
        C.store_critique(context.config, result)
        curve = learning.record_score(context.config, float(result.get("score") or 0))
        context.scratch["critique"] = result
        # الدروس طويلة المدى: الصيغة المكرّرة لفكرة قائمة تقوّي الأصل ولا تُضاف نسخة
        # (قيس: 5 صيغ لـ«انشر عن اليمن بعنوان صادم» محفوظة كخمسة دروس مستقلة).
        try:
            from ..memory import agent_memory as AM

            scope = AM.employee_scope(context.config.id)
            existing = AM.list_memories([scope], 60, kinds=("lesson",))
            for d in (result.get("directives") or [])[:3]:
                dup = learning.near_duplicate(str(d), existing)
                if dup:
                    AM.reinforce(dup["id"], scope)
                else:
                    saved = AM.add(scope, str(d), kind="lesson", importance=3,
                                   meta={"source": "critique",
                                         "cycle": context.config.runtime_state.cycles_completed})
                    if saved:
                        existing.append(saved)
        except Exception:  # noqa: BLE001
            logger.debug("storing critique lessons failed", exc_info=True)

        account = getattr(context.config, "account_username", "") or "الموظف الذكي"
        lines = [f"🧪 **تقييم دورة @{account}: {result.get('score')}/10**"]
        if curve.get("trend") in ("up", "down", "flat"):
            arrow = {"up": "↑ يتحسّن", "down": "↓ يتراجع", "flat": "→ مستقر"}[curve["trend"]]
            lines[0] += f" · المنحنى {curve['recent_avg']} (كان {curve['previous_avg']}) {arrow}"
        if result.get("verdict"):
            lines.append(f"_{result['verdict']}_")
        exp = context.scratch.get("experience") or {}
        for s in exp.get("suspended") or []:
            lines.append(f"• تعلّمت: أوقفت «{s['arm']}» مؤقتاً حتى الدورة {s['until']} — رُفض مراراً")
        for d in (result.get("directives") or [])[:3]:
            lines.append(f"• سأصحّح في الدورة القادمة: {d}")
        context.emit_progress("\n".join(lines), intent="aeos_cycle_critique")


class SupportPhase(ICognitivePhase):
    """Detect blockers the employee cannot resolve alone and ask the user for help.

    The employee is autonomous, but some problems require a human: an expired X
    session/cookies, every action failing, a persona with no defined specialty,
    or prolonged inactivity because nothing matches its interests. When detected,
    a concise ``help_request`` is placed on the scratch so the runtime can post a
    support message into the user's chat. A per-problem cooldown (based on the
    cycle counter) prevents spamming the same request every cycle.
    """

    name = "support"

    _AUTH_HINTS = (
        "login", "log in", "signin", "sign in", "cookie", "cookies", "auth",
        "unauthorized", "401", "403", "session", "expired", "not logged",
        "تسجيل الدخول", "الدخول", "كوكيز", "الجلسة", "انتهت", "صلاحية",
    )
    _COOLDOWN_CYCLES = 3

    def run(self, context: "ExecutionContext") -> None:
        obs = context.scratch.get("observations", {}) or {}
        results = [r for r in context.scratch.get("execution_results", []) or []
                   if r.get("status") != "skipped"]
        rs = context.config.runtime_state
        flags = rs.flags

        problems: List[Dict[str, str]] = []

        # 1) auth/cookie/session failure while browsing X
        for key in ("timeline_error", "x_trends_error"):
            err = str(obs.get(key) or "")
            if err and any(h in err.lower() for h in self._AUTH_HINTS):
                problems.append({
                    "kind": "auth",
                    "message": (
                        "يبدو أن جلسة/كوكيز حساب X انتهت أو لم تعد صالحة، فلا أستطيع "
                        "تصفّح المنصة أو التفاعل. أرجو إعادة رفع ملف الكوكيز للحساب."
                    ),
                })
                break

        # 2) every executed action failed this cycle
        if results:
            failed = [r for r in results if not r.get("success")]
            if failed and len(failed) == len(results):
                sample = "؛ ".join(
                    f"{r.get('capability')}: {str(r.get('error') or '')[:80]}"
                    for r in failed[:2]
                )
                problems.append({
                    "kind": "action_fail",
                    "message": (
                        "فشلت كل الإجراءات التي حاولت تنفيذها في هذه الدورة. "
                        f"تفاصيل: {sample}. قد تكون هناك مشكلة في الاتصال بالمنصة."
                    ),
                })

        # 3) persona has no specialty/interests -> cannot stay in-scope
        interests = getattr(context.config.persona, "interests", []) or []
        if not interests:
            problems.append({
                "kind": "persona",
                "message": (
                    "لا توجد اهتمامات/تخصص محدّد في برسونا الموظف، لذا لا أستطيع "
                    "التركيز على مجال واضح. أرجو تحديد الاهتمامات وأسلوب الشخصية."
                ),
            })

        # 4) prolonged inactivity: nothing to do for several cycles in a row
        no_action = not results
        streak = int(flags.get("noop_streak", 0))
        streak = streak + 1 if no_action else 0
        flags["noop_streak"] = streak
        if streak and streak % 6 == 0:
            problems.append({
                "kind": "idle",
                "message": (
                    f"مرّت {streak} دورات دون أي إجراء لعدم توفر مواضيع أو تفاعلات "
                    "ضمن تخصصي. قد يفيد توسيع الاهتمامات أو خفض صرامة المطابقة "
                    "(min_relevance_score) أو التأكد من مصادر الترند/الأخبار."
                ),
            })

        if not problems:
            context.scratch["help_request"] = None
            return

        # cooldown per problem kind to avoid repeating the same ask every cycle
        help_history: Dict[str, int] = flags.setdefault("help_history", {})
        now_cycle = int(rs.cycles_completed)
        chosen = None
        for p in problems:
            last = help_history.get(p["kind"])
            if last is None or (now_cycle - int(last)) >= self._COOLDOWN_CYCLES:
                chosen = p
                break

        if chosen is None:
            context.scratch["help_request"] = None
            return

        help_history[chosen["kind"]] = now_cycle
        context.scratch["help_request"] = {
            "kind": chosen["kind"],
            "message": chosen["message"],
            "all": [p["message"] for p in problems],
        }


class EmotionPhase(ICognitivePhase):
    """Zep/Graphiti-backed emotional & behavioral state.

    يستعلم من الرسم المعرفي الزمني (Graphiti) عن حقائق مستخرجة من تاريخ
    الموظف — مشاعر الجمهور، سلوك الحسابات المتفاعَل معها، ما نجح وما فشل —
    ثم يستنتج عبر نداء LLM واحد "حالة عاطفية/سلوكية" لهذه الدورة:
    المزاج، تعديل النبرة، قواعد سلوك، وما يجب تجنّبه. تُحقن هذه الحالة في
    ``persona_prompt`` فتتحكم بأسلوب كل منشور/رد/تفاعل في الدورة.

    **مبدأ حاسم**: الحالة تُستنتج من **حقائق الذاكرة المعرفية فقط**، لا من
    التعليمات الثابتة. كان البرومبت يبدأ بـ``persona_prompt()`` كاملاً (تعليمات
    المستخدم، افعل/لا تفعل، القيم، وتوجيهات حارس التقييم)، فكان النموذج يعيد
    صياغتها كـ«قواعد سلوك» فتصير الحالة نسخةً من التعليمات لا انعكاساً لتجربة
    الحساب مع جمهوره. الآن لا تدخل التعليمات البرومبت إطلاقاً — يُمرَّر تعريف
    هوية أدنى (الاسم/الدور/المجال/اللغة) والحقائق مرقّمة، ويُطلب من النموذج
    الإشارة إلى أرقام الحقائق التي بنى عليها (``based_on``)، مع حارس حتمي يرفض
    أي سطر يكرّر التعليمات حرفياً.

    التعطّل الآمن: بدون Graphiti أو LLM أو حقائق تُترك الحالة فارغة ويستمر كل شيء.
    """

    name = "emotion"

    #: نسبة تشابه تعني «هذا السطر إعادة صياغة لتعليمة ثابتة» فيُرفض
    _ECHO_THRESHOLD = 0.7

    def __init__(self, graph_store=None) -> None:
        self._graph = graph_store
        # ذاكرة تخزين مؤقتة داخل نسخة الطور (تعيش مع الـ runtime): تمنع نداء
        # LLM جديداً كل دورة عندما لا تتغيّر حقائق الرسم المعرفي.
        self._cache: Dict[str, Any] = {}

    def _build_queries(self, context: "ExecutionContext") -> List[str]:
        """استعلامات قصيرة مركّزة (لا استعلاماً واحداً مخلوطاً).

        البحث الهجين (BM25 + تشابه دلالي) يفشل تماماً مع "حساء كلمات" طويل،
        لذا نسأل الرسم المعرفي عدة أسئلة محدّدة: التخصص، ما فشل ولماذا،
        الدروس وما نجح، والحسابات التي نتفاعل معها الآن.
        """
        p = context.config.persona
        queries: List[str] = []
        interests = [str(i) for i in (getattr(p, "interests", None) or [])][:3]
        if interests:
            queries.append("تفاعل الجمهور وآراؤه حول " + "، ".join(interests))
        # إشارات الجمهور أولاً: هي مصدر «المشاعر» الحقيقي في الرسم المعرفي
        # (حلقات الدورات تسجّل «إشارات_الجمهور» صراحةً).
        queries.append("إشارات الجمهور وردودهم ومشاعرهم تجاه الحساب")
        queries.append("إجراءات فشلت وأسباب الفشل والرفض")
        queries.append("الدروس المستفادة وما نجح في جذب التفاعل")

        # الحسابات الحاضرة في هذه الدورة (تايم لاين/منشنات) — سلوكها السابق
        handles: List[str] = []
        obs = context.scratch.get("observations", {}) or {}
        for item in (obs.get("timeline_preview") or [])[:5]:
            if isinstance(item, dict) and item.get("handle"):
                handles.append(str(item["handle"]))
        for item in (context.scratch.get("mentions") or [])[:5]:
            if isinstance(item, dict):
                h = item.get("handle") or item.get("author") or item.get("username")
                if h:
                    handles.append(str(h))
        handles = [h for h in dict.fromkeys(handles) if h][:5]
        if handles:
            queries.append("سلوك وتفاعل الحسابات " + " ".join(handles))
        return queries

    @staticmethod
    def _candidate_handles(context: "ExecutionContext") -> List[str]:
        """الحسابات التي قد نتفاعل معها هذه الدورة (بترتيب تكرار الظهور).

        هذه هي الكيانات التي يجب أن نسأل الرسم المعرفي عنها **قبل** القرار،
        لا بعده: من هو هذا الحساب في تاريخنا؟ هل تجاوب سابقاً؟ هل حدث معه ما
        يستحق الحذر؟ بدون هذا كان الرسم يُقرأ باستعلامات عامة فقط.
        """
        from collections import Counter

        counts: "Counter[str]" = Counter()
        for key in ("mentions", "top_posts", "interest_tweets", "hashtag_tweets",
                    "trend_tweets", "timeline"):
            for item in (context.scratch.get(key) or [])[:12]:
                if not isinstance(item, dict):
                    continue
                handle = str(item.get("handle") or item.get("author")
                             or item.get("username") or "").strip().lstrip("@").lower()
                if handle:
                    counts[handle] += 1
        return [h for h, _ in counts.most_common(5)]

    def _load_handle_facts(self, context: "ExecutionContext") -> None:
        """حمّل ما يعرفه الرسم المعرفي عن أهداف هذه الدورة (لقرار التفاعل)."""
        context.scratch["graph_handle_facts"] = {}
        if not hasattr(self._graph, "facts_about"):
            return
        handles = self._candidate_handles(context)
        if not handles:
            return
        try:
            known = self._graph.facts_about(
                context.config.id, [f"@{h}" for h in handles], limit=3) or {}
        except Exception:  # noqa: BLE001 - الذاكرة لا تُسقط الدورة أبداً
            logger.exception("graph facts_about failed")
            return
        context.scratch["graph_handle_facts"] = known
        if known:
            logger.info("graph memory: recalled facts about %d target handles",
                        len(known))

    def run(self, context: "ExecutionContext") -> None:
        context.scratch["emotion_state"] = None
        context.scratch["graph_facts"] = []
        context.scratch["graph_handle_facts"] = {}
        if self._graph is None or not getattr(self._graph, "available", False):
            return

        # معرفة موجّهة بالهدف: تُستهلك في محرّك القرار وحارس الشخصية
        self._load_handle_facts(context)

        facts = self._graph.search_facts(
            context.config.id, self._build_queries(context), limit=12
        )
        context.scratch["graph_facts"] = facts
        if not facts:
            return

        numbered = [f"[{i + 1}] {f}" for i, f in enumerate(facts[:12])]
        facts_block = "\n".join(numbered)
        # نفس الحقائق ⇒ نفس الحالة: أعِد استخدام آخر استنتاج بدل نداء LLM جديد
        cache_key = hashlib.sha1(facts_block.encode("utf-8", "ignore")).hexdigest()
        cached = self._cache.get(context.config.id)
        if cached and cached.get("key") == cache_key:
            context.scratch["emotion_state"] = cached["state"]
            return

        state: Dict[str, Any] = {}
        if context.llm and context.llm.available:
            state = context.llm.complete_json(
                system=(
                    "أنت وحدة التحكم العاطفي والسلوكي لموظف ذكي يدير حساباً على منصة X. "
                    "مصدرك الوحيد هو حقائق مستخرجة من ذاكرته المعرفية الزمنية "
                    "(Zep/Graphiti): ما قاله جمهوره فعلاً، وما نجح وما فشل من تجاربه. "
                    "استنتج حالته العاطفية والسلوكية لهذه الدورة من هذه الحقائق وحدها. "
                    "قواعد إلزامية: (1) لا تخترع شيئاً لا تسنده حقيقة مذكورة. "
                    "(2) ممنوع كتابة تعليمات عامة عن الهوية أو التخصص أو الأدب أو "
                    "اللهجة — تلك محسومة سلفاً ولا تخصّك. (3) كل قاعدة سلوك يجب أن "
                    "تكون رد فعل على تجربة محدّدة في الحقائق (مثال: «الجمهور طلب "
                    "تفاصيل عملية ⇒ ابدأ بالخطوة لا بالمقدمة»). "
                    "أجب JSON فقط بالمفاتيح: "
                    '{"mood": "وصف موجز للمزاج مستنتَج من الحقائق", '
                    '"tone_adjustment": "تعديل النبرة الذي تفرضه هذه التجارب", '
                    '"behavior_rules": ["قاعدة سلوك ناتجة عن حقيقة محدّدة", "..."], '
                    '"avoid": ["ما يجب تجنّبه لأن حقيقةً معيّنة أثبتت ضرره", "..."], '
                    '"based_on": [أرقام الحقائق التي بنيتَ عليها], '
                    '"evidence": "سطر واحد يشرح الرابط بين الحقائق والحالة"}'
                ),
                prompt=(
                    # هوية أدنى فقط — بلا تعليمات ثابتة ولا محظورات ولا توجيهات
                    # حارس، حتى لا يعيد النموذج صياغتها كحالة عاطفية.
                    f"{self._identity_brief(context)}\n\n"
                    f"حقائق ذاكرته المعرفية (مصدرك الوحيد، مرقّمة):\n{facts_block}\n\n"
                    "استنتج الحالة العاطفية/السلوكية لهذه الدورة من هذه الحقائق، "
                    "وأشر إلى أرقام الحقائق التي استندت إليها."
                ),
                temperature=0.2,
            ) or {}

        if not isinstance(state, dict) or not state:
            return

        based_on = []
        for item in (state.get("based_on") or []):
            try:
                idx = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= idx <= len(numbered):
                based_on.append(idx)

        # حارس حتمي: أي سطر يكرّر تعليمة ثابتة ليس «حالة عاطفية» بل نسخاً منها
        rules = self._drop_instruction_echoes(
            context, [str(x)[:160] for x in (state.get("behavior_rules") or [])][:6])
        avoid = self._drop_instruction_echoes(
            context, [str(x)[:160] for x in (state.get("avoid") or [])][:6])

        cleaned = {
            "mood": str(state.get("mood", "")).strip()[:120],
            "tone_adjustment": str(state.get("tone_adjustment", "")).strip()[:200],
            "behavior_rules": rules[:5],
            "avoid": avoid[:5],
            "based_on": based_on,
            "evidence": str(state.get("evidence", "")).strip()[:240],
            "facts_used": [facts[i - 1][:200] for i in based_on][:5],
        }
        if not (cleaned["mood"] or cleaned["tone_adjustment"]
                or cleaned["behavior_rules"] or cleaned["avoid"]):
            logger.info("emotion state dropped: nothing left after echo filtering")
            return
        if not based_on:
            # ليست خطأً قاتلاً (الحقائق هي المصدر الوحيد في البرومبت أصلاً)،
            # لكن غياب الإسناد يستحق التسجيل لأنه يُضعف الشفافية.
            logger.info("emotion state has no fact references (based_on empty)")

        context.scratch["emotion_state"] = cleaned
        self._cache[context.config.id] = {"key": cache_key, "state": cleaned}
        if cleaned.get("mood"):
            line = (f"🧭 الحالة العاطفية لهذه الدورة: {cleaned['mood']}"
                    + (f" — {cleaned['tone_adjustment']}" if cleaned.get("tone_adjustment") else ""))
            if cleaned.get("evidence"):
                line += f"\n_مستنتَجة من ذاكرته المعرفية: {cleaned['evidence']}_"
            elif cleaned.get("facts_used"):
                line += f"\n_بناءً على: {cleaned['facts_used'][0]}_"
            context.emit_progress(line)

    @staticmethod
    def _identity_brief(context: "ExecutionContext") -> str:
        """تعريف هوية أدنى: من هو وفي أي مجال — بلا أي تعليمات أو محظورات."""
        p = context.config.persona
        name = p.display_name or context.config.account_username
        interests = "، ".join((getattr(p, "interests", None) or [])[:4])
        parts = [f"الحساب: {name} ({p.role or 'صانع محتوى'}) على {context.config.platform}."]
        if interests:
            parts.append(f"مجاله: {interests}.")
        parts.append(f"لغة الكتابة: {p.language or 'ar'}.")
        return " ".join(parts)

    def _drop_instruction_echoes(self, context: "ExecutionContext",
                                 lines: List[str]) -> List[str]:
        """أزل الأسطر التي هي إعادة صياغة لتعليمات الشخصية الثابتة.

        الحالة العاطفية يجب أن تنبع من تجربة الحساب مع جمهوره؛ سطرٌ يكرّر
        «التزم بتخصصك» أو «لا تخرج عن المجال» ليس مشاعر بل تعليمات مكرّرة.
        """
        from ..employee.persona_templates import (
            normalize_topic_words, topic_overlap_ratio,
        )

        p = context.config.persona
        instruction_texts = [str(p.instructions or "")]
        for field in ("dos", "donts", "values"):
            instruction_texts += [str(x) for x in (getattr(p, field, None) or [])]
        signatures = [normalize_topic_words(t) for t in instruction_texts if len(t) > 8]
        if not signatures:
            return lines

        kept: List[str] = []
        for line in lines:
            words = normalize_topic_words(line)
            if len(words) >= 3 and any(
                topic_overlap_ratio(words, sig) >= self._ECHO_THRESHOLD
                for sig in signatures
            ):
                logger.info("emotion: dropped instruction echo -> %s", line[:80])
                continue
            kept.append(line)
        return kept


class GraphMemoryPhase(ICognitivePhase):
    """Feed the finished cycle into the Zep/Graphiti knowledge graph.

    يبني حلقة (Episode) **مهيكلة** للدورة: الفاعل والمنصة، كل إجراء بهدفه
    ومحتواه ونتيجته وسبب فشله، ما رفضه حارس الشخصية ولماذا، إشارات الجمهور
    (منشنات/ردود وصلت)، حصيلة التقييم، الدرس المستفاد، والحالة العاطفية.
    الحلقة المهيكلة (JSON) تعطي Graphiti استخراجاً أدقّ بكثير من نص حرّ،
    فتُبنى حقائق زمنية قابلة للاسترجاع تغذّي دورات ``EmotionPhase`` القادمة.
    """

    name = "graph_memory"

    def __init__(self, graph_store=None) -> None:
        self._graph = graph_store

    @staticmethod
    def _actions(context: "ExecutionContext") -> List[Dict[str, Any]]:
        actions: List[Dict[str, Any]] = []
        for r in context.scratch.get("execution_results", []) or []:
            out = r.get("output") if isinstance(r.get("output"), dict) else {}
            skipped = r.get("status") == "skipped"
            action: Dict[str, Any] = {
                "الإجراء": r.get("capability", "?"),
                "النتيجة": "لم يُنفّذ" if skipped else ("نجح" if r.get("success") else "فشل"),
            }
            if not r.get("success") and r.get("error"):
                action["سبب_عدم_التنفيذ" if skipped else "سبب_الفشل"] = str(r["error"])[:160]
            target = out.get("handle") or out.get("username") or r.get("target") or out.get("tweet_url")
            if target:
                action["الهدف"] = str(target)[:120]
            content = out.get("text") or out.get("reply_text") or out.get("content")
            if content:
                action["المحتوى"] = str(content)[:280]
            topic = out.get("topic") or out.get("hashtag")
            if topic:
                action["الموضوع"] = str(topic)[:120]
            actions.append(action)
        return actions

    @staticmethod
    def _audience(context: "ExecutionContext") -> List[Dict[str, str]]:
        """ردود ومنشنات وصلت للموظف — مصدر مشاعر الجمهور الحقيقي."""
        signals: List[Dict[str, str]] = []
        for m in (context.scratch.get("mentions") or [])[:5]:
            if not isinstance(m, dict):
                continue
            handle = m.get("handle") or m.get("author") or m.get("username") or ""
            text = m.get("text") or m.get("content") or ""
            if text:
                signals.append({"من": str(handle)[:60], "قال": str(text)[:200]})
        return signals

    def run(self, context: "ExecutionContext") -> None:
        if self._graph is None or not getattr(self._graph, "available", False):
            return

        actor = (context.config.persona.display_name
                 or context.config.account_username or context.config.id)
        actions = self._actions(context)
        lesson = context.scratch.get("lesson", "")
        guard = context.scratch.get("guard_report") or {}
        rejected = guard.get("rejected") or []
        audience = self._audience(context)
        emo = context.scratch.get("emotion_state") or {}

        if not actions and not lesson and not audience \
                and not context.scratch.get("interaction_credits"):
            return

        # حصراً ما يحمل معنى قابلاً للتعلّم. البيانات الدفترية (رقم الدورة،
        # المنصة، الطابع الزمني) كانت تُستخرج كـ«حقائق» من نوع «عماد يعمل على
        # منصة X في الدورة 145» فتزحم فضاء الحقائق وتُشوّش الحالة العاطفية —
        # ورقم الدورة محفوظ أصلاً في اسم الحلقة وتاريخها.
        episode: Dict[str, Any] = {"الموظف": actor}
        if actions:
            episode["الإجراءات"] = actions
        if rejected:
            episode["رفض_حارس_الشخصية"] = {
                "عدد": len(rejected),
                "إجراءات": [str(x)[:120] for x in rejected[:5]],
                "الأسباب": [str(v)[:160] for v in (guard.get("reasons") or {}).values() if v][:5],
            }
        if audience:
            episode["إشارات_الجمهور"] = audience
        # أثر التفاعل لا سجلّه: «ردّ ناجح» يعني أن الزر عمل، أما «@فلان تجاوب مع
        # ردّنا» فهو المعلومة الوحيدة القابلة للتعلّم فعلاً. بدونها كان الرسم
        # يحفظ ما فعلناه ولا يحفظ أبداً ما نفع.
        credits = context.scratch.get("interaction_credits") or []
        if credits:
            episode["تفاعلات_أثمرت"] = [
                {"الحساب": f"@{c.get('handle', '')}", "نوع_التفاعل": c.get("cap", "")}
                for c in credits[:5]
            ]
        try:
            from ..employee import interaction_ledger as IL

            if IL.has_evidence(context.config):
                rep = IL.report(context.config, limit=3)
                episode["حصيلة_تفاعلي"] = {
                    "إجمالي": rep["samples"],
                    "أثمر": rep["reciprocated"],
                    "عائد_كل_نوع": {
                        cap: f"{v['reciprocated']}/{v['count']}"
                        for cap, v in (rep.get("by_capability") or {}).items()
                    },
                    "أكثر_الحسابات_تجاوباً": [
                        h["handle"] for h in (rep.get("top_handles") or [])
                        if h["reciprocated"]
                    ][:3],
                }
        except Exception:  # noqa: BLE001 - التغذية لا تُسقط الدورة
            logger.exception("interaction ledger summary for graph failed")
        evaluation = context.scratch.get("evaluation") or {}
        if evaluation:
            episode["الحصيلة"] = {
                "إجراءات": evaluation.get("actions", 0),
                "ناجحة": evaluation.get("successes", 0),
                "فاشلة": evaluation.get("failures", 0),
            }
        if lesson:
            episode["الدرس_المستفاد"] = str(lesson)[:400]
        if emo.get("mood"):
            # نُسجّل المزاج **مع سببه من التجربة**، فتتراكم في الرسم «ذاكرة
            # مشاعر مرتبطة بأسبابها» لا مزاجٌ معلّق بلا مصدر.
            episode["الحالة_العاطفية"] = {
                "المزاج": str(emo["mood"])[:120],
                "سببها": str(emo.get("evidence") or "")[:200],
            }

        # خطة المحتوى وأداؤها — بهذا يستخرج الرسم المعرفي حقائق مثل «الصيغة س
        # تحقّق تفاعلاً أعلى» فتغذّي دورات لاحقة (تعلّم فعلي لا سجلّ فقط).
        plan = context.scratch.get("content_plan") or {}
        if plan:
            episode["خطة_المحتوى"] = {
                "المحور": plan.get("pillar", ""),
                "الصيغة": plan.get("format_label", ""),
                "عدد_التغريدات": plan.get("parts", 1),
            }
        # تقييم الحارس: يجعل الرسم المعرفي يحمل حكماً على الجودة لا سجلّ إجراءات
        crit = context.scratch.get("critique") or {}
        if crit:
            episode["تقييم_الدورة"] = {
                "الدرجة": crit.get("score"),
                "الحكم": str(crit.get("verdict") or "")[:200],
                "نقاط_القوة": [str(s)[:120] for s in (crit.get("strengths") or [])[:3]],
                "الخلل": [str(w)[:140] for w in (crit.get("weaknesses") or [])[:3]],
                "توجيهات_الدورة_القادمة": [
                    str(d)[:140] for d in (crit.get("directives") or [])[:4]
                ],
            }
        report = context.scratch.get("performance_report") or {}
        if report.get("samples"):
            formats = report.get("formats") or {}
            pillars = report.get("pillars") or {}
            episode["أداء_محتواي"] = {
                "منشورات_مقيسة": report["samples"],
                "أنجح_الصيغ": [
                    f"{k}: متوسط تفاعل {v['avg_engagement']} من {v['posts']} منشور"
                    for k, v in list(formats.items())[:3]
                ],
                "أنجح_المحاور": [
                    f"{k}: متوسط تفاعل {v['avg_engagement']} من {v['posts']} منشور"
                    for k, v in list(pillars.items())[:3]
                ],
                "أفضل_ساعات_النشر": [h["hour"] for h in (report.get("best_hours") or [])[:3]],
            }

        self._graph.ingest(
            context.config.id,
            episode,
            name=f"cycle-{context.cycle_id or context.config.runtime_state.cycles_completed}",
            source_description=f"دورة معرفية لموظف AEOS على {context.config.platform}",
        )
        context.scratch["graph_ingested"] = True


class SavePhase(ICognitivePhase):
    """Persist runtime state back onto the (permanent) employee config."""

    name = "save"

    def run(self, context: "ExecutionContext") -> None:
        rs = context.config.runtime_state
        rs.last_cycle_at = datetime.now(timezone.utc)
        rs.cycles_completed += 1
        rs.last_decision = (context.scratch.get("decision_log") or [None])[0]
        rs.current_plan = context.scratch.get("plan_preview", [])
        # working memory scratch snapshot (small, useful for next cycle)
        rs.working_memory = {
            "last_thought": context.scratch.get("thought", ""),
            "last_lesson": context.scratch.get("lesson", ""),
        }
        context.scratch["saved"] = True
