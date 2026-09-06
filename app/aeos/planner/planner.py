"""
Planner.

Collects candidate actions from the employee's installed skills, augments them
with LLM-driven ideas when a reasoning provider is available, de-duplicates, and
returns an ordered plan. It does NOT decide what to execute — that is the
Decision Engine's job (separation of planning from deciding).
"""
from __future__ import annotations

import abc
import logging
from typing import TYPE_CHECKING, List

from ..skills.base import ProposedAction

if TYPE_CHECKING:
    from ..cognition.context import ExecutionContext

logger = logging.getLogger("aeos.planner")


class IPlanner(abc.ABC):
    @abc.abstractmethod
    def plan(self, context: "ExecutionContext") -> List[ProposedAction]: ...


class SkillBasedPlanner(IPlanner):
    """Aggregates proposals from installed skills (+ optional LLM ideation)."""

    def __init__(self, skill_registry, llm=None) -> None:
        self._skills = skill_registry
        self._llm = llm

    def plan(self, context: "ExecutionContext") -> List[ProposedAction]:
        proposals: List[ProposedAction] = []

        # 1) skill-driven proposals
        for skill in self._skills.resolve(context.config.installed_skills):
            try:
                proposals.extend(skill.propose(context))
            except Exception:  # noqa: BLE001
                logger.exception("skill %s failed to propose", skill.name)

        # 2) optional LLM ideation grounded in persona + goals + memory
        if self._llm and self._llm.available and not proposals:
            proposals.extend(self._llm_ideate(context))

        # 3) always allow the "wait" fallback so a cycle can be a no-op
        proposals.append(ProposedAction(
            capability="wait", params={"reason": "no higher-value action"},
            rationale="fallback", expected_impact=0.05, urgency=0.05, risk=0.0,
        ))

        return self._limit_per_target(self._dedupe(proposals), context)

    def _llm_ideate(self, context: "ExecutionContext") -> List[ProposedAction]:
        prompt = context.planning_prompt()
        data = self._llm.complete_json(
            system="You are the planning module of an autonomous social media AI employee. "
                   "Propose at most 3 concrete actions as JSON: "
                   '{"actions":[{"capability":"publish_content","params":{},"rationale":"",'
                   '"expected_impact":0.0,"urgency":0.0,"risk":0.0}]}',
            prompt=prompt,
        )
        out: List[ProposedAction] = []
        for a in (data.get("actions") or [])[:3]:
            cap = a.get("capability")
            if not cap or not context.capabilities.has(cap):
                continue
            # احترم هدف النشر اليومي: لا يقترح الـLLM تغريدة إن كان الهدف 0
            # أو بلغ الحدّ اليومي (نفس البوابة التي تلتزم بها مهارات المحتوى).
            if cap in ("publish_content", "publish_thread", "schedule_post") \
                    and not self._can_post(context):
                logger.info("dropping llm %s proposal (daily_post_target reached/zero)", cap)
                continue
            # The LLM has no access to real tweet ids/urls (observations expose
            # only text/handle previews), so any interaction target it proposes
            # is hallucinated. Restrict ideation to capabilities that don't act
            # on a specific existing tweet — grounded interactions come from the
            # skills that see the real timeline/top-posts URLs.
            if cap not in self._LLM_IDEATABLE_CAPS:
                logger.info("dropping llm proposal '%s' (not ideatable / needs real target)", cap)
                continue
            params = self._normalize_params(cap, dict(a.get("params", {})))
            if params is None:
                # interaction target could not be resolved -> skip to avoid a
                # guaranteed capability failure (e.g. "tweet_url is required").
                logger.info("dropping llm proposal '%s' with unresolved target", cap)
                continue
            out.append(ProposedAction(
                capability=cap,
                params=params,
                rationale=a.get("rationale", "llm proposal"),
                expected_impact=float(a.get("expected_impact", 0.5)),
                urgency=float(a.get("urgency", 0.5)),
                risk=float(a.get("risk", 0.3)),
            ))
        return out

    #: capabilities the LLM may ideate directly — none of these act on a specific
    #: existing tweet/account the LLM cannot actually observe, so they can't be
    #: grounded on a hallucinated target. Interactions (retweet/like/reply/follow)
    #: are intentionally excluded and must come from grounded skills.
    _LLM_IDEATABLE_CAPS = {
        "publish_content", "publish_thread", "schedule_post", "store_knowledge",
        "research_topic", "fetch_trends", "fetch_news", "browse_timeline",
        "browse_x_trends", "browse_top_posts", "read_notifications",
        "read_own_metrics", "generate_image", "wait",
    }

    #: capabilities that act on an existing tweet and therefore need a tweet_url
    _TWEET_TARGET_CAPS = {"retweet_post", "like_post", "reply_to_user",
                          "quote_post", "bookmark_post"}

    @staticmethod
    def _normalize_params(capability: str, params: dict):
        """Coerce LLM-proposed params into the shape capabilities expect.

        The LLM often returns ``tweet_id``/``id`` (or a bare ``url``) instead of
        the ``tweet_url`` the X interaction capabilities require, or a ``handle``
        instead of a ``profile_url`` for follows. Normalize those here so a valid
        proposal is not thrown away as a false failure. Returns ``None`` when a
        required target cannot be resolved (caller should drop the proposal)."""
        def _tweet_url_from_id(tid: str) -> str:
            tid = str(tid or "").strip().lstrip("#")
            return f"https://x.com/i/web/status/{tid}" if tid.isdigit() else ""

        def _profile_url_from_handle(handle: str) -> str:
            h = str(handle or "").strip().lstrip("@").strip()
            return f"https://x.com/{h}" if h else ""

        if capability in SkillBasedPlanner._TWEET_TARGET_CAPS:
            url = str(params.get("tweet_url") or params.get("url") or "").strip()
            if not url:
                url = _tweet_url_from_id(params.get("tweet_id") or params.get("id"))
            if not url:
                return None
            params["tweet_url"] = url
            params.pop("url", None)
            params.pop("tweet_id", None)
            params.pop("id", None)

        elif capability == "follow_user":
            profile = str(params.get("profile_url") or "").strip()
            if not profile:
                profile = _profile_url_from_handle(
                    params.get("handle") or params.get("username"))
            if not profile:
                return None
            params["profile_url"] = profile

        return params

    @staticmethod
    def _can_post(context: "ExecutionContext") -> bool:
        """True only if the employee is still allowed to publish today.

        Mirrors the content skills' gate so LLM ideation cannot bypass the
        user's ``daily_post_target`` (0 = never post)."""
        rules = context.config.business_rules or {}
        target = int(rules.get("daily_post_target", 1))
        max_per_day = int(rules.get("max_posts_per_day", 999))
        posts_today = int(context.config.runtime_state.counters.get("posts_today", 0))
        if target <= 0 or max_per_day <= 0:
            return False
        return posts_today < target and posts_today < max_per_day

    @staticmethod
    def _dedupe(proposals: List[ProposedAction]) -> List[ProposedAction]:
        seen = set()
        unique = []
        for p in proposals:
            key = (p.capability, str(sorted(p.params.items())) if p.params else "")
            if key in seen:
                continue
            seen.add(key)
            unique.append(p)
        return unique

    #: إجراءات «صاخبة» على التغريدة نفسها: كلها تُنتج ظهوراً علنياً باسمنا،
    #: فاجتماع اثنين منها على منشور واحد في دورة واحدة سلوك سبام صريح.
    _LOUD_CAPS = ("reply_to_user", "quote_post", "retweet_post")

    #: أولوية القيمة عند التزاحم على الهدف نفسه (الأعلى يبقى)
    _TARGET_PRIORITY = {
        "reply_to_user": 5, "quote_post": 4, "retweet_post": 3,
        "follow_user": 2, "like_post": 1, "bookmark_post": 0,
    }

    #: إجراءات غير علنية: صاحب الحساب لا يُشعَر بها إطلاقاً، فلا معنى لأن
    #: تستهلك سقف «الإلحاح على حساب واحد» المقصود بمنع إغراقه بالإشعارات.
    #: (قياس: حسابُها أسقط الحفظ من 14 إلى صفر في محاكاة 40 دورة.)
    _SILENT_CAPS = ("bookmark_post",)

    @staticmethod
    def _limit_per_target(proposals: List[ProposedAction],
                          context: "ExecutionContext") -> List[ProposedAction]:
        """امنع تكدّس عدة إجراءات صاخبة على الهدف/الحساب نفسه في دورة واحدة.

        ``_dedupe`` يقارن ``(capability, params)`` فقط، والمهارات تسحب كلها من
        ``_engagement_pool`` نفسه: ``TimelineEngagerSkill`` يردّ على تغريدة،
        و``ContentQuoterSkill`` يقتبس **نفس** التغريدة (أعلى العناصر صلةً)،
        وقد ينضم إليهما ريتويت — ثلاثة إشعارات لصاحب المنشور من حسابٍ واحد في
        دقيقة. الإعجاب يبقى مسموحاً مع إجراء صاخب واحد (سلوك بشري طبيعي).

        أوامر المالك (``owner_directed``) لا تُمسّ إطلاقاً: هو المرجع الأعلى
        وقد يطلب صراحةً «ردّ واقتبس نفس التغريدة».
        """
        from ..employee.interaction_ledger import handle_from_url, normalize_handle

        rules = getattr(context.config, "business_rules", None) or {}
        try:
            per_handle = max(int(rules.get("max_actions_per_handle_per_cycle", 2)), 1)
        except (TypeError, ValueError):
            per_handle = 2

        ordered = sorted(
            proposals,
            key=lambda p: (
                bool((p.params or {}).get("owner_directed")),
                SkillBasedPlanner._TARGET_PRIORITY.get(p.capability, 0),
                p.expected_impact,
            ),
            reverse=True,
        )
        loud_targets: set = set()
        handle_counts: dict = {}
        kept_ids: set = set()
        for p in ordered:
            params = p.params or {}
            if params.get("owner_directed") or p.capability == "wait":
                kept_ids.add(id(p))
                continue
            url = str(params.get("tweet_url") or params.get("profile_url") or "").strip()
            if url and p.capability in SkillBasedPlanner._LOUD_CAPS:
                if url in loud_targets:
                    logger.info("dropping %s on %s (a louder action already targets it)",
                                p.capability, url[:60])
                    continue
                loud_targets.add(url)
            handle = normalize_handle(params.get("handle") or "") or handle_from_url(url)
            # من خاطبنا يستحق رداً على كل رسالة: لا يُطبَّق سقف الحساب على
            # ردود المنشنات وإلا صمتنا عن شخص أرسل لنا ثلاث رسائل.
            if handle and not params.get("is_mention_reply") \
                    and p.capability not in SkillBasedPlanner._SILENT_CAPS:
                if handle_counts.get(handle, 0) >= per_handle:
                    logger.info("dropping %s on @%s (per-handle cycle cap %d)",
                                p.capability, handle, per_handle)
                    continue
                handle_counts[handle] = handle_counts.get(handle, 0) + 1
            kept_ids.add(id(p))

        # أعِد الترتيب الأصلي: الترتيب هنا كان لأغراض المفاضلة فقط
        return [p for p in proposals if id(p) in kept_ids]
