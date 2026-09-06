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
        """المهارات المثبّتة للموظف + المهارات الإلزامية (always_on).

        المهارات الإلزامية تنفّذ أمر المالك الصريح من الشات ولا تقترح شيئاً بلا
        أمر، فحجبها بسبب قائمة مهارات قديمة محفوظة في قاعدة البيانات يعني أن
        الموظف يتجاهل أمراً واضحاً بصمت.

        المهارات تُرتَّب بـ``claim_priority`` لا بترتيب ``installed_skills``:
        كلها تسحب أهدافها من حوض التفاعل نفسه، فمن يعمل أولاً يأخذ أفضل هدف.
        الترتيب المحفوظ في قاعدة البيانات عرضيّ (يعود لتاريخ إنشاء الموظف)،
        وتركُ الأولوية له يعني أن مهارةً كاملة قد لا تجد هدفاً أبداً — قِيس
        صفر اقتباس وصفر حفظ في 40 دورة لأن مهارة الإعجاب سبقتهما فحجزت كل شيء.
        """
        picked = [self._skills[n] for n in names or [] if n in self._skills]
        chosen = {s.name for s in picked}
        picked.extend(s for name, s in self._skills.items()
                      if getattr(s, "always_on", False) and name not in chosen)
        picked.sort(key=lambda s: getattr(s, "claim_priority", 0), reverse=True)
        return picked

    def list_skills(self) -> List[str]:
        return list(self._skills.keys())
