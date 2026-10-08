"""
مهارة موظف مدمجة في النظام: trend_participation — المصدر الوحيد لكودها (system/skills/trend_participation/).

المساعدات المشتركة بين المهارات (فلاتر السبام والمحظورات، حجز الأهداف، الميزانيات…) في
``app.aeos.skills.builtin`` وتُستدعى عبر ``_h.`` — فأي تعديل عليها (أو استبدال في اختبار)
يصل لكل المهارات.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.aeos.skills import builtin as _h

if TYPE_CHECKING:  # pragma: no cover
    from app.aeos.cognition.context import ExecutionContext


class TrendParticipationSkill(_h.ISkill):
    """المشاركة في الترند الذي يخص التخصص بالطريقة الصحيحة: منشور بالهاشتاق، أو
    اقتباس للتغريدة المحورية، أو رد على التغريدة التي تستضيف النقاش.

    القرار نفسه يُتخذ في ``trend_intel`` (فرز بمعرفة العالم ← قراءة التغريدات ←
    فهم). هنا نحوّله إلى فعل **ضمن الميزانية**: إن نفدت ميزانية الفعل المقترح ننتقل
    للبديل المتاح بدل أن يرفضه محرّك القرار بصمت. ترند واحد لكل دورة، وتبريد لكل
    ترند (لا نكرّر المشاركة في الهاشتاق نفسه)، وسقف يومي.
    """

    name = "trend_participation"
    claim_priority = 75  # قبل الاقتباس/الإعجاب: يحجز التغريدة المحورية أولاً
    uses_capabilities = ["publish_content", "quote_post", "reply_to_user"]
    # المهارة لا تقترح شيئاً بلا ترند مطابق ومفهوم، والموظفون القدامى لا يحملونها
    # في installed_skills — بلا always_on لن يشارك أي منهم في ترند أبداً.
    always_on = True

    _CAPS = {"post": "publish_content", "quote": "quote_post", "reply": "reply_to_user"}
    _FALLBACK = {"post": ("post", "quote", "reply"), "quote": ("quote", "reply", "post"),
                 "reply": ("reply", "quote", "post")}

    def propose(self, context: "ExecutionContext") -> List[_h.ProposedAction]:
        from app.aeos.employee import trend_intel

        rules = context.config.business_rules or {}
        if not bool(rules.get("trend_participation", not _h._on_this_day_mode(context))):
            return []
        counters = context.config.runtime_state.counters
        if int(counters.get("trends_today", 0)) >= int(rules.get("daily_trend_participations", 3)):
            return []
        best = (context.scratch.get("trend_intel") or {}).get("best")
        if not best or best.get("action") not in self._CAPS or best.get("risk") == "high":
            return []
        if trend_intel.norm_name(best["name"]) in trend_intel.recently_participated(context):
            return []
        if _h._violates_persona_donts(f"{best['name']} {best.get('summary', '')}", context):
            return []

        target = best.get("target_tweet") or next(
            (t for t in best.get("tweets") or [] if t.get("_in_scope") and t.get("url")), None)
        for act in self._FALLBACK[best["action"]]:
            cap = self._CAPS[act]
            if cap != "publish_content" and int(rules.get(_h.CYCLE_LIMIT_KEYS.get(cap, ""), 1) or 0) <= 0:
                continue
            if not _h._budget_left(context, cap):
                continue
            if act == "post":
                action = self._post(context, best)
            elif target and _h.claim_targets(context, [target], 1, owner=self.name):
                action = self._engage(context, best, target, act)
            else:
                continue
            # الترند صار مملوكاً لهذه المهارة: مرشّحو المحتوى الآخرون لا يبنون عليه
            # منشوراً ثانياً في الدورة نفسها (مشاركة مزدوجة في الهاشتاق نفسه)
            context.scratch["trend_owned"] = trend_intel.norm_name(best["name"])
            return [action]
        return []

    @staticmethod
    def _context_block(best: dict) -> str:
        lines = [f"الترند: {best['name']}"]
        if best.get("about"):
            lines.append(f"ما هو: {best['about']}")
        if best.get("summary"):
            lines.append(f"ماذا يحدث (من قراءة تغريداته): {best['summary']}")
        if best.get("mood"):
            lines.append(f"مزاج النقاش: {best['mood']}")
        if best.get("facts"):
            lines.append("حقائق متكررة/موثوقة: " + " | ".join(best["facts"]))
        if best.get("unverified"):
            lines.append("ادعاءات غير مؤكدة — لا تتبنّها ولا تكرر أرقامها: " + " | ".join(best["unverified"]))
        if best.get("angle"):
            lines.append(f"زاويتك: {best['angle']}")
        if best.get("sensitive") or best.get("risk") == "medium":
            lines.append("موضوع حساس: نبرة رصينة، بلا تحريض ولا خطاب كراهية ولا اتهامات غير مؤكدة.")
        return "\n".join(lines)

    def _post(self, context: "ExecutionContext", best: dict) -> _h.ProposedAction:
        from app.aeos.employee import trend_intel

        tag = best["name"] if trend_intel.is_hashtag(best["name"]) else ""
        lang = getattr(context.config.persona, "language", "ar") or "ar"
        brief = (
            f"شارك في ترند رائج الآن يخص تخصصك بمنشور أصلي قوي باللغة {lang}.\n"
            f"{self._context_block(best)}\n\n"
            "قدّم قيمة لا يقدّمها غيرك داخل الترند: تحليلاً أو سياقاً أو خلفية أو موقفاً واضحاً "
            "من زاويتك — لا تعيد سرد الخبر ولا تكرر ما في التغريدات."
            + (f" ضمّن الهاشتاق {tag} حرفياً في نهاية المنشور، ولا تضف أي هاشتاق آخر."
               if tag else f" اذكر «{trend_intel.readable(best['name'])}» نصاً بشكل طبيعي.")
        ) + _h._strategy_brief_suffix(context)
        media = next((_h._best_tweet_media((t.get("media") or "").strip())
                      for t in best.get("tweets") or [] if (t.get("media") or "").strip()), "")
        plan = _h._content_plan(context)
        return _h.ProposedAction(
            capability="publish_content",
            params={"brief": brief, "hashtag": tag, "trend": best["name"],
                    "research_query": trend_intel.readable(best["name"]),
                    "media_url": media, "pillar": plan.get("pillar", ""),
                    "format": plan.get("format", "")},
            rationale=f"join trend {best['name']} with an original post ({best.get('why', '')[:80]})",
            expected_impact=0.9, urgency=0.85,
            risk=0.3 if best.get("risk") == "medium" else 0.15,
        )

    def _engage(self, context: "ExecutionContext", best: dict, target: dict, act: str) -> _h.ProposedAction:
        from app.aeos.employee import trend_intel

        cap = self._CAPS[act]
        return _h.ProposedAction(
            capability=cap,
            params={"tweet_url": target.get("url", ""), "original_text": target.get("text", ""),
                    "handle": target.get("handle", ""),
                    "hashtag": best["name"] if trend_intel.is_hashtag(best["name"]) else "",
                    "trend": best["name"], "trend_context": self._context_block(best)},
            rationale=f"join trend {best['name']} via {act} on its pivotal tweet",
            expected_impact=0.85, urgency=0.85,
            risk=0.3 if best.get("risk") == "medium" else 0.15,
        )
