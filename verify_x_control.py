"""
إدارة حساب X من المنسّق (app/aeos/services/x_control.py + أداة x_account) — بلا
متصفح ولا LLM ولا قاعدة بيانات: كل الحدود (X/السجل/المنصة) مُستبدلة.

    .\\venv\\Scripts\\python.exe verify_x_control.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("PYTHONUTF8", "1")
sys.stdout.reconfigure(encoding="utf-8")

import app.aeos.bootstrap as B  # noqa: E402
from app.aeos.employee.models import EmployeeConfig, Persona  # noqa: E402
from app.aeos.mcp.base import MCPResponse  # noqa: E402
from app.aeos.services import insights as I  # noqa: E402
from app.aeos.services import orchestrator as O  # noqa: E402
from app.aeos.services import tools_service as TS  # noqa: E402
from app.aeos.services import x_control as XC  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:  # noqa: BLE001
        return str(e) or True
    return False


# ── فريق مزيّف ──
TEAM = [
    EmployeeConfig(id="e1", user_id=1, platform="x", account_username="alpha", cookie_label="alpha",
                   persona=Persona(display_name="أحمد")),
    EmployeeConfig(id="e2", user_id=1, platform="x", account_username="beta", cookie_label="beta"),
    EmployeeConfig(id="e3", user_id=2, platform="x", account_username="gamma", cookie_label="gamma"),
]
from scripts.security_fixture import setup
setup(1, [("e1", "alpha"), ("e2", "beta")])
SAVED, NOTES, MEMS = [], [], []
REPO = SimpleNamespace(list_all=lambda: TEAM, get=lambda i: next((c for c in TEAM if c.id == i), None),
                       save=lambda c: SAVED.append(c.id))
MEM = SimpleNamespace(remember=lambda eid, layer, text, importance=1: MEMS.append((eid, text)))
PLATFORM = SimpleNamespace(repository=REPO, llm=None,
                           container=SimpleNamespace(resolve=lambda k: MEM),
                           notify_owner=lambda eid, text, intent="": NOTES.append((eid, text)) or True)
B.get_platform = lambda: PLATFORM
O.employees_for.__globals__["_platform"] = lambda: PLATFORM

# سجل دورات alpha: منشور، رد، ثريد، ومنشور فاشل
ROWS = {"e1": [
    SimpleNamespace(created_at=datetime(2026, 10, 16, 9, 0), outcome=json.dumps({"results": [
        {"capability": "publish_content", "success": True,
         "output": {"tweet_url": "https://x.com/alpha/status/1111111111111", "text": "منشور الصباح"}},
        {"capability": "like_post", "success": True, "output": {}},
    ]})),
    SimpleNamespace(created_at=datetime(2026, 10, 15, 9, 0), outcome=json.dumps({"results": [
        {"capability": "reply_to_user", "success": True,
         "output": {"tweet_url": "https://x.com/alpha/status/2222222222222", "reply_text": "رد قديم"}},
        {"capability": "publish_thread", "success": True,
         "output": {"tweet_url": "https://x.com/alpha/status/3333333333333", "texts": ["ج1", "ج2"]}},
        {"capability": "publish_content", "success": False,
         "output": {"tweet_url": "https://x.com/alpha/status/4444444444444"}},
    ]})),
]}
I._fetch_rows = lambda eid, limit=200: ROWS.get(eid, [])

CALLS = []
NEXT = {}


def _fake_call(req):
    CALLS.append((req.tool, dict(req.params), dict(req.context)))
    r = NEXT.get(req.tool)
    return r(req) if callable(r) else (r or MCPResponse.ok(success=True))


TS.registry = lambda: SimpleNamespace(call=_fake_call)
STARTED = []
XC.run_background = lambda cfg, action, targets, fields: STARTED.append(
    (cfg.account_username, action, [t.get("tweet_id") or t.get("handle") or t.get("event_id") for t in targets], fields))
alpha = TEAM[0]

print("1) تمييز أوامر إدارة X في الشات")
for m in ("احذف آخر تغريدة لي", "امسح المنشور https://x.com/alpha/status/1111111111111",
          "الغ اللايك على هالتغريدة", "تراجع عن الريتويت", "الغي متابعة @someone", "delete this tweet",
          "شيل البوكمارك"):
    check(f"«{m}» ⇒ إدارة X", O.is_x_management(m))
for m in ("احذف الموظف @alpha", "انشر عن الذكاء الاصطناعي", "وش سوى الفريق اليوم", "امسح الذكرى #3"):
    check(f"«{m}» ليست إدارة X", not O.is_x_management(m))

print("2) منشورات الموظف من سجله")
posts = XC.recorded_posts(alpha)
check("3 منشورات ناجحة (الفاشل والإعجاب مستبعدان)", [p["tweet_id"] for p in posts]
      == ["1111111111111", "2222222222222", "3333333333333"], posts)
check("مرقّمة والأحدث أولاً", posts[0]["n"] == 1 and posts[0]["text"] == "منشور الصباح")
check("نص الثريد يُجمع", posts[2]["text"] == "ج1 / ج2" and posts[2]["kind"] == "ثريد")
check("موظف بلا سجل ⇒ قائمة فارغة", XC.recorded_posts(TEAM[1]) == [])

print("3) تحليل الأهداف")
R = lambda action, targets, posts=None: XC.resolve_targets(alpha, action, targets, posts)  # noqa: E731
t = R("delete_post", ["https://x.com/alpha/status/1111111111111"])[0]
check("رابط منشوره ⇒ معرّف + نص للمعاينة", t["tweet_id"] == "1111111111111" and t["text"] == "منشور الصباح", t)
check("twitter.com وmobile مقبولان", R("delete_post", ["https://mobile.twitter.com/Alpha/status/2222222222222"])[0]["tweet_id"] == "2222222222222")
check("حذف منشور حساب آخر ⇒ رفض",
      raises(lambda: R("delete_post", ["https://x.com/someone/status/5555555555555"]), PermissionError))
check("إلغاء لايك على منشور الآخرين مسموح", R("unlike", ["https://x.com/someone/status/5555555555555"])[0]["url"]
      == "https://x.com/someone/status/5555555555555")
check("معرّف رقمي ⇒ رابط على حساب الموظف", R("delete_post", ["٣٣٣٣٣٣٣٣٣٣٣٣٣"])[0]["url"]
      == "https://x.com/alpha/status/3333333333333")
check("«آخر» ⇒ أحدث منشور", R("delete_post", ["آخر"])[0]["tweet_id"] == "1111111111111")
check("رقم من القائمة", R("delete_post", ["2"])[0]["tweet_id"] == "2222222222222")
live = [{"n": 1, "tweet_id": "9999999999999", "url": "https://x.com/alpha/status/9999999999999", "text": "يدوي"}]
check("الرقم يُحلّ مقابل آخر قائمة رآها المالك", R("delete_post", ["1"], live)[0]["tweet_id"] == "9999999999999")
check("رقم خارج القائمة ⇒ خطأ واضح", raises(lambda: R("delete_post", ["9"]), LookupError))
check("هدف غامض ⇒ خطأ", raises(lambda: R("delete_post", ["المنشور الحلو"]), ValueError))
check("أكثر من 10 أهداف ⇒ رفض", raises(lambda: R("unlike", [str(10**12 + i) for i in range(11)]), ValueError))
check("تكرار الهدف يُدمج", len(R("delete_post", ["1", "https://x.com/alpha/status/1111111111111"])) == 1)
check("متابعة بـ@يوزر", R("unfollow", ["@SomeOne"])[0] == {"handle": "SomeOne", "url": "https://x.com/SomeOne"})
check("رابط ملف شخصي", R("follow", ["https://x.com/other/"])[0]["handle"] == "other")
check("لا يلغي متابعة نفسه", raises(lambda: R("unfollow", ["@alpha"]), ValueError))
check("بلا هدف ⇒ خطأ", raises(lambda: R("unlike", []), ValueError))

print("4) أداة x_account: معاينة ثم تأكيد في رسالة لاحقة")
turn1 = O._Tools(1, "احذف آخر تغريدة لألفا")
res = json.loads(turn1.call("x_account", json.dumps({"employee": "alpha", "action": "delete_post",
                                                       "targets": ["آخر"]})))
check("الحذف يعيد معاينة لا تنفيذاً", res.get("needs_confirmation") and not STARTED, res)
check("المعاينة فيها نص المنشور", res["preview"][0]["text"] == "منشور الصباح")
cid = res["confirmation_id"]
same = json.loads(turn1.call("x_account", json.dumps({"employee": "alpha", "action": "delete_post",
                                                        "confirmation_id": cid})))
check("التأكيد في الرسالة نفسها ⇒ مرفوض", "error" in same and not STARTED, same)
check("المعاينة بقيت بعد محاولة التأكيد المرفوضة", XC.pending_for(1))
brief = XC.pending_brief(1)
check("المعاينة المعلّقة تُحقن للرسالة التالية", cid in brief and "@alpha" in brief and "حذف منشور" in brief, brief)
check("المالك له معاينة معلّقة ⇒ يذهب ردّه للمنسّق", O.has_pending_x(1) and not O.has_pending_x(2))
other_user = json.loads(O._Tools(2, "نعم").call("x_account", json.dumps(
    {"employee": "gamma", "action": "delete_post", "confirmation_id": cid})))
check("مستخدم آخر لا يستعمل معرّف التأكيد", "error" in other_user and not STARTED)
wrong_emp = json.loads(O._Tools(1, "نعم").call("x_account", json.dumps(
    {"employee": "beta", "action": "delete_post", "confirmation_id": cid})))
check("معرّف تأكيد موظف لا يُستعمل لموظف آخر", "error" in wrong_emp and not STARTED, wrong_emp)
# ما سبق استهلك المعرّف؟ لا: الرفض قبل pop للموظف الآخر يجب ألا يُسقطه
wrong_action = json.loads(O._Tools(1, "نعم").call("x_account", json.dumps(
    {"employee": "alpha", "action": "unfollow", "confirmation_id": cid})))
check("معرّف حذف لا يؤكد إجراءً آخر", "error" in wrong_action and not STARTED, wrong_action)
check("المحاولات المرفوضة لا تستهلك المعرّف", any(p["confirmation_id"] == cid for p in XC.pending_for(1)))

ok = json.loads(O._Tools(1, "نعم أكد").call("x_account", json.dumps(
    {"employee": "alpha", "action": "delete_post", "confirmation_id": cid})))
check("التأكيد من رسالة لاحقة ⇒ تنفيذ في الخلفية", ok.get("started") and STARTED[-1][:3]
      == ("alpha", "delete_post", ["1111111111111"]), (ok, STARTED))
check("المعرّف يُستعمل مرة واحدة", "error" in json.loads(O._Tools(1, "نعم").call("x_account", json.dumps(
    {"employee": "alpha", "action": "delete_post", "confirmation_id": cid}))))
check("التنفيذ يُسجَّل حدثاً للمنسّق (لا المعاينة)", "x_account" in O._EVENT_TOOLS)

STARTED.clear()
r = json.loads(O._Tools(1, "الغ اللايك").call("x_account", json.dumps(
    {"employee": "alpha", "action": "unlike", "targets": ["https://x.com/n/status/5555555555555"]})))
check("التراجع عن لايك ينفّذ مباشرة (قابل للعكس)", r.get("started") and STARTED[-1][1] == "unlike", r)
r = json.loads(O._Tools(1, "غيّر البايو").call("x_account", json.dumps(
    {"employee": "alpha", "action": "update_profile", "bio": "محلل تقني"})))
check("تعديل الملف يحتاج تأكيداً ويحمل الحقول", r.get("needs_confirmation") and r["fields"] == {"bio": "محلل تقني"}, r)
check("تعديل الملف بلا حقول ⇒ خطأ", "error" in json.loads(O._Tools(1, "x").call("x_account", json.dumps(
    {"employee": "alpha", "action": "update_profile"}))))
check("موظف مستخدم آخر غير مرئي", "error" in json.loads(O._Tools(1, "x").call("x_account", json.dumps(
    {"employee": "gamma", "action": "unlike", "targets": ["1234567890123"]}))))
check("إجراء غير معروف ⇒ خطأ", "error" in json.loads(O._Tools(1, "x").call("x_account", json.dumps(
    {"employee": "alpha", "action": "delete_account"}))))

print("5) رسالة لا تؤكد ⇒ تسقط المعاينة")


class FakeClient:
    def __init__(self, script):
        self.script, self.seen = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.seen.append(kw)
        step = self.script.pop(0)
        msg = SimpleNamespace(content=step if isinstance(step, str) else "",
                              tool_calls=None if isinstance(step, str) else step)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _call(i, name, args):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


XC._PENDING.clear()
fake = FakeClient([[_call(1, "x_account", {"employee": "alpha", "action": "delete_post", "targets": ["2"]})],
                   "سأحذف «رد قديم». أؤكد؟"])
out = O.handle("احذف رقم 2 من منشورات ألفا", 1, client_factory=lambda: (fake, "m"))
check("المنسّق يعرض المعاينة ويسأل", out and "أؤكد" in out["message"])
check("المعاينة باقية بعد رسالتها", len(XC.pending_for(1)) == 1)
fake2 = FakeClient(["تمام، ما حذفت شيء."])
O.handle("لا خلاص، وش أخبار الفريق؟", 1, client_factory=lambda: (fake2, "m"))
check("الموجّه التالي رأى المعاينة المعلّقة", any("confirmation_id=" in m["content"] for m in fake2.seen[0]["messages"] if m["role"] == "user"))
check("رسالة لا تؤكد ⇒ المعاينة سقطت", XC.pending_for(1) == [])

fake3 = FakeClient([[_call(1, "x_account", {"employee": "alpha", "action": "delete_post", "targets": ["1"]})],
                    "أؤكد؟"])
O.handle("احذف المنشور 1", 1, client_factory=lambda: (fake3, "m"))
cid = XC.pending_for(1)[0]["confirmation_id"]
STARTED.clear()
fake4 = FakeClient([[_call(1, "x_account", {"employee": "alpha", "action": "delete_post", "confirmation_id": cid})],
                    "جارٍ الحذف."])
O.handle("نعم احذفه", 1, client_factory=lambda: (fake4, "m"))
check("«نعم» في الرسالة التالية ⇒ التنفيذ", STARTED and STARTED[-1][1] == "delete_post", STARTED)

print("6) وضع الموظف")
check("x_account متاحة للموظف حين يُخاطَب", "x_account" in O.EMPLOYEE_TOOL_NAMES
      and any(t["function"]["name"] == "x_account" for t in O._employee_tool_schemas()))
emp_tools = O._Tools(1, "احذف آخر تغريدة لي", only_employee=alpha)
r = json.loads(emp_tools.call("x_account", json.dumps({"employee": "beta", "action": "delete_post", "targets": ["آخر"]})))
check("رفض تمرير زميل عبر سياق موظف مقيّد", "error" in r, r)

print("7) التنفيذ الفعلي: مؤكد / لم يكن قائماً / فشل")
XC._PENDING.clear()
NEXT.clear()
CALLS.clear()
NOTES.clear()
NEXT["delete_tweet"] = lambda req: (MCPResponse.ok(success=True, verified=True)
                                    if req.params["tweet_id"] == "1111111111111"
                                    else MCPResponse.fail("رفض X طلب الحذف"))
targets = R("delete_post", ["1", "2"])
summary = XC.execute(alpha, "delete_post", targets, {}, notify=lambda t: NOTES.append(("n", t)))
check("الحذف يمرّر الحساب المتوقع للخادم", CALLS[0][1] == {"tweet_id": "1111111111111", "expected_handle": "alpha"}
      and CALLS[0][2] == {"server": "x", "cookie_label": "alpha"}, CALLS)
check("نجاح وفشل يُعدّان منفصلين", summary["done"] == 1 and summary["failed"] == 1, summary)
check("الإبلاغ يذكر الفشل بسببه", "رفض X طلب الحذف" in NOTES[-1][1] and "✅" in NOTES[-1][1], NOTES)
check("المحذوف يُحفظ في حالة الموظف", "1111111111111" in alpha.runtime_state.flags.get("deleted_posts", []))
check("المحذوف لا يظهر في القائمة بعدها", "1111111111111" not in [p["tweet_id"] for p in XC.recorded_posts(alpha)])
check("«آخر» صار المنشور التالي", R("delete_post", ["آخر"])[0]["tweet_id"] == "2222222222222")
check("ملاحظة في ذاكرة الموظف", MEMS and MEMS[-1][0] == "e1" and "حذف منشور" in MEMS[-1][1])

NEXT["unlike"] = MCPResponse.ok(success=True, already=True)
s = XC.execute(alpha, "unlike", R("unlike", ["https://x.com/n/status/5555555555555"]), {})
check("«لم يكن معجباً أصلاً» تُبلّغ كما هي لا كنجاح", s["already"] == 1 and s["done"] == 0
      and "لم يكن قائماً" in s["summary"], s)
NEXT["unretweet"] = MCPResponse.ok(success=True)
XC.execute(alpha, "unretweet", R("unretweet", ["https://x.com/n/status/6666666666666"]), {})
check("ما تراجع عنه المالك لا يعود إليه الموظف تلقائياً",
      "https://x.com/n/status/6666666666666" in alpha.runtime_state.flags.get("engaged_urls", []))
CALLS.clear()
XC.execute(alpha, "update_profile", [{}], {"bio": "جديد", "name": ""})
check("تعديل الملف يمرّر الحقول غير الفارغة فقط", CALLS[-1][0] == "update_profile" and CALLS[-1][1] == {"bio": "جديد"}, CALLS)
CALLS.clear()
XC.execute(alpha, "unfollow", R("unfollow", ["@other"]), {})
check("إلغاء المتابعة برابط الملف", CALLS[-1][1] == {"profile_url": "https://x.com/other"})

print("8) المجدول: منشورات هذا الحساب فقط")
from app.services import x_bridge  # noqa: E402

x_bridge.list_scheduled_posts = lambda status=None, limit=100: {"posts": [
    {"event_id": "ev1", "cookie_label": "alpha", "content": "مجدول ألفا", "run_at": "2026-10-17 09:00:00", "status": "SCHEDULED"},
    {"event_id": "ev2", "cookie_label": "gamma", "content": "مجدول غيره", "run_at": "2026-10-17 10:00:00", "status": "SCHEDULED"}]}
check("القائمة مفلترة بحساب الموظف", [p["event_id"] for p in XC.scheduled_for(alpha)] == ["ev1"])
r = json.loads(O._Tools(1, "الغ المجدول").call("x_account", json.dumps(
    {"employee": "alpha", "action": "cancel_scheduled", "targets": ["ev2"]})))
check("إلغاء مجدول حساب آخر ⇒ رفض", "error" in r, r)
r = json.loads(O._Tools(1, "الغ المجدول").call("x_account", json.dumps(
    {"employee": "alpha", "action": "cancel_scheduled", "targets": ["ev1"]})))
check("إلغاء مجدوله يحتاج تأكيداً ويعرض نصه", r.get("needs_confirmation") and r["preview"][0]["text"] == "مجدول ألفا", r)

print("9) خادم MCP لـ X وطبقة المتصفح")
from system.mcps.x.server import XPlatformMCP  # noqa: E402
from app.aeos.mcp.base import MCPRequest  # noqa: E402

seen = []
x_bridge.undo_like = lambda **k: seen.append(("undo_like", k)) or {"success": True, "already": False}
x_bridge.undo_repost = lambda **k: seen.append(("undo_repost", k)) or {"success": True}
x_bridge.undo_bookmark = lambda **k: seen.append(("undo_bookmark", k)) or {"success": False, "error": "لم يتأكد"}
x_bridge.delete_tweet = lambda **k: seen.append(("delete", k)) or {"success": True}
srv = XPlatformMCP()
check("أدوات التراجع معلنة", {"unlike", "unretweet", "unbookmark"} <= set(srv.tools))
srv.call(MCPRequest(employee_id="e1", tool="unlike", params={"tweet_url": "u"}, context={"cookie_label": "alpha"}))
srv.call(MCPRequest(employee_id="e1", tool="unretweet", params={"tweet_url": "u"}, context={"cookie_label": "alpha"}))
bad = srv.call(MCPRequest(employee_id="e1", tool="unbookmark", params={"tweet_url": "u"}, context={"cookie_label": "alpha"}))
srv.call(MCPRequest(employee_id="e1", tool="delete_tweet", params={"tweet_id": 123, "expected_handle": "alpha"},
                    context={"cookie_label": "alpha"}))
check("unlike/unretweet تصل لـ x_bridge بكوكيز الموظف", seen[0] == ("undo_like", {"cookie_label": "alpha", "tweet_url": "u"})
      and seen[1][0] == "undo_repost", seen)
check("فشل الطبقة ⇒ فشل MCP (لا نجاح زائف)", not bad.success and "لم يتأكد" in bad.error)
check("الحذف يمرّر expected_handle ومعرّفاً نصياً", seen[-1] == ("delete", {"cookie_label": "alpha", "tweet_id": "123",
                                                                             "expected_handle": "alpha"}), seen)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app", "x"))
from modules import x_delete as XD  # noqa: E402


class Resp:
    def __init__(self, status, body):
        self.status, self._b = status, body

    def text(self):
        return self._b


check("ردّ DeleteTweet سليم ⇒ مؤكد", XD._delete_confirmed([Resp(200, '{"data":{"delete_tweet":{}}}')]) is True)
check("ردّ فيه errors ⇒ مرفوض", XD._delete_confirmed([Resp(200, '{"errors":[{"message":"x"}]}')]) is False)
check("ردّ 403 ⇒ مرفوض", XD._delete_confirmed([Resp(403, "")]) is False)
check("لا ردّ بعد ⇒ غير محسوم (يُتحقق بإعادة التحميل)", XD._delete_confirmed([]) is None)
check("معرّف التغريدة من الرابط", XD._tweet_id("https://x.com/i/status/123456") == "123456" and XD._tweet_id("x") == "")
check("رابط غير صالح ⇒ خطأ قبل فتح المتصفح", raises(lambda: XD.delete_tweet("s.json", "https://x.com/home"), RuntimeError))

print("\n10) asyncio: Playwright Sync من داخل event loop (خطأ إنشاء الموظف)")
import asyncio  # noqa: E402
from app.x.modules.sync_bridge import run_sync  # noqa: E402


async def _direct():
    from playwright.sync_api import sync_playwright
    try:
        sync_playwright().start()
        return False
    except Exception as e:
        return "asyncio loop" in str(e)


def _browser_roundtrip():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        pg = b.new_page()
        pg.set_content("<h1>ok</h1>")
        text = pg.inner_text("h1")
        b.close()
        return text


async def _via_bridge():
    return run_sync(_browser_roundtrip)


check("الخطأ الأصلي قابل للتكرار مباشرةً داخل loop", asyncio.run(_direct()) is True)
check("run_sync يشغّل متصفحاً كاملاً داخل loop", asyncio.run(_via_bridge()) == "ok")
check("run_sync خارج loop = استدعاء مباشر", run_sync(_browser_roundtrip) == "ok")
check("الدوال العامة مزيّنة بـsync_safe (نشر/ملف/حذف)",
      all(getattr(f, "__wrapped__", None) is not None for f in (
          XD.delete_tweet, XD.delete_tweet_by_id)))

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص إدارة حساب X نجحت")
