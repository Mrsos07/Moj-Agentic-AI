"""
verify_unslop.py — مهارة unslop مثبّتة ومدمجة في كتابة كل الموظفين.

بلا نشر على X وبلا نداءات LLM (MCP وهمي يعيد نصوصاً محددة):

    .\\venv\\Scripts\\python.exe verify_unslop.py

1) المهارة الأصلية مثبّتة (MIT + نسب) ومتاحة للمنسّق مع ملفاتها المرجعية بأمان.
2) الماسح الإنجليزي الأصلي يعمل، والعربي يكشف فئات unslop ولا يعلّم الاستخدام الحرفي.
3) السياسة: إعادة كتابة واحدة، تُعتمد فقط إن كانت أنظف، ولا منع للنشر.
4) موصولة فعلياً: منشور، رد، اقتباس، ثريد، منشور مجدول — ومهارة always_on لكل موظف.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# قاعدة مؤقتة: كانت هذه الفحوص تكتب في data/app.db الحقيقية (طبقات الذاكرة تستخدم SessionLocal الافتراضي)
from scripts.security_fixture import setup
setup(1, [("verify-creator", "mazen_edu")])

from app.aeos.employee import unslop as U  # noqa: E402
from app.aeos.mcp.base import MCPResponse  # noqa: E402
from verify_creator import make_config, make_context  # noqa: E402

_results = []

SLOP_AR = "إليك الحقيقة: الاتفاق الجديد ليس مجرد صفقة تجارية، بل تحول في موازين المنطقة كلها."
CLEAN_AR = "الاتفاق يرفع التبادل التجاري بين البلدين إلى 40 مليار ريال خلال ثلاث سنوات حسب وزارة التجارة."


def check(name: str, passed: bool, detail: str = "") -> bool:
    _results.append((name, passed, detail))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    return passed


class FakeMCP:
    """يعيد نصوص «generate» بالترتيب ويسجّل ما يُنشر وما يُطلب."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.prompts, self.published = [], []

    def call(self, req):
        if req.tool == "generate":
            self.prompts.append(req.params.get("prompt", ""))
            return MCPResponse.ok(text=self.texts.pop(0) if self.texts else "")
        self.published.append((req.tool, dict(req.params)))
        return MCPResponse.ok(tweet_url="https://x.com/me/status/1")


def ctx_with(texts, **rules):
    cfg = make_config(research_before_quotes=False, research_before_replies=False, **rules)
    ctx = make_context(cfg, with_signals=False)
    ctx.mcp = FakeMCP(texts)
    return ctx


# ─────────────────────────── 1) التثبيت ───────────────────────────
def check_installed() -> None:
    from pathlib import Path

    from app.aeos.services.orchestrator import _Tools
    from app.aeos.skills import system_skills as SS

    base = Path(SS.SKILLS_DIR) / "unslop"
    sk = SS.get("unslop")
    check("unslop مثبّتة كمهارة نظام للمنسّق", bool(sk) and sk["kind"] == "coordinator"
          and sk["enabled"] and not sk.get("error"))
    check("ترخيص MIT ونسب المؤلف مرفقان", "MIT" in (base / "LICENSE").read_text(encoding="utf-8")
          and "theclaymethod" in (base / "LICENSE").read_text(encoding="utf-8"))
    check("الماسحات والمراجع الأصلية موجودة", all((base / p).is_file() for p in (
        "scripts/banned_phrase_scan.py", "scripts/_lang.py", "references/core-contract.md",
        "references/taboo-phrases.md", "presets/crisp-human.md")))

    orch = _Tools.__new__(_Tools)   # أداة المنسّق نفسها (لا تحتاج حالة)
    body = orch.use_system_skill({"name": "unslop"})
    check("المنسّق يحمّل تعليمات unslop", "references/core-contract.md" in body["instructions"])
    ref = orch.use_system_skill({"name": "unslop", "resource": "references/core-contract.md"})
    check("والمنسّق يقرأ الملف المرجعي الذي تحيله إليه", len(ref.get("content", "")) > 500)
    for bad in ("../unslop_guard/skill.py", "../../.env", "scripts/banned_phrase_scan.py"):
        try:
            orch.use_system_skill({"name": "unslop", "resource": bad})
            ok = False
        except (LookupError, ValueError):
            ok = True
        check(f"لا خروج من المجلد ولا قراءة كود: {bad}", ok)


# ─────────────────────────── 2) الكشف ───────────────────────────
def check_detection() -> None:
    en = U.scan("Here's the thing: our new tool is not just a product, it's a movement. "
                "Let that sink in. I hope this helps!")
    check("الإنجليزي بماسح unslop الأصلي", U.needs_fix(en) and all(f["lang"] == "en" for f in en),
          str(sorted({f["category"] for f in en})))
    check("نص إنجليزي نظيف لا يُعلَّم", not U.scan(
        "The ministry opened 12 new clinics in Jeddah this month, adding 400 beds to the city."))

    positives = {
        "throat_clearing": SLOP_AR,
        "self_qa": "لماذا يهم هذا القرار؟ لأن الرياض تريد توطين نصف الإنفاق العسكري بحلول 2030.",
        "vague_attribution": "يرى الخبراء أن أسعار النفط ستتراجع خلال الربع القادم بشكل واضح.",
        "em_dash": "القرار — كما يبدو — سيغير سوق العمل المحلي خلال عامين فقط.",
        "chatbot_artifact": "بالتأكيد! هذه أبرز ثلاث نقاط عن ميزانية 2027 وآمل أن يكون هذا مفيداً لكم.",
        "significance_inflation": "هذه نقلة نوعية في التعليم وحجر الزاوية لمستقبل أبنائنا جميعاً.",
    }
    for cat, text in positives.items():
        found = U.scan(text)
        check(f"عربي يكشف: {U.LABELS[cat]}", U.needs_fix(found) and cat in {f["category"] for f in found},
              str([(f["category"], f["severity"]) for f in found]))
    check("التاء المربوطة هاءً وهمزات مختلفة («اليك الحقيقه») تُكشف أيضاً",
          U.needs_fix(U.scan("اليك الحقيقه التي يتجاهلها الجميع عن سوق العقار في الرياض اليوم.")))
    check("«ليس مجرد X بل Y» يُكشف", "negative_parallelism" in {f["category"] for f in U.scan(SLOP_AR)})
    # نصّان نُشرا فعلاً 2026-10-26 بصيغة «ليس X؛ هو Y» / «لم X؛ بل Y» ونجوا من النسخة الأولى
    for real in ("اجتماع طهران ليس بروتوكولاً عادياً؛ هو اختبار لميزان القوة بين الرياض وطهران.",
                 "خالد بن سلمان لم يذهب لتجميل المشهد؛ بل لإقناع واشنطن أن كلفة التصعيد ستصيب الإقليم كله."):
        check(f"صيغة المقابلة المنشورة فعلاً تُكشف: «{real[:30]}…»",
              "negative_parallelism" in {f["category"] for f in U.scan(real)})
    for legit in ("الاجتماع ليس اليوم بل غداً في التاسعة صباحاً حسب جدول الوزارة.",
                  "لم يحضر الوزير المؤتمر لأنه في زيارة رسمية إلى واشنطن هذا الأسبوع."):
        check(f"ولا تُعلَّم الجملة الطبيعية: «{legit[:30]}…»", not U.needs_fix(U.scan(legit)))
    # قيس حيّاً: «ستغير قواعد اللعبة» نجت من نسخة النمط الأولى (يغير/تغير/سيغير فقط)
    check("كل تصريفات «قواعد اللعبة» تُكشف", all(
        "significance_inflation" in {f["category"] for f in U.scan(f"الميزانية الجديدة {v} قواعد اللعبة في السوق المحلي")}
        for v in ("ستغير", "سيغير", "غيرت", "تغير")))

    negatives = {
        "شاهد حرفي": "شاهد على الحادث قال للشرطة إن السيارة كانت مسرعة جداً قرب الدوار.",
        "تصحيح حقيقي بـ«ليس… بل»": "الاجتماع ليس اليوم بل غداً في التاسعة صباحاً حسب جدول الوزارة.",
        "إسناد مسمّى": "قال وزير الطاقة الأمير عبدالعزيز بن سلمان إن أوبك+ ستحافظ على الإنتاج.",
        "سؤال حقيقي": "هل ستنخفض أسعار الوقود في نوفمبر بعد قرار أوبك+ الأخير؟",
        "اقتباس كلام الغير": "كتب أحدهم «إليك الحقيقة: ليس مجرد قرار بل ثورة» وهذا تهويل لا يخدم النقاش.",
        "خبر عادي": "أعلنت الهيئة العامة للصناعات العسكرية أن لا صفقات سلاح دون محتوى محلي بنسبة 50٪.",
        "شرطة واحدة": "الرياض 2030 — مدينة تتغير كل يوم بمشاريع جديدة في النقل والسكن.",
        "علامة لينة واحدة": "وصف الوزير المشروع بأنه نقلة نوعية للنقل العام في مدينة الرياض.",
    }
    for name, text in negatives.items():
        found = U.scan(text)
        check(f"لا إعادة كتابة: {name}", not U.needs_fix(found),
              str([(f["category"], f["phrase"]) for f in found]))

    hint = U.fix_hint(U.scan(SLOP_AR))
    check("التصحيح يذكر العبارات الفعلية ويطلب حفظ الحقائق",
          "الحقيقه" in hint and "ليس مجرد" in hint and "الأرقام" in hint, hint[:120])


# ─────────────────────────── 3) السياسة ───────────────────────────
def check_policy() -> None:
    cfg = make_config()
    calls = []

    def regen(hint):
        calls.append(hint)
        return CLEAN_AR

    text, left = U.polish(SLOP_AR, regen, cfg)
    check("إعادة كتابة واحدة بالتصحيح المحدد ثم الأنظف", text == CLEAN_AR and len(calls) == 1
          and "ليس مجرد" in calls[0] and not left)
    text, _ = U.polish(SLOP_AR, lambda h: SLOP_AR + " ودع هذا يترسخ في ذهنك.", cfg)
    check("نسخة ليست أنظف ← يبقى الأصل", text == SLOP_AR)
    text, _ = U.polish(SLOP_AR, lambda h: (_ for _ in ()).throw(RuntimeError("llm down")), cfg)
    check("فشل النموذج لا يمنع النشر ← الأصل", text == SLOP_AR)
    text, _ = U.polish(SLOP_AR, lambda h: CLEAN_AR, cfg, accept=lambda r: False)
    check("شرط القبول (عدم التكرار) يُحترم", text == SLOP_AR)
    calls.clear()
    text, _ = U.polish(CLEAN_AR, regen, cfg)
    check("نص نظيف لا يكلّف أي نداء إضافي", text == CLEAN_AR and not calls)

    off = make_config(unslop=False)
    calls.clear()
    check("المالك يطفئها لموظفه (business_rules.unslop=false)",
          U.polish(SLOP_AR, regen, off)[0] == SLOP_AR and not calls and not U.prompt_rules(off))
    from app.aeos.skills import system_skills as SS

    orig = SS.is_enabled
    SS.is_enabled = lambda slug: slug != U.SKILL_SLUG
    try:
        check("المطوّر يطفئها للجميع من اللوحة", not U.enabled(cfg))
    finally:
        SS.is_enabled = orig
    check("قاعدة الوقاية بلغة الموظف", "ليس مجرد" in U.prompt_rules(cfg)
          and "not just" in U.prompt_rules(make_config_en()))


def make_config_en():
    cfg = make_config()
    cfg.persona.language = "en"
    return cfg


# ─────────────────────────── 4) الدمج ───────────────────────────
def check_wiring() -> None:
    from app.aeos.capabilities import handlers as H
    from app.aeos.capabilities.base import CapabilityRequest
    from app.aeos.skills import builtin as BI
    from app.aeos.skills.registry import SkillRegistry

    reg = SkillRegistry()
    for s in BI.default_skills():
        reg.register(s)
    check("unslop_guard تعمل لكل موظف دون تثبيت (always_on)",
          "unslop_guard" in [s.name for s in reg.resolve([])])

    ctx = ctx_with([])
    check("بوابة الجودة تعلّم ai_slop", "ai_slop" in H._quality_issues(SLOP_AR, ctx))
    check("ai_slop يعيد التوليد ولا يمنع النشر (قرار المالك)",
          "ai_slop" in H._HARD_QUALITY_ISSUES and "ai_slop" not in H._BLOCKING_QUALITY_ISSUES)
    check("قاعدة unslop في موجّه المنشورات/الثريد/المجدول", "ليس مجرد" in H._content_prompt("موضوع", ctx))

    ctx = ctx_with([SLOP_AR, CLEAN_AR])
    text, issues = H._generate_quality_post("اكتب عن اتفاق التجارة", ctx, echo_ref="اكتب عن اتفاق التجارة")
    check("منشور: يُعاد توليده بالعبارات الفعلية ويُنشر الأنظف",
          text == CLEAN_AR and "ليس مجرد" in ctx.mcp.prompts[1], str(issues))

    H_record = H._record_interaction
    H._record_interaction = lambda *a, **k: None
    try:
        ctx = ctx_with([SLOP_AR, CLEAN_AR])
        res = H.QuotePostCapability().execute(CapabilityRequest(name="quote_post", params={"tweet_url": "https://x.com/a/status/5",
                                             "original_text": "وقّعت المملكة اتفاقاً تجارياً جديداً"}), ctx)
        posted = [p for t, p in ctx.mcp.published if t == "quote"]
        check("اقتباس: يُنشر النص المصحّح", res.success and posted and posted[0]["text"] == CLEAN_AR,
              str(posted))

        ctx = ctx_with([SLOP_AR, CLEAN_AR])
        res = H.ReplyToUserCapability().execute(CapabilityRequest(name="reply_to_user", params={"tweet_url": "https://x.com/a/status/6",
                                                "original_text": "وقّعت المملكة اتفاقاً تجارياً جديداً"}), ctx)
        posted = [p for t, p in ctx.mcp.published if t == "reply"]
        check("رد: يُنشر النص المصحّح", res.success and posted and posted[0]["reply_text"] == CLEAN_AR,
              str(posted or res.error))
        check("رد: قاعدة unslop في موجّه الرد", "ليس مجرد" in ctx.mcp.prompts[0])

        ctx = ctx_with([CLEAN_AR])          # unslop مفعّلة: ومع ذلك لا يُستدعى أي توليد
        res = H.ReplyToUserCapability().execute(CapabilityRequest(name="reply_to_user", params={"tweet_url": "https://x.com/a/status/7",
                                                "reply_text": SLOP_AR}), ctx)
        posted = [p for t, p in ctx.mcp.published if t == "reply"]
        check("نص كتبه المالك بنفسه لا يُمسّ (حتى مع unslop مفعّلة)",
              posted and posted[0]["reply_text"] == SLOP_AR and not ctx.mcp.prompts)

        ctx = ctx_with([SLOP_AR, CLEAN_AR])
        res = H.SchedulePostCapability().execute(CapabilityRequest(name="schedule_post", params={"brief": "اتفاق التجارة", "hours_ahead": 2}), ctx)
        posted = [p for t, p in ctx.mcp.published if t == "schedule_post"]
        check("منشور مجدول: يُجدول النص المصحّح", posted and posted[0]["content"] == CLEAN_AR,
              str(posted or res.error))
    finally:
        H._record_interaction = H_record

    import inspect

    src = inspect.getsource(H.PublishThreadCapability.execute)
    check("ثريد: unslop على الثريد كاملاً مع إعادة توليد بالتصحيح",
          "unslop.polish" in src and "_generate_parts(request, context, hint)" in src)


def main() -> int:
    print("=== 1) التثبيت ===")
    check_installed()
    print("\n=== 2) الكشف (إنجليزي أصلي + عربي) ===")
    check_detection()
    print("\n=== 3) السياسة: مرة واحدة، الأنظف، لا منع ===")
    check_policy()
    print("\n=== 4) الدمج في كل مسارات الكتابة ===")
    check_wiring()
    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n=== النتيجة: {len(_results) - len(failed)}/{len(_results)} نجحت ===")
    for n in failed:
        print("  FAIL:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
