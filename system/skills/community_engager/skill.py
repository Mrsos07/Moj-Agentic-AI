"""
مهارة موظف مدمجة في النظام: community_engager — المصدر الوحيد لكودها (system/skills/community_engager/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class CommunityEngagerSkill(_h.ISkill):
    """Reacts to mentions/replies to the account.

    Two sources:
    1. An event-delivered mention/reply (webhook/notification wake).
    2. The Mentions tab read during Observe (``scratch['mentions']``) — the
       people who replied to / mentioned the account. The employee replies back
       **briefly**, **one reply per incoming reply**, and **never twice** to the
       same one (permanent de-duplication via ``engaged_urls``).
    """

    name = "community_engager"
    claim_priority = 90
    uses_capabilities = ["reply_to_user", "like_post", "read_notifications"]

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        payload = context.event.payload or {}
        actions: List[_h.ProposedAction] = []
        tweet_url = payload.get("tweet_url")
        if tweet_url and context.event.type.value in ("mention", "reply", "notification"):
            original_text = payload.get("text", "") or ""
            if _h._is_specialty_match(context, original_text):
                actions.append(_h.ProposedAction(
                    capability="reply_to_user",
                    params={"tweet_url": tweet_url, "original_text": original_text,
                            "brief": True, "is_mention_reply": True},
                    rationale="incoming mention/reply is within employee specialty",
                    expected_impact=0.8, urgency=0.9, risk=0.3,
                ))
                actions.append(_h.ProposedAction(
                    capability="like_post",
                    params={"tweet_url": tweet_url},
                    rationale="acknowledge in-scope engagement", expected_impact=0.3,
                    urgency=0.4, risk=0.05,
                ))

        # الردود/المنشنات المقروءة من تبويب التنبيهات هذه الدورة: ردّ مختصر واحد
        # لكل ردّ، دون تكرار الردّ على نفس الردّ أبداً.
        actions.extend(self._reply_to_mentions(context))
        return actions

    @staticmethod
    def _reply_to_mentions(context: "ExecutionContext") -> List[_h.ProposedAction]:
        mentions = context.scratch.get("mentions") or []
        if not mentions:
            return []

        engaged = _h._engaged_urls(context)
        rules = context.config.business_rules or {}
        # حد أقصى للردود على التنبيهات في الدورة الواحدة (افتراضي 3)
        cap = int(rules.get("max_reply_notifications_per_cycle",
                            rules.get("max_replies_per_cycle", 3)))

        out: List[_h.ProposedAction] = []
        for m in mentions:
            if len(out) >= cap:
                break
            url = (m.get("url") or "").strip()
            text = (m.get("text") or "").strip()
            # ردّ واحد لكل ردّ بشكل دائم: تخطَّ ما سبق التفاعل معه
            if not url or url in engaged:
                continue
            # لا ننجرّ خلف السبام/الإعلانات أو ما يخالف محظورات الشخصية
            if _h._is_ad_or_spam_text(text) or _h._violates_persona_donts(text, context):
                continue
            out.append(_h.ProposedAction(
                capability="reply_to_user",
                params={"tweet_url": url, "original_text": text, "brief": True,
                        "is_mention_reply": True},
                rationale="reply back briefly to someone who replied to us (one reply, no repeats)",
                expected_impact=0.85, urgency=0.95, risk=0.2,
            ))
        return out
