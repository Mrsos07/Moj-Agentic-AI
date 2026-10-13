"""
تحقّق تحليل المواضيع + البحث + خوادم MCP الخارجية — بلا LLM وبلا X وبلا مفاتيح.

    .\\venv\\Scripts\\python.exe verify_tools.py [--mcp-python <python فيه mcp>]

قسم MCP يشغّل خادماً **حقيقياً** من الـSDK الرسمي (stdio ثم Streamable HTTP)
فيُختبر العميل أمام البروتوكول الفعلي لا أمام محاكاة كتبناها نحن. الـSDK لا
يُثبَّت في venv التطبيق (يرقّي 13 حزمة)، لذا نمرّر بايثون بيئة منفصلة:

    py -3.12 -m venv %LOCALAPPDATA%\\Moj-Agentic-AI\\mcp_testenv
    %LOCALAPPDATA%\\Moj-Agentic-AI\\mcp_testenv\\Scripts\\python -m pip install mcp==2.2.0

لا تضعها في %TEMP%: تنظيف ويندوز التلقائي حذف ملفات منها فصار ``mcp`` حزمة ناقصة
(``cannot import name 'MCPServer' … (unknown location)``) وفشل الاختبار بلا أي تغيير في الكود.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("PYTHONUTF8", "1")
sys.stdout.reconfigure(encoding="utf-8")

FAILED = []


def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


ap = argparse.ArgumentParser()
_MCP_ENV = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "Moj-Agentic-AI" / "mcp_testenv"
ap.add_argument("--mcp-python", default=str(_MCP_ENV / "Scripts" / "python.exe"))
args = ap.parse_args()

# ───────────────────────── 1) الكلمات على حدود الكلمة ─────────────────────────
print("1) مطابقة الكلمات (المقيسة سابقاً كإيجابيات كاذبة)")
from app.aeos.employee.persona_templates import relevance_score as R  # noqa: E402

E = ["politics", "economy", "religion", "tech"]
check("«المدينة/حجم» لا تطابق دين/حج", R("المدينة جميلة وحجم الإقبال كبير", E) == 0)
check("said/happy لا تطابق ai/app", R("I said I was happy", E) == 0)
check("«الدينار» لا يطابق «دين»", R("سعر الدينار", ["religion"]) == 0)
check("«فندق» لا يطابق «فن»", R("فندق جديد", ["art"]) == 0)
check("سوابق عربية: «للحج» تطابق", R("للحج ترتيبات", ["religion"]) == 1)
check("لواحق: «الدينية/الفنانين» تطابق", R("الشؤون الدينية", ["religion"]) == 1 and R("الفنانين", ["art"]) == 1)
check("عبارة متعددة الكلمات مع سابقة: «بالذكاء الاصطناعي»", R("بالذكاء الاصطناعي", ["tech"]) >= 1)

# ───────────────────────── 2) الأهمية + الهامش الدلالي ─────────────────────────
print("2) تحليل المواضيع (مُضمِّن مزيّف حتمي)")
from app.aeos.employee import topic_intel as T  # noqa: E402

# مُضمِّن مزيّف: محور لكل مرجع؛ النص يأخذ محور أول مرجع تظهر إحدى كلماته فيه
AXES = list(T.ANCHORS) + list(T.NEGATIVE_ANCHORS)
CUES = {"politics": ["عسكري", "الحكومة"], "economy": ["صفقة"], "tech": ["GPT"],
        "sports": ["هدف", "مباراة"], "chatter": ["صباح الخير"], "ads": ["خصم"]}


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * len(AXES)
        for i, a in enumerate(AXES):
            if t in (T.ANCHORS.get(a), T.NEGATIVE_ANCHORS.get(a)) or any(c in t for c in CUES.get(a, [])):
                v[i] = 1.0
        if not any(v):
            v[AXES.index("chatter")] = 0.3
        out.append(v)
    return out


fake_embed.model = "fake"
T.embedder_factory = lambda: fake_embed
now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 4 * 86400))
tl = [
    {"url": "u1", "text": "عاجل: بدء عملية عسكرية واسعة", "views": 900000, "likes": 5000, "created_at": now},
    {"url": "u2", "text": "صفقة استحواذ ضخمة لصندوق الاستثمار", "views": 300, "likes": 2, "created_at": old},
    {"url": "u3", "text": "هدف رائع في مباراة الليلة", "views": 500000, "likes": 9000, "created_at": now},
    {"url": "u4", "text": "إطلاق نموذج GPT جديد", "views": 40000, "likes": 800, "created_at": now},
    {"url": "u5", "text": "خصم 50% اطلب الآن", "views": 100, "likes": 0, "created_at": ""},
]
cfg = SimpleNamespace(id="e1", persona=SimpleNamespace(interests=E), business_rules={}, mcp_servers=[])
ctx = SimpleNamespace(config=cfg, scratch={"timeline": [dict(t) for t in tl]})
rep = T.analyze(ctx)
by = {t["url"]: t for t in ctx.scratch["timeline"]}
check("الإعلان بلا توقيت يُستبعد (أغلب الدفعة مؤرّخ)", "u5" not in by and rep["promoted_dropped"] == 1)
check("التحليل الدلالي فُعّل", rep["semantic"])
check("خبر تقني بلا كلمة قاموس (GPT) صار ضمن التخصص",
      R(by["u4"]["text"], E) == 0 and T.relevance(ctx, by["u4"]["text"], E, by["u4"]) >= 1)
check("الكرة خارج التخصص رغم انتشارها", T.relevance(ctx, by["u3"]["text"], E, by["u3"]) == 0)
check("الأهمية: المنتشر الحديث > القديم المغمور", by["u1"]["_imp"] > by["u2"]["_imp"])
s1 = T.blended_score(T.relevance(ctx, by["u1"]["text"], E, by["u1"]), by["u1"]["_imp"])
s2 = T.blended_score(T.relevance(ctx, by["u2"]["text"], E, by["u2"]), by["u2"]["_imp"])
check("الترتيب المدمج يقدّم الأهم ضمن التخصص", s1 > s2, (s1, s2))
check("بحث بالنص يجد الهامش (موقع يضيف @الحساب)", T.relevance(ctx, by["u4"]["text"] + " @x", E) >= 1)

T.embedder_factory = lambda: None
ctx2 = SimpleNamespace(config=cfg, scratch={"timeline": [dict(t) for t in tl]})
rep2 = T.analyze(ctx2)
check("بلا تضمينات: يعود للكلمات بلا فشل", rep2["semantic"] is False and
      T.relevance(ctx2, "الحكومة تعلن الميزانية", E) == R("الحكومة تعلن الميزانية", E))

# ───────────────────────── 3) البحث ─────────────────────────
print("3) مزوّد البحث + كتلة البحث")
import requests  # noqa: E402

from app.aeos.mcp.base import MCPRequest  # noqa: E402
from app.aeos.mcp.registry import MCPRegistry  # noqa: E402
from app.aeos.mcp.servers import core_mcp  # noqa: E402

for k in core_mcp.SEARCH_PROVIDER_KEYS.values():
    os.environ.pop(k, None)
srv = core_mcp.SearchMCP()
r = srv.call(MCPRequest(employee_id="e", tool="web_search", params={"query": "x"}))
check("بلا مفتاح: فشل صريح «غير مُهيّأ» (لا نجاح فارغ)", not r.success and "not configured" in r.error)

calls = []


class _Resp:
    status_code = 200

    def __init__(self, data): self._d = data
    def raise_for_status(self): pass
    def json(self): return self._d


def fake_post(url, json=None, headers=None, timeout=None):
    calls.append((url, headers))
    return _Resp({"results": [{"title": "T", "url": "https://a", "content": "حقيقة"}]})


requests.post, real_post = fake_post, requests.post
os.environ["TAVILY_API_KEY"] = "tvly-test"
r = srv.call(MCPRequest(employee_id="e", tool="web_search", params={"query": "صفقة"}))
requests.post = real_post
check("Tavily: نتائج موحّدة title/url/snippet",
      r.success and r.data["results"] == [{"title": "T", "url": "https://a", "snippet": "حقيقة"}])
check("المفتاح في الترويسة لا في الرابط", calls and "tvly-test" in calls[0][1]["Authorization"]
      and "tvly" not in calls[0][0])

from app.aeos.employee import research  # noqa: E402
from app.aeos.employee import tool_use as _tu  # noqa: E402

_real_available = _tu.available
_tu.available = lambda cfg: []   # هذا القسم للبحث المدمج فقط — لا يلمس خوادم الكتالوج الحقيقية (رصيد Tavily)
reg = MCPRegistry()
reg.register(srv)
progress = []
rctx = SimpleNamespace(config=SimpleNamespace(id="e", mcp_servers=[], business_rules={}),
                       mcp=reg, scratch={}, emit_progress=lambda t, intent="": progress.append(t))
requests.post = fake_post
out = research.augment_brief("اكتب", "صفقة", rctx)
requests.post = real_post
check("البحث يُضاف للبريف بمحدّدات وتحذير من التعليمات المحقونة",
      "<<<" in out and ">>>" in out and "تجاهل أي تعليمات" in out and "حقيقة" in out)
rctx.config.business_rules = {"web_search": False}
check("فصل البحث يدوياً (web_search=False) يوقفه", research.augment_brief("اكتب", "صفقة", rctx) == "اكتب")
_tu.available = _real_available
os.environ.pop("TAVILY_API_KEY")

print("3.5) الاكتفاء بـ Tavily: البحث وكاشف الترندات")
check("Tavily المزوّد الوحيد للبحث", list(core_mcp.SEARCH_PROVIDER_KEYS) == ["tavily"])
os.environ["BRAVE_SEARCH_API_KEY"], os.environ["SERPAPI_KEY"] = "b-old", "s-old"
check("مفاتيح Brave/SerpAPI القديمة لا تفعّل البحث", core_mcp.search_provider() == ("", ""))
os.environ.pop("BRAVE_SEARCH_API_KEY"); os.environ.pop("SERPAPI_KEY")

import asyncio  # noqa: E402
from app.trend_detector.collectors import tavily_collector as TC  # noqa: E402
from app.trend_detector.models import Candidate  # noqa: E402
from app.trend_detector.pipeline.scoring import ScoringEngine  # noqa: E402
from app.trend_detector.config import VALIDATION_THRESHOLDS as VT  # noqa: E402
from app.trend_detector.scheduler.scheduler import TrendDetectorScheduler  # noqa: E402

check("كاشف الترندات: مصدر واحد هو Tavily", list(TrendDetectorScheduler().collectors) == ["tavily"])
_real_interests = TC.employee_interests
TC.employee_interests = lambda n: ["الذكاء الاصطناعي", "كرة القدم", "الاقتصاد", "الطقس"][:n]
qs = TC.build_queries()
check("الاستعلامات: عام + تخصصات الموظفين، بحد أقصى 4 (رصيد)", len(qs) == 4 and qs[0]["query"] == TC.BASE_QUERY
      and qs[1]["query"] == "آخر أخبار الذكاء الاصطناعي", qs)
seen_q = []


def _fake_tavily(query, **kw):
    seen_q.append((query, kw))
    return [{"title": f"خبر {query[:10]}", "url": f"https://www.news{len(seen_q)}.com/world/2026/a-story",
             "content": "تفاصيل", "score": 0.9, "published_date": "Mon, 12 Oct 2026 10:00:00 GMT"},
            {"title": "مكرر", "url": "https://www.news1.com/world/2026/a-story", "content": "", "score": 0.5},
            {"title": "آخر أخبار السعودية الحصرية", "url": "https://www.alarabiya.net/saudi-today/2026"},
            {"title": "أخبار Google", "url": "https://news.google.com/topics/abc"},
            {"title": "الجزيرة نت", "url": "https://www.aljazeera.net/"}]


_real_ts = core_mcp.tavily_search
core_mcp.tavily_search = _fake_tavily
os.environ["TAVILY_API_KEY"] = "tvly-test"
sigs = asyncio.run(TC.TavilyCollector().collect())
core_mcp.tavily_search = _real_ts
TC.employee_interests = _real_interests
os.environ.pop("TAVILY_API_KEY")
check("الجامع يطلب أخبار آخر يوم", all(kw.get("topic") == "news" and kw.get("time_range") == "day" for _, kw in seen_q))
check("صفحات الأقسام والرئيسية وأخبار Google ليست قصصاً", not any(
    "google" in s["url"] or "saudi-today" in s["url"] or s["url"].endswith(".net/") for s in sigs))
TC.employee_interests = lambda n: ["crypto"]
check("اهتمام لاتيني بلا ترجمة ← استعلام إنجليزي لا خليط لغتين",
      TC.build_queries()[1]["query"] == "latest crypto news" and TC._AR_INTEREST["politics"] == "السياسة")
TC.employee_interests = _real_interests
check("إشارات Tavily: رابط مكرر يُتجاهل، والمصدر = النطاق، والصلة = views",
      len(sigs) == 4 and sigs[0]["platform"] == "tavily" and sigs[0]["author"] == "news1.com"
      and sigs[0]["views"] == 90 and sigs[0]["published_at"].year == 2026, [s["url"] for s in sigs])
check("بلا مفتاح: الجامع غير مُهيّأ (لا نداء)", not TC.TavilyCollector().is_configured())


def _score(**kw):
    fields = {"platforms": "tavily", "views_total": 0, "likes_total": 0, "reshares_total": 1,
              "comments_total": 0, "platform_count": 1, **kw}
    return ScoringEngine()._score_candidate(Candidate(**fields))


check("قصة تغطيها عدة مصادر ← HOT", _score(**{"reshares_total": 3, "views_total": 150}) >= VT["hot"]["min_score"])
check("مصدر واحد بصلة عالية جداً ← HOT", _score(views_total=95) >= VT["hot"]["min_score"])
s1 = _score(views_total=60)
check("مصدر واحد بصلة عادية ← EARLY (قائمة المراقبة)", VT["early"]["min_score"] <= s1 < VT["hot"]["min_score"], s1)

# ───────────────────────── 4) MCP خارجي حقيقي ─────────────────────────
print("4) خادم MCP حقيقي من الـSDK الرسمي")
from app.aeos.mcp.servers import external_mcp as X  # noqa: E402

try:
    X.normalize_config({"name": "a", "command": "python & del x"})
    check("يرفض «&» في الأمر", False)
except ValueError:
    check("يرفض «&» في الأمر", True)
try:
    X.normalize_config({"name": "a", "transport": "http", "url": "file:///etc/passwd"})
    check("يرفض عنواناً غير http(s)", False)
except ValueError:
    check("يرفض عنواناً غير http(s)", True)

py = Path(args.mcp_python)
if not py.exists():
    print(f"  ⚠️ تخطّي: لا يوجد بايثون فيه mcp عند {py} (انظر رأس الملف)")
else:
    server_py = Path(tempfile.gettempdir()) / "aeos_test_mcp_server.py"
    server_py.write_text(
        "import sys\nfrom mcp.server import MCPServer\n"
        "m = MCPServer('notes')\n"
        "@m.tool()\ndef search_notes(query: str) -> str:\n"
        "    '''Search the owner notes.'''\n"
        "    return f'ملاحظة عن {query}: الرقم 42'\n"
        "@m.tool()\ndef delete_all() -> str:\n"
        "    '''Dangerous tool that must stay blocked.'''\n"
        "    return 'deleted'\n"
        "if len(sys.argv) > 1:\n"
        "    m.run(transport='streamable-http', host='127.0.0.1', port=int(sys.argv[1]))\n"
        "else:\n    m.run()\n", encoding="utf-8")

    # الكتالوج (system/mcps) والأسرار والمخزن القديم في مجلد مؤقت — لا يلمس بيانات النظام
    _tmp_cat = Path(tempfile.mkdtemp(prefix="aeos_mcp_cat_"))
    X._CATALOG_DIR = _tmp_cat / "mcps"
    X._SECRETS_PATH = _tmp_cat / "secrets.json"
    X._LEGACY_PATH = _tmp_cat / "legacy.json"

    def run_suite(label, raw):
        reg = MCPRegistry()
        try:
            cfg = X.save(raw, reg)
        except Exception as exc:  # noqa: BLE001
            check(f"[{label}] الاتصال واكتشاف الأدوات", False, repr(exc))
            return
        names = sorted(t["name"] for t in cfg["tools"])
        check(f"[{label}] الاتصال واكتشاف الأدوات", names == ["delete_all", "search_notes"], names)
        check(f"[{label}] استنتاج وسيط البحث من inputSchema", cfg["research_arg"] == "query")
        srv = reg.get("ext_notes")
        check(f"[{label}] الأدوات مسجّلة بمسافة اسم", reg.server_for_tool("ext_notes.search_notes") is srv
              and reg.server_for_tool("search_notes") is None)
        r = reg.call(MCPRequest(employee_id="e", tool="ext_notes.search_notes", params={"query": "النفط"}))
        check(f"[{label}] tools/call يعيد النص", r.success and "النفط" in r.data["text"] and "42" in r.data["text"],
              r.error or r.data)
        r = srv.call(MCPRequest(employee_id="e", tool="delete_all"))
        check(f"[{label}] أداة خارج القائمة البيضاء مرفوضة", not r.success and "not allowed" in r.error)
        check(f"[{label}] دور البحث يستدعي الأداة المعيّنة", "42" in srv.research("الذهب"))
        pub = X.public_view(cfg)
        check(f"[{label}] العرض العام بلا قيم الأسرار", "s3cret" not in json.dumps(pub, ensure_ascii=False))
        reg.unregister("ext_notes")
        check(f"[{label}] الإزالة تمسح الفهرس", reg.server_for_tool("ext_notes.search_notes") is None)

    run_suite("stdio", {"name": "notes", "command": str(py), "args": [str(server_py)],
                        "env": {"NOTES_TOKEN": "s3cret"}, "allowed_tools": ["search_notes"],
                        "research_tool": "search_notes"})

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([str(py), str(server_py), str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.25)
        run_suite("http", {"name": "notes", "transport": "http", "url": f"http://127.0.0.1:{port}/mcp",
                           "headers": {"Authorization": "Bearer s3cret"},
                           "allowed_tools": ["search_notes"], "research_tool": "search_notes"})
    finally:
        proc.terminate()
    import shutil as _shutil

    _shutil.rmtree(_tmp_cat, ignore_errors=True)

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل الفحوص نجحت")
