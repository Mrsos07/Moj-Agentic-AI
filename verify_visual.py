"""
verify_visual.py — المخرج البصري لمولّد الصور: توجيه فني ملموس، برومبت منظّم، مراجعة الجودة،
وإعادة التوليد عند العيب فقط. بلا شبكة ولا تكلفة (LLM وخادم الصور والمراجِع مُحاكاة).

    .\\venv\\Scripts\\python.exe verify_visual.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("TOPIC_SEMANTIC", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from app.aeos.employee import visual_director as VD  # noqa: E402
from app.aeos.mcp.base import MCPResponse  # noqa: E402
from verify_creator import make_config, make_context  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


class FakeLLM:
    available = True

    def __init__(self):
        self.prompts = []

    def complete_json(self, system, prompt, *, temperature=0.3):
        self.prompts.append((system, prompt))
        return {"concept": "air defense battery at night", "subject": "a Gulf air-defense launcher",
                "setting": "desert base at night", "composition": "launcher lower-left, sky right",
                "lighting": "moonlight and floodlights", "camera": "35mm, low angle",
                "palette": ["deep navy", "desert sand", "steel gray"], "mood": "vigilant",
                "avoid": ["explosions", "fighter jets"]}

    def complete(self, *a, **k):
        return ""


class FakeImageMCP:
    def __init__(self):
        self.calls = []

    def has(self, name):
        return name == "image"

    def call(self, req):
        self.calls.append(req.params)
        return MCPResponse.ok(url=f"https://img.local/{len(self.calls)}.png", local_path=f"/tmp/{len(self.calls)}.png")


TEXT = "الدفاع الجوي ليس تفصيلاً ثانوياً؛ اعتراض 13 هدفاً خلال 24 ساعة يؤكد أن الردع يعمل."

print("1) التوجيه الفني والبرومبت")
cfg = make_config(image_style="cinematic", image_prompt_extra="navy palette")
ctx = make_context(cfg, with_signals=False)
ctx.llm = FakeLLM()
brief = VD.art_direct(TEXT, ctx)
system, prompt = ctx.llm.prompts[-1]
check("المدير الفني يمنع كليشيهات الشطرنج والتيجان والخرائط والأعلام (سبب الصور السابقة)",
      all(c in system for c in ("chess", "crowns", "maps", "flags")))
check("ويشترط الدقة الثقافية الخليجية وإخفاء هوية الأشخاص ومنع النص",
      "Gulf" in system and "anonymous" in system and "No text" in system)
check("نص المنشور ونمط الحساب يصلان للمدير الفني", TEXT[:20] in prompt and VD.STYLES["cinematic"] in prompt)
p = VD.build_prompt(brief, ctx)
check("البرومبت منظّم: موضوع ← مشهد ← تكوين ← إضاءة ← كاميرا ← ألوان ← مزاج",
      all(k in p for k in ("Subject:", "Setting:", "Composition:", "Lighting:", "Camera:", "Color palette:", "Mood:")))
check("القائمة تُكتب نصاً لا «['…']» (palette قائمة من النموذج)", "deep navy, desert sand" in p and "['" not in p, p[:400])
check("16:9 والقيود (بلا نص/خرائط/أعلام/وجوه حقيقية) والإضافة المخصّصة", "16:9" in p and "NO maps" in p
      and "NO flags" in p and "recognizable real people" in p and "navy palette" in p and "cinematic" in p)
check("ما يجب تجنّبه من الموجز يدخل البرومبت", "explosions" in p)
VD.remember_concept(ctx, brief["concept"])
VD.art_direct(TEXT, ctx)
check("المفاهيم الأخيرة تُمرَّر للمدير الفني حتى لا تتكرر", "air defense battery at night" in ctx.llm.prompts[-1][1])
ctx2 = make_context(make_config(), with_signals=False)
ctx2.llm = None
fb = VD.art_direct(TEXT, ctx2)
check("بلا LLM: توجيه حتمي معقول (لا «استعارة لهذا المنشور»)", fb.get("subject") and "metaphor" not in VD.build_prompt(fb))

print("2) المراجعة وإعادة التوليد")
real_review = VD.review


def run(verdicts, **rules):
    c = make_context(make_config(**rules), with_signals=False)
    c.llm, c.mcp = FakeLLM(), FakeImageMCP()
    seq = iter(verdicts)
    VD.review = lambda path, brief: next(seq)
    out = VD.create(c, TEXT)
    VD.review = real_review
    return out, c


out, c = run([{"ok": True, "severe": False, "issues": [], "score": 8}])
check("صورة جيدة من أول مرة ← تُنشر بلا إعادة (توفير التكلفة)", out["url"].endswith("/1.png") and len(c.mcp.calls) == 1)
check("ونسبة 16:9 تُطلب من خادم الصور", c.mcp.calls[0].get("aspect_ratio") == "16:9")
out, c = run([{"ok": False, "severe": False, "issues": ["minor taste"], "score": 7}])
check("رفض ذوقي بدرجة ≥6 ← لا إعادة (قيس: رُفضت صورة جيدة وكلّفت توليداً ثانياً)",
      len(c.mcp.calls) == 1 and out["url"])
out, c = run([{"ok": False, "severe": False, "issues": ["remove the chess pieces"], "score": 3},
              {"ok": True, "severe": False, "issues": [], "score": 8}])
check("عيب حقيقي ← إعادة مرة واحدة بالتصحيحات المحددة", len(c.mcp.calls) == 2 and out["url"].endswith("/2.png")
      and "remove the chess pieces" in c.mcp.calls[1]["prompt"])
check("وتاريخ المراجعات محفوظ (لماذا رُفضت الأولى)", [h["score"] for h in out["history"]] == [3, 8])
out, c = run([{"ok": False, "severe": True, "issues": ["garbled text on the sign"], "score": 2},
              {"ok": False, "severe": True, "issues": ["a flag appears"], "score": 2}])
check("عيب جسيم بعد محاولتين ← بلا صورة (المنشور نصاً أفضل من صورة سيئة)",
      out["url"] == "" and "severe" in out["reason"] and len(c.mcp.calls) == 2)
out, c = run([], image_review=False)
check("المراجعة معطّلة (image_review=false) ← توليد واحد بلا فحص", len(c.mcp.calls) == 1 and out["url"])
check("الصورة المقبولة تُسجَّل كمفهوم مستخدم", "air defense battery at night" in VD.recent_concepts(c))
check("acceptable: درجة 6 مقبولة، 5 لا، والجسيم لا مهما كانت الدرجة",
      VD.acceptable({"ok": False, "score": 6}) and not VD.acceptable({"ok": False, "score": 5})
      and not VD.acceptable({"ok": True, "severe": True, "score": 9}))
check("مراجعة بلا ملف محلي ← مقبولة بلا انهيار", VD.review("", {})["ok"])

print("3) خادم الصور")
from types import SimpleNamespace as NS  # noqa: E402
from unittest.mock import patch  # noqa: E402

from app.aeos.mcp.base import MCPRequest  # noqa: E402
from app.core.system_loader import mcp_module  # noqa: E402

ImageMCP = mcp_module("image").ImageMCP
seen = {}


def fake_or(endpoint, model, prompt, aspect, quality=""):
    seen.update(aspect=aspect, quality=quality)
    return "https://img.local/x.png", ""


ep = NS(available=True, is_openrouter=True, provider="openrouter", image_model="openai/gpt-image-2")
with patch.object(ImageMCP, "_enabled", return_value=True), patch.object(ImageMCP, "_endpoint", return_value=ep), \
        patch.object(ImageMCP, "_generate_openrouter", side_effect=fake_or):
    r = ImageMCP().call(MCPRequest(employee_id="t", tool="generate_image", params={"prompt": "p"}))
    check("الافتراضي: 16:9 بجودة high (كان مربّعاً 1024x1024 يُقصّ في التايم لاين)",
          r.success and seen == {"aspect": "16:9", "quality": "high"}, seen)
    ImageMCP().call(MCPRequest(employee_id="t", tool="generate_image", params={"prompt": "p", "size": "1024x1024"}))
    check("مقاس صريح (الصورة الشخصية 1024x1024) ← 1:1 كما هو", seen["aspect"] == "1:1", seen)

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص المخرج البصري نجحت")
