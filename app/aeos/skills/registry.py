"""Skill registry — resolves an employee's installed skills."""
from __future__ import annotations

import logging
from typing import Dict, List

from .base import ISkill

logger = logging.getLogger("aeos.skills")


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: Dict[str, ISkill] = {}

    def register(self, skill: ISkill) -> None:
        self._skills[skill.name] = skill
        logger.info("skill registered: %s", skill.name)

    def get(self, name: str) -> ISkill | None:
        return self._skills.get(name)

    def resolve(self, names: List[str]) -> List[ISkill]:
        return [self._skills[n] for n in names if n in self._skills]

    def list_skills(self) -> List[str]:
        return list(self._skills.keys())
