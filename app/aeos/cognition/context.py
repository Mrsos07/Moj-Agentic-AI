"""
ExecutionContext — the "mind snapshot" assembled for a single cognitive cycle.

It bundles everything a cycle/phase/capability/skill needs, all scoped to ONE
employee: its config, the triggering event, memory, workspace, the MCP registry
and capability registry, plus a shared scratch area used to pass data between
phases. It exists only for the lifetime of one runtime, then is discarded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ..capabilities.registry import CapabilityRegistry
    from ..core.events import Event
    from ..core.interfaces import ILLMProvider
    from ..employee.models import EmployeeConfig
    from ..mcp.registry import MCPRegistry
    from ..memory.manager import MemoryManager
    from ..workspace.manager import Workspace


@dataclass
class ExecutionContext:
    config: "EmployeeConfig"
    event: "Event"
    memory: "MemoryManager"
    mcp: "MCPRegistry"
    capabilities: "CapabilityRegistry"
    workspace: "Workspace"
    llm: Optional["ILLMProvider"] = None
    cycle_id: str = ""

    # shared per-cycle scratch, filled by phases (observations, analysis, plan...)
    scratch: Dict[str, Any] = field(default_factory=dict)

    # optional live-progress sink: injected by the runtime so phases can stream
    # human-readable updates (thinking / observations) into the user's chat while
    # the cycle is still running. Signature: (text: str, intent: str) -> None.
    progress_cb: Optional[Callable[[str, str], None]] = None

    def emit_progress(self, text: str, intent: str = "aeos_cycle_progress") -> None:
        """Stream a live update to the user's chat (best-effort; never raises)."""
        cb = self.progress_cb
        if not (cb and text and str(text).strip()):
            return
        try:
            cb(str(text).strip(), intent)
        except Exception:  # noqa: BLE001 - progress must never break a cycle
            pass

    # ---- convenience helpers used by phases/skills/capabilities ---------
    def top_goal_description(self) -> str:
        goals = sorted(self.config.goals, key=lambda g: g.priority)
        return goals[0].description if goals else ""

    #: كم يخدم كل إجراء كل نوع هدف — هذه هي "نظرية النمو" المكتوبة صراحةً:
    #: نمو المتابعين يأتي من الظهور (نشر/ثريد/اقتباس/متابعة)، والتفاعل يأتي من
    #: المحادثة (ردود/اقتباس)، وبناء السلطة يأتي من المحتوى العميق (ثريد/شرح).
    _GOAL_KIND_WEIGHTS = {
        "followers": {"publish_thread": 1.0, "publish_content": 0.8, "quote_post": 0.7,
                      "follow_user": 0.6, "reply_to_user": 0.5, "retweet_post": 0.3,
                      "like_post": 0.2},
        "engagement": {"reply_to_user": 1.0, "quote_post": 0.8, "publish_content": 0.7,
                       "publish_thread": 0.7, "like_post": 0.4, "retweet_post": 0.4},
        "authority": {"publish_thread": 1.0, "publish_content": 0.8, "research_topic": 0.7,
                      "quote_post": 0.6, "reply_to_user": 0.4, "store_knowledge": 0.4},
        "reach": {"publish_thread": 0.9, "publish_content": 0.9, "quote_post": 0.7,
                  "retweet_post": 0.5, "reply_to_user": 0.4, "like_post": 0.2},
    }

    #: كلمات دالة (عربي/إنجليزي) تُصنّف الهدف إلى نوع
    _GOAL_KEYWORDS = {
        "followers": ("follower", "متابع", "جمهور", "audience", "grow", "نمو"),
        "engagement": ("engage", "تفاعل", "رد", "reply", "نقاش", "community", "مجتمع"),
        "authority": ("authority", "مرجع", "خبير", "expert", "ثقة", "trust",
                      "brand", "علامة", "سلطة"),
        "reach": ("reach", "وصول", "impression", "انطباع", "مشاهد", "views", "viral", "انتشار"),
    }

    def _goal_kinds(self) -> Dict[str, float]:
        """أنواع الأهداف الفعلية لهذا الموظف بأوزان حسب الأولوية والتقدّم.

        الهدف الأعلى أولوية (priority=1) والأقل تقدّماً يأخذ وزناً أكبر — فيصير
        القرار مدفوعاً بأهداف المستخدم الحقيقية لا بجدول ثابت.
        """
        kinds: Dict[str, float] = {}
        for goal in self.config.goals:
            if goal.status.value in ("achieved", "abandoned"):
                continue
            hay = f"{goal.description} {goal.metric}".lower()
            matched = [k for k, words in self._GOAL_KEYWORDS.items()
                       if any(w in hay for w in words)]
            if not matched:
                matched = ["reach"]          # هدف غير مصنّف: يخدمه الظهور
            # priority 1..5 → وزن 1.0..0.2، ومضاعف صغير لما لم يكتمل بعد
            weight = max(0.2, (6 - int(goal.priority or 3)) / 5.0)
            weight *= 1.0 + (1.0 - float(goal.progress or 0.0)) * 0.5
            for kind in matched:
                kinds[kind] = kinds.get(kind, 0.0) + weight / len(matched)
        return kinds

    def goal_alignment(self, capability: str) -> float:
        """0..1 — كم يخدم هذا الإجراء أهداف الموظف الفعلية (لا جدولاً ثابتاً)."""
        kinds = self._goal_kinds()
        if not kinds:
            return 0.0
        total = sum(kinds.values()) or 1.0
        score = 0.0
        for kind, weight in kinds.items():
            score += (weight / total) * self._GOAL_KIND_WEIGHTS[kind].get(capability, 0.1)
        return round(min(score, 1.0), 3)

    def persona_prompt(self) -> str:
        p = self.config.persona
        parts = [
            f"You are {p.display_name or self.config.account_username}, "
            f"a {p.role} on {self.config.platform}.",
            f"Tone: {p.tone}. Primary language: {p.language}.",
        ]
        # التعليمات الثابتة من المستخدم أولاً وبأعلى درجة إلزام — هي المرجع الأول
        # في كل قرار (منشور/رد/إعجاب/إعادة نشر/متابعة). طبّقها حرفياً دائماً.
        if p.instructions:
            parts.append(
                "🔒 التعليمات الثابتة من المستخدم (إلزامية وفوق كل اعتبار، طبّقها "
                f"حرفياً في كل منشور ورد وتفاعل): {p.instructions}"
            )
        if getattr(p, "dialect", ""):
            parts.append(f"Arabic dialect/style requirement: {p.dialect}. Always keep this dialect in phrasing.")
        if p.bio:
            parts.append(f"Bio: {p.bio}")
        if getattr(p, "interests", None):
            parts.append(
                "مجال التخصص/الاهتمام: " + "، ".join(p.interests)
                + ". ممنوع منعاً باتاً الخروج عن هذا المجال في أي منشور أو تفاعل "
                "(رد/إعجاب/إعادة نشر/متابعة)؛ تجاهل تماماً أي موضوع خارج تخصصك."
            )
        if getattr(p, "values", None):
            parts.append("القيم التي تلتزم بها: " + "؛ ".join(p.values))
        if p.dos:
            parts.append("افعل دائماً: " + "؛ ".join(p.dos))
        if p.donts:
            parts.append("لا تفعل أبداً: " + "؛ ".join(p.donts))
        # الحالة العاطفية/السلوكية المستنتَجة من الذاكرة المعرفية (Zep/Graphiti)
        # — تضبط نبرة وسلوك كل منشور/رد/تفاعل في هذه الدورة.
        emo = self.scratch.get("emotion_state") or {}
        if emo:
            emo_lines = []
            if emo.get("mood"):
                emo_lines.append(f"مزاجك الحالي: {emo['mood']}.")
            if emo.get("tone_adjustment"):
                emo_lines.append(f"عدّل نبرتك: {emo['tone_adjustment']}.")
            if emo.get("behavior_rules"):
                emo_lines.append("قواعد سلوك لهذه الدورة: " + "؛ ".join(emo["behavior_rules"]))
            if emo.get("avoid"):
                emo_lines.append("تجنّب (بناءً على تجاربك السابقة): " + "؛ ".join(emo["avoid"]))
            if emo.get("evidence"):
                emo_lines.append(f"سببها من تجربتك: {emo['evidence']}")
            elif emo.get("facts_used"):
                emo_lines.append(
                    "مستندة إلى: " + "؛ ".join(str(f)[:120] for f in emo["facts_used"][:2])
                )
            if emo_lines:
                parts.append(
                    "🧭 حالتك العاطفية والسلوكية (مستنتَجة من **ذاكرتك المعرفية** — أي "
                    "من تفاعل جمهورك الفعلي وتجاربك السابقة، لا من هذه التعليمات — "
                    "التزم بها في صياغة كل منشور ورد وتفاعل): " + " ".join(emo_lines)
                )
        # توجيهات حارس التقييم من الدورة السابقة — التحسين الذاتي المستمر.
        # تأتي أخيراً (بعد الهوية والحالة) لأنها طبقة تهذيب لا تُخالف الهوية.
        try:
            from .critique import directives_block
            block = directives_block(self.config)
        except Exception:  # noqa: BLE001 - التحسين لا يُعطّل بناء البرومبت
            block = ""
        if block:
            parts.append(block)
        return "\n".join(parts)

    def recent_activity_prompt(self) -> str:
        """Summary of what the employee recently posted/replied — so it reviews
        its own memory before deciding and never repeats a topic or phrasing."""
        return self.scratch.get("recent_activity_summary", "")

    def planning_prompt(self) -> str:
        goals = "\n".join(f"- ({g.priority}) {g.description}" for g in self.config.goals) or "- (none)"
        recent = self.scratch.get("recent_memory_summary", "")
        activity = self.recent_activity_prompt()
        obs = self.scratch.get("observations", {})
        activity_block = f"\nنشاطك الأخير (راجعه قبل أن تقرر ولا تكرّره):\n{activity}\n" if activity else ""
        # خطة المحتوى وأداؤه الفعلي — يجعل التفكير والتخطيط مبنيين على دليل
        strategy_block = self.scratch.get("strategy_block") or ""
        strategy_part = f"\nاستراتيجية المحتوى لهذه الدورة:\n{strategy_block}\n" if strategy_block else ""
        report = self.scratch.get("performance_report") or {}
        perf_part = ""
        if report.get("samples"):
            formats = ", ".join(
                f"{k} (متوسط {v['avg_engagement']})"
                for k, v in list((report.get("formats") or {}).items())[:3]
            )
            pillars = ", ".join(
                f"{k} (متوسط {v['avg_engagement']})"
                for k, v in list((report.get("pillars") or {}).items())[:3]
            )
            perf_part = (
                f"\nأداء محتواك الفعلي ({report['samples']} منشوراً مقيساً):\n"
                f"- أنجح الصيغ: {formats or 'لا بيانات'}\n"
                f"- أنجح المحاور: {pillars or 'لا بيانات'}\n"
            )
        return (
            f"{self.persona_prompt()}\n\n"
            f"GOALS:\n{goals}\n\n"
            f"TRIGGER EVENT: {self.event.type.value} | payload={self.event.payload}\n"
            f"OBSERVATIONS: {obs}\n"
            f"RECENT MEMORY:\n{recent}\n"
            f"{activity_block}"
            f"{strategy_part}"
            f"{perf_part}\n"
            f"Given the above, propose the most valuable actions for this cycle."
        )
