"""
verify_cycle_quality.py — عيوب في دورة الموظف قيست في سجلات الدورات الحقيقية (2026-11-16)، بلا شبكة ولا X.

1. رئيس التحرير: رقم «choice» يشير لخبر و«why» يتكلم عن آخر ⇒ بحث عن موضوع وكتابة عن آخر.
2. لازمة مستهلكة: «يا خبل» ختمت 5 من 9 منشورات و«هيا خذها» 4 — كاشف الردود لا يراها.
3. ادعاء انفراد: «خاص:» لخبر نشرته كل الوكالات.

    .\\venv\\Scripts\\python.exe verify_cycle_quality.py
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


print("1) رئيس التحرير: الاختيار يطابق ما كتبه")
from app.aeos.skills import builtin as B  # noqa: E402

shortlist = [
    {"source": "news", "title": "معسكر آيزنكوت بالكنيست يشهر أبرز ملفاته في وجه نتنياهو",
     "summary": "تصعيد داخل الكنيست الإسرائيلي بين المعارضة والحكومة", "media_url": ""},
    {"source": "news", "title": "العراق يعيد فتح معبر الشيب الحدودي مع إيران وسط ضغوط أمريكية",
     "summary": "بغداد توازن بين واشنطن وطهران في ملف المعابر", "media_url": ""},
    {"source": "news", "title": "باب المندب: إعادة تموضع القوات في ذوباب",
     "summary": "تأمين الساحل الغربي وخطوط الإمداد", "media_url": ""},
]
# الحالة المقيسة حرفياً: الرقم 1 (الكنيست) والسبب عن العراق وإيران والولايات المتحدة
idx, ok = B._reconcile_pick(shortlist, 0, {
    "choice": 1, "why": "لأنه يمس توازنات العراق مع إيران والولايات المتحدة مباشرة",
    "angle": "بغداد تستخدم معبر الشيب ورقة توازن بين واشنطن وطهران"})
check("بلا عنوان: الزاوية تدلّ على الموضوع المقصود ⇒ يُصحَّح الاختيار", idx == 1 and ok, (idx, ok))
idx, ok = B._reconcile_pick(shortlist, 0, {
    "choice": 1, "title": "العراق يعيد فتح معبر الشيب الحدودي مع إيران",
    "why": "توازن بغداد بين واشنطن وطهران"})
check("العنوان المنسوخ يحسم خطأ الرقم", idx == 1 and ok, (idx, ok))
idx, ok = B._reconcile_pick(shortlist, 0, {
    "choice": 1, "title": "معسكر آيزنكوت بالكنيست يشهر أبرز ملفاته",
    "angle": "بغداد تستخدم معبر الشيب ورقة توازن مع إيران وطهران"})
check("العنوان والزاوية متناقضان ⇒ الموضوع المؤكد بلا زاوية مضلِّلة", idx == 0 and not ok, (idx, ok))
idx, ok = B._reconcile_pick(shortlist, 2, {
    "choice": 3, "title": "باب المندب: إعادة تموضع القوات في ذوباب",
    "angle": "تأمين الساحل الغربي أولوية لا استعراض", "hook": "ذوباب مو انسحاب"})
check("اختيار متّسق يبقى كما هو بزاويته", idx == 2 and ok, (idx, ok))


class _LLM:
    available = True

    def __init__(self, out):
        self.out = out

    def complete_json(self, system, prompt):
        return self.out


def _ctx(llm):
    persona = SimpleNamespace(interests=["السياسة"], role="محلل", language="ar", dialect="")
    return SimpleNamespace(llm=llm, scratch={}, config=SimpleNamespace(persona=persona))


_orig = B._grounded_candidates
B._grounded_candidates = lambda context: [dict(c, _rel=1, _priority=2) for c in shortlist]
try:
    pick = B._editorial_pick(_ctx(_LLM({"choice": 1, "why": "توازنات العراق مع إيران والولايات المتحدة",
                                        "angle": "معبر الشيب ورقة بغداد بين واشنطن وطهران"})))
    check("_editorial_pick يعيد الموضوع الذي كُتبت له الزاوية (فالبحث والمنشور عن موضوع واحد)",
          pick and "الشيب" in pick["title"] and "الشيب" in pick.get("angle", ""), pick)
finally:
    B._grounded_candidates = _orig

print("2) اللازمة المستهلكة في المنشورات")
from app.aeos.capabilities import handlers as H  # noqa: E402

posts = [
    "ليش مسوينها فجّتين؟ صاروخ نحو مكة.\n—-\nهذي مو مقاومة يا خبل، هذا بنك أهداف.",
    "لماذا أيباك تتراجع الآن؟\n—-\nهيا خذها من الجزيرة: الحرب غيّرت المزاج يا خبل.",
    "باب المندب مو ممر بحري بس.\n—-\nيا خبل، الممر ما ينكسر بالشعارات.",
    "نتنياهو خاف من صوت عربي.\n—-\nهيا خذها: السياسة هناك تنقلب بصوت واحد.",
    "إعادة التموضع في ذوباب تحصين لعدن.\n—-\nيا خبل، البحر ما ينخدع.",
]
ledger = {"posts": {f"u{i}": {"seq": i + 1, "text": t} for i, t in enumerate(posts)}}
from app.aeos.employee import strategy  # noqa: E402

flags = {strategy._LEDGER_KEY: ledger}
ctx = SimpleNamespace(config=SimpleNamespace(runtime_state=SimpleNamespace(flags=flags), business_rules={}),
                      scratch={})
check("النصوص الكاملة من سجل الأداء (لا ذاكرة مقطوعة عند 200 حرف)", H._recent_post_texts(ctx) == posts)
rep = H._overused_in_post("تحرك جديد في الساحل الغربي.\n—-\nيا خبل، الميدان ما يكذب.", ctx, "الساحل الغربي")
check("«يا خبل» في خاتمة جديدة ⇒ لازمة مستهلكة (تبدأ بأداة فلم يرها كاشف الردود)", rep == ["يا خبل"], rep)
check("«هيا خذها» ⇒ لازمة مستهلكة",
      "هيا خذها" in H._overused_in_post("خبر جديد عن الحدود.\n—-\nهيا خذها: الأيام تفصل.", ctx, ""))
check("خاتمة جديدة ⇒ لا عيب",
      H._overused_in_post("تحرك جديد في الساحل الغربي.\n—-\nوالأيام الجاية تفصل بين الكلام والفعل.", ctx, "") == [])
check("اسم الموضوع نفسه في التوجيه ليس لازمة",
      H._overused_in_post("باب المندب يعود للواجهة.\n—-\nالتفاصيل تحسم.", ctx, "اكتب عن باب المندب") == [])
check("كلمات شائعة ولفظ الدعاء لا تُحسب لازمة (لا «اللي» ولا «إن شاء الله»)",
      not H._post_grams("اللي صار إن شاء الله خير") & H._post_grams("اللي قلته إن شاء الله يصير"))
check("repeated_signature يطلب إعادة توليد ولا يمنع النشر",
      "repeated_signature" in H._HARD_QUALITY_ISSUES and "repeated_signature" not in H._BLOCKING_QUALITY_ISSUES)

print("3) ادعاء انفراد كاذب")
for raw in ("خاص: باب المندب مو ممر بحري بس، والتحكم فيه ضغط على البحر الأحمر كله.",
            "حصري - تحرك جديد في الساحل الغربي يكشف ترتيب الأولويات بوضوح."):
    out = H._normalize_post_text(raw)
    check(f"تُزال البادئة «{raw.split()[0]}»", not out.startswith(("خاص", "حصري")), out[:30])
check("كلمة «خاص» داخل الجملة لا تُمس",
      "بشكل خاص" in H._normalize_post_text("الملف يهم الخليج بشكل خاص لأن الممر شريان التجارة."))

print(f"\n{'❌ فشل ' + str(len(FAILED)) if FAILED else '✅ كل الفحوص نجحت'}")
for n in FAILED:
    print("  -", n)
sys.exit(1 if FAILED else 0)
