"""
sim_decisions.py — محاكاة قياس: ماذا يختار الموظف فعلاً عبر عشرات الدورات؟

تشغّل **المخطّط ومحرّك القرار الحقيقيين** (بلا شبكة وبلا LLM) على مصادر متغيّرة
عبر عدة أيام، وتحاكي التنفيذ (عدّادات، تاريخ الأنواع، أهداف مستهلكة، سجل أداء)
ثم تطبع توزيع ما اختاره — نوع الإجراء وصيغة المحتوى والحسابات المستهدفة.

الغرض: قياس التنويع بدلاً من تخمينه.

    .\\venv\\Scripts\\python.exe sim_decisions.py [عدد_الدورات]
"""
from __future__ import annotations

import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.aeos.decision.engine import (                            # noqa: E402
    ENGAGEMENT_BUDGETS, PUBLISHING_CAPABILITIES, WeightedUtilityDecisionEngine,
)
from app.aeos.employee import strategy                            # noqa: E402
from app.aeos.planner.planner import SkillBasedPlanner            # noqa: E402
from app.aeos.skills.builtin import default_skills               # noqa: E402
from app.aeos.skills.registry import SkillRegistry                # noqa: E402
from verify_creator import make_config, make_context              # noqa: E402

TOPICS = [
    ("نماذج اللغة الكبيرة تغيّر طريقة تصحيح الاختبارات في المدارس فعلياً هذا العام", "@edu_ai"),
    ("أدوات التعليم المدعومة بالذكاء الاصطناعي: مقارنة عملية بين خمس منصات جرّبتها", "@teach_tools"),
    ("الذكاء الاصطناعي في تحضير الدروس يوفّر ساعتين أسبوعياً لكل معلم تقريباً", "@ai_daily"),
    ("منصة تعليمية جديدة تبني خطط دروس تلقائياً من المنهج الدراسي المعتمد", "@edtech_now"),
    ("بحث جديد: أثر الذكاء الاصطناعي على تحصيل الطلاب في الرياضيات", "@research_ed"),
    ("أدوات التعليم المفتوحة المصدر صارت تنافس المنصات المدفوعة في الذكاء الاصطناعي", "@open_edu"),
]


def _signals(cycle: int):
    """مصادر هذه الدورة — مواضيع وحسابات متغيّرة كما في الواقع."""
    def pick(offset, count):
        out = []
        for i in range(count):
            text, handle = TOPICS[(cycle + offset + i) % len(TOPICS)]
            out.append({
                "url": f"https://x.com/{handle.lstrip('@')}/status/{cycle}{offset}{i}",
                "handle": handle, "text": text,
                "media": "https://pbs.twimg.com/media/x?name=small" if i == 0 else "",
            })
        return out

    return {
        "top_posts": pick(0, 3),
        "timeline": pick(2, 3),
        "trend_tweets": pick(4, 2),
        "mentions": ([{
            "handle": "@sara_edu", "text": "ممكن مقارنة بين الأدوات؟",
            "url": f"https://x.com/sara_edu/status/{cycle}",
        }] if cycle % 4 == 0 else []),
    }


def _seed_thread_win(cfg) -> None:
    """حالة واقعية: ثريد واحد حقّق مشاهدات عالية مبكراً، والبقية عادية.

    هذه بالضبط الحالة التي يشتكي منها المستخدم: بعد نجاح مبكّر واحد يتجمّد
    الحساب على تلك الصيغة. نزرعها لنقيس هل يتجمّد النظام فعلاً.
    """
    for i in range(2):
        strategy.record_published(cfg, url=f"https://x.com/me/t{i}", text="ثريد",
                                  pillar="الذكاء الاصطناعي", format_id="thread", parts=4)
        strategy.update_metrics(cfg, [{"url": f"https://x.com/me/t{i}", "likes": 20,
                                       "reposts": 5, "replies": 3, "views": 20000}])
    for i in range(2):
        strategy.record_published(cfg, url=f"https://x.com/me/i{i}", text="زبدة",
                                  pillar="أدوات التعليم", format_id="insight")
        strategy.update_metrics(cfg, [{"url": f"https://x.com/me/i{i}", "likes": 1,
                                       "reposts": 0, "replies": 0, "views": 150}])


def measure(cycles: int = 40, seed: bool = False) -> dict:
    """شغّل المحاكاة وأعد الأرقام فقط (يستعملها سكربت التحقق كضمانة دائمة)."""
    return run(cycles, seed, quiet=True)


def run(cycles: int = 40, seed: bool = False, quiet: bool = False) -> dict:
    cfg = make_config(
        llm_decision_review=False,        # نقيس النظام لا النموذج
        max_actions_per_cycle=4, daily_post_target=2, max_posts_per_day=3,
        max_likes_per_cycle=3, max_replies_per_cycle=1, max_retweets_per_cycle=1,
        max_quotes_per_cycle=1, max_follows_per_cycle=1, max_bookmarks_per_cycle=1,
    )
    cfg.installed_skills = [
        "daily_content_creator", "community_engager", "timeline_engager",
        "viral_content_creator", "content_quoter", "swipe_file",
    ]
    if seed:
        _seed_thread_win(cfg)
    registry = SkillRegistry()
    for sk in default_skills():
        registry.register(sk)
    planner = SkillBasedPlanner(registry, None)
    engine = WeightedUtilityDecisionEngine()

    caps = Counter()
    formats = Counter()
    pillars = Counter()
    handles = Counter()
    per_cycle = []

    for cycle in range(cycles):
        # يوم جديد كل 8 دورات: تصفير العدّادات اليومية
        if cycle and cycle % 8 == 0:
            cfg.runtime_state.counters.clear()

        ctx = make_context(cfg, with_signals=False)
        ctx.scratch.update(_signals(cycle))

        plan = strategy.choose_plan(cfg)
        ctx.scratch["content_plan"] = plan
        ctx.scratch["strategy_block"] = strategy.strategy_prompt_block(plan)

        proposals = planner.plan(ctx)
        decisions = engine.decide(proposals, ctx)

        chosen = []
        for d in decisions:
            cap = d.action.capability
            if cap == "wait":
                continue
            caps[cap] += 1
            chosen.append(cap)
            params = d.action.params or {}
            if params.get("handle"):
                handles[params["handle"]] += 1
            # محاكاة تنفيذ ناجح
            counters = cfg.runtime_state.counters
            if cap in PUBLISHING_CAPABILITIES:
                counters["posts_today"] = counters.get("posts_today", 0) + 1
                if cap == "publish_thread":
                    counters["threads_today"] = counters.get("threads_today", 0) + 1
                formats[plan["format"]] += 1
                pillars[plan["pillar"]] += 1
                strategy.record_published(
                    cfg, url=f"https://x.com/me/status/{cycle}",
                    text=f"post {cycle}", pillar=plan["pillar"],
                    format_id=plan["format"], parts=plan.get("parts", 1))
            elif cap in ENGAGEMENT_BUDGETS:
                key = ENGAGEMENT_BUDGETS[cap][0]
                counters[key] = counters.get(key, 0) + 1
            url = params.get("tweet_url") or params.get("profile_url")
            if url:
                cfg.runtime_state.flags.setdefault("engaged_urls", []).append(url)

        done = sorted(set(chosen))
        if done:
            hist = list(cfg.runtime_state.flags.get("action_type_history") or [])
            hist.insert(0, done)
            cfg.runtime_state.flags["action_type_history"] = hist[:6]
        cfg.runtime_state.cycles_completed += 1
        per_cycle.append(chosen)

    def _max_streak(cap: str) -> int:
        best = cur = 0
        for chosen in per_cycle:
            cur = cur + 1 if cap in chosen else 0
            best = max(best, cur)
        return best

    stats = {
        "capabilities": dict(caps),
        "formats": dict(formats),
        "pillars": dict(pillars),
        "handles": dict(handles),
        "streaks": {cap: _max_streak(cap) for cap in caps},
        "idle_cycles": sum(1 for c in per_cycle if not c),
        "cycles": cycles,
        "days": max(1, cycles // 8),
    }
    if quiet:
        return stats

    total = sum(caps.values()) or 1
    print("=" * 70)
    print(f"محاكاة {cycles} دورة — توزيع الإجراءات المنفَّذة ({total} إجراء)")
    print("=" * 70)
    for cap, n in caps.most_common():
        bar = "█" * max(1, round(40 * n / total))
        print(f"  {cap:18} {n:4}  {100 * n / total:5.1f}%  {bar}")

    tf = sum(formats.values()) or 1
    print(f"\nصيغ المحتوى المنشورة ({tf} منشور):")
    for f, n in formats.most_common():
        print(f"  {f:12} {n:4}  {100 * n / tf:5.1f}%")
    print("\nالمحاور:")
    for p, n in pillars.most_common():
        print(f"  {p:24} {n:4}")

    print("\nأكثر الحسابات استهدافاً:")
    for h, n in handles.most_common(6):
        print(f"  {h:16} {n:4}")

    # أطول سلسلة دورات متتالية نُفِّذ فيها نفس النوع
    print("\nأطول تكرار متتالٍ لكل نوع عبر الدورات:")
    for cap, streak in stats["streaks"].items():
        print(f"  {cap:18} {streak}")

    print(f"\nدورات بلا أي إجراء: {stats['idle_cycles']}/{cycles}")
    return stats


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run(int(args[0]) if args else 40, seed="--seed" in sys.argv)
