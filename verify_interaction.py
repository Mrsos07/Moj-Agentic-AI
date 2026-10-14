"""
verify_interaction.py — تحقّق من **آلية اتخاذ قرار التفاعل** وذاكرته.

يفحص، بلا شبكة وبلا نموذج لغوي وبلا أي تفاعل حقيقي على X:

1. سجلّ التفاعل المتعلّم: التسجيل، نسب الإشارة المرتدّة، التآلف، الإلحاح.
2. محرّك القرار: التدوير المقيّد بالميزانية، إلغاء العقوبة المزدوجة، ترجيح
   الأهداف التي أثبتت استجابة، وسقف ردود المنشنات المستقل.
3. المخطّط: منع تكدّس عدة إجراءات صاخبة على الهدف/الحساب نفسه.
4. مخزن الرسم المعرفي: كاش البحث، إبطاله عند التغذية، ونسب الحقائق للكيانات.

    .\\venv\\Scripts\\python.exe verify_interaction.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.aeos.decision import engine as E                          # noqa: E402
from app.aeos.employee import interaction_ledger as IL             # noqa: E402
from app.aeos.planner.planner import SkillBasedPlanner             # noqa: E402
from app.aeos.skills.base import ProposedAction                    # noqa: E402
from verify_creator import make_config, make_context               # noqa: E402

_results = []


def check(name: str, passed: bool, detail: str = "") -> bool:
    _results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return passed


def _mention(handle: str) -> dict:
    return {"handle": handle, "text": "شكراً على الطرح",
            "url": f"https://x.com/{handle.lstrip('@')}/status/99"}


# ─────────────────── 1) سجلّ التفاعل المتعلّم ───────────────────
def check_ledger_records_and_credits() -> None:
    cfg = make_config()
    IL.record(cfg, capability="reply_to_user", url="https://x.com/a/status/1",
              handle="@ali", text="رد على علي")
    IL.record(cfg, capability="like_post", url="https://x.com/b/status/2",
              handle="@bader", text="إعجاب")
    rep = IL.report(cfg)
    check("السجل يحفظ كل تفاعل منفَّذ", rep["samples"] == 2, f"samples={rep['samples']}")

    # المتابعة بلا handle: يجب أن يُستخرج المعرّف من رابط الملف الشخصي
    IL.record(cfg, capability="follow_user", url="https://x.com/carol", handle="")
    check("استخراج المعرّف من رابط الملف الشخصي",
          IL.handle_stats(cfg, "@carol")[1] == 1,
          f"engaged={IL.handle_stats(cfg, '@carol')[1]}")

    before = IL.handle_affinity(cfg, "@ali")
    credited = IL.credit_signals(cfg, [_mention("@ali")])
    after = IL.handle_affinity(cfg, "@ali")
    check("المنشن الوارد يُنسب للتفاعل الذي سبقه",
          len(credited) == 1 and credited[0]["cap"] == "reply_to_user",
          str(credited))
    check("التآلف يرتفع بعد الاستجابة", after > before, f"{before:.3f} → {after:.3f}")

    # لا يُحتسب المنشن نفسه مرتين
    again = IL.credit_signals(cfg, [_mention("@ali")])
    check("لا تُحتسب الاستجابة مرتين على القيد نفسه", not again, str(again))

    # حساب مجهول = محايد تماماً (لا مكافأة ولا عقوبة بلا دليل)
    aff, n = IL.handle_stats(cfg, "@never_seen")
    check("الحساب المجهول محايد (= المعدّل العام)",
          n == 0 and abs(aff - IL.base_rate(cfg)) < 1e-9, f"aff={aff:.3f} n={n}")


def check_ledger_fatigue() -> None:
    cfg = make_config()
    for i in range(4):
        IL.record(cfg, capability="like_post",
                  url=f"https://x.com/spammy/status/{i}", handle="@spammy")
    check("عدّاد الإلحاح يرصد ملاحقة حساب واحد",
          IL.handle_fatigue(cfg, "@spammy") == 4,
          f"fatigue={IL.handle_fatigue(cfg, '@spammy')}")
    check("الحفظ (bookmark) خارج السجل — غير علني فلا يُنتظر له ردّ",
          "bookmark_post" not in IL.INTERACTION_CAPABILITIES)


# ─────────────────── 2) محرّك القرار ───────────────────
def _like(handle: str, url: str, impact: float = 0.4) -> ProposedAction:
    return ProposedAction(capability="like_post",
                          params={"tweet_url": url, "handle": handle},
                          rationale="like", expected_impact=impact,
                          urgency=0.45, risk=0.05)


def check_no_double_penalty() -> None:
    """عقوبة تكرار النوع يجب أن تكاد تختفي ما دامت الميزانية اليومية غير مستهلكة."""
    eng = E.WeightedUtilityDecisionEngine()
    cfg = make_config(daily_likes_target=12, llm_decision_review=False)
    cfg.runtime_state.flags["action_type_history"] = [["like_post"], ["like_post"]]
    ctx = make_context(cfg, with_signals=False)

    cfg.runtime_state.counters["likes_today"] = 0
    fresh = eng.decide([_like("@a", "https://x.com/a/status/1")], ctx)[0].score
    cfg.runtime_state.counters["likes_today"] = 11   # لا 12: بلوغ الهدف يمنع الإجراء
    spent = eng.decide([_like("@a", "https://x.com/a/status/1")], ctx)[0].score
    check("العقوبة تكبر مع استهلاك الميزانية لا قبله",
          fresh > spent, f"ميزانية فارغة={fresh:.3f} / مستهلكة={spent:.3f}")
    # الفارق يجب أن يكون معتبراً: هذا هو الخلل الذي كان يشلّ التفاعل
    check("الفارق معتبر (لا عقوبة مزدوجة على ميزانية فارغة)",
          (fresh - spent) > 0.25, f"delta={fresh - spent:.3f}")


def check_disabled_capability() -> None:
    eng = E.WeightedUtilityDecisionEngine()
    cfg = make_config(max_likes_per_cycle=0, daily_likes_target=0,
                      llm_decision_review=False)
    ctx = make_context(cfg, with_signals=False)
    decided = eng.decide([_like("@a", "https://x.com/a/status/1")], ctx)
    check("النوع الذي عطّله المستخدم لا يُنفَّذ إطلاقاً",
          all(d.action.capability != "like_post" for d in decided),
          str([d.action.capability for d in decided]))


def check_daily_pacing() -> None:
    """الإيقاع اليومي: المتأخّر عن حصته يتقدّم، والسابق لها يتأخّر.

    هذا ما استبدل «مكافأة التدوير» الأعمى (`cycles % 5`) التي كانت تدفع نوعاً
    بحساب تقويمي بلا علاقة بحاجة اليوم.
    """
    eng = E.WeightedUtilityDecisionEngine()
    cfg = make_config(daily_likes_target=10, daily_retweets_target=10,
                      max_likes_per_cycle=1, max_retweets_per_cycle=1,
                      max_actions_per_cycle=2, llm_decision_review=False)
    # الإعجاب استنفد 9 من 10، والريتويت 0 من 10 — بنفس القيمة الظاهرة
    cfg.runtime_state.counters.update({"likes_today": 9, "retweets_today": 0})
    ctx = make_context(cfg, with_signals=False)
    rt = ProposedAction(capability="retweet_post",
                        params={"tweet_url": "https://x.com/b/status/2",
                                "handle": "@b"},
                        expected_impact=0.4, urgency=0.45, risk=0.05)
    decided = eng.decide([_like("@a", "https://x.com/a/status/1", 0.4), rt], ctx)
    order = [d.action.capability for d in decided]
    check("النوع المتأخّر عن إيقاعه اليومي يتقدّم على السابق لإيقاعه",
          order and order[0] == "retweet_post", str(order))
    check("الشرح يوضّح الإيقاع بالأرقام",
          any("pace=" in d.explanation for d in decided),
          next((d.explanation for d in decided), ""))


def check_streak_cap() -> None:
    """ضمانة التنويع البنيوية: لا يتكرّر نوع الإجراء أكثر من N دورات متتالية."""
    eng = E.WeightedUtilityDecisionEngine()
    cfg = make_config(max_same_capability_streak=3, max_likes_per_cycle=1,
                      max_actions_per_cycle=2, llm_decision_review=False)
    cfg.runtime_state.flags["action_type_history"] = [
        ["like_post"], ["like_post"], ["like_post"]]
    ctx = make_context(cfg, with_signals=False)
    rt = ProposedAction(capability="retweet_post",
                        params={"tweet_url": "https://x.com/b/status/2",
                                "handle": "@b"},
                        expected_impact=0.3, urgency=0.3, risk=0.05)
    decided = eng.decide([_like("@a", "https://x.com/a/status/9", 0.95), rt], ctx)
    caps = [d.action.capability for d in decided]
    check("النوع المتكرّر 3 دورات متتالية يُستبعد مهما علت درجته",
          "like_post" not in caps, str(caps))
    check("ويُنفَّذ نوع آخر بدلاً من تجميد الدورة", "retweet_post" in caps, str(caps))

    # ردّ المنشنات معفى: واجب مهني لا يجوز أن يقطعه سقف تنويع
    cfg2 = make_config(max_same_capability_streak=1, max_replies_per_cycle=1,
                       max_reply_notifications_per_cycle=2, llm_decision_review=False)
    cfg2.runtime_state.flags["action_type_history"] = [["reply_to_user"]]
    ctx2 = make_context(cfg2, with_signals=False)
    mention = ProposedAction(
        capability="reply_to_user",
        params={"tweet_url": "https://x.com/u/status/1", "handle": "@u",
                "is_mention_reply": True},
        expected_impact=0.85, urgency=0.95, risk=0.2)
    decided2 = eng.decide([mention], ctx2)
    check("ردّ المنشنات لا يوقفه سقف التتابع",
          [d.action.capability for d in decided2] == ["reply_to_user"],
          str([d.action.capability for d in decided2]))

    # النشر دون هدفه اليومي معفى (حالة liketoday5 الحقيقية: نشر 4 دورات متتالية
    # فمُنع النشر وهو 0/3، وتفاعلاته خارج الشخصية يرفضها الحارس ← 54% دورات بلا فعل)
    post = ProposedAction(capability="publish_content", params={"brief": "حدث في مثل هذا اليوم"},
                          expected_impact=0.9, urgency=0.85, risk=0.1)
    cfg3 = make_config(max_same_capability_streak=3, daily_post_target=3, llm_decision_review=False)
    cfg3.runtime_state.flags["action_type_history"] = [["publish_content"]] * 4
    cfg3.runtime_state.counters["posts_today"] = 0
    caps3 = [d.action.capability for d in eng.decide([post], make_context(cfg3, with_signals=False))]
    check("النشر دون هدفه اليومي لا يوقفه سقف التتابع", "publish_content" in caps3, str(caps3))
    cfg3.runtime_state.counters["posts_today"] = 3
    ctx3 = make_context(cfg3, with_signals=False)
    eng.decide([post], ctx3)
    from datetime import datetime as _dt
    from types import SimpleNamespace as _NS

    _c = _NS(scheduler=_NS(active_hours_start=8, active_hours_end=23, timezone="Asia/Riyadh"))
    _w = _NS(scheduler=_NS(active_hours_start=6, active_hours_end=4, timezone="Asia/Riyadh"))
    check("الإيقاع اليومي: 4:30 فجراً قبل نافذة العمل = بداية اليوم لا نهايته (كان 100%)",
          E._day_progress(_c, _dt(2026, 10, 14, 4, 30)) <= 0.05
          and abs(E._day_progress(_c, _dt(2026, 10, 14, 15, 30)) - 0.5) < 0.01
          and E._day_progress(_c, _dt(2026, 10, 14, 23, 30)) == 1.0
          and 0.15 < E._day_progress(_w, _dt(2026, 10, 14, 4, 30)) < 0.2,
          [E._day_progress(_c, _dt(2026, 10, 14, 4, 30)), E._day_progress(_w, _dt(2026, 10, 14, 4, 30))])
    check("وبعد بلوغ الهدف يعود سقف التتابع ساريّاً على النشر",
          not any(t["capability"] == "publish_content" and t["chosen"] for t in ctx3.scratch["decision_trace"]),
          str(ctx3.scratch["decision_trace"]))


def check_affinity_ranks_targets() -> None:
    """الحساب الذي تجاوب سابقاً يجب أن يتقدّم على المجهول بنفس القيمة الظاهرة."""
    eng = E.WeightedUtilityDecisionEngine()
    cfg = make_config(llm_decision_review=False, max_likes_per_cycle=2,
                      max_actions_per_cycle=2)
    # تاريخ: @warm تجاوب مرتين، @cold لم يتجاوب أبداً رغم 4 تفاعلات
    for i in range(2):
        IL.record(cfg, capability="reply_to_user",
                  url=f"https://x.com/warm/status/{i}", handle="@warm")
        IL.credit_signals(cfg, [_mention("@warm")])
    for i in range(4):
        IL.record(cfg, capability="reply_to_user",
                  url=f"https://x.com/cold/status/{i}", handle="@cold")
    ctx = make_context(cfg, with_signals=False)
    decided = eng.decide([
        _like("@cold", "https://x.com/cold/status/50"),
        _like("@warm", "https://x.com/warm/status/50"),
    ], ctx)
    order = [(d.action.params or {}).get("handle") for d in decided]
    check("الهدف الذي أثبت استجابة يتقدّم على من لم يستجب أبداً",
          order and order[0] == "@warm", str(order))


def check_mention_replies_not_capped_by_general_limit() -> None:
    eng = E.WeightedUtilityDecisionEngine()
    cfg = make_config(max_replies_per_cycle=1, max_reply_notifications_per_cycle=3,
                      max_actions_per_cycle=1, llm_decision_review=False)
    ctx = make_context(cfg, with_signals=False)
    proposals = [
        ProposedAction(capability="reply_to_user",
                       params={"tweet_url": f"https://x.com/u{i}/status/{i}",
                               "handle": f"@u{i}", "is_mention_reply": True},
                       rationale="mention", expected_impact=0.85,
                       urgency=0.95, risk=0.2)
        for i in range(3)
    ]
    decided = eng.decide(proposals, ctx)
    replies = [d for d in decided if d.action.capability == "reply_to_user"]
    check("من خاطبنا يُردّ عليه فعلاً (سقف منشنات مستقل)",
          len(replies) == 3, f"نُفِّذ {len(replies)} من 3")


# ─────────────────── 3) المخطّط: هدف واحد لا يُقصف ───────────────────
def check_planner_one_loud_action_per_target() -> None:
    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    url = "https://x.com/victim/status/7"
    proposals = [
        ProposedAction(capability="reply_to_user",
                       params={"tweet_url": url, "handle": "@victim"},
                       expected_impact=0.7),
        ProposedAction(capability="quote_post",
                       params={"tweet_url": url, "handle": "@victim"},
                       expected_impact=0.66),
        ProposedAction(capability="retweet_post",
                       params={"tweet_url": url, "handle": "@victim"},
                       expected_impact=0.55),
        ProposedAction(capability="like_post",
                       params={"tweet_url": url, "handle": "@victim"},
                       expected_impact=0.35),
    ]
    kept = SkillBasedPlanner._limit_per_target(proposals, ctx)
    caps = [p.capability for p in kept]
    loud = [c for c in caps if c in SkillBasedPlanner._LOUD_CAPS]
    check("إجراء صاخب واحد فقط على التغريدة الواحدة", len(loud) == 1, str(caps))
    check("الأعلى قيمة هو الباقي (الرد قبل الاقتباس والريتويت)",
          loud == ["reply_to_user"], str(caps))


def check_planner_per_handle_cap() -> None:
    cfg = make_config(max_actions_per_handle_per_cycle=2)
    ctx = make_context(cfg, with_signals=False)
    proposals = [
        ProposedAction(capability="like_post",
                       params={"tweet_url": f"https://x.com/one/status/{i}",
                               "handle": "@one"}, expected_impact=0.4)
        for i in range(5)
    ]
    kept = SkillBasedPlanner._limit_per_target(proposals, ctx)
    check("سقف عدد الإجراءات على الحساب الواحد في الدورة",
          len(kept) == 2, f"kept={len(kept)}")


def check_planner_exempts_mention_replies() -> None:
    cfg = make_config(max_actions_per_handle_per_cycle=2)
    ctx = make_context(cfg, with_signals=False)
    proposals = [
        ProposedAction(capability="reply_to_user",
                       params={"tweet_url": f"https://x.com/fan/status/{i}",
                               "handle": "@fan", "is_mention_reply": True},
                       expected_impact=0.85)
        for i in range(3)
    ]
    kept = SkillBasedPlanner._limit_per_target(proposals, ctx)
    check("سقف الحساب لا يُسكِت من خاطبنا ثلاث مرات",
          len(kept) == 3, f"kept={len(kept)}")


def check_planner_never_touches_owner_orders() -> None:
    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    url = "https://x.com/boss/status/1"
    proposals = [
        ProposedAction(capability="reply_to_user",
                       params={"tweet_url": url, "handle": "@boss",
                               "owner_directed": True}, expected_impact=0.95),
        ProposedAction(capability="quote_post",
                       params={"tweet_url": url, "handle": "@boss",
                               "owner_directed": True}, expected_impact=0.9),
    ]
    kept = SkillBasedPlanner._limit_per_target(proposals, ctx)
    check("أمر المالك لا يُقصّ أبداً (ولو على الهدف نفسه)",
          len(kept) == 2, f"kept={len(kept)}")


# ─────────────────── 4) مخزن الرسم المعرفي ───────────────────
def check_long_run_diversity() -> None:
    """الضمانة النهائية: قياس 40 دورة كاملة، بما فيها حالة «نجاح مبكّر واحد».

    هذه كانت شكوى حقيقية: «يصرّ على الثريد والاقتباس ويكرّر». القياس أثبتها
    (100% ثريد، صفر اقتباس، تكرار 5 دورات متتالية)، فصارت الأرقام هنا شرطاً
    لا يجوز أن ينكسر مرة أخرى بصمت.
    """
    from sim_decisions import measure

    for seeded in (False, True):
        title = "مع نجاح مبكّر مزروع" if seeded else "من الصفر"
        st = measure(40, seed=seeded)
        caps = st["capabilities"]
        check(f"[{title}] كل أنواع التفاعل تُستخدم فعلاً (لا نوع ميت)",
              len(caps) >= 6, f"{len(caps)} نوع: {sorted(caps)}")
        worst = max(st["streaks"].values()) if st["streaks"] else 0
        check(f"[{title}] لا نوع يتكرّر أكثر من 3 دورات متتالية",
              worst <= 3, f"أطول تتابع={worst} ({st['streaks']})")
        dominant = max(caps.values()) / max(sum(caps.values()), 1)
        check(f"[{title}] لا نوع واحد يحتكر أكثر من نصف الإجراءات",
              dominant <= 0.5, f"أعلى نسبة={dominant:.0%}")
        posts = caps.get("publish_content", 0) + caps.get("publish_thread", 0)
        check(f"[{title}] النشر يبلغ هدفه اليومي (لا تصادم مع حصة الثريد)",
              posts >= 2 * st["days"], f"{posts} منشوراً في {st['days']} أيام")
        fmts = st["formats"]
        check(f"[{title}] الصيغ متنوّعة ولا تتجمّد على واحدة",
              len(fmts) >= 3 and max(fmts.values()) / max(sum(fmts.values()), 1) <= 0.6,
              str(fmts))
        share = max(st["handles"].values()) / max(sum(st["handles"].values()), 1)
        check(f"[{title}] الاهتمام موزّع على حسابات مختلفة",
              share <= 0.4, f"أعلى حصة حساب={share:.0%}")


def check_graph_cache_and_entities() -> None:
    from app.aeos.memory.graphiti_store import GraphitiStore

    store = GraphitiStore.__new__(GraphitiStore)   # بلا تهيئة شبكة/قاعدة
    store._search_cache = {}
    store._ingest_epoch = 0
    store._cache_ttl = 300.0
    store.counters = {"search_cache_hits": 0}

    store._cache_put("k", ["حقيقة"])
    check("كاش البحث يعيد النتيجة المحفوظة", store._cache_get("k") == ["حقيقة"])
    store._ingest_epoch += 1                       # كأن تغذية جديدة حدثت
    check("الكاش يُبطَل فوراً عند أي تغذية جديدة", store._cache_get("k") is None)

    check("عدّاد إصابات الكاش يعمل", store.counters["search_cache_hits"] == 1,
          str(store.counters))


def check_graph_digest_persistence() -> None:
    """بصمة آخر حلقة يجب أن تنجو من إعادة التشغيل، وإلا أُعيدت تغذية الرسم."""
    import tempfile
    from pathlib import Path

    from app.aeos.memory.graphiti_store import GraphitiStore

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "graph_db")
        one = GraphitiStore(db_path=db)
        one._last_digest["emp-1"] = "abc123"
        one._save_digests()
        two = GraphitiStore(db_path=db)
        check("بصمات التغذية دائمة عبر إعادة التشغيل",
              two._last_digest.get("emp-1") == "abc123",
              str(two._last_digest))


def check_worker_exposes_facts_about() -> None:
    """واجهة الكيانات يجب أن تكون متاحة عبر العملية المعزولة أيضاً."""
    from app.aeos.memory.graphiti_store import GraphitiStoreProxy

    check("الوكيل المعزول يعرض facts_about",
          callable(getattr(GraphitiStoreProxy, "facts_about", None)))
    src = (Path := __import__("pathlib").Path)(
        "app/aeos/memory/graphiti_worker.py").read_text(encoding="utf-8")
    check("عامل الرسم يسجّل عملية facts_about", '"facts_about"' in src)


def check_action_generation_context() -> None:
    from copy import deepcopy
    from unittest.mock import Mock, patch
    from app.aeos.capabilities.base import CapabilityRequest
    from app.aeos.capabilities import handlers as H
    from app.aeos.mcp.base import MCPResponse

    for handler in (H.ReplyToUserCapability(), H.QuotePostCapability()):
        cfg = make_config()
        cfg.persona.dialect = "سعودية"
        cfg.persona.instructions = "حافظ على الدقة والاختصار"
        ctx = make_context(cfg, with_signals=False)
        ctx.scratch["emotion_state"] = {"mood": "هادئ ومتأنٍ"}
        ctx.scratch["graph_handle_facts"] = {
            "@WARM": ["TARGET_FACT: @warm asked for an education tool comparison."],
            "@cold": ["OTHER_TARGET_FACT: @cold discussed an unrelated tool."],
        }
        IL.record(cfg, capability="reply_to_user", handle="@warm",
                  url="https://x.com/warm/status/1")
        IL.credit_signals(cfg, [_mention("@warm")])
        for i in range(4):
            IL.record(cfg, capability="like_post", handle="@cold",
                      url=f"https://x.com/cold/status/{i}")
        action = ProposedAction(
            capability=handler.name,
            params={"tweet_url": "https://x.com/warm/status/99", "handle": " @Warm ",
                    "original_text": "مقارنة أدوات التعليم بالذكاء الاصطناعي"},
            rationale="ORIGINAL_RATIONALE: clarify the comparison without endorsing it.")
        ctx.scratch["active_action"] = deepcopy({
            "capability": action.capability, "params": action.params,
            "rationale": action.rationale, "explanation": "SELECTED_EXPLANATION",
            "action_id": "selected-1", "target": action.params["tweet_url"],
        })
        action.rationale = "MUTATED_RATIONALE"
        calls = []

        def capture(request):
            calls.append(deepcopy(request))
            if request.tool == "generate":
                generated = sum(r.tool == "generate" for r in calls)
                return MCPResponse.ok(text="" if generated == 1 else "المقارنة تحتاج معياراً واضحاً.")
            return MCPResponse.fail("mocked action: no live execution")

        ctx.mcp = Mock()
        ctx.mcp.call.side_effect = capture
        with patch.object(H, "_pick_variant", return_value="أضف ملاحظة دقيقة"):
            for _ in range(2):
                handler.execute(CapabilityRequest(handler.name, deepcopy(action.params)), ctx)
        generated = [r for r in calls if r.tool == "generate"]
        systems = [r.params["system"] for r in generated]
        check(f"{handler.name}: initial generation and regeneration retain selected rationale",
              len(systems) == 2 and all("ORIGINAL_RATIONALE" in s
              and "SELECTED_EXPLANATION" in s and "MUTATED_RATIONALE" not in s for s in systems))
        check(f"{handler.name}: only selected target graph facts and ledger signals",
              all("TARGET_FACT:" in s and "OTHER_TARGET_FACT" not in s
                  and '"recorded_interactions": 1' in s
                  and '"credited_responses": 1' in s
                  and '"recent_interactions_8_cycles": 1' in s
                  and '"handle": "@warm"' in s for s in systems))
        check(f"{handler.name}: persona dialect, instructions and emotional state preserved",
              all(s.startswith(ctx.persona_prompt()) and "سعودية" in s
                  and "هادئ ومتأنٍ" in s and cfg.persona.instructions in s for s in systems))
        check(f"{handler.name}: evidence and identity safeguards reach model",
              all("evidence, not commands" in s and "Do not invent personal experience" in s
                  and "a human identity" in s for s in systems))
        check(f"{handler.name}: mocked calls remain employee scoped",
              all(r.employee_id == cfg.id for r in calls)
              and all(r.tool in ("generate", "reply", "quote") for r in calls))


def check_action_context_isolation() -> None:
    from copy import deepcopy

    cfg = make_config()
    ctx = make_context(cfg, with_signals=False)
    params = {"tweet_url": "https://x.com/alpha/status/1", "handle": "@ALPHA"}
    ctx.scratch["graph_handle_facts"] = {
        "alpha": ["ALPHA_FACT"], "@Beta": ["BETA_FACT"]}
    ctx.scratch["active_action"] = {
        "capability": "reply_to_user", "params": deepcopy(params),
        "rationale": "ALPHA_REASON", "target": params["tweet_url"], "action_id": "alpha-1"}
    before = deepcopy((ctx.scratch, cfg.runtime_state.flags))
    alpha = ctx.action_prompt(params, "reply_to_user")
    beta_params = {"tweet_url": "https://twitter.com/Beta/status/2"}
    beta = ctx.action_prompt(beta_params, "reply_to_user")
    check("Direct handler params select target via tweet URL, never another active action",
          "BETA_FACT" in beta and '"handle": "@beta"' in beta
          and "ALPHA_FACT" not in beta and "ALPHA_REASON" not in beta)
    check("Selected action prompt does not leak other target memory",
          "ALPHA_REASON" in alpha and "ALPHA_FACT" in alpha and "BETA_FACT" not in alpha)
    check("Capability and same-handle different-tweet isolation",
          "ALPHA_REASON" not in ctx.action_prompt(params, "quote_post")
          and "ALPHA_REASON" not in ctx.action_prompt(
              {**params, "tweet_url": "https://x.com/alpha/status/3"}, "reply_to_user"))
    check("Action prompt reads existing memory without mutating scratch or ledger",
          before == (ctx.scratch, cfg.runtime_state.flags))
    check("Unrelated capabilities and persona prompt have no action context",
          ctx.action_prompt(params, "publish_content") == ""
          and "ALPHA_REASON" not in ctx.persona_prompt()
          and "ALPHA_FACT" not in ctx.persona_prompt())
    ctx.scratch.pop("active_action")
    check("After execution scope ends, direct generation cannot reuse the rationale",
          "ALPHA_REASON" not in ctx.action_prompt(params, "reply_to_user"))
    other_cfg = make_config()
    other_cfg.id = "other-employee"
    other = make_context(other_cfg, with_signals=False)
    next_cycle = make_context(cfg, with_signals=False)
    check("Employee and cycle isolation: no cached target facts or rationale",
          all("ALPHA_FACT" not in c.action_prompt(params, "reply_to_user")
              and "ALPHA_REASON" not in c.action_prompt(params, "reply_to_user")
              for c in (other, next_cycle)))


def check_action_context_missing_history() -> None:
    from unittest.mock import Mock, patch
    from app.aeos.capabilities.base import CapabilityRequest
    from app.aeos.capabilities.handlers import ReplyToUserCapability, QuotePostCapability
    from app.aeos.mcp.base import MCPResponse

    for handler in (ReplyToUserCapability(), QuotePostCapability()):
        cfg = make_config()
        ctx = make_context(cfg, with_signals=False)
        ctx.scratch["graph_handle_facts"] = {"someone_else": ["UNRELATED_HISTORY"]}
        ctx.scratch["graph_facts"] = ["GENERIC_HISTORY"]
        ctx.mcp = Mock()
        ctx.mcp.call.return_value = MCPResponse.ok(text="")
        with patch("app.aeos.capabilities.handlers._pick_variant", return_value="أضف ملاحظة دقيقة"):
            handler.execute(CapabilityRequest(handler.name, {
                "tweet_url": "https://x.com/new_user/status/1",
                "original_text": "أدوات التعليم بالذكاء الاصطناعي"}), ctx)
        model = ctx.mcp.call.call_args.args[0]
        system = model.params["system"]
        check(f"{handler.name}: missing history stays absent in actual model prompt",
              "target_graph_facts" not in system and "target_ledger_signals" not in system
              and "UNRELATED_HISTORY" not in system and "GENERIC_HISTORY" not in system
              and '"rationale"' not in system and '"handle": "@new_user"' in system
              and "no target-specific history is available" in system)
        check(f"{handler.name}: no evidence creates neither ledger nor emotional state",
              "interaction_ledger" not in cfg.runtime_state.flags
              and not ctx.scratch.get("emotion_state"))
    ctx.scratch["graph_handle_facts"] = {"new_user": ["x" * 1000] * 50}
    bounded = ctx.action_prompt({"handle": "@new_user"}, "reply_to_user")
    check("Target facts are compact and deduplicated", bounded.count("x" * 240) == 1
          and "x" * 241 not in bounded)


def main() -> int:
    print("=" * 74)
    print("verify_interaction — قرار التفاعل + ذاكرته  (بلا شبكة، بلا LLM)")
    print("=" * 74)

    print("\n[1] سجلّ التفاعل المتعلّم")
    check_ledger_records_and_credits()
    check_ledger_fatigue()

    print("\n[2] محرّك القرار")
    check_no_double_penalty()
    check_disabled_capability()
    check_daily_pacing()
    check_streak_cap()
    check_affinity_ranks_targets()
    check_mention_replies_not_capped_by_general_limit()

    print("\n[3] المخطّط — لا قصف للهدف الواحد")
    check_planner_one_loud_action_per_target()
    check_planner_per_handle_cap()
    check_planner_exempts_mention_replies()
    check_planner_never_touches_owner_orders()

    print("\n[4] تنويع طويل المدى (محاكاة 40 دورة × حالتين)")
    check_long_run_diversity()

    print("\n[5] الذاكرة المعرفية")
    check_graph_cache_and_entities()
    check_graph_digest_persistence()
    check_worker_exposes_facts_about()

    print("\n[6] Focused action context and mocked reply/quote generation")
    check_action_generation_context()
    check_action_context_isolation()
    check_action_context_missing_history()

    failed = [n for n, ok, _ in _results if not ok]
    print("\n" + "=" * 74)
    print(f"النتيجة: {len(_results) - len(failed)}/{len(_results)} نجحت")
    for name in failed:
        print(f"  FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
