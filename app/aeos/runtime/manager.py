"""
RuntimeManager — the heart of the platform.

It is the ONLY component allowed to bring an AI Employee "to life", and it does
so strictly for one cycle:

    1. LOAD    the permanent employee config (via repository)
    2. BUILD   a temporary ExecutionContext (memory, workspace, MCP, capabilities)
    3. RUN     exactly one CognitiveCycle
    4. PERSIST runtime state, goal progress, and an audit log
    5. DESTROY the runtime (clear working memory, tmp) and reschedule next wake

The runtime is temporary; the employee is permanent. Nothing about the employee
lives in memory between cycles except what step 4 persists.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from app.db.database import SessionLocal
from app.db.models import ExecutionLogRecord

from ..core.events import Event
from ..core.exceptions import EmployeeNotFoundError
from ..core.interfaces import IEmployeeRepository, IRuntimeManager
from ..cognition.context import ExecutionContext
from ..cognition.cycle import CognitiveCycle
from ..employee.models import EmployeeStatus

logger = logging.getLogger("aeos.runtime")


# Human-readable Arabic verbs for what the employee actually did this cycle.
_ACTION_VERBS = {
    "publish_content": "نشر تغريدة جديدة",
    "reply_to_user": "ردّ على تغريدة",
    "retweet_post": "أعاد نشر تغريدة",
    "like_post": "تفاعل بإعجاب مع تغريدة",
    "follow_user": "تابع حساباً جديداً",
    "research_topic": "بحث في موضوع",
    "store_knowledge": "حفظ معلومة في ذاكرته",
    "browse_timeline": "تصفّح الخط الزمني",
    "wait": "اكتفى بالمراقبة دون إجراء",
}


def _action_line(result: Dict[str, Any]) -> str:
    """Turn a single execution result into a short human sentence."""
    if not isinstance(result, dict):
        return ""
    cap = result.get("capability") or "إجراء"
    ok = bool(result.get("success"))
    verb = _ACTION_VERBS.get(cap, cap)
    output = result.get("output") or {}
    mark = "✓" if ok else "✗"

    detail = ""
    if ok and isinstance(output, dict):
        if cap == "publish_content":
            txt = (output.get("text") or "").strip().replace("\n", " ")
            if txt:
                detail = f": «{txt[:140]}»"
        elif cap == "reply_to_user":
            txt = (output.get("reply_text") or "").strip().replace("\n", " ")
            if txt:
                detail = f": «{txt[:120]}»"
    if not ok:
        err = (result.get("error") or "").strip()
        if result.get("status") == "skipped":
            return f"لم يُنفّذ {cap}: {err[:100]}"
        if err:
            detail = f" (تعذّر: {err[:100]})"
    target = str(result.get("target") or "")
    return f"{mark} {verb}{detail}" + (f" — {target}" if target else "")


def _observed_line(observations: Dict[str, Any]) -> str:
    """Summarize what the employee saw this cycle (trends/timeline/news)."""
    if not isinstance(observations, dict):
        return ""
    bits: list = []
    top_trend = observations.get("x_top_trend")
    if top_trend:
        bits.append(f"أبرز ترند على X: {str(top_trend)[:60]}")
    hashtags = [h for h in (observations.get("trend_hashtags") or []) if h]
    if hashtags:
        bits.append("وسوم الترند: " + " ".join(str(h)[:30] for h in hashtags[:4]))
    matched = observations.get("matched_hashtag")
    if matched:
        bits.append(f"الوسم المطابق لتخصصه: {str(matched)[:40]}")
    if observations.get("top_posts_count"):
        bits.append(f"{observations['top_posts_count']} من أهم بوستات منشئي المحتوى")
    if observations.get("trends_count"):
        bits.append(f"{observations['trends_count']} موضوعاً رائجاً")
    if observations.get("news_count") or observations.get("specialty_news_count"):
        n = observations.get("news_count") or observations.get("specialty_news_count")
        bits.append(f"{n} خبراً")
    if observations.get("timeline_count"):
        bits.append(f"{observations['timeline_count']} تغريدة في الخط الزمني")
    return "؛ ".join(bits)


def topic_suggestions(observations: Dict[str, Any], limit: int = 8) -> list:
    """المواضيع/الوسوم التي اطّلع عليها الموظف هذه الدورة — تُعرض للمستخدم
    مرقّمة ليوجّه الموظف نحو أحدها («انشر عن 2» أو «تكلم عن …»)."""
    if not isinstance(observations, dict):
        return []
    out: list = []
    seen: set = set()

    def _add(text) -> None:
        t = str(text or "").replace("\n", " ").strip()
        if t and t.lower() not in seen and len(out) < limit:
            seen.add(t.lower())
            out.append(t[:90])

    _add(observations.get("matched_hashtag"))
    for h in observations.get("trend_hashtags") or []:
        _add(h)
    for name in observations.get("x_trends_preview") or []:
        _add(name)
    for p in observations.get("top_posts_preview") or []:
        _add((p or {}).get("text") if isinstance(p, dict) else p)
    for n in observations.get("specialty_news_preview") or []:
        _add(n)
    for t in observations.get("timeline_preview") or []:
        _add((t or {}).get("text") if isinstance(t, dict) else t)
    return out


def build_cycle_narrative(config, report: Dict[str, Any]) -> str:
    """Compose a friendly, human summary of what the employee did this cycle."""
    account = getattr(config, "account_username", "") or "الموظف الذكي"
    error = report.get("error") or ""
    phase_errors = report.get("phase_errors") or []
    status_icon = "✅" if (not error and not phase_errors) else "⚠️"

    parts = [f"{status_icon} الموظف الذكي @{account} أنهى دورة عمل الآن."]

    observed = _observed_line(report.get("observations") or {})
    if observed:
        parts.append(f"👀 ما رصده: {observed}.")

    guard = report.get("guard_report") or {}
    if guard.get("rejected"):
        reasons = "؛ ".join(dict.fromkeys(
            str(r)[:160] for r in (guard.get("reasons") or {}).values() if r))
        parts.append(
            f"مراجعة الخطة: استُبعد {len(guard['rejected'])} من الاختيارات الأولية. "
            + (f"السبب: {reasons}. " if reasons else "")
            + ("اعتُمد بديل اجتاز الحارس قبل التنفيذ." if guard.get("substituted") else ""))

    results = report.get("results") or []
    action_lines = [_action_line(r) for r in results if isinstance(r, dict)]
    action_lines = [ln for ln in action_lines if ln]
    directed_note = (report.get("directed_note") or "").strip()
    if action_lines:
        parts.append("🛠️ ما قام به:\n" + "\n".join(f"- {ln}" for ln in action_lines[:5]))
    elif directed_note:
        # توجيه تفاعل مباشر لم يُعثر على تغريدته — أبلغ المستخدم بوضوح واطلب رابطاً
        parts.append(
            f"🔎 {directed_note}\n"
            "👉 جرّب لصق **رابط التغريدة** مباشرةً أو اسم الحساب (@handle) لأتفاعل معها بدقّة."
        )
    else:
        # تفاعلي: ما لقى شيءاً مناسباً — اعرض ما اطّلع عليه واطلب توجيه المستخدم
        suggestions = topic_suggestions(report.get("observations") or {})
        if suggestions:
            listing = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(suggestions))
            parts.append(
                "🕊️ ما لقيت شيئاً مناسباً تماماً ضمن تخصصي هذه الدورة، وهذه المواضيع "
                "والوسوم التي اطّلعت عليها:\n" + listing +
                "\n\n👉 وجّهني بحرّية وأنفّذ فوراً، مثلاً:\n"
                "- «انشر عن 2» أو «تكلم عن موضوع معيّن»\n"
                "- «روح للتايم لاين وشوف تغريدة تناسب تخصصي وردّ عليها»\n"
                "- «شوف المنشنات وردّ على اللي يهمّني»\n"
                "- «اقتبس أقوى تغريدة في الترند» أو «تابع حسابين في مجالي»\n"
                "أو ناقشني في أي شيء وأنا أشرح لك رأيي وأسبابي."
            )
        else:
            parts.append(
                "🕊️ لم يجد محتوى أو تفاعلات ضمن تخصصه هذه الدورة، فاكتفى بالرصد "
                "والمتابعة (لا ينشر محتوى عاماً خارج اهتماماته)."
            )

    if error:
        parts.append(f"❌ خطأ: {str(error)[:200]}")
    elif phase_errors:
        parts.append(f"⚠️ تعثّرت مراحل: {', '.join(str(x) for x in phase_errors)[:150]}")

    return "\n\n".join(p for p in parts if p and str(p).strip())


def build_help_message(config, help_request: Dict[str, Any]) -> str:
    """Compose a support-request message asking the user to intervene."""
    account = getattr(config, "account_username", "") or "الموظف الذكي"
    msg = (help_request or {}).get("message") or "أحتاج إلى دعمك لمتابعة العمل."
    return (
        f"🆘 الموظف الذكي @{account} يحتاج دعمك:\n\n{msg}\n\n"
        "بعد المعالجة سيكمل عمله تلقائياً في الدورة التالية."
    )


class RuntimeManager(IRuntimeManager):
    """Owns the temporary employee runtime lifecycle."""

    def __init__(
        self,
        *,
        repository: IEmployeeRepository,
        memory_manager,
        mcp_registry,
        capability_registry,
        workspace_manager,
        cycle_factory,          # callable() -> CognitiveCycle
        llm=None,
        event_bus=None,
    ) -> None:
        self._repo = repository
        self._memory = memory_manager
        self._mcp = mcp_registry
        self._capabilities = capability_registry
        self._workspaces = workspace_manager
        self._cycle_factory = cycle_factory
        self._llm = llm
        self._bus = event_bus
        # دورة واحدة لكل موظف في اللحظة الواحدة: مجمّع العمال فيه 4 خيوط،
        # فدورة مجدولة + «ابدأ دورة» يدوية لنفس الموظف كانتا تعملان معاً —
        # كلتاهما تحمّل config وتحفظه في النهاية، وآخر حفظ يمسح تعلّم الأخرى.
        # موظفون مختلفون يعملون بتوازٍ طبيعي (يتناوبون على قفل المتصفح فقط).
        self._cycle_guards: Dict[str, threading.Lock] = {}
        self._guards_lock = threading.Lock()

    def _cycle_guard(self, employee_id: str) -> threading.Lock:
        with self._guards_lock:
            return self._cycle_guards.setdefault(employee_id, threading.Lock())

    # ---- IRuntimeManager ------------------------------------------------
    def run_cycle(self, employee_id: str, event: Event) -> Dict[str, Any]:
        guard = self._cycle_guard(employee_id)
        if not guard.acquire(blocking=False):
            logger.info("cycle already running for %s; skipping duplicate", employee_id)
            # طلب يدوي أثناء دورة جارية: أخبر المستخدم بدل الصمت — التجاهل
            # الصامت يبدو كأن «الزر لا يعمل» لهذا الموظف.
            if getattr(getattr(event, "type", None), "value", "") == "manual":
                cfg = self._repo.get(employee_id)
                if cfg is not None and getattr(cfg, "user_id", None) is not None:
                    account = getattr(cfg, "account_username", "") or ""
                    self._emit_progress(
                        cfg.user_id,
                        f"⏳ الموظف @{account or 'الذكي'} في منتصف دورة الآن — "
                        "سينهيها أولاً، وطلبك الجديد يمكن إرساله بعدها مباشرة.",
                        "aeos_cycle_busy", account,
                    )
            return {"skipped": True, "reason": "cycle_already_running",
                    "employee_id": employee_id}
        try:
            return self._run_cycle_locked(employee_id, event)
        finally:
            guard.release()

    def _run_cycle_locked(self, employee_id: str, event: Event) -> Dict[str, Any]:
        cycle_id = uuid.uuid4().hex

        # 1) LOAD
        config = self._repo.get(employee_id)
        if config is None:
            raise EmployeeNotFoundError(f"employee '{employee_id}' not found")
        if config.status != EmployeeStatus.ACTIVE:
            logger.info("employee %s is %s; skipping cycle", employee_id, config.status.value)
            return {"skipped": True, "reason": f"status={config.status.value}"}

        self._reset_daily_counters(config)

        # 2) BUILD temporary context
        workspace = self._workspaces.get(employee_id)
        context = ExecutionContext(
            config=config,
            event=event,
            memory=self._memory,
            mcp=self._mcp,
            capabilities=self._capabilities,
            workspace=workspace,
            llm=self._llm,
            cycle_id=cycle_id,
        )
        # live-progress sink: stream the employee's thinking/observations into the
        # user's chat while the cycle is still running (real-time interaction).
        context.progress_cb = self._make_progress_cb(config)
        _account = getattr(config, "account_username", "") or "الموظف الذكي"
        context.emit_progress(
            f"⚙️ **@{_account} بدأ دورة تفكير جديدة الآن...**",
            intent="aeos_cycle_started",
        )

        # 3) RUN one cognitive cycle
        cycle: CognitiveCycle = self._cycle_factory()
        try:
            report = cycle.run(context)
        except Exception as exc:  # noqa: BLE001
            logger.exception("cycle crashed for %s", employee_id)
            report = {"cycle_id": cycle_id, "employee_id": employee_id, "error": str(exc)}

        # 4) PERSIST
        try:
            self._persist(config, report)
        except Exception:  # noqa: BLE001
            logger.exception("failed to persist cycle for %s", employee_id)

        # 5) DESTROY runtime
        self._destroy(employee_id)
        return report

    # ---- lifecycle helpers ---------------------------------------------
    def _reset_daily_counters(self, config) -> None:
        """Reset per-day counters when a new day begins — في منتصف ليل الموظف (منطقته
        الزمنية) لا منتصف ليل UTC: كان اليوم يبدأ 03:00 بتوقيت الرياض بينما الإيقاع
        اليومي يُحسب بالتوقيت المحلي، فيتعارض مرجعا «اليوم» في القرار نفسه."""
        from ..decision.engine import local_now

        rs = config.runtime_state
        today = local_now(config).strftime("%Y-%m-%d")
        if rs.flags.get("counters_day") != today:
            rs.counters = {}
            rs.flags["counters_day"] = today

    def _persist(self, config, report: Dict[str, Any]) -> None:
        # خزّن مواضيع الدورة المقترحة ليستطيع المستخدم توجيه الموظف من الشات
        # برقم الموضوع («انشر عن 2») حتى بعد انتهاء الدورة.
        try:
            suggestions = topic_suggestions(report.get("observations") or {})
            if suggestions:
                config.runtime_state.flags["topic_suggestions"] = suggestions
        except Exception:  # noqa: BLE001
            logger.debug("storing topic suggestions failed", exc_info=True)

        # runtime state + goal progress back onto the permanent config
        self._repo.save(config)

        # reschedule next wake based on the employee's cadence
        try:
            self._reschedule(config)
        except Exception:  # noqa: BLE001
            logger.exception("reschedule failed for %s", config.id)

        # audit log row + notify chat with "current cycle" summary
        db = SessionLocal()
        try:
            outcome_snapshot = {
                "results": report.get("results"),
                "initial_decisions": report.get("initial_decisions"),
                "execution_plan": report.get("execution_plan"),
                "guard_report": report.get("guard_report"),
                "thought": report.get("thought"),
                "analysis": report.get("analysis"),
                "plan": report.get("plan"),
                "evaluation": report.get("evaluation"),
                "lesson": report.get("lesson"),
                "phase_errors": report.get("phase_errors"),
                "decision_trace": report.get("decision_trace"),
                "decision_review": report.get("decision_review"),
                "suspended_arms": report.get("suspended_arms"),
                "research": report.get("research"),
                "tool_calls": report.get("tool_calls"),
                "available_tools": report.get("available_tools"),
                "phase_timings_ms": report.get("phase_timings_ms"),
                "error": report.get("error"),
            }
            log_row = ExecutionLogRecord(
                employee_id=config.id,
                cycle_id=report.get("cycle_id", ""),
                event_type=report.get("event_type"),
                phase="cycle",
                decision=json.dumps(report.get("decisions"), ensure_ascii=False),
                outcome=json.dumps(outcome_snapshot, ensure_ascii=False),
                success=not report.get("error") and not report.get("phase_errors"),
                duration_ms=report.get("duration_ms"),
            )
            db.add(log_row)
            logger.info(
                "Persist cycle log: employee_id=%s cycle_id=%s (will commit)",
                config.id,
                report.get("cycle_id", ""),
            )
            db.commit()
            logger.info(
                "Persisted cycle log: id=%s employee_id=%s cycle_id=%s",
                log_row.id,
                config.id,
                report.get("cycle_id", ""),
            )

            # Report each real cycle into the user's chat (stored + pushed live).
            # The employee narrates what it observed and did; if it is blocked it
            # asks the user for support in a distinct message.
            try:
                self._notify_chat(db, config, report)
            except Exception:  # noqa: BLE001
                logger.exception("failed to notify chat for employee %s cycle", config.id)

        finally:
            db.close()

    def _notify_chat(self, db, config, report: Dict[str, Any]) -> None:
        """Store the cycle narrative (and any help request) as assistant messages
        and push them live to the user's connected chat client(s)."""
        from app.services.memory_service import memory_service

        user_id = getattr(config, "user_id", None)
        if user_id is None:
            return

        account = getattr(config, "account_username", "") or ""
        conversation = memory_service.get_or_create_conversation(
            db=db, user_id=int(user_id),
            session_id=(f"emp_{account}" if account else None),
            title=(f"🤖 الموظف @{account}" if account else None),
        )

        # 1) narrative of what happened / what the employee is doing
        narrative = build_cycle_narrative(config, report)
        if narrative:
            memory_service.add_message(
                db=db, conversation_id=conversation.id, role="assistant",
                content=narrative, intent="aeos_cycle_update", agent="AEOS",
            )
            self._push_chat(user_id, narrative, account)

        # 2) support request when the employee is blocked and needs the user
        help_request = report.get("help_request")
        if help_request:
            help_msg = build_help_message(config, help_request)
            memory_service.add_message(
                db=db, conversation_id=conversation.id, role="assistant",
                content=help_msg, intent="aeos_help_request", agent="AEOS",
            )
            self._push_chat(user_id, help_msg, account)

        db.commit()

    def _make_progress_cb(self, config):
        """Build a live-progress callback bound to this employee's owner, or None.

        The callback persists each update as an assistant message AND pushes it to
        the user's chat in real time, so the employee's thinking/observations show
        up live during the cycle (not only in the post-cycle summary)."""
        user_id = getattr(config, "user_id", None)
        if user_id is None:
            return None
        account = getattr(config, "account_username", "") or ""

        def _cb(text: str, intent: str = "aeos_cycle_progress") -> None:
            self._emit_progress(user_id, text, intent, account)

        return _cb

    def _emit_progress(self, user_id, text: str, intent: str, account: str = "") -> None:
        """Persist a live progress message and push it to the chat (best-effort).

        Progress lands in the employee's own conversation (session ``emp_<account>``)
        so the full employee thread stays in one place and appears in the sidebar."""
        try:
            from app.services.memory_service import memory_service

            db = SessionLocal()
            try:
                conversation = memory_service.get_or_create_conversation(
                    db=db, user_id=int(user_id),
                    session_id=(f"emp_{account}" if account else None),
                    title=(f"🤖 الموظف @{account}" if account else None),
                )
                memory_service.add_message(
                    db=db, conversation_id=conversation.id, role="assistant",
                    content=text, intent=intent, agent="AEOS",
                )
                db.commit()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            logger.debug("progress persist skipped", exc_info=True)
        self._push_chat(user_id, text, account)

    @staticmethod
    def _push_chat(user_id, content: str, account: str = "") -> None:
        """Best-effort real-time delivery to the chat WebSocket clients.

        ``session_id``/``account`` إلزاميان مع تعدد الموظفين الفعالين: بدونهما
        تلصق الواجهة رسائل كل الموظفين في المحادثة المفتوحة أياً كانت، فتختلط
        دورات @A مع @B على الشاشة رغم أن التخزين منفصل وصحيح.
        """
        try:
            from app.services import realtime_bus

            realtime_bus.publish_chat(user_id, {
                "type": "assistant_message",
                "message": content,
                "account": account or None,
                "session_id": (f"emp_{account}" if account else None),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:  # noqa: BLE001
            logger.debug("realtime push skipped", exc_info=True)

    def _reschedule(self, config) -> None:
        if not config.scheduler.enabled:
            return
        from ..scheduler.timewindow import compute_next_wake

        next_wake = compute_next_wake(config.scheduler, datetime.now(timezone.utc))
        config.scheduler.next_wake_at = next_wake
        if hasattr(self._repo, "set_next_wake"):
            self._repo.set_next_wake(config.id, next_wake)

    def _destroy(self, employee_id: str) -> None:
        """Tear down the temporary runtime; the employee persists in storage."""
        try:
            self._memory.clear_working(employee_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            self._workspaces.destroy_tmp(employee_id)
        except Exception:  # noqa: BLE001
            pass
        logger.debug("runtime destroyed for %s", employee_id)
