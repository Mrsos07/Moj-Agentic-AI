"""
مهارة موظف مدمجة في النظام: timeline_engager — المصدر الوحيد لكودها (system/skills/timeline_engager/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class TimelineEngagerSkill(_h.ISkill):
    """Interest-aware engagement with the authenticated home timeline.

    Filters the timeline (read during Observe) down to tweets that match the
    employee's persona interests (e.g. a political persona engages only with
    political content/accounts), then proposes like / retweet / reply / follow
    on the most relevant ones. Reply text is left empty so the reply capability
    generates it from the persona/instructions. Requires the ``browse_timeline``
    capability (playwright_x MCP) to have populated the timeline scratch.
    """

    name = "timeline_engager"
    claim_priority = 10
    uses_capabilities = [
        "browse_timeline", "like_post", "retweet_post", "reply_to_user", "follow_user",
    ]

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        relevant = _h._engagement_pool(context)
        if not relevant:
            return []

        rules = context.config.business_rules or {}
        max_likes = int(rules.get("max_likes_per_cycle", 3))
        max_replies = int(rules.get("max_replies_per_cycle", 1))
        max_retweets = int(rules.get("max_retweets_per_cycle", 1))
        max_follows = int(rules.get("max_follows_per_cycle", 1))

        actions: List[_h.ProposedAction] = []
        engaged = _h._engaged_urls(context)

        def _take(n: int, tag: str) -> List[dict]:
            """أهداف مختلفة لكل نوع تفاعل — ومحجوزة عن بقية المهارات أيضاً."""
            return _h.claim_targets(context, relevant, n, owner=f"timeline:{tag}")

        def _impact(t: dict, base: float) -> float:
            """تأثير ديناميكي حسب جودة الهدف: درجة الصلة بالتخصص + وجود
            وسائط — فيُفاضل محرّك القرار بين الأهداف الحقيقية بدل قيم ثابتة."""
            rel = int(t.get("_rel", 0) or 0)
            bonus = 0.04 * min(rel, 4)
            if (t.get("media") or "").strip():
                bonus += 0.05
            return round(min(base + bonus, 0.95), 3)

        # رد حسب البرسونا على الأكثر صلة (أعلى قيمة تفاعلية)
        for t in _take(max_replies, "reply"):
            url = t.get("url")
            if not url:
                continue
            actions.append(_h.ProposedAction(
                capability="reply_to_user",
                params={"tweet_url": url, "original_text": t.get("text", ""),
                        "handle": t.get("handle", "")},
                rationale="reply to a relevant tweet in the employee's specialty",
                expected_impact=_impact(t, 0.68), urgency=0.6, risk=0.3,
            ))

        # إعادة نشر (رتويت) لتغريدة مختلفة
        for t in _take(max_retweets, "retweet"):
            url = t.get("url")
            if not url:
                continue
            actions.append(_h.ProposedAction(
                capability="retweet_post",
                params={"tweet_url": url, "original_text": t.get("text", ""),
                        "handle": t.get("handle", "")},
                rationale="amplify highly-relevant content in the employee's field",
                expected_impact=_impact(t, 0.55), urgency=0.5, risk=0.2,
            ))

        # إعجاب بتغريدات أخرى ذات صلة
        for t in _take(max_likes, "like"):
            url = t.get("url")
            if not url:
                continue
            actions.append(_h.ProposedAction(
                capability="like_post",
                params={"tweet_url": url, "original_text": t.get("text", ""),
                        "handle": t.get("handle", "")},
                rationale=f"like relevant tweet by {t.get('handle','')}",
                expected_impact=_impact(t, 0.35), urgency=0.45, risk=0.05,
            ))

        # متابعة أصحاب أبرز التغريدات ذات الصلة (توجّههم يوافق الموظف)
        follows = 0
        for t in relevant:
            if follows >= max_follows:
                break
            profile_url = _h._profile_url_from_handle(t.get("handle", ""))
            if not profile_url or profile_url in engaged:
                continue
            follows += 1
            actions.append(_h.ProposedAction(
                capability="follow_user",
                params={"profile_url": profile_url, "handle": t.get("handle", ""),
                        "original_text": t.get("text", "")},
                rationale=f"follow relevant account {t.get('handle','')} in the field",
                expected_impact=_impact(t, 0.45), urgency=0.4, risk=0.25,
            ))

        return actions
