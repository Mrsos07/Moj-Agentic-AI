"""
Domain models for an AI Employee.

These are plain dataclasses (framework-agnostic). Persistence maps them to ORM
rows in ``app/db/models.py`` via :mod:`app.aeos.employee.repository`, keeping the
domain independent of SQLAlchemy.

Core principle: One Connected Account = One AI Employee. Each employee owns its
identity, persona, goals, knowledge, environment, workspace, runtime state,
credentials, scheduler config, skills and MCP connections — and may never
access another employee's resources.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class EmployeeStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class GoalStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


@dataclass
class Persona:
    """Who the employee *is* — drives tone, behaviour and boundaries."""

    display_name: str = ""
    role: str = "Social Media Manager"
    bio: str = ""
    tone: str = "professional"          # professional | casual | witty | ...
    language: str = "ar"                # primary language
    dialect: str = ""                   # optional dialect/style variant (e.g. Saudi, Egyptian)
    values: List[str] = field(default_factory=list)
    dos: List[str] = field(default_factory=list)
    donts: List[str] = field(default_factory=list)
    interests: List[str] = field(default_factory=list)   # topic focus (politics, economy, ...)
    instructions: str = ""              # free-form standing instructions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "display_name": self.display_name,
            "role": self.role,
            "bio": self.bio,
            "tone": self.tone,
            "language": self.language,
            "dialect": self.dialect,
            "values": self.values,
            "dos": self.dos,
            "donts": self.donts,
            "interests": self.interests,
            "instructions": self.instructions,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Persona":
        data = data or {}
        return cls(
            display_name=data.get("display_name", ""),
            role=data.get("role", "Social Media Manager"),
            bio=data.get("bio", ""),
            tone=data.get("tone", "professional"),
            language=data.get("language", "ar"),
            dialect=data.get("dialect", ""),
            values=data.get("values", []),
            dos=data.get("dos", []),
            donts=data.get("donts", []),
            interests=data.get("interests", []),
            instructions=data.get("instructions", ""),
        )


@dataclass
class Goal:
    """A single objective the planner turns into tasks."""

    id: str
    description: str
    metric: str = ""                    # e.g. "engagement", "followers"
    target: Optional[float] = None
    priority: int = 3                   # 1 (highest) .. 5 (lowest)
    status: GoalStatus = GoalStatus.PENDING
    progress: float = 0.0               # 0..1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "metric": self.metric,
            "target": self.target,
            "priority": self.priority,
            "status": self.status.value,
            "progress": self.progress,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Goal":
        return cls(
            id=data["id"],
            description=data.get("description", ""),
            metric=data.get("metric", ""),
            target=data.get("target"),
            priority=int(data.get("priority", 3)),
            status=GoalStatus(data.get("status", "pending")),
            progress=float(data.get("progress", 0.0)),
        )


@dataclass
class SchedulerConfig:
    """When the employee should wake itself."""

    enabled: bool = True
    interval_minutes: int = 60          # base cadence
    active_hours_start: int = 8         # 24h, employee "business hours"
    active_hours_end: int = 23
    timezone: str = "Asia/Riyadh"
    wake_on_mention: bool = True
    wake_on_reply: bool = True
    wake_on_news: bool = False
    next_wake_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "active_hours_start": self.active_hours_start,
            "active_hours_end": self.active_hours_end,
            "timezone": self.timezone,
            "wake_on_mention": self.wake_on_mention,
            "wake_on_reply": self.wake_on_reply,
            "wake_on_news": self.wake_on_news,
            "next_wake_at": self.next_wake_at.isoformat() if self.next_wake_at else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SchedulerConfig":
        data = data or {}
        nwa = data.get("next_wake_at")
        return cls(
            enabled=data.get("enabled", True),
            interval_minutes=int(data.get("interval_minutes", 60)),
            active_hours_start=int(data.get("active_hours_start", 8)),
            active_hours_end=int(data.get("active_hours_end", 23)),
            timezone=data.get("timezone", "Asia/Riyadh"),
            wake_on_mention=data.get("wake_on_mention", True),
            wake_on_reply=data.get("wake_on_reply", True),
            wake_on_news=data.get("wake_on_news", False),
            next_wake_at=datetime.fromisoformat(nwa) if nwa else None,
        )


@dataclass
class RuntimeState:
    """Mutable state persisted between (temporary) runtimes.

    The runtime is temporary; this snapshot is what makes the *employee*
    permanent. It is loaded on wake and saved before the runtime is destroyed.
    """

    last_cycle_at: Optional[datetime] = None
    cycles_completed: int = 0
    working_memory: Dict[str, Any] = field(default_factory=dict)
    current_plan: List[Dict[str, Any]] = field(default_factory=list)
    last_decision: Optional[Dict[str, Any]] = None
    counters: Dict[str, int] = field(default_factory=dict)   # e.g. posts_today
    flags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "cycles_completed": self.cycles_completed,
            "working_memory": self.working_memory,
            "current_plan": self.current_plan,
            "last_decision": self.last_decision,
            "counters": self.counters,
            "flags": self.flags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RuntimeState":
        data = data or {}
        lca = data.get("last_cycle_at")
        return cls(
            last_cycle_at=datetime.fromisoformat(lca) if lca else None,
            cycles_completed=int(data.get("cycles_completed", 0)),
            working_memory=data.get("working_memory", {}),
            current_plan=data.get("current_plan", []),
            last_decision=data.get("last_decision"),
            counters=data.get("counters", {}),
            flags=data.get("flags", {}),
        )


@dataclass
class EmployeeConfig:
    """The permanent, isolated configuration of one AI Employee.

    ``credentials_ref`` and ``cookie_label`` point to secrets stored in the
    per-employee vault/workspace — they are never inlined here in plaintext.
    """

    id: str
    user_id: Optional[int]
    platform: str                       # "x", "instagram", ...
    account_username: str               # the connected account handle
    cookie_label: str = ""              # label used by the X automation layer
    status: EmployeeStatus = EmployeeStatus.ACTIVE

    persona: Persona = field(default_factory=Persona)
    goals: List[Goal] = field(default_factory=list)
    knowledge: List[str] = field(default_factory=list)      # knowledge base entries/refs
    installed_skills: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)     # names of connected MCP servers
    business_rules: Dict[str, Any] = field(default_factory=dict)

    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    runtime_state: RuntimeState = field(default_factory=RuntimeState)

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "platform": self.platform,
            "account_username": self.account_username,
            "cookie_label": self.cookie_label,
            "status": self.status.value,
            "persona": self.persona.to_dict(),
            "goals": [g.to_dict() for g in self.goals],
            "knowledge": self.knowledge,
            "installed_skills": self.installed_skills,
            "mcp_servers": self.mcp_servers,
            "business_rules": self.business_rules,
            "scheduler": self.scheduler.to_dict(),
            "runtime_state": self.runtime_state.to_dict(),
        }
