"""
Composition root.

This is the ONE place where concrete implementations are wired together via the
DI container. Everything else depends on interfaces. Swapping an implementation
(e.g. a Redis job queue, a Gemini LLM provider, a Postgres repository) is a
one-line change here.

``AEOSPlatform`` exposes lifecycle (start/stop) and the handful of façade methods
the FastAPI layer needs (trigger an event, run a cycle now, create an employee).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .capabilities.handlers import default_capabilities
from .capabilities.registry import CapabilityRegistry
from .cognition.cycle import CognitiveCycle
from .cognition.phases import (
    AnalyzePhase, CritiquePhase, DecidePhase, EmotionPhase, EvaluatePhase,
    ExecutePhase, GraphMemoryPhase, LearnPhase, ObservePhase, PerformancePhase,
    PersonaGuardPhase, PlanPhase, PrioritizePhase, SavePhase, StrategyPhase,
    SupportPhase, ThinkPhase, TopicIntelPhase,
)
from .core.container import Container
from .core.events import Event, EventType, InMemoryEventBus
from .core.interfaces import IEmployeeRepository, IJobQueue, IRuntimeManager
from .core.llm import build_llm_provider
from .decision.engine import WeightedUtilityDecisionEngine
from .employee.repository import SqlEmployeeRepository
from .mcp.registry import MCPRegistry
from .memory.graphiti_store import get_graphiti_store
from .memory.manager import MemoryManager
from .planner.planner import SkillBasedPlanner
from .runtime.manager import RuntimeManager
from .scheduler.queue import InMemoryJobQueue
from .scheduler.scheduler import WakeScheduler
from .scheduler.workers import WorkerPool
from .security.vault import build_vault
from .skills.builtin import default_skills
from .skills.registry import SkillRegistry
from .workspace.manager import WorkspaceManager

logger = logging.getLogger("aeos.bootstrap")


class AEOSPlatform:
    """The assembled AI Employee Operating System."""

    def __init__(self, num_workers: int = 4, poll_interval: float = 20.0) -> None:
        self.container = Container()
        self._num_workers = num_workers
        self._poll_interval = poll_interval
        self._build()

    def _build(self) -> None:
        c = self.container

        # --- infrastructure ---
        bus = InMemoryEventBus()
        c.register_singleton("event_bus", bus)

        llm = build_llm_provider()
        c.register_singleton("llm", llm)
        self._llm = llm

        vault = build_vault()
        c.register_singleton("vault", vault)

        repo: IEmployeeRepository = SqlEmployeeRepository()
        c.register_singleton(IEmployeeRepository, repo)

        memory = MemoryManager()
        c.register_singleton("memory", memory)

        # ذاكرة معرفية زمنية (Zep/Graphiti + Kuzu محلياً) — للمشاعر والسلوك
        graph_memory = get_graphiti_store()
        c.register_singleton("graph_memory", graph_memory)
        self._graph_memory = graph_memory

        workspaces = WorkspaceManager(vault)
        c.register_singleton("workspaces", workspaces)

        # --- MCP servers: كلها من المجلد الرئيسي system/mcps ---
        # المدمجة (كود داخل العملية: X، القراءة، المحتوى، الذاكرة، المتصفح، الأخبار، البحث،
        # الصور) ثم المرفوعة (stdio/HTTP/حزم) — قائمة واحدة، ولا تسجيل يدوي هنا.
        mcp = MCPRegistry()
        from .mcp.catalog import register_builtins

        register_builtins(mcp, {"llm": llm, "memory": memory})
        try:
            from .mcp.servers.external_mcp import register_all
            from .services import nawah_clone as _nawah

            # خادم نواه يأخذ مفتاح النموذج من resolve_llm() وقت الاتصال (قبل أول تسجيل)
            _nawah.install()
            register_all(mcp)
        except Exception:  # noqa: BLE001
            logger.exception("external MCP servers failed to load")
        # أدوات الموظفين الخاصة (يضيفها المالك لموظف بعينه — HTTP عام فقط)
        try:
            from .mcp import employee_mcp

            employee_mcp.register_all(mcp)
        except Exception:  # noqa: BLE001
            logger.exception("employee MCP servers failed to load")
        c.register_singleton(MCPRegistry, mcp)

        # --- capabilities ---
        caps = CapabilityRegistry(event_bus=bus)
        for cap in default_capabilities():
            caps.register(cap)
        c.register_singleton(CapabilityRegistry, caps)

        # --- skills ---
        skills = SkillRegistry()
        for sk in default_skills():
            skills.register(sk)
        c.register_singleton(SkillRegistry, skills)

        # --- planner + decision engine ---
        planner = SkillBasedPlanner(skills, llm)
        decision_engine = WeightedUtilityDecisionEngine()

        # --- cognitive cycle factory (fresh instance per runtime) ---
        def cycle_factory() -> CognitiveCycle:
            phases = [
                ObservePhase(), TopicIntelPhase(), PerformancePhase(),
                EmotionPhase(graph_memory),
                AnalyzePhase(), ThinkPhase(), StrategyPhase(), PrioritizePhase(),
                PlanPhase(planner), DecidePhase(decision_engine),
                PersonaGuardPhase(), ExecutePhase(),
                EvaluatePhase(), LearnPhase(), CritiquePhase(),
                GraphMemoryPhase(graph_memory), SupportPhase(), SavePhase(),
            ]
            return CognitiveCycle(phases, event_bus=bus)

        # --- runtime manager ---
        runtime: IRuntimeManager = RuntimeManager(
            repository=repo,
            memory_manager=memory,
            mcp_registry=mcp,
            capability_registry=caps,
            workspace_manager=workspaces,
            cycle_factory=cycle_factory,
            llm=llm,
            event_bus=bus,
        )
        c.register_singleton(IRuntimeManager, runtime)

        # --- scheduler + workers ---
        # AEOS_RUNTIME=durable: الطابور الدائم (execution_jobs) بعقود وسياج وسجل أفعال؛ الافتراضي legacy
        from .runtime.config import WorkerConfig, runtime_mode

        self._mode = runtime_mode()
        self._rt_cfg = None
        if self._mode == "durable":
            from .runtime.durable_queue import DbJobQueue, DbLock
            from .runtime.durable_workers import DurableWorkerPool

            self._rt_cfg = WorkerConfig.from_env()
            queue = DbJobQueue(self._rt_cfg)
            self._workers = DurableWorkerPool(queue, runtime, self._rt_cfg)
            self._scheduler = WakeScheduler(repo, queue, event_bus=bus, poll_interval=self._poll_interval,
                                            leader_lock=DbLock("aeos_scheduler"),
                                            owner=self._rt_cfg.worker_id, on_enqueue=self._workers.notify)
        else:
            queue = InMemoryJobQueue()
            self._scheduler = WakeScheduler(repo, queue, event_bus=bus,
                                            poll_interval=self._poll_interval)
            self._workers = WorkerPool(queue, runtime, num_workers=self._num_workers)
        c.register_singleton(IJobQueue, queue)

        self._bus = bus
        self._repo = repo
        self._runtime = runtime
        self._queue = queue

        logger.info("AEOS platform assembled (llm_available=%s, workers=%d)",
                    llm.available, self._num_workers)

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        self._workers.start()
        self._scheduler.start()
        logger.info("AEOS platform started")

    def stop(self) -> None:
        self._scheduler.stop()
        self._workers.stop()   # durable: drain — لا استلام جديد، ينتظر الجاري ثم يلغيه تعاونياً

    @property
    def runtime_mode(self) -> str:
        return self._mode

    def _execute(self, employee_id: str, event: Event, priority: int = 1) -> Dict[str, Any]:
        """شغّل دورة وانتظر تقريرها. legacy: مباشرة. durable: عبر الطابور (مهمة واحدة نشطة لكل موظف،
        عقد وسياج وسجل أفعال) ثم انتظار النتيجة — المستدعون يرون التقرير نفسه."""
        if getattr(self, "_mode", "legacy") != "durable":
            return self._runtime.run_cycle(employee_id, event)
        from .core.exceptions import EmployeeNotFoundError
        from .runtime.durable_queue import CURRENT_JOB, QueueFull, TaskLimitExceeded

        cfg = self._repo.get(employee_id)
        if cfg is None:
            raise EmployeeNotFoundError(f"employee '{employee_id}' not found")
        try:
            job_id, _ = self._queue.submit(employee_id, event, priority=priority,
                                           idempotency_key=f"evt:{event.event_id}",
                                           tenant_id=getattr(cfg, "user_id", None))
        except (QueueFull, TaskLimitExceeded) as exc:
            return {"skipped": True, "reason": str(exc)}
        self._workers.notify()
        if CURRENT_JOB.get() is not None:
            # داخل مهمة أخرى (Agent-to-Agent): لا يحجز العامل منتظراً — منع الجمود
            return {"skipped": True, "reason": "dispatched_async", "job_id": job_id}
        return self.job_report(self._queue.wait_for(job_id, self._rt_cfg.queue_wait_seconds))

    @staticmethod
    def job_report(view: Dict[str, Any]) -> Dict[str, Any]:
        """حالة المهمة النهائية ⇒ تقرير بالشكل الذي يتوقعه المستدعون القدامى."""
        result = dict(view.get("result") or {}) if isinstance(view.get("result"), dict) else {}
        status = view.get("status")
        result["job"] = {k: view.get(k) for k in ("job_id", "status", "attempts", "dead_letter")}
        if status == "COMPLETED":
            return result
        if status == "CANCELLED":
            return {**result, "skipped": True, "reason": f"cancelled: {view.get('error') or ''}".strip()}
        if status in ("FAILED", "TIMED_OUT"):
            return {**result, "error": result.get("error") or view.get("error") or status}
        return {**result, "skipped": True, "reason": "queue_timeout"}

    def cancel_employee(self, employee_id: str, reason: str = "أوقفه المالك") -> Dict[str, Any]:
        """ألغِ مهام موظف: المنتظرة فوراً، والجارية تعاونياً عند نقطة الفحص التالية."""
        if self._mode != "durable":
            return {"supported": False}
        out = self._queue.request_cancel(employee_id=employee_id, reason=reason)
        return {"supported": True, **out}

    def runtime_stats(self) -> Dict[str, Any]:
        if self._mode != "durable":
            return {"mode": "legacy", "queue_size": self._queue.size()}
        return {"mode": "durable", "worker_id": self._rt_cfg.worker_id, "breaker": self._workers.breaker.state,
                "inflight": self._workers.inflight(), **self._workers.metrics.snapshot(self._queue.stats())}

    # ---- façade for the API layer --------------------------------------
    def trigger_event(self, event_type: str, employee_id: str,
                      payload: Optional[Dict[str, Any]] = None) -> str:
        """Publish an external event (async execution via workers)."""
        event = Event(EventType(event_type), employee_id=employee_id,
                      payload=payload or {}, source="api")
        self._bus.publish(event)
        return event.event_id

    def run_cycle_now(self, employee_id: str, event_type: str = "manual",
                      payload: Optional[Dict[str, Any]] = None,
                      source: str = "api_sync") -> Dict[str, Any]:
        """Run a single cycle synchronously (blocking) — handy for testing/API.
        ``source``: ربط نقاط الاستعادة (``order:<id>`` / ``resume:<id>``) — انظر RuntimeManager."""
        event = Event(EventType(event_type), employee_id=employee_id,
                      payload=payload or {}, source=source)
        return self._execute(employee_id, event)

    def resume_cycle(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """أعد تشغيل دورة انقطعت (بطلب المالك): الحدث نفسه (أمر الشات بنصّه إن كان أمراً)،
        والأفعال التي نجحت قبل الانقطاع تُتخطّى (``resume_skip``). تعذّر البدء (موقوف/مشغول)
        ⇒ تعود المهمة «مقطوعة» ويُبلَّغ المالك بالسبب بدل الصمت."""
        from app.services import task_checkpoints as TC

        req = task.get("request") or {}
        employee_id = task.get("employee_id") or ""
        order = req.get("order")
        if order:
            # أمر مالك (انقطع قبل دورته أو أثناءها): يعود عبر المُرسِل نفسه — انتظار الدورة الجارية
            # والإبلاغ بالنتيجة على X — ويتخطّى ما نُفّذ قبل الانقطاع (resume:<id>)
            from .services import employee_mission

            cfg = self._repo.get(employee_id)
            if cfg is None:
                TC.finish(task["id"], TC.CANCELLED, "الموظف لم يعد موجوداً")
                return {"skipped": True, "reason": "employee_not_found"}
            employee_mission.dispatch_directed(
                cfg, dict(req.get("payload") or {}), list(order.get("expected_caps") or []),
                str(order.get("label") or task.get("title") or ""), resume_of=task["id"])
            return {"dispatched": True}
        try:
            etype = EventType(req.get("event_type") or "manual")
        except ValueError:
            etype = EventType.MANUAL
        event = Event(etype, employee_id=employee_id, payload=dict(req.get("payload") or {}),
                      source=RuntimeManager.resume_source(task["id"]))
        try:
            # صريحاً عبر الصنف: المستدعون (والاختبارات) قد يمرّرون كائناً بسمات المنصة فقط
            report = AEOSPlatform._execute(self, employee_id, event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("resume cycle failed for %s", employee_id)
            report = {"skipped": True, "reason": str(exc)[:200]}
        if report.get("skipped"):
            reason = {"cycle_already_running": "الموظف في منتصف دورة أخرى الآن",
                      "queue_timeout": "بقي في الطابور أطول من المهلة"}.get(
                report.get("reason"), report.get("reason") or "")
            view = TC.reopen(task["id"], f"تعذّر الاستئناف: {reason}")
            RuntimeManager._push_task(self._repo.get(employee_id), view)
            self.notify_owner(employee_id, f"⚠️ تعذّر استئناف الدورة المقطوعة: {reason}. "
                                           "يمكنك المحاولة مرة أخرى من سجل التنفيذ.",
                              intent="aeos_cycle_resume_failed")
        return report

    def notify_owner(self, employee_id: str, text: str,
                     intent: str = "aeos_cycle_progress") -> bool:
        """أبلغ صاحب الحساب في الشات (تخزين + دفع حيّ) من خارج الدورة.

        ضرورية لأوامر الشات: أمر المالك قد لا يُنفّذ لسبب مشروع (الموظف موقوف،
        دورة جارية، لم يُعثر على هدف) وترْكُه بلا خبر يجعل النظام يبدو كأنه
        تجاهل الأمر — وهو أسوأ من الفشل المعلن.
        """
        cfg = self._repo.get(employee_id)
        if cfg is None or getattr(cfg, "user_id", None) is None:
            return False
        try:
            self._runtime._emit_progress(
                cfg.user_id, text, intent,
                getattr(cfg, "account_username", "") or "")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("notify_owner failed for %s", employee_id)
            return False

    @property
    def repository(self) -> IEmployeeRepository:
        return self._repo

    @property
    def llm(self):
        return self._llm

    @property
    def graph_memory(self):
        """الذاكرة المعرفية الزمنية (Zep/Graphiti) — للتشخيص والاستعلام."""
        return self._graph_memory

    # ---- agent activation (chatbot toggle) -----------------------------
    def toggle_agent(
        self,
        *,
        platform: str,
        account_username: str,
        enabled: bool,
        cookie_label: str = "",
        user_id: Optional[int] = None,
        interval_minutes: Optional[int] = None,
        active_hours_start: Optional[int] = None,
        active_hours_end: Optional[int] = None,
        persona: Optional[Dict[str, Any]] = None,
        goals: Optional[list] = None,
        business_rules: Optional[Dict[str, Any]] = None,
        installed_skills: Optional[list] = None,
        mcp_servers: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Enable/disable an autonomous AI Employee for a connected account.

        On enable it is created if missing, marked active, scheduled to wake
        immediately (if within active hours) so it starts working on its own.
        On disable it is paused and removed from the wake schedule.
        """
        import uuid
        from datetime import datetime, timezone

        from .employee.models import (
            EmployeeConfig, EmployeeStatus, Goal, Persona, SchedulerConfig,
        )
        from .scheduler.timewindow import compute_next_wake, is_within_active_hours

        cfg = self._repo.get_by_account(platform, account_username)
        newly_created = cfg is None
        if cfg is None:
            cfg = EmployeeConfig(
                id=uuid.uuid4().hex,
                user_id=user_id,
                platform=platform,
                account_username=account_username,
                cookie_label=cookie_label or account_username,
                persona=Persona.from_dict(persona) if persona else Persona(
                    display_name=account_username),
                installed_skills=[
                    "on_this_day", "daily_content_creator", "community_engager",
                    "timeline_engager", "viral_content_creator",
                    "content_quoter", "swipe_file", "directed_engager",
                    "directed_mission",
                ],
                mcp_servers=[
                    "x", "playwright_x", "content", "memory", "browser", "news",
                    "image",
                ],
                business_rules=business_rules or {
                    "daily_post_target": 1,
                    "max_posts_per_day": 3,
                    "max_posts_per_cycle": 1,
                    "max_actions_per_cycle": 5,
                    "max_likes_per_cycle": 3,
                    "max_retweets_per_cycle": 1,
                    "max_replies_per_cycle": 1,
                    "max_follows_per_cycle": 1,
                    "enforce_dialect": True,
                    "enforce_interest_scope": True,
                    "require_grounded_topic": True,
                    "min_relevance_score": 1,
                },
            )
        # apply overrides
        if cookie_label:
            cfg.cookie_label = cookie_label
        if user_id is not None:
            cfg.user_id = user_id
        if persona:
            cfg.persona = Persona.from_dict(persona)
        if goals:
            cfg.goals = [
                Goal(id=g.get("id") or uuid.uuid4().hex,
                     description=g.get("description", ""),
                     metric=g.get("metric", ""), target=g.get("target"),
                     priority=int(g.get("priority", 3)))
                for g in goals
            ]
        if business_rules:
            cfg.business_rules.update(business_rules)
        if installed_skills is not None:
            cfg.installed_skills = list(installed_skills)
        if mcp_servers is not None:
            # خادم x إلزامي للتنفيذ على المنصة
            servers = list(dict.fromkeys(list(mcp_servers) + ["x"]))
            cfg.mcp_servers = servers

        # ضمان القدرات الأساسية (إضافةً لا استبدالاً): مصدر أخبار حقيقي + مهارات
        # المحتوى والتفاعل، حتى تعمل الموظفون القدامى فور إعادة تفعيلهم.
        # news = متابعة أخبار قوقل حسب تخصص الموظف (Google News RSS، بلا مفتاح).
        _baseline_servers = ["x", "playwright_x", "content", "memory", "browser",
                             "news", "image"]
        cfg.mcp_servers = list(dict.fromkeys(list(cfg.mcp_servers or []) + _baseline_servers))
        # البحث: يُربط تلقائياً متى وُجد مفتاح مزوّد (كان stub فكان يُنزع دائماً).
        from .mcp.servers.core_mcp import search_provider

        if search_provider()[0]:
            if "search" not in cfg.mcp_servers:
                cfg.mcp_servers.append("search")
        else:
            cfg.mcp_servers = [s for s in cfg.mcp_servers if s != "search"]
        _baseline_skills = [
            "on_this_day", "daily_content_creator", "community_engager",
            "timeline_engager", "viral_content_creator",
            "content_quoter", "swipe_file", "directed_engager",
            "directed_mission",
        ]
        cfg.installed_skills = list(dict.fromkeys(list(cfg.installed_skills or []) + _baseline_skills))
        cfg.business_rules.setdefault("max_posts_per_cycle", 1)
        cfg.business_rules.setdefault("enforce_dialect", True)
        cfg.business_rules.setdefault("enforce_interest_scope", True)
        cfg.business_rules.setdefault("require_grounded_topic", True)
        cfg.business_rules.setdefault("min_relevance_score", 1)
        # إعادة معايرة: القيمة القديمة 3 كانت تشترط 3 كلمات مفتاحية في التغريدة
        # الواحدة فتحجب التايم لاين/الهاشتاقات كلها تقريباً. الدقة الآن مسؤولية
        # الحارس (الذي يدقق كل إجراء)، والترشيح يكتفي بكلمة مطابقة واحدة.
        if int(cfg.business_rules.get("min_relevance_score", 1)) >= 3:
            cfg.business_rules["min_relevance_score"] = 1
        # حدود التفاعل والتنويع — قابلة للتعديل من الواجهة لكل موظف
        cfg.business_rules.setdefault("max_actions_per_cycle", 5)
        cfg.business_rules.setdefault("max_replies_per_cycle", 1)
        cfg.business_rules.setdefault("max_retweets_per_cycle", 1)
        cfg.business_rules.setdefault("max_likes_per_cycle", 3)
        cfg.business_rules.setdefault("max_follows_per_cycle", 1)
        cfg.business_rules.setdefault("daily_post_target", 1)
        # ذاكرة رصد الإشارات: صلاحية آخر مسح للمصادر بالدقائق (0 = مسح حي كل دورة)
        cfg.business_rules.setdefault("observation_cache_minutes", 60)
        # ميزانيات التفاعل اليومية — توزّع الردود/الرتويت/الإعجابات/المتابعات
        # على مدار اليوم بدل تكرارها كل دورة (ردود المنشنات معفاة دائماً)
        cfg.business_rules.setdefault("daily_replies_target", 6)
        cfg.business_rules.setdefault("daily_retweets_target", 4)
        cfg.business_rules.setdefault("daily_likes_target", 12)
        cfg.business_rules.setdefault("daily_follows_target", 3)
        # مراجعة LLM نهائية لاختيار توليفة الإجراءات الأنسب من المرشّحين الحقيقيين
        cfg.business_rules.setdefault("llm_decision_review", True)
        # ── هوية صانع المحتوى: محاور وصيغ وثريدات وأداء ─────────────────────
        # محاور المحتوى تُشتق من الاهتمامات إن لم تُحدَّد، والصيغ تتناوب بترجيح
        # الأداء الفعلي؛ نسبة الاستكشاف تضمن تجربة الصيغ الجديدة.
        cfg.business_rules.setdefault("content_strategy_enabled", True)
        cfg.business_rules.setdefault("strategy_exploration", 0.35)
        cfg.business_rules.setdefault("allow_threads", True)
        cfg.business_rules.setdefault("daily_thread_target", 1)
        # حلقة التعلّم: كل كم دقيقة يقرأ أداء منشوراته (متصفح، فلا نُكثر)
        cfg.business_rules.setdefault("metrics_read_every_minutes", 180)
        cfg.business_rules.setdefault("metrics_posts_limit", 15)
        # المحتوى البصري: auto = صورة مولّدة فقط عند غياب ميديا حقيقية
        cfg.business_rules.setdefault("generate_images", "auto")
        cfg.business_rules.setdefault("max_generated_images_per_day", 3)
        cfg.business_rules.setdefault("image_style", "editorial")
        # ميزانيات الأدوات الجديدة (اقتباس/حفظ مراجع)
        cfg.business_rules.setdefault("daily_quotes_target", 2)
        cfg.business_rules.setdefault("daily_bookmarks_target", 4)
        # سقوف الدورة للأدوات الجديدة (السقف المُعلَن = السقف الفعلي)
        cfg.business_rules.setdefault("max_quotes_per_cycle", 1)
        cfg.business_rules.setdefault("max_bookmarks_per_cycle", 1)
        # بوابة جودة المحتوى قبل النشر (ترفض القوالب الفارغة وإعادة صياغة التوجيه)
        cfg.business_rules.setdefault("quality_gate", True)
        cfg.business_rules.setdefault("min_post_chars", 70)
        if interval_minutes is not None:
            cfg.scheduler.interval_minutes = int(interval_minutes)
        if active_hours_start is not None:
            cfg.scheduler.active_hours_start = int(active_hours_start)
        if active_hours_end is not None:
            cfg.scheduler.active_hours_end = int(active_hours_end)

        now = datetime.now(timezone.utc)
        if enabled:
            cfg.status = EmployeeStatus.ACTIVE
            cfg.scheduler.enabled = True
            # start immediately if within active hours, else at next window
            cfg.scheduler.next_wake_at = (
                now if is_within_active_hours(cfg.scheduler, now)
                else compute_next_wake(cfg.scheduler, now)
            )
        else:
            cfg.status = EmployeeStatus.PAUSED
            cfg.scheduler.enabled = False
            cfg.scheduler.next_wake_at = None
            if not newly_created:
                self.cancel_employee(cfg.id, "أوقفه المالك")

        saved = self._repo.save(cfg)
        if hasattr(self._repo, "set_next_wake") and saved.scheduler.next_wake_at:
            self._repo.set_next_wake(saved.id, saved.scheduler.next_wake_at)

        # هوية الحساب من البرسونا — عند الإنشاء الأول فقط، في الخلفية:
        # اسم معروض + نبذة (LLM). لا صورة: الصورة والهيدر يعاينهما المالك
        # ويعتمدهما أولاً (identity_service) — لا يُرفع شيء بصري لم يره.
        if newly_created and enabled:
            try:
                from .services.identity import apply_persona_identity_async

                apply_persona_identity_async(saved, llm=self._llm, repo=self._repo)
            except Exception:  # noqa: BLE001
                logger.exception("identity apply scheduling failed for %s", saved.id)

        return self._agent_view(saved)

    def _agent_view(self, cfg) -> Dict[str, Any]:
        from .scheduler.timewindow import is_within_active_hours
        from .services import identity_store

        return {
            "employee_id": cfg.id,
            "platform": cfg.platform,
            "account_username": cfg.account_username,
            "cookie_label": cfg.cookie_label,
            "enabled": cfg.scheduler.enabled and cfg.status.value == "active",
            "status": cfg.status.value,
            "within_active_hours": is_within_active_hours(cfg.scheduler),
            "interval_minutes": cfg.scheduler.interval_minutes,
            "active_hours_start": cfg.scheduler.active_hours_start,
            "active_hours_end": cfg.scheduler.active_hours_end,
            "timezone": cfg.scheduler.timezone,
            "next_wake_at": cfg.scheduler.next_wake_at.isoformat() if cfg.scheduler.next_wake_at else None,
            "last_cycle_at": cfg.runtime_state.last_cycle_at.isoformat() if cfg.runtime_state.last_cycle_at else None,
            "cycles_completed": cfg.runtime_state.cycles_completed,
            "persona": cfg.persona.to_dict() if cfg.persona else None,
            "goals": [g.to_dict() for g in cfg.goals] if cfg.goals else [],
            "installed_skills": list(cfg.installed_skills or []),
            "mcp_servers": list(cfg.mcp_servers or []),
            "business_rules": dict(cfg.business_rules or {}),
            # هوية الحساب كما قُرئت من X (اسم/نبذة/صورة/هيدر) — تعرضها بطاقة الموظف
            "x_profile": identity_store.load_x_profile(cfg.id),
        }

    def get_agent_by_account(self, platform: str, account_username: str) -> Optional[Dict[str, Any]]:
        cfg = self._repo.get_by_account(platform, account_username)
        return self._agent_view(cfg) if cfg else None

    def agents_overview(self) -> Dict[str, Any]:
        agents = [self._agent_view(c) for c in self._repo.list_all()]
        running = sum(1 for a in agents if a["enabled"])
        return {"total": len(agents), "running": running, "agents": agents}

    def delete_employee(self, employee_id: str) -> bool:
        """Stop and permanently remove an AI employee (and its cycle logs).

        Pauses the schedule first so the worker pool won't wake a half-deleted
        employee, then removes the row via the repository.
        """
        cfg = self._repo.get(employee_id)
        if not cfg:
            return False
        # عطّل الجدولة أولاً حتى لا يوقظه المجدول أثناء الحذف
        from .employee.models import EmployeeStatus
        cfg.status = EmployeeStatus.PAUSED
        cfg.scheduler.enabled = False
        cfg.scheduler.next_wake_at = None
        try:
            self._repo.save(cfg)
        except Exception:  # noqa: BLE001
            pass
        self.cancel_employee(employee_id, "حُذف الموظف")
        # نظّف حقائقه من الرسم المعرفي (إن كان مفعّلاً) — أفضل جهد
        try:
            self._graph_memory.clear(employee_id)
        except Exception:  # noqa: BLE001
            pass
        # أدواته الخاصة وأسرارها تُحذف معه
        try:
            from .mcp import employee_mcp

            employee_mcp.purge(employee_id, self.container.resolve(MCPRegistry))
        except Exception:  # noqa: BLE001
            logger.exception("purging employee MCP servers failed")
        # هويته المعتمدة/المعلّقة تُحذف معه حتى لا يستأنفها الإقلاع التالي
        try:
            from .services import identity_store

            identity_store.purge(employee_id)
        except Exception:  # noqa: BLE001
            logger.exception("purging employee identity store failed")
        return bool(self._repo.delete(employee_id))

    def delete_agent(self, platform: str, account_username: str) -> bool:
        """Delete an employee identified by its connected account."""
        cfg = self._repo.get_by_account(platform, account_username)
        if not cfg:
            return False
        return self.delete_employee(cfg.id)

    def stats(self) -> Dict[str, Any]:
        mcp: MCPRegistry = self.container.resolve(MCPRegistry)
        caps: CapabilityRegistry = self.container.resolve(CapabilityRegistry)
        skills: SkillRegistry = self.container.resolve(SkillRegistry)
        return {
            "queue_size": self._queue.size(),
            "runtime": self.runtime_stats(),
            "active_employees": len(self._repo.list_active()),
            "mcp_servers": mcp.list_servers(),
            "capabilities": caps.list_capabilities(),
            "skills": skills.list_skills(),
        }


# process-wide singleton, created lazily on first access
_platform: Optional[AEOSPlatform] = None


def get_platform() -> AEOSPlatform:
    global _platform
    if _platform is None:
        _platform = AEOSPlatform()
    return _platform
