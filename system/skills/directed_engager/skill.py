"""
مهارة موظف مدمجة في النظام: directed_engager — المصدر الوحيد لكودها (system/skills/directed_engager/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class DirectedEngagementSkill(_h.ISkill):
    """Engage with a SPECIFIC post the account owner pasted in chat.

    Triggered by an owner directive («تفاعل مع هذا المحتوى …» / «رد على …» /
    «اعمل ريتويت …») delivered via the ``directed_engagement`` event payload.
    The owner is the top authority, so this bypasses the specialty scope (the
    guard exempts directed engagement). If a tweet URL is given it engages
    directly; otherwise it live-searches X for the pasted text/handle, picks the
    best-matching tweet, and proposes reply / like / retweet as requested.
    Proposes nothing (and records a chat note) when the target cannot be found.
    """

    name = "directed_engager"
    claim_priority = 100
    uses_capabilities = ["reply_to_user", "like_post", "retweet_post"]
    always_on = True

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        eng = (context.event.payload or {}).get("directed_engagement")
        if not eng:
            return []
        content = str(eng.get("text") or "").strip()
        handle = str(eng.get("handle") or "").strip()
        url = str(eng.get("url") or "").strip()
        wanted = set(eng.get("actions") or ["reply", "like"])

        if not url:
            url, content, handle = self._resolve_tweet(context, content, handle)
        if not url:
            context.scratch["directed_engagement_note"] = (
                "لم أعثر على التغريدة المطابقة للمحتوى المطلوب على X، فتعذّر التفاعل معها."
            )
            return []

        original = content[:400]
        actions: List[_h.ProposedAction] = []
        # الرد أولاً (أعلى قيمة)، ثم الإعجاب، ثم إعادة النشر إن طُلبت
        if "reply" in wanted:
            actions.append(_h.ProposedAction(
                capability="reply_to_user",
                params={"tweet_url": url, "original_text": original, "handle": handle,
                        "owner_directed": True},
                rationale="owner-directed engagement: reply to a specific post",
                expected_impact=0.95, urgency=0.95, risk=0.1,
            ))
        if "like" in wanted:
            actions.append(_h.ProposedAction(
                capability="like_post",
                params={"tweet_url": url, "original_text": original, "handle": handle,
                        "owner_directed": True},
                rationale="owner-directed engagement: like a specific post",
                expected_impact=0.6, urgency=0.85, risk=0.05,
            ))
        if "retweet" in wanted:
            actions.append(_h.ProposedAction(
                capability="retweet_post",
                params={"tweet_url": url, "original_text": original, "handle": handle,
                        "owner_directed": True},
                rationale="owner-directed engagement: retweet a specific post",
                expected_impact=0.7, urgency=0.85, risk=0.15,
            ))
        return actions

    @staticmethod
    def _resolve_tweet(context: "ExecutionContext", text: str, handle: str):
        """Locate the pasted content on X via live search.

        Returns ``(url, text, handle)`` — url is empty when no match is found.
        """
        from app.aeos.mcp.base import MCPRequest

        if "playwright_x" not in (context.config.mcp_servers or []):
            return "", text, handle

        # نص التغريدة: إن كان المحتوى بصيغة «اسم: نص» خذ ما بعد أول نقطتين
        body = text
        if ":" in body or "：" in body:
            body = _h.re.split(r"[:：]", body, 1)[-1].strip()
        body = body.strip("*«»\"'“” ").strip()

        h = handle.lstrip("@").strip()
        words = [w for w in _h.re.split(r"\s+", body) if len(w) > 1]
        query = " ".join(words[:8]).strip()
        if not query and h:
            query = f"from:{h}"
        if not query:
            return "", text, handle

        sr = context.mcp.call(MCPRequest(
            employee_id=context.config.id, tool="search_tweets",
            params={"cookie_label": context.config.cookie_label,
                    "query": query, "limit": 12},
            context={"server": "playwright_x",
                     "cookie_label": context.config.cookie_label},
        ))
        tweets = sr.data.get("tweets", []) if sr.success else []
        if not tweets:
            return "", text, handle

        body_words = _h.normalize_topic_words(body)

        def _match(t: dict) -> float:
            score = _h.topic_overlap_ratio(_h.normalize_topic_words(t.get("text", "")), body_words)
            if h and h.lower() in (t.get("handle", "") or "").lower():
                score += 0.5
            return score

        best = max(tweets, key=_match)
        if _match(best) <= 0:
            return "", text, handle
        return (
            (best.get("url", "") or "").strip(),
            (best.get("text") or text),
            (best.get("handle") or handle),
        )
