"""
Skill contract.

A Skill is a higher-order, *installable* behaviour that an employee can be given
(e.g. "Daily Content Creator", "Community Engager", "Trend Watcher"). Skills are
composed on top of Capabilities — they propose candidate actions during the
Planner phase given the current context. This makes the platform extensible:
authoring a new autonomous behaviour = writing a new Skill, no core changes.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from ..cognition.context import ExecutionContext


@dataclass
class ProposedAction:
    """A candidate action a skill suggests for this cycle."""

    capability: str
    params: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    expected_impact: float = 0.5    # 0..1
    urgency: float = 0.5            # 0..1
    risk: float = 0.2              # 0..1


class ISkill(abc.ABC):
    """An installable autonomous behaviour."""

    #: unique skill name (referenced from EmployeeConfig.installed_skills)
    name: str
    #: capabilities this skill relies on
    uses_capabilities: List[str] = []

    @abc.abstractmethod
    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        """Return candidate actions given the current execution context."""
