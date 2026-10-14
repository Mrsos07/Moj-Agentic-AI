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
import logging
from dataclasses import dataclass
from datetime import datetime
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


def _day_progress(config) -> float:
    """كم انقضى من يوم عمل الموظف (0..1) — مرجع الإيقاع اليومي.

    بلا مرجع زمني تصير «نسبة الاستهلاك» بلا معنى: 50% من الحصة في الساعة
    الأولى سلوك متكتّل، ونفسها في آخر ساعة تأخّر. نقيس التقدّم داخل ساعات
    العمل المعلنة للموظف (ومنطقته الزمنية)، ونعيد 1.0 خارجها (آخر اليوم).
    """
    sched = getattr(config, "scheduler", None)
    start = int(getattr(sched, "active_hours_start", 0) or 0)
    end = int(getattr(sched, "active_hours_end", 23) or 23)
    tz_name = getattr(sched, "timezone", "") or ""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(tz_name)) if tz_name else datetime.now()
    except Exception:  # noqa: BLE001 - منطقة غير معروفة ⇒ توقيت الخادم
        now = datetime.now()
    span = (end - start) % 24 or 24
    elapsed = (now.hour + now.minute / 60.0 - start) % 24
    return max(0.05, min(1.0, elapsed / span))


def capability_streak(config, capability: str) -> int:
    """كم دورة متتالية (الأحدث أولاً) نُفِّذ فيها هذا النوع من الإجراءات."""
    history = list((config.runtime_state.flags or {}).get("action_type_history") or [])
    streak = 0
    for types in history:
        if capability in (types or []):
            streak += 1
        else:
            break
    return streak


def streak_limit(rules: Dict) -> int:
    """أقصى عدد دورات متتالية يُسمح فيها بتكرار نوع الإجراء نفسه."""
    try:
        return max(int((rules or {}).get("max_same_capability_streak", 3)), 1)
    except (TypeError, ValueError):
        return 3


def is_mention_reply(action: "ProposedAction") -> bool:
    """ردّ على من خاطبنا — له مسار خاص في كل مكان (ميزانية وسقف دورة).

    السقف العام ``max_replies_per_cycle`` (افتراضياً 1) كان يقصّ ردود المنشنات
    أيضاً، فيقترح ``CommunityEngagerSkill`` ثلاثة ردود على ثلاثة أشخاص خاطبونا
    ثم يُنفَّذ واحد فقط — بينما القاعدة المعلنة أن «من يخاطبنا يستحق رداً».
    لذلك لها سقفها الخاص ``max_reply_notifications_per_cycle``.
    """
    return (action.capability == "reply_to_user"
            and bool((action.params or {}).get("is_mention_reply")))


def mention_reply_limit(rules: Dict) -> int:
    """سقف الردود على المنشنات في الدورة الواحدة (مستقل عن سقف الردود العام)."""
    rules = rules or {}
    try:
        return max(int(rules.get("max_reply_notifications_per_cycle", 3)), 0)
    except (TypeError, ValueError):
        return 3


class WeightedUtilityDecisionEngine(IDecisionEngine):
    """Transparent, explainable weighted-utility policy."""

    # ملاحظة تاريخية: كان هنا ``ENGAGEMENT_ROTATION`` يمنح مكافأة لنوع تفاعل
    # يُختار بحساب ``cycles % 5``. أُزيل: التنويع صار ينشأ من الإيقاع اليومي
    # (المتأخّر عن حصته يتقدّم) وسقف التتابع، لا من دورة تقويمية عشوائية.

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
        # أمر مالك صريح (مهمّة من الشات): لا تقتطعه سقوف الدورة التلقائية.
        # لو طلب المالك ثلاثة ردود وسقف الردود/الدورة = 1 لنُفّذ ردّاً واحداً
        # ويظنّ أن الموظف تجاهل أمره.
        owner_count = sum(1 for p in proposals
                          if (p.params or {}).get("owner_directed"))
        if owner_count:
            max_actions = max(max_actions, owner_count)

        counters = context.config.runtime_state.counters

        def _budget_state(cap: str) -> tuple:
            """(المستهلك، الهدف، نسبة الاستهلاك 0..1) لميزانية النوع اليومية."""
            if cap not in ENGAGEMENT_BUDGETS:
                return 0, 0, 0.0
            counter_key, rule_key, default = ENGAGEMENT_BUDGETS[cap]
            target = max(int(rules.get(rule_key, default)), 0)
            used = int(counters.get(counter_key, 0))
            if target <= 0:
                return used, target, 1.0
            return used, target, min(used / target, 1.0)

        # ── الإيقاع اليومي: هل هذا النوع متأخّر عن حصته أم سابق لها؟ ──
        # يستبدل منطقاً كان يعاقب على «الاستهلاك» مطلقاً بلا مرجع زمني: نوعٌ
        # استهلك 50% من حصته في أول ساعة كان يُعامَل كنوعٍ استهلكها في آخر
        # اليوم. النتيجة سلوك متكتّل: يفرغ ميزانية نوع مبكراً ثم يصمت عنه.
        # الآن المرجع هو تقدّم يوم العمل: المتأخّر عن إيقاعه يُدفع، والسابق
        # لإيقاعه يُكبح — تنويعٌ ينشأ من البنية لا من مكافأة تدوير عشوائية.
        day_progress = _day_progress(context.config)
        pace_weight = float(rules.get("pace_weight", 0.35))

        def _pace_delta(cap: str) -> float:
            if cap not in ENGAGEMENT_BUDGETS:
                return 0.0
            _, target, ratio = _budget_state(cap)
            if target <= 0:
                return 0.0
            return max(-1.0, min(1.0, day_progress - ratio))

        # سجل أنواع الإجراءات عبر آخر عدة دورات (الأحدث أولاً) — عقوبة تكرار
        # متدرّجة: ما نُفّذ في الدورة السابقة يُعاقب بقوة، والأقدم أخف — فلا يتكرر
        # الرد/الرتويت دورةً بعد دورة بل يتناوب السلوك طبيعياً.
        flags = context.config.runtime_state.flags
        history: List[List[str]] = list(flags.get("action_type_history") or [])
        if not history and flags.get("last_action_types"):
            history = [list(flags["last_action_types"])]
        repeat_penalty = float(rules.get("repeat_penalty", 0.25))

        def _history_penalty(cap: str) -> float:
            """عقوبة تكرار النوع — **مخفّفة ما دامت ميزانيته اليومية غير مستهلكة**.

            كانت عقوبتان تُطرحان على السبب نفسه: عقوبة «تكرار النوع» وعقوبة
            «استهلاك الميزانية». فالإعجاب بهدف يومي = 12 كان يُعاقَب على تكراره
            وهو لم يستهلك إلا إعجابين — فينفّذ الموظف تفاعلاً أقل بكثير مما
            ضبطه المستخدم صراحةً. التنويع مطلوب، لكن ليس على حساب بلوغ الهدف:
            العقوبة تكبر تدريجياً مع استهلاك الميزانية.
            """
            pen = 0.0
            for age, types in enumerate(history[:6]):
                if cap in (types or []):
                    pen += repeat_penalty * (0.5 ** age)
            pen = min(pen, repeat_penalty * 2)
            if cap in ENGAGEMENT_BUDGETS:
                pen *= 0.35 + 0.65 * _budget_state(cap)[2]
            return pen

        # ── إشارات التعلّم من سجلّ التفاعل (ما أثمر فعلاً) ──
        from ..employee import interaction_ledger as IL

        cfg = context.config
        base_rate = IL.base_rate(cfg)
        w_affinity = float(rules.get("affinity_weight", 0.25))
        w_yield = float(rules.get("capability_yield_weight", 0.20))
        w_explore = float(rules.get("interaction_exploration", 0.10))
        fatigue_penalty = float(rules.get("handle_fatigue_penalty", 0.12))

        from ..cognition import learning

        scored: List[Decision] = []
        suspended: List[str] = []
        for action in proposals:
            if not self._allowed(action, context):
                continue
            # ما تعلّمه الموظف من دوراته: ذراع رفضه الحارس مراراً يُعلَّق مؤقتاً،
            # والبقية تُرفع أو تُخفض بقيمتها المتعلَّمة (قبول/رفض/فشل/تفاعل حقيقي).
            learned, learned_note = learning.adjustment(context.config, action)
            if learned is None:
                suspended.append(learning.arm_of(action))
                continue
            params = action.params or {}
            cap = action.capability
            score = (
                self.w_impact * action.expected_impact
                + self.w_urgency * action.urgency
                - self.w_risk * action.risk
                + learned
            )
            # محاذاة الأهداف الحقيقية (نوع الهدف × أولويته × ما بقي منه) — وزنها
            # معتبر لا رمزي، وإلا لا تؤثّر أهداف المستخدم في أي قرار.
            w_goal = float(rules.get("goal_weight", 0.25))
            alignment = context.goal_alignment(cap)
            score += w_goal * alignment
            bits = [
                f"impact={action.expected_impact:.2f}",
                f"urgency={action.urgency:.2f}", f"risk={action.risk:.2f}",
            ]
            if alignment:
                bits.append(f"goal_fit={alignment:.2f}")
            if learned_note:
                bits.append(learned_note)

            # ── ما تعلّمه الموظف فعلاً عن تفاعله (لا أرقاماً مكتوبة يدوياً) ──
            # كل الحدود مركزة حول ``base_rate``: الهدف/النوع المجهول = صفر
            # تأثير، فلا يُكافأ ولا يُعاقب بلا دليل.
            if cap in IL.INTERACTION_CAPABILITIES:
                cap_yield, cap_samples = IL.capability_stats(cfg, cap)
                delta = w_yield * (cap_yield - base_rate) / max(base_rate, 0.05)
                score += delta
                if abs(delta) >= 0.01:
                    bits.append(f"cap_yield={cap_yield:.2f}/{cap_samples}")
                # استكشاف: نوع لم يُجرَّب يستحق فرصة، وإلا تجمّد الموظف على
                # أول نوع صادف نجاحاً ولم يعرف أبداً ما كان يمكن أن ينجح.
                explore = w_explore / (1.0 + cap_samples)
                score += explore
                if explore >= 0.02:
                    bits.append(f"explore={explore:.2f}")

                handle = str(params.get("handle") or params.get("profile_url") or "")
                if handle:
                    aff, aff_n = IL.handle_stats(cfg, handle)
                    if aff_n:
                        adelta = w_affinity * (aff - base_rate) / max(base_rate, 0.05)
                        score += adelta
                        bits.append(f"affinity={aff:.2f}/{aff_n}")
                    # إلحاح على حساب واحد = سلوك سبام حتى لو كان كل تفاعل
                    # مبرَّراً وحده. نوزّع الاهتمام على حسابات مختلفة.
                    fatigue = IL.handle_fatigue(cfg, handle)
                    if fatigue >= 2 and not params.get("is_mention_reply"):
                        fpen = fatigue_penalty * min(fatigue - 1, 4)
                        score -= fpen
                        bits.append(f"fatigue={fatigue}(-{fpen:.2f})")

            # عقوبة تكرار متدرّجة عبر الدورات السابقة (عدا الانتظار والنشر —
            # النشر محدود أصلاً بهدف يومي في _allowed، ومعاقبته على «تكرار»
            # النشر تحرم حسابات daily_post_target>1 من بلوغ هدفها: الحساب
            # التوثيقي نشر مرة ثم خسر النشر كل الدورات التالية أمام التفاعل).
            if cap != "wait" and cap not in PUBLISHING_CAPABILITIES:
                pen = _history_penalty(cap)
                if pen:
                    score -= pen
                    bits.append(f"repeat_pen={pen:.2f}")
            # الإيقاع اليومي: المتأخّر عن حصته يُدفع والسابق لها يُكبح
            # (ردود المنشنات معفاة — من يخاطبنا يستحق رداً دائماً).
            if not params.get("is_mention_reply"):
                used, target, _ratio = _budget_state(cap)
                pace = _pace_delta(cap)
                if pace:
                    score += pace_weight * pace
                    bits.append(f"pace={used}/{target}@{day_progress:.0%}"
                                f"({pace:+.2f})")
            bits.append(f"score={score:.2f}")
            scored.append(Decision(
                action=action, score=score, explanation=" ".join(bits),
            ))

        scored.sort(key=lambda d: d.score, reverse=True)

        # اختر الأعلى فوق العتبة مع احترام **سقف كل نوع في الدورة** كما ضبطه
        # المستخدم (إعجاب/دورة، ردود/دورة، ...). التنويع محفوظ عبر مكافأة
        # التدوير وعقوبة التكرار، لا عبر سقف صلب = 1 يُبطل إعدادات المستخدم.
        # ردود المنشنات لها سقفها الخاص: من خاطبنا يستحق رداً، ولا يصح أن
        # يقصّه سقف الردود العام (كان ثلاثة أشخاص يخاطبوننا فيُردّ على واحد).
        mention_cap = mention_reply_limit(rules)
        max_actions = max(max_actions, min(
            mention_cap, sum(1 for p in proposals if is_mention_reply(p))))

        # ضمانة تنويع بنيوية: نوعٌ نُفِّذ في آخر N دورات متتالية يُستبعد هذه
        # الدورة مهما علت درجته. الأوزان وحدها لا تكفي — القياس أظهر ردّاً
        # يتكرّر 5 دورات متتالية لأن درجته تبقى الأعلى دائماً. الاستثناءات:
        # ردّ المنشنات (واجب مهني) وأمر المالك (مرجع أعلى).
        max_streak = streak_limit(rules)
        blocked_by_streak = {
            cap for cap in {d.action.capability for d in scored}
            if cap not in ("wait",)
            and capability_streak(context.config, cap) >= max_streak
        }

        def _eligible(d: "Decision") -> bool:
            if d.action.capability not in blocked_by_streak:
                return True
            return bool(is_mention_reply(d.action)
                        or (d.action.params or {}).get("owner_directed"))

        chosen: List[Decision] = []
        used_counts: Dict[str, int] = {}
        for d in scored:
            if d.score < self.min_score:
                continue
            if not _eligible(d):
                continue
            cap = d.action.capability
            if cap != "wait" and not (d.action.params or {}).get("owner_directed"):
                group = ("mention_reply" if is_mention_reply(d.action)
                         else cycle_group(cap))
                limit = (mention_cap if group == "mention_reply"
                         else cycle_limit(cap, rules))
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
            # فضّل مرشّحاً لا يكسر التنويع، وإن لم يوجد فالفعل خير من الجمود
            best_real = next(
                (d for d in scored
                 if d.action.capability != "wait" and d.score > 0.0 and _eligible(d)),
                None,
            ) or next(
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
            try:
                chosen = self._llm_review(scored, chosen, context, max_actions)
            except Exception:
                logging.getLogger(__name__).warning(
                    "decision review failed; retaining weighted selection", exc_info=True)

        # مرشّحون بدلاء للحارس: عندما يرفض الحارس القرار الوحيد (شوهد مع
        # liketoday5: max_actions=1 ورُفض الرد لخروجه عن التخصص) كانت الدورة
        # تنتهي بلا أي فعل رغم وجود 6 مقترحات صالحة أخرى. الحارس يجرّب
        # البديل التالي بدل إهدار الدورة كاملة.
        context.scratch["suspended_arms"] = sorted(set(suspended))
        chosen_ids = {id(d) for d in chosen}
        # أثر القرار في سجل الدورة: لماذا اختير هذا وتُرك ذاك (الدرجة ومكوّناتها + مراجعة LLM).
        # كان السجل يحفظ الاختيار النهائي فقط، فدورة نفّذت رداً واحداً من 11 مقترحاً بلا تفسير.
        context.scratch["decision_trace"] = [
            {"capability": d.action.capability, "arm": learning.arm_of(d.action),
             "score": round(d.score, 3), "chosen": id(d) in chosen_ids,
             "why": d.explanation[:320]}
            for d in scored[:12]
        ]
        context.scratch["decision_alternates"] = [
            d for d in scored
            if id(d) not in chosen_ids and d.action.capability != "wait"
            and d.score > 0.0 and _eligible(d)
        ]
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

        from ..employee import interaction_ledger as IL

        cfg = context.config
        graph_facts = context.scratch.get("graph_handle_facts") or {}
        top = scored[:10]
        lines = []
        for i, d in enumerate(top):
            p = d.action.params or {}
            target = p.get("handle") or p.get("profile_url") or p.get("tweet_url") or ""
            content = str(p.get("original_text") or p.get("brief") or p.get("text") or "")[:140]
            line = (
                f"[{i}] {d.action.capability} | score={d.score:.2f} | الهدف: {target} | "
                f"المحتوى: {content} | المبرر: {str(d.action.rationale)[:100]}"
            )
            # ما نعرفه فعلاً عن هذا الهدف: تاريخ تفاعلنا معه وما يقوله الرسم
            # المعرفي عنه. بدونها كانت المراجعة «تختار» بلا أي معرفة بالعلاقة.
            handle = str(p.get("handle") or p.get("profile_url") or "")
            if handle:
                aff, n = IL.handle_stats(cfg, handle)
                if n:
                    line += (f" | تاريخنا معه: تفاعلنا {n} مرة، "
                             f"احتمال استجابته {aff:.0%}")
                known = graph_facts.get(IL.normalize_handle(handle)) or []
                if known:
                    line += " | من ذاكرتك: " + "؛ ".join(str(f)[:90] for f in known[:2])
            lines.append(line)

        # حالة الميزانيات اليومية — المراجعة كانت عمياء عنها فتختار نوعاً
        # كاد يستنفد حصته وتترك نوعاً لم يُستهلك منه شيء.
        counters = cfg.runtime_state.counters
        budget_lines = []
        for cap, (counter_key, rule_key, default) in ENGAGEMENT_BUDGETS.items():
            target = int((cfg.business_rules or {}).get(rule_key, default))
            if target > 0:
                budget_lines.append(
                    f"{cap}: {int(counters.get(counter_key, 0))}/{target}")
        emo = context.scratch.get("emotion_state") or {}
        learned = IL.summary_line(cfg)
        data = llm.complete_json(
            system=(
                "أنت وحدة القرار النهائي لموظف ذكي يدير حساب X باحترافية. "
                "أمامك مرشّحون حقيقيون مُقيّمون بالمنفعة. اختر التوليفة الأنسب "
                "لهذه الدورة: قيمة حقيقية للجمهور، تنوّع في أنواع التفاعل، لا تكرار "
                "لمواضيع/حسابات تفاعلنا معها مؤخراً. رجّح الأهداف التي أثبتت "
                "تجربتنا معها استجابة، ولا تُلحّ على حساب واحد. "
                "يجوز اختيار أكثر من إجراء من "
                "النوع نفسه بحدود سقفه المسموح، لكن فضّل التنويع عند تساوي القيمة. "
                "أجب JSON فقط: "
                '{"selected": [أرقام المرشّحين], "reason": "مبرر موجز"}'
            ),
            prompt=(
                f"{context.persona_prompt()}\n\n"
                f"الحالة العاطفية: {emo.get('mood', 'طبيعية')}\n"
                + (f"{learned}\n" if learned else "")
                + (f"ميزانياتك اليومية المستهلكة: {'، '.join(budget_lines)}\n"
                   if budget_lines else "")
                + f"\nنشاطك الأخير (لا تكرره):\n{context.recent_activity_prompt()[:800]}\n\n"
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
        context.scratch["decision_review"] = {"selected": idxs, "reason": reason}

        rules = context.config.business_rules or {}
        mention_cap = mention_reply_limit(rules)
        max_streak = streak_limit(rules)
        picked: List[Decision] = []
        used_counts: Dict[str, int] = {}
        for i in dict.fromkeys(idxs):
            if not (0 <= i < len(top)):
                continue
            d = top[i]
            cap = d.action.capability
            if cap == "wait":
                continue
            if not (d.action.params or {}).get("owner_directed"):
                mention = is_mention_reply(d.action)
                # قيود البنية تسري على اختيار النموذج أيضاً: المراجعة تُرتِّب
                # المرشّحين ولا تُلغي التنويع. النموذج يميل بثبات إلى الأفعال
                # «الأكثر إثارة» (الاقتباس/الثريد) فيُنتج نفس السلوك كل دورة.
                if not mention and capability_streak(context.config, cap) >= max_streak:
                    continue
                group = "mention_reply" if mention else cycle_group(cap)
                limit = mention_cap if mention else cycle_limit(cap, rules)
                if used_counts.get(group, 0) >= limit:
                    continue
                used_counts[group] = used_counts.get(group, 0) + 1
            d.explanation += f" | llm_review: {reason}" if reason else " | llm_review"
            picked.append(d)
            if len(picked) >= max_actions:
                break
        # إن أعاد الـLLM اختياراً فارغاً/غير صالح نبقي اختيار المنفعة الموزونة
        if not picked:
            return chosen
        # أوامر المالك ليست محلّ تصويت: أعِد أي إجراء موجّه أسقطته المراجعة.
        picked_ids = {id(d) for d in picked}
        for d in chosen:
            if (d.action.params or {}).get("owner_directed") and id(d) not in picked_ids:
                d.explanation += " | owner_directed: محفوظ رغم مراجعة الـLLM"
                picked.insert(0, d)
        return picked

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

        # أمر صريح من المالك (موضوع موجّه / تفاعل موجّه / مهمّة حرّة من الشات):
        # الميزانيات اليومية وأهداف النشر وُضعت لضبط السلوك التلقائي، لا لتعطيل
        # أمر طلبه المستخدم الآن. كان «انشر عن 2» يُرفض بصمت إن بلغ الموظف هدفه
        # اليومي — فيرى المستخدم دورة بلا أي فعل وبلا تفسير.
        if (action.params or {}).get("owner_directed"):
            return True

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
