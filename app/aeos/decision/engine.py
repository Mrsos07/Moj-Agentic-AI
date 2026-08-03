"""
Decision Engine.

Given the planner's candidate actions, it scores each on a weighted utility
function (impact, urgency, risk, goal alignment) subject to the employee's
business rules (rate limits, quiet hours, allow-lists), then selects the actions
to execute this cycle.

This is intentionally rule-based + transparent (every decision is explainable and
logged). It can be swapped for an LLM/RL policy behind :class:`IDecisionEngine`.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List

from ..skills.base import ProposedAction

if TYPE_CHECKING:
    from ..cognition.context import ExecutionContext


@dataclass
class Decision:
    action: ProposedAction
    score: float
    explanation: str

    def to_dict(self) -> Dict:
        return {
            "capability": self.action.capability,
            "params": self.action.params,
            "score": round(self.score, 4),
            "explanation": self.explanation,
            "rationale": self.action.rationale,
        }


class IDecisionEngine(abc.ABC):
    @abc.abstractmethod
    def decide(
        self, proposals: List[ProposedAction], context: "ExecutionContext"
    ) -> List[Decision]: ...


#: ميزانية يومية لكل نوع تفاعل: (اسم العداد، مفتاح القاعدة، الهدف الافتراضي)
#: تُستهلك على مدار اليوم فتتوزّع التفاعلات بدل تكدّسها في أول الدورات.
ENGAGEMENT_BUDGETS = {
    "reply_to_user": ("replies_today", "daily_replies_target", 6),
    "retweet_post": ("retweets_today", "daily_retweets_target", 4),
    "like_post": ("likes_today", "daily_likes_target", 12),
    "follow_user": ("follows_today", "daily_follows_target", 3),
    "quote_post": ("quotes_today", "daily_quotes_target", 2),
    "bookmark_post": ("bookmarks_today", "daily_bookmarks_target", 4),
}

#: قدرات النشر (تستهلك ميزانية النشر اليومية لا ميزانية التفاعل)
PUBLISHING_CAPABILITIES = ("publish_content", "publish_thread")

#: سقف كل نوع إجراء **في الدورة الواحدة**: (مفتاح القاعدة، الافتراضي)
#: كان المحرّك يسمح بإجراء واحد فقط لكل نوع مهما رفع المستخدم الحد، فكانت
#: خيارات «إعجاب/دورة = 3» بلا أي أثر. الآن الحد المُعلَن هو الحد الفعلي.
CYCLE_LIMIT_RULES = {
    "reply_to_user": ("max_replies_per_cycle", 1),
    "retweet_post": ("max_retweets_per_cycle", 1),
    "like_post": ("max_likes_per_cycle", 3),
    "follow_user": ("max_follows_per_cycle", 1),
    "quote_post": ("max_quotes_per_cycle", 1),
    "bookmark_post": ("max_bookmarks_per_cycle", 1),
    "publish": ("max_posts_per_cycle", 1),
}


def cycle_group(capability: str) -> str:
    """مجموعة السقف: التغريدة المفردة والثريد يتشاركان سقف النشر نفسه."""
    return "publish" if capability in PUBLISHING_CAPABILITIES else capability


def cycle_limit(capability: str, rules: Dict) -> int:
    """سقف هذا النوع في الدورة الواحدة حسب قواعد عمل الموظف."""
    group = cycle_group(capability)
    if group not in CYCLE_LIMIT_RULES:
        return 1
    rule_key, default = CYCLE_LIMIT_RULES[group]
    try:
        return max(int((rules or {}).get(rule_key, default)), 0)
    except (TypeError, ValueError):
        return default


class WeightedUtilityDecisionEngine(IDecisionEngine):
    """Transparent, explainable weighted-utility policy."""

    #: engagement capabilities rotated across cycles for continuous diversity
    ENGAGEMENT_ROTATION = ["like_post", "retweet_post", "reply_to_user",
                           "quote_post", "follow_user"]

    def __init__(
        self,
        w_impact: float = 0.45,
        w_urgency: float = 0.35,
        w_risk: float = 0.30,
        max_actions_per_cycle: int = 4,
        min_score: float = 0.15,
    ) -> None:
        self.w_impact = w_impact
        self.w_urgency = w_urgency
        self.w_risk = w_risk
        self.max_actions = max_actions_per_cycle
        self.min_score = min_score

    def decide(self, proposals: List[ProposedAction], context: "ExecutionContext") -> List[Decision]:
        rules = context.config.business_rules
        max_actions = int(rules.get("max_actions_per_cycle", self.max_actions))

        # تدوير نوع التفاعل المُفضّل كل دورة لضمان التنويع المستمر
        cycles = int(getattr(context.config.runtime_state, "cycles_completed", 0) or 0)
        preferred_engagement = self.ENGAGEMENT_ROTATION[cycles % len(self.ENGAGEMENT_ROTATION)]
        # سجل أنواع الإجراءات عبر آخر عدة دورات (الأحدث أولاً) — عقوبة تكرار
        # متدرّجة: ما نُفّذ في الدورة السابقة يُعاقب بقوة، والأقدم أخف — فلا يتكرر
        # الرد/الرتويت دورةً بعد دورة بل يتناوب السلوك طبيعياً.
        flags = context.config.runtime_state.flags
        history: List[List[str]] = list(flags.get("action_type_history") or [])
        if not history and flags.get("last_action_types"):
            history = [list(flags["last_action_types"])]
        repeat_penalty = float(rules.get("repeat_penalty", 0.25))

        def _history_penalty(cap: str) -> float:
            pen = 0.0
            for age, types in enumerate(history[:6]):
                if cap in (types or []):
                    pen += repeat_penalty * (0.5 ** age)
            return min(pen, repeat_penalty * 2)

        counters = context.config.runtime_state.counters
        budget_pressure = float(rules.get("budget_pressure", 0.30))

        def _budget_factor(cap: str) -> tuple:
            """(عقوبة استهلاك الميزانية، المستهلك، الهدف) — تناقص العائد اليومي."""
            if cap not in ENGAGEMENT_BUDGETS:
                return 0.0, 0, 0
            counter_key, rule_key, default = ENGAGEMENT_BUDGETS[cap]
            target = max(int(rules.get(rule_key, default)), 0)
            used = int(counters.get(counter_key, 0))
            if target <= 0:
                return 0.0, used, target
            return budget_pressure * min(used / target, 1.0), used, target

        scored: List[Decision] = []
        for action in proposals:
            if not self._allowed(action, context):
                continue
            score = (
                self.w_impact * action.expected_impact
                + self.w_urgency * action.urgency
                - self.w_risk * action.risk
            )
            # محاذاة الأهداف الحقيقية (نوع الهدف × أولويته × ما بقي منه) — وزنها
            # معتبر لا رمزي، وإلا لا تؤثّر أهداف المستخدم في أي قرار.
            w_goal = float(rules.get("goal_weight", 0.25))
            alignment = context.goal_alignment(action.capability)
            score += w_goal * alignment
            # مكافأة تدوير: ادفع نوع التفاعل المستهدف هذه الدورة للأعلى.
            rotation_bonus = float(rules.get("rotation_bonus", 0.20))
            if action.capability == preferred_engagement:
                score += rotation_bonus
            bits = [
                f"impact={action.expected_impact:.2f}",
                f"urgency={action.urgency:.2f}", f"risk={action.risk:.2f}",
            ]
            if alignment:
                bits.append(f"goal_fit={alignment:.2f}")
            # عقوبة تكرار متدرّجة عبر الدورات السابقة (عدا الانتظار)
            if action.capability != "wait":
                pen = _history_penalty(action.capability)
                if pen:
                    score -= pen
                    bits.append(f"repeat_pen={pen:.2f}")
            # تناقص العائد: كلما استُهلكت ميزانية النوع اليومية انخفضت أولويته
            # (ردود المنشنات معفاة — من يخاطبنا يستحق رداً دائماً).
            if not action.params.get("is_mention_reply"):
                bpen, used, target = _budget_factor(action.capability)
                if bpen:
                    score -= bpen
                    bits.append(f"budget={used}/{target}")
            bits.append(f"score={score:.2f}")
            scored.append(Decision(
                action=action, score=score, explanation=" ".join(bits),
            ))

        scored.sort(key=lambda d: d.score, reverse=True)

        # اختر الأعلى فوق العتبة مع احترام **سقف كل نوع في الدورة** كما ضبطه
        # المستخدم (إعجاب/دورة، ردود/دورة، ...). التنويع محفوظ عبر مكافأة
        # التدوير وعقوبة التكرار، لا عبر سقف صلب = 1 يُبطل إعدادات المستخدم.
        chosen: List[Decision] = []
        used_counts: Dict[str, int] = {}
        for d in scored:
            if d.score < self.min_score:
                continue
            cap = d.action.capability
            if cap != "wait":
                group = cycle_group(cap)
                limit = cycle_limit(cap, rules)
                if used_counts.get(group, 0) >= limit:
                    continue
                used_counts[group] = used_counts.get(group, 0) + 1
            chosen.append(d)
            if len(chosen) >= max_actions:
                break

        # مضاد الانتظار: إن لم يتجاوز شيء العتبة لكن يوجد مرشّح تفاعل حقيقي
        # موجب القيمة (هدف فعلي ضمن التخصص)، نفّذ أفضله بدل الجلوس بلا فعل —
        # الانتظار قرار متعمّد فقط عندما لا يوجد أي خيار ذو قيمة إطلاقاً.
        if not chosen and scored:
            best_real = next(
                (d for d in scored
                 if d.action.capability != "wait" and d.score > 0.0),
                None,
            )
            if best_real is not None:
                best_real.explanation += " | anti-wait: أفضل مرشّح حقيقي بدل الانتظار"
                chosen = [best_real]
            else:
                wait = next(
                    (d for d in scored if d.action.capability == "wait"), scored[-1])
                wait.explanation += (
                    " | سبب الانتظار: لا يوجد هدف ضمن التخصص أو استُنفدت "
                    "ميزانيات التفاعل اليومية"
                )
                chosen = [wait]

        # مراجعة ذكية نهائية: الـLLM يختار التوليفة الأنسب من المرشّحين
        # الحقيقيين (لا يخترع أهدافاً) — يراعي الشخصية والحالة العاطفية
        # وسجل النشاط الأخير لتجنّب التكرار واختيار الأعلى قيمة فعلاً.
        if bool(rules.get("llm_decision_review", True)):
            chosen = self._llm_review(scored, chosen, context, max_actions)
        return chosen

    def _llm_review(self, scored: List[Decision], chosen: List[Decision],
                    context: "ExecutionContext", max_actions: int) -> List[Decision]:
        """مراجعة LLM للاختيار النهائي — تختار من المرشّحين المُقيّمين فقط.

        تفشل بأمان: أي خلل/غياب LLM يعيد اختيار المنفعة الموزونة كما هو.
        """
        llm = getattr(context, "llm", None)
        if llm is None or not llm.available:
            return chosen
        # لا جدوى من المراجعة إن لم يوجد إلا مرشّح واحد أو انتظار فقط
        real = [d for d in scored if d.action.capability != "wait"]
        if len(real) <= 1:
            return chosen

        top = scored[:10]
        lines = []
        for i, d in enumerate(top):
            p = d.action.params or {}
            target = p.get("handle") or p.get("profile_url") or p.get("tweet_url") or ""
            content = str(p.get("original_text") or p.get("brief") or p.get("text") or "")[:140]
            lines.append(
                f"[{i}] {d.action.capability} | score={d.score:.2f} | الهدف: {target} | "
                f"المحتوى: {content} | المبرر: {str(d.action.rationale)[:100]}"
            )
        emo = context.scratch.get("emotion_state") or {}
        data = llm.complete_json(
            system=(
                "أنت وحدة القرار النهائي لموظف ذكي يدير حساب X باحترافية. "
                "أمامك مرشّحون حقيقيون مُقيّمون بالمنفعة. اختر التوليفة الأنسب "
                "لهذه الدورة: قيمة حقيقية للجمهور، تنوّع في أنواع التفاعل، لا تكرار "
                "لمواضيع/حسابات تفاعلنا معها مؤخراً. يجوز اختيار أكثر من إجراء من "
                "النوع نفسه بحدود سقفه المسموح، لكن فضّل التنويع عند تساوي القيمة. "
                "أجب JSON فقط: "
                '{"selected": [أرقام المرشّحين], "reason": "مبرر موجز"}'
            ),
            prompt=(
                f"{context.persona_prompt()}\n\n"
                f"الحالة العاطفية: {emo.get('mood', 'طبيعية')}\n\n"
                f"نشاطك الأخير (لا تكرره):\n{context.recent_activity_prompt()[:800]}\n\n"
                f"المرشّحون:\n" + "\n".join(lines) + "\n\n"
                f"اختر حتى {max_actions} إجراءات (ويجوز إجراء واحد فقط إن كان الأعلى قيمة)."
            ),
            temperature=0.2,
        ) or {}

        try:
            idxs = [int(i) for i in (data.get("selected") or [])]
        except (TypeError, ValueError):
            return chosen
        reason = str(data.get("reason", ""))[:200]

        rules = context.config.business_rules or {}
        picked: List[Decision] = []
        used_counts: Dict[str, int] = {}
        for i in idxs:
            if not (0 <= i < len(top)):
                continue
            d = top[i]
            cap = d.action.capability
            if cap == "wait":
                continue
            group = cycle_group(cap)
            if used_counts.get(group, 0) >= cycle_limit(cap, rules):
                continue
            used_counts[group] = used_counts.get(group, 0) + 1
            d.explanation += f" | llm_review: {reason}" if reason else " | llm_review"
            picked.append(d)
            if len(picked) >= max_actions:
                break
        # إن أعاد الـLLM اختياراً فارغاً/غير صالح نبقي اختيار المنفعة الموزونة
        return picked or chosen

    def _allowed(self, action: ProposedAction, context: "ExecutionContext") -> bool:
        """Enforce business rules (rate limits, quiet hours, allow-lists)."""
        rules = context.config.business_rules
        blocked = set(rules.get("blocked_capabilities", []))
        if action.capability in blocked:
            return False

        # سقف الدورة = 0 يعني تعطيل النوع كلياً. يُفحص هنا (لا في حلقة الاختيار
        # فقط) وإلا تجاوزه مسارُ «مضاد الانتظار» ومراجعةُ الـLLM فيُنفَّذ إجراء
        # عطّله المستخدم صراحةً.
        if action.capability != "wait" and cycle_limit(action.capability, rules) <= 0:
            return False

        # النشر: النقطة المرجعية الحاسمة لاحترام تعليمات المستخدم.
        # - daily_post_target = 0 يعني: لا نشر إطلاقاً (حتى لو اقترح الـLLM تغريدة).
        # - وإلا لا نتجاوز هدف النشر اليومي ولا الحدّ الأقصى اليومي.
        if action.capability in PUBLISHING_CAPABILITIES:
            counters = context.config.runtime_state.counters
            posts_today = int(counters.get("posts_today", 0))
            daily_target = int(rules.get("daily_post_target", 1))
            max_per_day = int(rules.get("max_posts_per_day", 999))
            if daily_target <= 0 or max_per_day <= 0:
                return False
            if posts_today >= daily_target or posts_today >= max_per_day:
                return False
            # الثريد مكلف (متصفح أطول + انتباه الجمهور) فله سقف يومي خاص
            if action.capability == "publish_thread":
                thread_target = int(rules.get("daily_thread_target", 1))
                if thread_target <= 0:
                    return False
                if int(counters.get("threads_today", 0)) >= thread_target:
                    return False
            if not bool(rules.get("allow_threads", True)) and action.capability == "publish_thread":
                return False

        # ميزانيات التفاعل اليومية: بلوغ الهدف اليومي للنوع يوقفه حتى الغد
        # (ردود المنشنات معفاة — الرد على من يخاطبنا واجب مهني دائماً).
        if action.capability in ENGAGEMENT_BUDGETS and not action.params.get("is_mention_reply"):
            counter_key, rule_key, default = ENGAGEMENT_BUDGETS[action.capability]
            target = int(rules.get(rule_key, default))
            if target <= 0:
                return False
            used = int(context.config.runtime_state.counters.get(counter_key, 0))
            if used >= target:
                return False
        return True
