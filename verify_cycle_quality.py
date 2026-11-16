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

print("4) ميديا المخزون: فيديو أيضاً لا صور فقط")
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

from app.aeos.employee import stock_media as SM  # noqa: E402
from app.aeos.mcp.base import MCPResponse  # noqa: E402


def _media_ctx(video_tags, image_tags, kind_rule=None, scores=None):
    calls = []

    class _MCP:
        def has(self, name):
            return name == "pixabay"

        def call(self, req):
            calls.append(req.tool)
            if req.tool == "search_videos":
                return MCPResponse.ok(results=[{"id": 90, "kind": "video", "url": "https://cdn.pixabay.com/v.mp4",
                                                "tags": video_tags, "duration": 20, "size": 5_000_000}])
            return MCPResponse.ok(results=[{"id": 10, "kind": "image", "url": "https://pixabay.com/get/i.jpg",
                                            "tags": image_tags, "width": 1920}])

    class _LLM:
        available = True

        def complete_json(self, system, prompt, temperature=0.2):
            if "queries" in system:
                return {"queries": ["warship red sea"], "kind": "image", "avoid": []}
            lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit() and ". [" in ln]
            return {"scores": [{"n": i + 1, "score": (scores or {}).get("video" if "[video]" in ln else "image", 9)}
                               for i, ln in enumerate(lines)]}

    rules = {} if kind_rule is None else {"stock_media_kind": kind_rule}
    cfg = SimpleNamespace(id=f"emp-{len(FAILED)}-{id(calls)}", business_rules=rules)
    return SimpleNamespace(config=cfg, mcp=_MCP(), llm=_LLM(), scratch={}, emit_progress=lambda *a, **k: None), calls


_saved = (SM.available, SM._USED_PATH, SM._vision_ok)
SM.available = lambda c: True
SM._vision_ok = lambda text, cand: True
SM._USED_PATH = Path(tempfile.mkdtemp()) / "used.json"
try:
    ctx, calls = _media_ctx("warship, navy, sea", "warship, navy, sea")
    url = SM.find(ctx, "تحرك قطع بحرية في البحر الأحمر")
    check("auto: يبحث في الفيديو والصور معاً حتى لو اقترح المخطِّط صورة",
          {"search_videos", "search_images"} <= set(calls), calls)
    check("auto: تعادل المطابقة ⇒ الفيديو يُختار", url.endswith(".mp4") and ctx.scratch["stock_media"]["kind"] == "video",
          url)
    ctx, _ = _media_ctx("warship, navy", "warship, navy, red sea", scores={"video": 7, "image": 9})
    check("auto: صورة أدق بفارق واضح تبقى أولى (فيديو بعيد أسوأ من صورة دقيقة)",
          SM.find(ctx, "تحرك قطع بحرية في البحر الأحمر").endswith(".jpg"))
    ctx, calls = _media_ctx("warship", "warship", kind_rule="image")
    check("stock_media_kind=image ⇒ لا بحث فيديو أصلاً",
          SM.find(ctx, "تحرك بحري").endswith(".jpg") and "search_videos" not in calls, calls)
    ctx, calls = _media_ctx("warship", "warship", kind_rule="video", scores={"video": 3, "image": 9})
    url = SM.find(ctx, "تحرك بحري")
    check("stock_media_kind=video ⇒ فيديو أولاً، وصورة احتياطاً إن لم يطابق فيديو",
          calls[0] == "search_videos" and url.endswith(".jpg"), (calls, url))
    ctx, _ = _media_ctx("warship", "warship", scores={"video": 6, "image": 6})
    check("حدّ القبول على الدرجة الخام: أفضلية الفيديو لا ترفع مرشّحاً ضعيفاً فوق 7",
          SM.find(ctx, "تحرك بحري") == "")
finally:
    SM.available, SM._USED_PATH, SM._vision_ok = _saved

print("5) مكتبة ميديا الموضوع من التوجيه الإعلامي ← كل موظف يختار لتغريدته")
calls5 = []


class _PoolMCP:
    def has(self, name):
        return name == "pixabay"

    def call(self, req):
        calls5.append(req.tool)
        if req.tool == "search_videos":
            return MCPResponse.ok(results=[
                {"id": 501, "kind": "video", "url": "https://cdn.pixabay.com/ship.mp4", "tags": "warship, navy, sea",
                 "duration": 20, "size": 5_000_000, "preview": "p"},
                {"id": 502, "kind": "video", "url": "https://cdn.pixabay.com/jet.mp4", "tags": "fighter jet, takeoff",
                 "duration": 15, "size": 4_000_000, "preview": "p"}])
        return MCPResponse.ok(results=[
            {"id": 601, "kind": "image", "url": "https://pixabay.com/get/port.jpg", "tags": "cargo port, cranes, sea",
             "width": 1920, "preview": "p"},
            {"id": 602, "kind": "image", "url": "https://pixabay.com/get/ufo.jpg", "tags": "ufo, science fiction",
             "width": 1920, "preview": "p"}])


class _PoolLLM:
    available = True

    def complete_json(self, system, prompt, temperature=0.2):
        if "queries" in system:
            return {"queries": ["red sea navy", "cargo port"], "kind": "video", "avoid": []}
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit() and ". [" in ln]
        post = prompt.split("مرشّحو المخزون")[0]
        def sc(ln):
            if "سفن" in post and ("warship" in ln or "port" in ln):
                return 9
            if "مقاتلات" in post and "jet" in ln:
                return 9
            return 8 if "باب المندب" in post else 3
        return {"scores": [{"n": i + 1, "score": sc(ln)} for i, ln in enumerate(lines)]}


_saved5 = (SM.available, SM._USED_PATH, SM._vision_ok)
SM.available = lambda c: bool((getattr(c.config, "business_rules", {}) or {}).get("stock_media", "auto") != "off")
SM._vision_ok = lambda text, cand: True
SM._USED_PATH = Path(tempfile.mkdtemp()) / "used5.json"
try:
    topic5 = "تصعيد في باب المندب"
    pool = SM.build_pool(topic5, "الرسالة: تحركات بحرية وجوية حول باب المندب", 7, llm=_PoolLLM(), mcp=_PoolMCP())
    kinds = {i["kind"] for i in pool["items"]}
    check("المكتبة تُبنى من الرسالة: فيديو وصور معاً، والوسوم الخيالية مُسقطة",
          kinds == {"video", "image"} and all("ufo" not in (i["tags"] or "") for i in pool["items"]), pool["items"])
    n_calls = len(calls5)
    again = SM.build_pool(topic5, "…", 7, llm=_PoolLLM(), mcp=_PoolMCP())
    check("موظفو الحملة الآخرون يأخذون المكتبة نفسها (لا بحث ثانٍ)", again is pool and len(calls5) == n_calls)
    key5 = SM.pool_key(7, topic5)

    def _emp(eid, rules=None):
        return SimpleNamespace(config=SimpleNamespace(id=eid, business_rules=rules or {}), llm=_PoolLLM(),
                               scratch={}, emit_progress=lambda *a, **k: None)

    a, b = _emp("emp-a"), _emp("emp-b")
    url_a = SM.pick_from_pool(a, "سفن حربية تعبر قرب باب المندب", key5)
    url_b = SM.pick_from_pool(b, "مقاتلات تقلع لتأمين أجواء باب المندب", key5)
    check("كل موظف يختار ما يناسب تغريدته هو (سفن ← سفينة، مقاتلات ← مقاتلة)",
          url_a.endswith("ship.mp4") and url_b.endswith("jet.mp4"), (url_a, url_b))
    url_c = SM.pick_from_pool(_emp("emp-c"), "سفن حربية تعبر قرب باب المندب", key5)
    check("ملف أخذه زميل لا يتكرر في الحملة", url_c and url_c != url_a and url_c != url_b, url_c)
    check("الاختيار مسجّل في سجل الدورة (من المكتبة + ما فُحص)",
          a.scratch["stock_media"].get("from_pool") and a.scratch["stock_media"].get("checked"))
    check("موظف أطفأ ميديا المخزون لا يأخذ من المكتبة",
          SM.pick_from_pool(_emp("emp-d", {"stock_media": "off"}), "سفن حربية", key5) == "")
    check("مفتاح مكتبة منتهٍ/مجهول (بعد إعادة تشغيل) ⇒ لا شيء، فيكمل بالبحث العادي",
          SM.pick_from_pool(_emp("emp-e"), "سفن حربية", "0|غير موجود") == "")
    picked = []
    _orig_pick, _orig_find = SM.pick_from_pool, SM.find
    SM.pick_from_pool = lambda ctx, text, key: picked.append(key) or "https://cdn.pixabay.com/x.mp4"
    SM.find = lambda *a, **k: "should-not-be-called"
    try:
        hctx = SimpleNamespace(config=SimpleNamespace(id="emp-h", business_rules={}),
                               event=SimpleNamespace(payload={"directed_topic": topic5, "media_pool_key": key5}))
        got = H._stock_media(hctx, "نص", "", SimpleNamespace(params={}))
    finally:
        SM.pick_from_pool, SM.find = _orig_pick, _orig_find
    check("النشر يأخذ من مكتبة الموضوع قبل البحث العادي", got.endswith("x.mp4") and picked == [key5], got)
    check("الحمولة تحمل المفتاح وحده (لا روابط تضخّم موجّه التخطيط)", len(key5) < 220 and "http" not in key5)
finally:
    SM.available, SM._USED_PATH, SM._vision_ok = _saved5

print(f"\n{'❌ فشل ' + str(len(FAILED)) if FAILED else '✅ كل الفحوص نجحت'}")
for n in FAILED:
    print("  -", n)
sys.exit(1 if FAILED else 0)
