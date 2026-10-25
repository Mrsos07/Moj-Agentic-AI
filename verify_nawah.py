# -*- coding: utf-8 -*-
"""
فحص تكامل خادم نواه للاستنساخ (system/mcps/nawah_clone + app/aeos/services/nawah_clone.py).

بلا شبكة افتراضياً:  .\\venv\\Scripts\\python.exe verify_nawah.py
استنساخ حقيقي عبر OpenRouter (يستهلك سنتات):  ... verify_nawah.py --live

ما يُفحص:
1. manifest الخادم: stdio عبر node، نطاق مغلق للمنسّق والموظفين، الأدوات مكتشفة، بلا أسرار.
2. بيئة التشغيل تُشتق من resolve_llm() (NAWAH_LLM_*) وتفرّغ GEMINI_API_KEY، وNAWAH_MODEL يغلب.
3. تحويل بصمة نواه إلى شكل analysis الذي تقرؤه voice_block وpersona_from_voice.
4. analyze(nawah=...) يستخدم نواه أولاً، وعند فشلها يعلن السبب (nawah_error) ويكمل.
5. مزوّد البيئة في external_mcp يُدمج وقت الاتصال ويُعيد الاتصال إن تغيّرت البيئة.
6. (--live) create_clone حقيقي عبر الخادم ثم score.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("PYTHONUTF8", "1")

from app.aeos.employee import voice_profile as VP  # noqa: E402
from app.aeos.mcp.servers import external_mcp as X  # noqa: E402
from app.aeos.services import nawah_clone as NC  # noqa: E402
from app.aeos.services import persona_clone as PC  # noqa: E402

LIVE = "--live" in sys.argv
ROOT = Path(__file__).parent
PKG = ROOT / "system" / "mcps" / "nawah_clone"
passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS  {name}")
    else:
        failed += 1
        print(f"FAIL  {name}" + (f"  — {detail}" if detail else ""))


# ─────────────────────────── 1) manifest ───────────────────────────
print("\n=== 1) manifest ===")
m = json.loads((PKG / "mcp.json").read_text(encoding="utf-8"))
check("slug nawah_clone", m.get("slug") == "nawah_clone")
check("stdio عبر node", m.get("transport") == "stdio" and m.get("command") == "node")
check("المسار بالرمز {package} لا مساراً مطلقاً", all("{package}" in a for a in m.get("args", [])))
check("النطاق مغلق للمنسّق والموظفين", m.get("scope") == {"coordinator": False, "employees": False})
names = {t["name"] for t in m.get("tools", [])}
check("الأدوات مكتشفة (create_clone, score, get_clone, list_clones)", {"create_clone", "score", "get_clone", "list_clones"} <= names, str(names))
check("write/delete ليستا في allowed_tools", not ({"write", "delete_clone"} & set(m.get("allowed_tools") or [])))
check("لا أسرار في manifest — إعدادات الموديل فقط", set(m.get("env_keys") or []) == set(NC.SETTING_KEYS) and not m.get("header_keys"))
check("الملف المجمّع موجود", (PKG / "dist" / "clone-server.mjs").exists())
check("لا @google/genai في package.json", "@google/genai" not in json.dumps(json.load(open(PKG / "package.json", encoding="utf-8")).get("dependencies", {})))
check("لا gemini.ts في المصدر", not (PKG / "src" / "core" / "gemini.ts").exists() and (PKG / "src" / "core" / "llm.ts").exists())
cmd, args, cwd = X.runtime_command({**m, "slug": "nawah_clone"})
check("runtime_command يحلّ {package}", args and args[0].endswith("clone-server.mjs") and "{package}" not in args[0] and cwd and cwd.endswith("nawah_clone"))
check("مسجّل في الكتالوج كخادم مرفوع مفعّل", any(c["slug"] == "nawah_clone" and c.get("enabled") for c in X.list_configs()))

# ─────────────────────────── 2) runtime env ───────────────────────────
print("\n=== 2) بيئة التشغيل ===")
saved = {k: os.environ.get(k) for k in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "NAWAH_MODEL", "NAWAH_FALLBACK_MODEL", "TAVILY_API_KEY", "GEMINI_API_KEY")}
os.environ["OPENROUTER_API_KEY"] = "sk-or-test-key"
os.environ["TAVILY_API_KEY"] = "tvly-test"
os.environ["GEMINI_API_KEY"] = "AIza-should-not-leak"
os.environ.pop("NAWAH_MODEL", None)
os.environ.pop("NAWAH_FALLBACK_MODEL", None)
env = NC.runtime_env()
check("المفتاح من resolve_llm (OpenRouter)", env.get("NAWAH_LLM_API_KEY") == "sk-or-test-key")
check("العنوان OpenRouter", "openrouter.ai" in env.get("NAWAH_LLM_BASE_URL", ""))
check("الموديل الافتراضي مؤهَّل بالمزوّد", env.get("NAWAH_LLM_MODEL") == NC.DEFAULT_MODEL and "/" in NC.DEFAULT_MODEL)
check("GEMINI_API_KEY مفرَّغ للطفل", env.get("GEMINI_API_KEY") == "")
check("Tavily يُمرَّر", env.get("TAVILY_API_KEY") == "tvly-test")
check("مجلد البيانات data/nawah", env.get("NAWAH_DATA_DIR", "").replace("\\", "/").endswith("data/nawah"))
os.environ["NAWAH_MODEL"] = "google/gemini-3.8-flash"
check("NAWAH_MODEL يغلب الافتراضي", NC.runtime_env().get("NAWAH_LLM_MODEL") == "google/gemini-3.8-flash")
for k, v in saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v
check("clone_id ثابت ونظيف", NC.clone_id("@My Label", "@tmrrah9") == "My-Label__tmrrah9")
check("clone_id يقاوم المحارف", "/" not in NC.clone_id("a/b", "c:d") and NC.clone_id("", "") == "x__x")

# ─────────────────────────── 3) mapping ───────────────────────────
print("\n=== 3) تحويل البصمة ===")
FP = {
    "version": 1, "model": "google/gemini-3.7-flash", "source_count": 8,
    "data": {
        "dialect": "فصحى", "dialect_family": "فصحى", "register": "إخباري موجز ورسمي",
        "one_line": "إخباري موجز · ينقل الأحداث بأسلوب البرقيات", "description": "يرصد الأخبار العاجلة بسطور قصيرة.",
        "signature_phrases": ["عاجل:\n…", "أنباء:\n…", "تشمل …"],
        "never_uses": ["الرموز التعبيرية", "الوسوم والروابط", "النقط في نهايات الأسطر"],
        "openings_note": "يفتتح بكلمة تصنيفية", "punctuation_note": "بلا ترقيم ختامي", "emoji_note": "لا ينطبق",
        "hashtag_note": "لا وسوم", "numerals_note": "أرقام مشرقية",
        "style_guide": ["قسّم المنشور إلى ثلاثة أسطر.", "ابدأ بكلمة تصنيفية.", "اعتمد الفصحى الإخبارية."],
    },
    "stats": {"count": 8, "medianLines": 3, "avgWordsPerLine": 3.1, "hashtagRate": 0, "emojiRate": 0, "questionRate": 0, "numerals": "arabic"},
}
a = NC.to_analysis(FP)
check("voice_summary = one_line + description", a["voice_summary"].startswith("إخباري موجز ·") and "يرصد" in a["voice_summary"])
check("style_guide منقول", a["style_guide"] == FP["data"]["style_guide"] and a["persona"]["dos"] == a["style_guide"])
check("never_uses → donts", a["persona"]["donts"] == FP["data"]["never_uses"] and a["never_uses"])
check("العبارات بلا أسطر جديدة", all("\n" not in p for p in a["lexicon"]["signature"]) and len(a["lexicon"]["signature"]) == 3)
check("ملاحظة «لا ينطبق» تُسقط", "emoji_note" not in a["style_notes"] and "openings_note" in a["style_notes"])
check("اللهجة والنبرة", a["persona"]["dialect"] == "فصحى" and a["persona"]["tone"] == "formal")
check("لا اختراع لمحاور/قيم", a["pillars"] == [] and a["values"] == [] and a["red_lines"] == [])
check("نبرة ساخرة", NC._tone("ساخر لاذع", "") == "witty" and NC._tone("عامّي مباشر", "نجدية") == "casual")

# ─────────────────────────── 4) analyze with nawah ───────────────────────────
print("\n=== 4) analyze(nawah=…) ===")
TWEETS = [{"text": f"عاجل:\nخبر رقم {i} عن الاقتصاد السعودي\nالتفاصيل لاحقاً", "created_at": f"2026-0{1 + i % 9}-1{i % 9}T10:00:00Z", "likes": i}
          for i in range(12)]
REPLIES = [{"text": "الله يحفظك", "created_at": "2026-03-01T10:00:00Z", "in_reply_to": {"handle": "@x", "text": "صباح الخير", "self": False}}] * 3
PROFILE = {"handle": "@src", "display_name": "المصدر", "tweets": TWEETS, "replies": REPLIES}
calls = []


def fake_builder(profile, stats):
    calls.append((len(profile["tweets"]), bool(stats)))
    return {"analysis": NC.to_analysis(FP), "nawah": {"id": "lbl__src", "model": FP["model"], "source_count": 8}}


vp = VP.analyze(PROFILE, llm=None, nawah=fake_builder)
check("نواه استُدعيت بالبروفايل والقياسات", calls == [(12, True)])
check("source = nawah", vp.get("source") == "nawah" and vp.get("nawah", {}).get("id") == "lbl__src")
check("القياسات العربية بقيت (style/reply_kinds/exemplars)", vp["style"].get("len_median") and "reply_kinds" in vp["style"] and vp.get("exemplars"))
block = VP.voice_block(vp, "post")
check("voice_block يعرض دليل الأسلوب والممنوعات", "دليل أسلوبك:" in block and "لا تفعل أبداً:" in block and "قسّم المنشور" in block)
check("كتلة الرد لا تحمل دليل المنشورات", "دليل أسلوبك:" not in VP.voice_block(vp, "reply"))
persona = PC.persona_from_voice(PROFILE, vp)
check("البرسونا تأخذ dos/اللهجة/النبرة من نواه", persona["dos"] == FP["data"]["style_guide"] and persona["dialect"] == "فصحى" and persona["tone"] == "formal")
check("البرسونا لا تنقل الاسم ولا تدّعي الأصل", persona["display_name"] == "" and any("لا تدّعِ" in d for d in persona["donts"]))


class _LLM:
    available = True

    def complete(self, *_a, **_k):
        return json.dumps({"voice_summary": "من المحلّل القديم", "persona": {"dos": ["قاعدة قديمة"]}}, ensure_ascii=False)


def failing_builder(profile, stats):
    raise RuntimeError("Node.js غير مثبّت")


vp2 = VP.analyze(PROFILE, llm=_LLM(), nawah=failing_builder)
check("فشل نواه → السبب معلَن والمحلّل القديم يكمل", vp2.get("nawah_error") == "Node.js غير مثبّت" and vp2.get("source") == "llm"
      and vp2["analysis"].get("voice_summary") == "من المحلّل القديم")
vp3 = VP.analyze(PROFILE, llm=None, nawah=lambda p, s: {"analysis": {"voice_summary": "بلا دليل"}})
check("بصمة بلا style_guide تُرفض وتُعلَن", vp3.get("nawah_error") and vp3.get("source") == "heuristic")
check("بلا نواه وبلا نموذج: heuristic كما كان", VP.analyze(PROFILE, llm=None).get("source") == "heuristic")

# build_cloned_persona يمرّر الحالة
orig_enabled, orig_builder = NC.enabled, NC.builder
NC.enabled = lambda: True
NC.builder = lambda label: (lambda p, s: {"analysis": NC.to_analysis(FP), "nawah": {"id": NC.clone_id(label, p["handle"]), "model": "m"}})
res = PC.build_cloned_persona(PROFILE, llm=None, test=False, cookie_label="emp")
check("build_cloned_persona: source=nawah وid لكل (موظف، هدف)", res["source"] == "nawah" and res["voice_profile"]["nawah"]["id"] == "emp__src")
NC.enabled = lambda: False
orig_status = NC.status
NC.status = lambda: {"ok": False, "reason": "Node.js غير مثبّت"}
res2 = PC.build_cloned_persona(PROFILE, llm=None, test=False, cookie_label="emp")
check("نواه معطّلة → nawah_error يحمل السبب", res2.get("nawah_error") == "Node.js غير مثبّت" and res2["source"] == "heuristic")
NC.enabled, NC.builder, NC.status = orig_enabled, orig_builder, orig_status

# ─────────────────────────── 5) env provider in external_mcp ───────────────────────────
print("\n=== 5) مزوّد البيئة ===")
X.register_env_provider("zz_test", lambda: {"A": "1", "B": "2"})
cfg = {"slug": "zz_test", "transport": "stdio", "command": "node", "args": [], "env": {"B": "old", "C": "3"}}
e = X.runtime_env(cfg)
check("المزوّد يغلب env المحفوظة ويُدمج", e == {"A": "1", "B": "2", "C": "3"})
check("بلا مزوّد: env كما هي", X.runtime_env({"slug": "none", "env": {"K": "v"}}) == {"K": "v"})
X.register_env_provider("zz_bad", lambda: 1 / 0)
check("مزوّد يفشل لا يُسقط الاتصال", X.runtime_env({"slug": "zz_bad", "env": {"K": "v"}}) == {"K": "v"})
check("مزوّد nawah_clone مسجّل بعد install()", (NC.install() or True) and X._ENV_PROVIDERS.get("nawah_clone") is NC.runtime_env)
# إعادة الاتصال عند تغيّر البيئة: نحاكي نقلاً حيّاً
state = {"env": {"A": "1"}}
X.register_env_provider("zz_re", lambda: state["env"])
client = X.MCPClient({"slug": "zz_re", "transport": "stdio", "command": "node", "args": [], "env": {}})
closed = []
client._t = SimpleNamespace(alive=True, close=lambda: closed.append(1))
client._env_sig = X._env_signature(X.runtime_env(client._cfg))
client._connect = lambda: closed.append("connect")
client._ensure()
check("بيئة ثابتة → لا إعادة اتصال", closed == [])
state["env"] = {"A": "2"}
client._ensure()
check("بيئة تغيّرت → إغلاق ثم اتصال جديد", closed == [1, "connect"])
X._ENV_PROVIDERS.pop("zz_test", None); X._ENV_PROVIDERS.pop("zz_bad", None); X._ENV_PROVIDERS.pop("zz_re", None)

# ─────────────────────────── 7) التوجيه الإعلامي (nawah_brief) — بلا شبكة ───────────────────────────
print("\n=== 7) nawah_brief: manifest والإعدادات ===")
from app.aeos.services import nawah_brief as NB  # noqa: E402

mb = json.loads((ROOT / "system" / "mcps" / "nawah_brief" / "mcp.json").read_text(encoding="utf-8"))
check("manifest nawah_brief: stdio عبر مُطلِق داخل مجلده", mb.get("transport") == "stdio" and mb.get("args") == ["{package}/brief-server.mjs"])
check("المُطلِق يستورد dist المشترك", "nawah_clone/dist/brief-server.mjs" in (ROOT / "system" / "mcps" / "nawah_brief" / "brief-server.mjs").read_text(encoding="utf-8"))
check("أدوات brief/research مكتشفة", {t["name"] for t in mb.get("tools", [])} == {"brief", "research"})
check("النطاق مغلق (داخلي)", mb.get("scope") == {"coordinator": False, "employees": False})
check("إعدادات الموديل قابلة للتعديل من اللوحة (env_keys) في الخادمين", set(mb.get("env_keys")) == set(NC.SETTING_KEYS) == set(m.get("env_keys") or json.loads((PKG / "mcp.json").read_text(encoding="utf-8"))["env_keys"]))
check("الخادمان مسجّلان في الكتالوج", {c["slug"] for c in X.list_configs() if c.get("enabled")} >= {"nawah_clone", "nawah_brief"})
# ما يضبطه المطوّر من اللوحة يغلب .env ثم الافتراضي؛ والقيم الفارغة لا تطمس شيئاً
os.environ["OPENROUTER_API_KEY"] = "sk-or-test-key"
os.environ.pop("NAWAH_MODEL", None)
e1 = NC.runtime_env({"slug": "nawah_brief", "env": {"NAWAH_MODEL": "google/gemini-3.8-flash", "NEWSDATA_API_KEY": "nd-1", "NAWAH_FALLBACK_MODEL": ""}})
check("NAWAH_MODEL من اللوحة يغلب الافتراضي", e1["NAWAH_LLM_MODEL"] == "google/gemini-3.8-flash" and e1["NAWAH_LLM_FALLBACK_MODEL"] == NC.DEFAULT_FALLBACK)
check("NEWSDATA_API_KEY يُمرَّر للطفل", e1["NEWSDATA_API_KEY"] == "nd-1")
merged = X.runtime_env({"slug": "nawah_brief", "env": {"NAWAH_MODEL": "google/gemini-3.8-flash", "NEWSDATA_API_KEY": ""}})
check("external_mcp.runtime_env يمرّر cfg للمزوّد ويُسقط القيم الفارغة", merged.get("NAWAH_LLM_MODEL") == "google/gemini-3.8-flash" and "NAWAH_LLM_API_KEY" in merged and merged.get("NEWSDATA_API_KEY", "") == "")
for k, v in saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v
pv = X.public_view({"slug": "nawah_clone", "env": {"NAWAH_MODEL": "google/gemini-3.7-flash", "NEWSDATA_API_KEY": "secret-value"}})
check("public_view يكشف قيمة الإعداد غير السري ويخفي المفتاح", pv["env_values"] == {"NAWAH_MODEL": "google/gemini-3.7-flash"} and "secret-value" not in json.dumps(pv))

print("\n=== 8) nawah_brief: أصوات الفريق والتوجيه ===")
from app.aeos.employee.models import EmployeeConfig, Persona  # noqa: E402

def _emp(handle, **kw):
    return EmployeeConfig(id=f"id-{handle}", user_id=1, platform="x", account_username=handle,
                          persona=Persona(role="محلّل اقتصادي", dialect="نجدية", tone="formal",
                                          interests=["الاقتصاد"], dos=["ابدأ برقم"], donts=["لا تهاجم أشخاصاً"],
                                          instructions="اكتب بإيجاز"), **kw)

cloned = _emp("cloned_emp", business_rules={"voice_profile": {"version": 2, "nawah": {"id": "cloned_emp__src"}}})
(NC.DATA_DIR / "clones").mkdir(parents=True, exist_ok=True)
tmp_clone = NC.DATA_DIR / "clones" / "cloned_emp__src.json"
tmp_clone.write_text("{}", encoding="utf-8")
try:
    v1 = NB.voice_for(cloned)
    check("موظف مستنسَخ ← استنساخه المحفوظ", v1 == {"clone": "cloned_emp__src", "count": 1})
finally:
    tmp_clone.unlink(missing_ok=True)
plain = _emp("plain_emp")
v2 = NB.voice_for(plain)
check("موظف غير مستنسَخ ← persona من شخصيته", "persona" in v2 and v2["persona"]["handle"] == "plain_emp" and "محلّل اقتصادي" in v2["persona"]["description"]
      and v2["persona"]["style_guide"] == ["ابدأ برقم"] and v2["persona"]["never_uses"] == ["لا تهاجم أشخاصاً"] and v2["persona"]["dialect"] == "نجدية")
missing_clone = _emp("gone_emp", business_rules={"voice_profile": {"version": 2, "nawah": {"id": "gone_emp__x"}}})
check("استنساخ مسجّل بلا ملف ← persona (لا فشل)", "persona" in NB.voice_for(missing_clone))
voices, by_id = NB.team_voices(1, [cloned, plain])
check("خريطة الأصوات ← يوزر الموظف", by_id.get("persona:plain_emp") == "plain_emp" and by_id.get("persona:cloned_emp") == "cloned_emp" and len(voices) == 2)

SC = {"directive": {"message": "الرسالة العامة", "facts": ["حقيقة 1 [1]", "حقيقة 2 [2]"],
                    "voices": [{"clone": "persona:plain_emp", "name": "plain_emp", "beliefs": "يؤمن", "stance": "موقف", "angle": "الزاوية الاقتصادية", "avoid": "التهويل"},
                               {"clone": "cloned_emp__src", "name": "src", "beliefs": "", "stance": "", "angle": "الخلفية", "avoid": ""}]},
      "pieces": [{"clone": "persona:plain_emp", "text": "مسودة بلا قياس", "score": None},
                 {"clone": "cloned_emp__src", "text": "مسودة أضعف", "score": 70}, {"clone": "cloned_emp__src", "text": "مسودة أقوى", "score": 90}],
      "research": {"brief": "موجز [1]", "sources": [{"title": "خبر", "uri": "https://www.alarabiya.net/x", "date": "2026-10-25"}]},
      "voices": [{"clone": "persona:plain_emp", "name": "plain_emp", "adhoc": True, "measured": False}, {"clone": "cloned_emp__src", "name": "src", "adhoc": False, "measured": True}],
      "failed": [], "markdown": "# توجيه"}
nb = NB.normalize(SC, {"persona:plain_emp": "plain_emp", "cloned_emp__src": "cloned_emp"}, "موضوع")
check("normalize: الأصوات مفهرسة بيوزر الموظف", set(nb["voices"]) == {"plain_emp", "cloned_emp"} and nb["voices"]["plain_emp"]["adhoc"] is True)
check("normalize: أقوى مسودة تُختار", nb["voices"]["cloned_emp"]["draft"] == "مسودة أقوى" and nb["voices"]["cloned_emp"]["score"] == 90)
check("normalize: المصادر مرقّمة بموقعها", nb["sources"][0]["n"] == 1 and nb["sources"][0]["site"] == "alarabiya.net")
txt = NB.for_employee(nb, "plain_emp")
check("for_employee: الرسالة والحقائق والدور والمسودة", all(x in txt for x in ("الرسالة العامة", "حقيقة 1", "الزاوية الاقتصادية", "مسودة بلا قياس", "[1] alarabiya.net", "طريقة الكتابة")))
check("for_employee: درجة المسودة تظهر للمقيس فقط", "تطابق 90/100" in NB.for_employee(nb, "cloned_emp") and "تطابق" not in txt.split("مسودة كتبها")[1][:30])
check("owner_summary/owner_line", NB.owner_summary(nb)["who_says_what"][0]["employee"] == "plain_emp" and "التوجيه الإعلامي من نواه جاهز" in NB.owner_line(nb))
check("research_items للمخرج البصري", NB.research_items(nb)[0]["source"] == "alarabiya.net")

# الذاكرة المؤقتة ومنع التكرار: نداء واحد لكل (مستخدم، موضوع) ولو طلبه موظفان معاً
calls = []
_orig_call, _orig_team = NB._call, NB.team_voices
NB.team_voices = lambda uid, employees=None: ([{"persona": {"name": "x"}}], {"persona:x": "x"})
def fake_call(topic, voices, timeout=0):
    calls.append(topic); time.sleep(0.3); return SC
NB._call = fake_call
import threading as _th
results = []
ts = [_th.Thread(target=lambda: results.append(NB.build("موضوع متزامن", 7))) for _ in range(3)]
[t.start() for t in ts]; [t.join() for t in ts]
check("ثلاث طلبات متزامنة ← نداء واحد والنتيجة نفسها", calls == ["موضوع متزامن"] and len(results) == 3 and all(r.get("ok") for r in results))
check("peek يعيد الجاهز وprepare يقول ready", NB.peek("موضوع متزامن", 7) is not None and NB.prepare("موضوع متزامن", 7) == "ready")
check("announce_once مرة واحدة فقط", NB.announce_once(results[0], 7) is True and NB.announce_once(results[0], 7) is False)
def boom(topic, voices, timeout=0):
    raise RuntimeError("Node.js غير مثبّت")
NB._call = boom
bad = NB.build("موضوع يفشل", 7)
check("فشل نواه ← ok=False بسببه (للاحتياط المعلَن)", bad.get("ok") is False and bad.get("error") == "Node.js غير مثبّت")
payload = {"directed_topic": "موضوع يفشل"}
got, err = NB.attach(payload, plain)
check("attach عند الفشل: لا media_brief والسبب يعود", got is None and err == "Node.js غير مثبّت" and "media_brief" not in payload)
NB._call = fake_call
payload = {"directed_topic": "موضوع ناجح"}
got, err = NB.attach(payload, plain)
check("attach عند النجاح: media_brief محقون بمحرّك نواه", got and payload.get("media_brief_engine") == "nawah" and "التوجيه الإعلامي (نواه)" in payload["media_brief"])
NB._call, NB.team_voices = _orig_call, _orig_team

# ─────────────────────────── 6) live ───────────────────────────
if LIVE:
    print("\n=== 6) حيّ: create_clone عبر OpenRouter ===")
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    import tempfile
    import time

    tmp = tempfile.mkdtemp(prefix="nawah_verify_")
    live_env = {**NC.runtime_env(), "NAWAH_DATA_DIR": tmp}
    cfg = next(c for c in X.list_configs() if c["slug"] == "nawah_clone")
    X.register_env_provider("nawah_clone", lambda: live_env)
    try:
        cli = X.MCPClient(cfg)
        tools = {t["name"] for t in cli.list_tools()}
        check("الخادم يقلع ويُعلن 6 أدوات", len(tools) == 6, str(tools))
        t0 = time.time()
        texts = NC._texts(PROFILE)
        res = cli.call_tool("create_clone", {"name": "src", "handle": "src", "kind": "posts", "texts": texts, "id": NC.clone_id("verify", "src")}, timeout=240)
        dt = round(time.time() - t0, 1)
        sc = res.get("structuredContent") or {}
        fp = sc.get("fingerprint") or {}
        check(f"create_clone نجح ({dt} ث)", not res.get("isError") and fp.get("data", {}).get("style_guide"), X.result_text(res)[:200])
        check("الموديل الذي أجاب مؤهَّل بالمزوّد", "/" in str(fp.get("model", "")), str(fp.get("model")))
        a_live = NC.to_analysis(fp)
        check("البصمة الحيّة تتحوّل إلى analysis", a_live["voice_summary"] and a_live["style_guide"])
        r2 = cli.call_tool("score", {"clone": sc.get("id"), "text": texts[0], "format": "post"}, timeout=60)
        check("score يعيد درجة", isinstance((r2.get("structuredContent") or {}).get("score"), (int, float)))
        check("الملف في مجلد البيانات المعزول", any(Path(tmp, "clones").glob("verify__src.json")))
        cli.close()
    finally:
        X.register_env_provider("nawah_clone", NC.runtime_env)

if LIVE:
    print("\n=== 9) حيّ: brief عبر OpenRouter (صوت مستنسَخ + صوت بشخصية) ===")
    voices_live = [
        {"persona": {"name": "محلّل اقتصادي", "handle": "econ_emp", "description": "محلّل اقتصادي سعودي يكتب عن سوق العمل والطاقة بنبرة رسمية موجزة.",
                     "dialect": "فصحى", "style_guide": ["ابدأ برقم أو حقيقة", "سطران إلى ثلاثة"], "never_uses": ["الإيموجي"],
                     "samples": ["ارتفع عدد العاملين في القطاع الخاص إلى 11 مليوناً.\nالرقم الأعلى منذ 2019.", "أسعار النفط فوق 90 دولاراً.\nالميزانية تتنفس.", "الصادرات غير النفطية تنمو 8%.\nالتنويع يعمل."]}},
        {"persona": {"name": "صانع محتوى شبابي", "handle": "youth_emp", "description": "شاب سعودي يكتب بالعامية النجدية بخفة دم عن التقنية والحياة.", "dialect": "نجدية"}},
    ]
    cfg = next(c for c in X.list_configs() if c["slug"] == "nawah_brief")
    X.register_env_provider("nawah_brief", lambda c=None: {**NC.runtime_env(c), "NAWAH_DATA_DIR": tmp})
    try:
        cli = X.MCPClient(cfg)
        check("خادم التوجيه يقلع ويُعلن أداتين", {t["name"] for t in cli.list_tools()} == {"brief", "research"})
        t0 = time.time()
        res = cli.call_tool("brief", {"topic": "سوق العمل السعودي", "voices": voices_live, "research": "web", "drafts": True, "report": False}, timeout=420)
        dt = round(time.time() - t0, 1)
        sc = res.get("structuredContent") or {}
        d = sc.get("directive") or {}
        check(f"brief نجح ({dt} ث)", not res.get("isError") and d.get("message"), X.result_text(res)[:300])
        check("توجيه لكل صوت (موقف وزاوية)", len(d.get("voices") or []) == 2 and all(v.get("angle") for v in d["voices"]))
        pieces = sc.get("pieces") or []
        check("مسودة لكل صوت", {p.get("clone") for p in pieces} >= {"persona:econ_emp", "persona:youth_emp"}, str([p.get("clone") for p in pieces]))
        check("الصوت المقيس له درجة والمجهول بلا درجة", any(p.get("score") is not None for p in pieces if p.get("clone") == "persona:econ_emp")
              and all(p.get("score") is None for p in pieces if p.get("clone") == "persona:youth_emp"))
        check("بحث بمصادر", bool((sc.get("research") or {}).get("sources")))
        nb_live = NB.normalize(sc, {"persona:econ_emp": "econ_emp", "persona:youth_emp": "youth_emp"}, "سوق العمل السعودي")
        check("for_employee الحيّ يحمل الزاوية والمسودة", "زاويتك" in NB.for_employee(nb_live, "youth_emp") and nb_live["voices"]["youth_emp"]["draft"])
        cli.close()
    finally:
        X.register_env_provider("nawah_brief", NC.runtime_env)

print(f"\n=== النتيجة: {passed}/{passed + failed} ===")
sys.exit(1 if failed else 0)
