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
    SupportPhase, ThinkPhase,
)
from .core.container import Container
from .core.events import Event, EventType, InMemoryEventBus
from .core.interfaces import IEmployeeRepository, IJobQueue, IRuntimeManager
from .core.llm import build_llm_provider
from .decision.engine import WeightedUtilityDecisionEngine
from .employee.repository import SqlEmployeeRepository
from .mcp.registry import MCPRegistry
from .mcp.servers.core_mcp import BrowserMCP, ContentMCP, MemoryMCP, NewsMCP, SearchMCP
from .mcp.servers.image_mcp import ImageMCP
from .mcp.servers.playwright_x_mcp import PlaywrightXMCP
from .mcp.servers.x_mcp import XPlatformMCP
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

        # --- MCP servers (pluggable) ---
        mcp = MCPRegistry()
        mcp.register(XPlatformMCP())
        mcp.register(PlaywrightXMCP())
        mcp.register(ContentMCP(llm))
        mcp.register(MemoryMCP(memory))
        mcp.register(BrowserMCP())
        mcp.register(NewsMCP())
        mcp.register(SearchMCP())
        mcp.register(ImageMCP())      # صور أصلية مولّدة للمحتوى البصري
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
                ObservePhase(), PerformancePhase(), EmotionPhase(graph_memory),
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
        queue: IJobQueue = InMemoryJobQueue()
        c.register_singleton(IJobQueue, queue)

        self._scheduler = WakeScheduler(repo, queue, event_bus=bus,
                                        poll_interval=self._poll_interval)
        self._workers = WorkerPool(queue, runtime, num_workers=self._num_workers)

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
        self._workers.stop()

    # ---- façade for the API layer --------------------------------------
    def trigger_event(self, event_type: str, employee_id: str,
                      payload: Optional[Dict[str, Any]] = None) -> str:
        """Publish an external event (async execution via workers)."""
        event = Event(EventType(event_type), employee_id=employee_id,
                      payload=payload or {}, source="api")
        self._bus.publish(event)
        return event.event_id

    def run_cycle_now(self, employee_id: str, event_type: str = "manual",
                      payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a single cycle synchronously (blocking) — handy for testing/API."""
        event = Event(EventType(event_type), employee_id=employee_id,
                      payload=payload or {}, source="api_sync")
        return self._runtime.run_cycle(employee_id, event)

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
                    "daily_content_creator", "community_engager",
                    "timeline_engager", "viral_content_creator",
                    "content_quoter", "swipe_file", "directed_engager",
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
        # أزل خادم البحث القديم فقط (search = stub غير مُهيّأ)؛ ونُبقي news فعّالاً.
        cfg.mcp_servers = [s for s in cfg.mcp_servers if s not in ("search",)]
        _baseline_skills = [
            "daily_content_creator", "community_engager",
            "timeline_engager", "viral_content_creator",
            "content_quoter", "swipe_file", "directed_engager",
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

        saved = self._repo.save(cfg)
        if hasattr(self._repo, "set_next_wake") and saved.scheduler.next_wake_at:
            self._repo.set_next_wake(saved.id, saved.scheduler.next_wake_at)
        return self._agent_view(saved)

    def _agent_view(self, cfg) -> Dict[str, Any]:
        from .scheduler.timewindow import is_within_active_hours

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
        # نظّف حقائقه من الرسم المعرفي (إن كان مفعّلاً) — أفضل جهد
        try:
            self._graph_memory.clear(employee_id)
        except Exception:  # noqa: BLE001
            pass
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
