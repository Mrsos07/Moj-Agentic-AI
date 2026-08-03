"""
Cross-cutting abstract interfaces (SOLID / DIP).

These are the seams that let every component be replaced without affecting the
rest of the platform. Domain-specific contracts (MCP servers, capabilities,
memory layers) live next to their implementations; the interfaces here are the
truly cross-cutting ones that many layers depend on.
"""
from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # avoid import cycles at runtime
    from ..core.events import Event
    from ..employee.models import EmployeeConfig
    from ..runtime.job import ExecutionJob


class ILLMProvider(abc.ABC):
    """Reasoning engine used by cognitive phases, planner and skills.

    Kept deliberately small so it can wrap OpenAI, Gemini, a local model, or a
    deterministic stub in tests.
    """

    @abc.abstractmethod
    def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 1000,
    ) -> str:
        """Return a text completion."""

    @abc.abstractmethod
    def complete_json(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.3,
    ) -> Dict[str, Any]:
        """Return a parsed JSON object completion (best-effort)."""

    @property
    @abc.abstractmethod
    def available(self) -> bool:
        """Whether the provider is configured and usable."""


class IEmployeeRepository(abc.ABC):
    """Persistence contract for employee configurations.

    The runtime loads config through this interface and never touches the ORM
    directly, so the storage backend (SQLite/Postgres/Mongo) is swappable.
    """

    @abc.abstractmethod
    def get(self, employee_id: str) -> Optional["EmployeeConfig"]: ...

    @abc.abstractmethod
    def list_active(self) -> List["EmployeeConfig"]: ...

    @abc.abstractmethod
    def save(self, config: "EmployeeConfig") -> "EmployeeConfig": ...

    @abc.abstractmethod
    def update_runtime_state(self, employee_id: str, state: Dict[str, Any]) -> None: ...

    @abc.abstractmethod
    def list_due(self, now_iso: str) -> List["EmployeeConfig"]:
        """Employees whose next scheduled wake time is <= now."""


class IJobQueue(abc.ABC):
    """A durable-ish queue of execution jobs consumed by shared workers.

    Default impl is in-memory; can be reimplemented on Redis/DB for durability
    and multi-process scale without changing producers or workers.
    """

    @abc.abstractmethod
    def enqueue(self, job: "ExecutionJob") -> None: ...

    @abc.abstractmethod
    def dequeue(self, timeout: float = 1.0) -> Optional["ExecutionJob"]: ...

    @abc.abstractmethod
    def size(self) -> int: ...


class IRuntimeManager(abc.ABC):
    """The ONLY component allowed to create an AI Employee runtime.

    Contract: load -> build context -> run exactly one cognitive cycle ->
    persist everything -> destroy the runtime.
    """

    @abc.abstractmethod
    def run_cycle(self, employee_id: str, event: "Event") -> Dict[str, Any]:
        """Execute a single decision cycle for an employee and return a report."""
