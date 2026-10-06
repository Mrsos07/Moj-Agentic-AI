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
    #: المهارة التي اقترحته (يملؤها المخطِّط) — مفتاح التعلّم من التجربة: أي مهارة
    #: تُقبل اقتراحاتها وتُثمر، وأيّها يرفضها الحارس دورةً بعد دورة.
    source: str = ""


class ISkill(abc.ABC):
    """An installable autonomous behaviour."""

    #: unique skill name (referenced from EmployeeConfig.installed_skills)
    name: str
    #: capabilities this skill relies on
    uses_capabilities: List[str] = []
    #: ترتيب حجز الأهداف: الأعلى يختار هدفه أولاً من حوض التفاعل المشترك.
    #: ضروري لأن المهارات كلها تقرأ نفس الحوض بنفس الترتيب، فمن يسبق يأخذ
    #: أفضل الأهداف. الأفعال **النادرة عالية القيمة** (اقتباس: 2 يومياً) يجب
    #: أن تسبق الوفيرة (إعجاب: 12 يومياً) التي تجد بديلاً دائماً — وإلا صفر
    #: اقتباس/حفظ في 40 دورة (مُقاس). لا يجوز أن يحدّد هذا الترتيبَ ترتيبُ
    #: ``installed_skills`` المحفوظ في قاعدة البيانات: إنه عرضي وقديم.
    claim_priority: int = 0

    #: always resolved regardless of ``installed_skills`` — for skills that only
    #: react to an explicit owner directive in the event payload (they propose
    #: nothing otherwise). Without this, an employee created before the skill
    #: existed would silently ignore the owner's command.
    always_on: bool = False

    @abc.abstractmethod
    def propose(self, context: "ExecutionContext") -> List[ProposedAction]:
        """Return candidate actions given the current execution context."""
