"""
مهارة موظف مدمجة في النظام: directed_mission — المصدر الوحيد لكودها (system/skills/directed_mission/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class DirectedMissionSkill(_h.ISkill):
    """Execute a free-form owner mission where the EMPLOYEE picks the target.

    Triggered by ``directed_mission`` in the event payload — produced from open
    chat («روح للتايم لاين وشوف تغريدة تناسب تخصصك وردّ عليها»، «تابع حسابين في
    مجالك»، «اقتبس أقوى تغريدة في الترند»). Unlike ``directed_engager`` (which
    acts on a post the owner pasted), here the owner names only the *source*,
    the *actions* and optionally a topic constraint; the employee scans that
    source, ranks candidates by the constraint then by specialty, and proposes
    the requested actions on DIFFERENT targets (never the same tweet twice).

    Owner authority: actions carry ``owner_directed`` so the persona guard and
    the daily engagement budgets do not silently drop an explicit order. The
    specialty filter still applies unless the owner allowed a free scope.
    Records an Arabic note when the source has no usable target, so the chat
    report explains *why* nothing happened instead of going silent.
    """

    name = "directed_mission"
    claim_priority = 100
    uses_capabilities = ["reply_to_user", "like_post", "retweet_post",
                         "quote_post", "follow_user"]
    always_on = True

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        mission = (context.event.payload or {}).get("directed_mission")
        if not isinstance(mission, dict):
            return []
        wanted = [a for a in (mission.get("actions") or [])
                  if a in _h._MISSION_CAPABILITY]
        if not wanted:
            return []

        pool = _h._mission_candidates(context, mission)
        if not pool and str(mission.get("scope")) != "free":
            # لا هدف داخل التخصص: أعِد المحاولة بلا قيد الموضوع قبل الاستسلام،
            # فالقيد النصّي قد يكون أضيق من محتوى المصدر الفعلي.
            pool = _h._mission_candidates(
                context, {**mission, "query": ""})
        if not pool:
            context.scratch["directed_mission_note"] = (
                "نفّذت المهمّة لكن لم أجد في المصدر المطلوب هدفاً يصلح للتفاعل "
                "(إمّا لا شيء ضمن تخصصي، أو تفاعلت مسبقاً مع كل ما ظهر)."
            )
            return []

        try:
            count = max(1, min(int(mission.get("count") or 1), 5))
        except (TypeError, ValueError):
            count = 1

        actions: List[_h.ProposedAction] = []
        queue = list(pool)
        engaged = _h._engaged_urls(context)
        # ترتيب القيمة: رد > اقتباس > إعادة نشر > متابعة > إعجاب (وزن الخوارزمية)
        order = ["reply", "quote", "retweet", "follow", "like"]
        for act in [a for a in order if a in wanted]:
            capability = _h._MISSION_CAPABILITY[act]
            taken = 0
            while queue and taken < count:
                t = queue.pop(0)
                url = (t.get("url") or "").strip()
                params = {"original_text": (t.get("text") or "")[:400],
                          "handle": t.get("handle", ""),
                          "owner_directed": True}
                if act == "follow":
                    profile = _h._profile_url_from_handle(t.get("handle", ""))
                    if not profile or profile in engaged:
                        continue
                    params["profile_url"] = profile
                else:
                    if not url:
                        continue
                    params["tweet_url"] = url
                if act == "reply" and mission.get("source") == "mentions":
                    params["brief"] = True
                    params["is_mention_reply"] = True
                taken += 1
                actions.append(_h.ProposedAction(
                    capability=capability, params=params,
                    rationale=f"owner mission: {act} a target the employee "
                              f"picked from {mission.get('source', 'any')}",
                    expected_impact=0.95, urgency=0.95, risk=0.05,
                ))
            # أعِد تعبئة الطابور للفعل التالي كي لا يجوع: الأهداف المختلفة أولاً،
            # وعند نضوبها يُسمح بإعادة استخدام نفس الهدف لفعل مختلف (إعجاب+رد).
            if not queue:
                queue = list(pool)
        return actions
