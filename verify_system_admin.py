"""
verify_system_admin.py — لوحة تحكم النظام: الأدوار، خوادم MCP العامة، مهارات النظام.

على قاعدة بيانات وكتالوج مؤقتين (لا يلمس system/ ولا data/ الحقيقيين)، ومع **خادم MCP
حقيقي** (JSON-RPC عبر stdio يُكتب هنا) يُرفع كملف إعداد وكحزمة، فيُتحقق من المسار كاملاً:
رفع ← تشغيل ← اكتشاف الأدوات ← ظهورها في منسّق كل مستخدم ← استدعاؤها فعلاً.

    .\\venv\\Scripts\\python.exe verify_system_admin.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

os.environ.setdefault("TOPIC_SEMANTIC", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine  # noqa: E402

import app.db.database as DB  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="moj_sysadmin_"))
_engine = create_engine(f"sqlite:///{TMP / 'app.db'}", connect_args={"check_same_thread": False})
DB.engine = _engine
DB.SessionLocal.configure(bind=_engine)
from app.db import models as M  # noqa: E402

DB.Base.metadata.create_all(bind=_engine)

import app.aeos.mcp.servers.external_mcp as X  # noqa: E402
import app.aeos.skills.system_skills as SS  # noqa: E402

X._CATALOG_DIR = TMP / "system" / "mcps"
X._SECRETS_PATH = TMP / "data" / "system_mcp_secrets.json"
X._LEGACY_PATH = TMP / "data" / "external_mcp.json"
SS.SKILLS_DIR = TMP / "system" / "skills"
SS.STATE_PATH = TMP / "data" / "system_state.json"
shutil.copytree(Path("system/skills"), SS.SKILLS_DIR)          # المهارات المدمجة الحقيقية
# الخوادم المدمجة الأصلية الحقيقية (manifests + الكود) في الكتالوج المؤقت نفسه
for _m in Path("system/mcps").glob("*/mcp.json"):
    if json.loads(_m.read_text(encoding="utf-8")).get("kind") == "builtin":
        shutil.copytree(_m.parent, X._CATALOG_DIR / _m.parent.name)

from fastapi.testclient import TestClient  # noqa: E402

import app.aeos.bootstrap as BOOT  # noqa: E402
from app.aeos.employee.models import EmployeeConfig, Persona  # noqa: E402
from app.aeos.employee.repository import SqlEmployeeRepository  # noqa: E402
from app.aeos.mcp.registry import MCPRegistry  # noqa: E402
from app.auth.roles import has, set_role  # noqa: E402
from app.auth.security import create_access_token  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


class _Container:
    def __init__(self, reg):
        self.reg = reg

    def resolve(self, _cls):
        return self.reg


class FakePlatform:
    def __init__(self):
        self.repository = SqlEmployeeRepository()
        self.registry = MCPRegistry()
        self.container = _Container(self.registry)
        self.llm = None

        class _G:
            def clear(self, *_): pass
            def stats(self, *_): return {}
        self.graph_memory = _G()

    def delete_employee(self, eid):
        return self.repository.delete(eid)

    def stats(self):
        return {}

    def agents_overview(self):
        return {"agents": []}


BOOT._platform = FakePlatform()
from app.aeos.mcp.catalog import register_builtins  # noqa: E402
from app.aeos.memory.manager import MemoryManager  # noqa: E402

LOADED_BUILTINS = register_builtins(BOOT._platform.registry, {"llm": None, "memory": MemoryManager()})
from app.main import app  # noqa: E402

client = TestClient(app)

# ── خادم MCP حقيقي بسيط (بروتوكول JSON-RPC عبر stdio، بلا SDK) ──
SERVER_PY = r'''
import json, os, sys
def send(m): sys.stdout.write(json.dumps(m) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    msg = json.loads(line)
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18",
              "capabilities": {"tools": {}}, "serverInfo": {"name": "echo-test", "version": "1.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "echo", "description": "يعيد النص", "inputSchema": {"type": "object",
             "properties": {"text": {"type": "string"}}, "required": ["text"]}},
            {"name": "secret_env", "description": "يقرأ متغير بيئة", "inputSchema": {"type": "object", "properties": {}}}]}})
    elif method == "tools/call":
        p = msg["params"]; args = p.get("arguments") or {}
        out = ("echo:" + args.get("text", "")) if p["name"] == "echo" else ("env:" + os.getenv("ECHO_KEY", "-"))
        send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": out}]}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
'''
SERVER_FILE = TMP / "echo_server.py"
SERVER_FILE.write_text(SERVER_PY, encoding="utf-8")


def _user(email, role):
    db = DB.SessionLocal()
    u = M.User(email=email, password_hash="x")
    set_role(u, role)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    return u.id, {"Authorization": f"Bearer {create_access_token({'sub': str(u.id), 'email': email})}"}


U, HU = _user("user@t.local", "user")
A, HA = _user("admin@t.local", "admin")
D, HD = _user("dev@t.local", "developer")

print("1) الأدوار والصلاحيات")
dbs = DB.SessionLocal()
uu, ua, ud = (dbs.query(M.User).get(i) for i in (U, A, D))
check("المستخدم لا يفتح اللوحة", not has(uu, "admin.dashboard"))
check("المسؤول يعرض ولا يضيف MCP", has(ua, "system.mcp.view") and not has(ua, "system.mcp.manage"))
check("المطوّر يضيف MCP ومهارات", has(ud, "system.mcp.manage") and has(ud, "system.skills.manage"))
check("is_admin متزامن مع الدور", ua.is_admin and ud.is_admin and not uu.is_admin)
dbs.close()
check("مستخدم ← /api/admin/system/mcps = 403",
      client.get("/api/admin/system/mcps", headers=HU).status_code == 403)
check("مسؤول ← عرض الخوادم = 200", client.get("/api/admin/system/mcps", headers=HA).status_code == 200)
files = {"file": ("x.json", b'{"mcpServers":{}}', "application/json")}
check("مسؤول ← رفع MCP = 403", client.post("/api/admin/system/mcps/upload", headers=HA, files=files).status_code == 403)
check("مستخدم عادي ← الطريق القديم /api/aeos/tools/external = 403 (كان مفتوحاً لأي مسجّل)",
      client.post("/api/aeos/tools/external", headers=HU, json={"name": "x", "command": "python"}).status_code == 403)

print("2) رفع ملف إعداد JSON (صيغة mcpServers) — خادم حقيقي")
cfg_json = json.dumps({"mcpServers": {"echo": {"command": sys.executable, "args": [str(SERVER_FILE)],
                                               "env": {"ECHO_KEY": "s3cret-value"}}}})
r = client.post("/api/admin/system/mcps/upload", headers=HD,
                files={"file": ("servers.json", cfg_json.encode(), "application/json")},
                data={"description": "خادم اختبار", "coordinator": "true", "employees": "true"})
body = r.json()
check("المطوّر يرفع ← يُتصل بالخادم وتُكتشف أدواته", r.status_code == 200
      and {t["name"] for t in body["saved"][0]["tools"]} == {"echo", "secret_env"}, body)
manifest = json.loads((X._CATALOG_DIR / "echo" / "mcp.json").read_text(encoding="utf-8"))
check("manifest في system/mcps/echo بلا قيم الأسرار", "s3cret-value" not in json.dumps(manifest)
      and manifest["env_keys"] == ["ECHO_KEY"])
check("قيمة السرّ في data/ فقط", "s3cret-value" in X._SECRETS_PATH.read_text(encoding="utf-8"))
check("الواجهة لا ترى قيمة السرّ", "s3cret-value" not in json.dumps(
    client.get("/api/admin/system/mcps", headers=HA).json()))
check("سُجّل فوراً في سجل الخوادم", BOOT._platform.registry.has("ext_echo"))
check("من رفعه ومتى محفوظان", body["saved"][0]["added_by"] == "dev@t.local" and body["saved"][0]["added_at"])

print("3) رفع حزمة خادم (.py) و(.zip)")
r = client.post("/api/admin/system/mcps/upload", headers=HD,
                files={"file": ("pkg_echo.py", SERVER_PY.encode(), "text/x-python")})
check("حزمة .py ← مجلد في الكتالوج + تشغيل بمفسّر المشروع",
      r.status_code == 200 and (X._CATALOG_DIR / "pkg_echo" / "server.py").exists()
      and json.loads((X._CATALOG_DIR / "pkg_echo" / "mcp.json").read_text(encoding="utf-8"))["command"] == "{python}",
      r.text[:200])
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as zf:
    zf.writestr("zipsrv/app_main.py", SERVER_PY)
    zf.writestr("zipsrv/mcp.json", json.dumps({"entry": "app_main.py", "description": "من zip"}))
r = client.post("/api/admin/system/mcps/upload", headers=HD,
                files={"file": ("zipsrv.zip", buf.getvalue(), "application/zip")})
check("حزمة .zip بملف mcp.json يحدّد entry ← تعمل", r.status_code == 200
      and r.json()["saved"][0]["entry"] == "app_main.py", r.text[:200])
evil = io.BytesIO()
with zipfile.ZipFile(evil, "w") as zf:
    zf.writestr("../../escape.py", "print(1)")
r = client.post("/api/admin/system/mcps/upload", headers=HD,
                files={"file": ("evil.zip", evil.getvalue(), "application/zip")})
check("zip بمسار ../ ← مرفوض ولا يُكتب شيء خارج الكتالوج",
      r.status_code == 400 and not (TMP / "escape.py").exists() and not Path("escape.py").exists(), r.text[:120])
r = client.post("/api/admin/system/mcps/upload", headers=HD,
                files={"file": ("broken.py", b"import sys\nsys.exit(1)\n", "text/x-python")})
check("حزمة لا تعمل ← لا تُحفظ ولا يبقى مجلدها", r.status_code == 502
      and not (X._CATALOG_DIR / "broken").exists(), r.status_code)

print("4) الربط التلقائي بمنسّق كل مستخدم")
from app.aeos.services import orchestrator as O  # noqa: E402

tools = O.system_mcp_tools()
check("أدوات خوادم النظام تظهر للمنسّق كأدوات أصلية", "mcp__echo__echo" in tools, list(tools))
schema = next(s for s in O._system_mcp_schemas() if s["function"]["name"] == "mcp__echo__echo")
check("مخطط الأداة من inputSchema الخادم نفسه", schema["function"]["parameters"]["required"] == ["text"])
res = json.loads(O._Tools(U, "x").call("mcp__echo__echo", json.dumps({"text": "مرحبا"})))
check("المنسّق يستدعي الأداة فعلاً (لمستخدم عادي — مدمجة لكل مستخدم)", res.get("result") == "echo:مرحبا", res)
res = json.loads(O._Tools(U, "x").call("mcp__echo__secret_env", "{}"))
check("الخادم يتلقى قيمة السرّ من data/ وقت التشغيل", res.get("result") == "env:s3cret-value", res)
block = O._system_capabilities_block()
check("موجّه المنسّق يذكر مهارات النظام وأدواته", "team-weekly-report" in block and "echo" in block, block[-300:])
emp_cfg = EmployeeConfig(id=uuid.uuid4().hex, user_id=U, platform="x", account_username="emp_u",
                         cookie_label="emp_u", persona=Persona(display_name="E"))
BOOT._platform.repository.save(emp_cfg)
emp_res = json.loads(O._Tools(U, "x", only_employee=emp_cfg).call("mcp__echo__echo", '{"text":"x"}'))
check("الموظف المخاطَب لا يستدعي أدوات المنسّق النظامية", "error" in emp_res)

print("5) التعديل: النطاق والتفعيل")
r = client.patch("/api/admin/system/mcps/echo", headers=HD, json={"scope": {"coordinator": False}})
check("نطاق المنسّق مُطفأ ← تختفي أدواته من المنسّق", r.status_code == 200
      and "mcp__echo__echo" not in O.system_mcp_tools())
from app.aeos.services import tools_service as TS  # noqa: E402

TS.set_attached(emp_cfg, "ext_echo", True)
check("نطاق الموظفين مفعّل ← المالك يربط موظفه", "ext_echo" in emp_cfg.mcp_servers)
client.patch("/api/admin/system/mcps/echo", headers=HD, json={"scope": {"employees": False}})
try:
    TS.set_attached(emp_cfg, "ext_pkg_echo", True)
    attached_other = True
except ValueError:
    attached_other = False
check("خادم نظام متاح للموظفين يُربط", attached_other)
try:
    TS.call_tool(emp_cfg, "ext_echo", "echo", {"text": "x"})
    blocked = False
except ValueError:
    blocked = True
check("نطاق الموظفين أُطفئ ← استدعاء الموظف له مرفوض", blocked)
r = client.patch("/api/admin/system/mcps/pkg_echo", headers=HD, json={"enabled": False})
check("التعطيل ← يُلغى تسجيله فوراً", r.status_code == 200 and not BOOT._platform.registry.has("ext_pkg_echo"))
check("مسؤول لا يعدّل (403)", client.patch("/api/admin/system/mcps/echo", headers=HA,
                                         json={"enabled": False}).status_code == 403)
r = client.patch("/api/admin/system/mcps/echo", headers=HD, json={"env": {"ECHO_KEY": ""}})
check("سرّ فارغ في التعديل = أبقِ القيمة", "s3cret-value" in X._SECRETS_PATH.read_text(encoding="utf-8"))

print("6) الحذف يفصل الخادم عن كل الموظفين")
r = client.delete("/api/admin/system/mcps/pkg_echo", headers=HD)
emp_after = BOOT._platform.repository.get(emp_cfg.id)
check("الحذف ← المجلد يُزال والموظف يُفصل", r.status_code == 200
      and not (X._CATALOG_DIR / "pkg_echo").exists() and "ext_pkg_echo" not in (emp_after.mcp_servers or []))

print("7) مهارات النظام (SKILL.md)")
r = client.get("/api/admin/system/skills", headers=HA)
names = {s["name"] for s in r.json()["skills"]}
check("المهارات المدمجة الثلاث معروضة", {"team-weekly-report", "campaign-planner", "employee-onboarding"} <= names)
skill_md = "---\nname: crisis-response\ndescription: إدارة أزمة سمعة على X\nallowed-tools: list_employees\n---\nالخطوات: 1) ...\n"
check("مسؤول لا يرفع مهارة (403)", client.post("/api/admin/system/skills/upload", headers=HA,
      files={"file": ("SKILL.md", skill_md.encode(), "text/markdown")}).status_code == 403)
r = client.post("/api/admin/system/skills/upload", headers=HD,
                files={"file": ("SKILL.md", skill_md.encode(), "text/markdown")})
check("المطوّر يرفع SKILL.md ← تُحفظ في system/skills", r.status_code == 200
      and (SS.SKILLS_DIR / "crisis-response" / "SKILL.md").exists(), r.text[:150])
check("تظهر فوراً في منسّق كل مستخدم", "crisis-response" in O._system_capabilities_block())
res = json.loads(O._Tools(U, "x").call("use_system_skill", '{"name":"crisis-response"}'))
check("use_system_skill يحمّل التعليمات الكاملة", "الخطوات" in res.get("instructions", ""), res)
bad = client.post("/api/admin/system/skills/upload", headers=HD,
                  files={"file": ("SKILL.md", b"no frontmatter", "text/markdown")})
check("SKILL.md بلا ترويسة ← مرفوض بسبب واضح", bad.status_code == 400 and "---" in bad.json()["detail"])
zbuf = io.BytesIO()
with zipfile.ZipFile(zbuf, "w") as zf:
    zf.writestr("s/SKILL.md", skill_md.replace("crisis-response", "zip-skill"))
    zf.writestr("s/../../evil.txt", "x")
check("zip مهارة بمسار ../ ← مرفوض", client.post("/api/admin/system/skills/upload", headers=HD,
      files={"file": ("s.zip", zbuf.getvalue(), "application/zip")}).status_code == 400)
client.patch("/api/admin/system/skills/crisis-response", headers=HD, json={"enabled": False})
check("التعطيل ← تختفي من المنسّق", "crisis-response" not in O._system_capabilities_block())
check("ولا تُحمَّل بالأداة", "error" in json.loads(O._Tools(U, "x").call("use_system_skill", '{"name":"crisis-response"}')))
edited = skill_md.replace("الخطوات: 1) ...", "خطوات معدّلة")
r = client.patch("/api/admin/system/skills/crisis-response", headers=HD, json={"content": edited})
check("تعديل نص المهارة من اللوحة", r.status_code == 200 and "خطوات معدّلة" in SS.raw("crisis-response"))
r = client.delete("/api/admin/system/skills/crisis-response", headers=HD)
check("الحذف", r.status_code == 200 and not (SS.SKILLS_DIR / "crisis-response").exists())

print("8) إدارة الأدوار من اللوحة")
r = client.put(f"/api/admin/users/{U}", headers=HA, json={"role": "developer"})
check("المسؤول لا يمنح دور المطوّر (403)", r.status_code == 403)
r = client.put(f"/api/admin/users/{U}", headers=HA, json={"role": "admin"})
check("المسؤول يمنح «مسؤول» ← صف كامل (كان خطأ 500)", r.status_code == 200 and r.json()["role"] == "admin"
      and "conversations_count" in r.json(), r.text[:150])
r = client.put(f"/api/admin/users/{D}", headers=HD, json={"role": "user"})
check("لا أحد يغيّر دوره بنفسه", r.status_code == 400)
r = client.put(f"/api/admin/users/{U}", headers=HD, json={"role": "developer"})
check("المطوّر يمنح دور المطوّر", r.status_code == 200 and r.json()["role"] == "developer")
perms = client.get("/api/admin/permissions", headers=HA).json()
check("مصفوفة الصلاحيات تُعرض", {r["id"] for r in perms["roles"]} == {"user", "admin", "developer"}
      and any(p["id"] == "system.mcp.manage" and p["min_role"] == "developer" for p in perms["permissions"]))
ov = client.get("/api/admin/system/overview", headers=HA).json()
check("نظرة عامة: المستخدمون حسب الدور والقدرات", ov["users"]["total"] == 3
      and ov["skills"]["coordinator"] == 3 and ov["skills"]["employee"] == 11, ov.get("skills"))

print("9) المجلد الرئيسي الموحّد: كل الخوادم والمهارات في system/")
check("الخوادم المدمجة الثمانية تُحمَّل من system/mcps (لا تسجيل يدوي)",
      LOADED_BUILTINS == ["x", "playwright_x", "content", "memory", "browser", "news", "search", "image"],
      LOADED_BUILTINS)
import app.aeos.mcp.servers.playwright_x_mcp as OLD_PW  # noqa: E402
from app.core.system_loader import mcp_module  # noqa: E402

check("المسار القديم اسم بديل لنفس الوحدة (لا نسختان)", OLD_PW is mcp_module("playwright_x"))
servers = client.get("/api/admin/system/mcps", headers=HA).json()["servers"]
kinds = {s["slug"]: s["kind"] for s in servers}
check("قائمة واحدة: الأصلية والمرفوعة معاً", kinds.get("x") == "builtin" and kinds.get("echo") == "uploaded"
      and kinds.get("zipsrv") == "uploaded", kinds)
tools = O.system_mcp_tools()
check("المنسّق يتصل بالخوادم المدمجة الأصلية أيضاً",
      {"mcp__browser__read_page", "mcp__news__fetch_feed", "mcp__playwright_x__read_trends"} <= set(tools))
check("لا أدوات فعل على X ولا ذاكرة الموظفين للمنسّق",
      not any(t.startswith(("mcp__x__", "mcp__memory__")) for t in tools))
sch = next(s for s in O._system_mcp_schemas() if s["function"]["name"] == "mcp__playwright_x__read_trends")
check("قراءة X تتطلب حساباً (account)", "account" in sch["function"]["parameters"]["required"])
res = json.loads(O._Tools(U, "x").call("mcp__playwright_x__read_trends", '{"account": "someone_elses_acc"}'))
check("قراءة X بحساب ليس للمالك ← مرفوضة قبل أي تشغيل للمتصفح", "ليس من حساباتك" in res.get("error", ""), res)
res = json.loads(O._Tools(D, "x").call("mcp__playwright_x__read_trends", '{"account": "emp_u"}'))
check("حتى المطوّر لا يقرأ بحساب مستخدم آخر عبر منسّقه", "ليس من حساباتك" in res.get("error", ""), res)
r = client.post("/api/admin/system/mcps/upload", headers=HD,
                files={"file": ("x.py", SERVER_PY.encode(), "text/x-python")})
check("رفع خادم باسم خادم مدمج (x) ← مرفوض", r.status_code == 400 and "مدمج" in r.json()["detail"])
r1 = client.patch("/api/admin/system/mcps/x", headers=HD, json={"enabled": False})
r2 = client.delete("/api/admin/system/mcps/x", headers=HD)
check("الخادم المدمج الأصلي لا يُعدَّل ولا يُحذف من اللوحة (400)",
      r1.status_code == 400 and r2.status_code == 400 and (X._CATALOG_DIR / "x" / "server.py").exists())
from app.aeos.skills import builtin as BI  # noqa: E402

skills_loaded = [s.name for s in BI.default_skills()]
check("مهارات الموظفين الـ11 تُحمَّل من system/skills بترتيبها",
      skills_loaded[0] == "custom_skills" or len(skills_loaded) == 12, skills_loaded)
check("الترتيب محفوظ (الاقتباس/الترند قبل الإعجاب)",
      skills_loaded.index("trend_participation") < skills_loaded.index("timeline_engager"), skills_loaded)
from app.core.system_loader import load_module  # noqa: E402

check("الاسم القديم builtin.OnThisDaySkill = الفئة من المجلد نفسه",
      BI.OnThisDaySkill is SS.employee_skill_class("OnThisDaySkill"))
r = client.delete("/api/admin/system/skills/on_this_day", headers=HD)
check("مهارة موظف مدمجة لا تُحذف من اللوحة (400)", r.status_code == 400
      and (SS.SKILLS_DIR / "on_this_day" / "skill.py").exists())
emp_md = "---\nname: hack\ndescription: x\nkind: employee\nclass: Hack\n---\nbody\n"
r = client.post("/api/admin/system/skills/upload", headers=HD,
                files={"file": ("SKILL.md", emp_md.encode(), "text/markdown")})
check("رفع مهارة kind: employee ← مرفوض (المرفوع للمنسّق فقط)", r.status_code == 400)
res = json.loads(O._Tools(U, "x").call("use_system_skill", '{"name":"on_this_day"}'))
check("المنسّق لا يحمّل مهارة موظف كتعليمات", "error" in res)
check("كتالوج المنسّق: مهارات المنسّق فقط", "on_this_day" not in O._system_capabilities_block()
      and "team-weekly-report" in O._system_capabilities_block())

print("10) أدوات MCP خاصة بموظف — يضيفها مالكه ولا يراها غيره")
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

import app.aeos.mcp.employee_mcp as EM  # noqa: E402

EM._DIR = TMP / "data" / "employee_mcps"


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{PORT}/mcp")
            self.end_headers()
            return
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mid, method = msg.get("id"), msg.get("method")
        res = {}
        if method == "initialize":
            res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "http-echo"}}
        elif method == "tools/list":
            res = {"tools": [{"name": "lookup", "description": "بحث", "inputSchema": {
                "type": "object", "properties": {"query": {"type": "string"}}}}]}
        elif method == "tools/call":
            auth = self.headers.get("Authorization", "")
            res = {"content": [{"type": "text", "text": f"found:{msg['params']['arguments'].get('query')}|{auth}"}]}
        body = json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}).encode() if mid is not None else b""
        self.send_response(200 if mid is not None else 202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


_srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
PORT = _srv.server_address[1]
threading.Thread(target=_srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{PORT}/mcp"
# «مالك آخر» مستخدم عادي: المسؤول يتجاوز العزل عمداً (طبقة الملكية) فلا يصلح لاختبار العزل
P2, HA = _user("plain2@t.local", "user")
emp_a = EmployeeConfig(id=uuid.uuid4().hex, user_id=P2, platform="x", account_username="emp_a",
                       cookie_label="emp_a", persona=Persona(display_name="A"))
BOOT._platform.repository.save(emp_a)
base = f"/api/aeos/employees/{emp_cfg.id}/tools/private"

r = client.post(base, headers=HU, json={"name": "lk", "url": URL})
check("عنوان داخلي (127.0.0.1) ← مرفوض (لا وصول لخوادم النظام الداخلية)", r.status_code == 400
      and "داخلية" in r.json()["detail"], r.text[:150])
r = client.post(base, headers=HU, json={"name": "lk", "url": "http://169.254.169.254/latest"})
check("عنوان metadata السحابي ← مرفوض", r.status_code == 400)
stdio_cfg = json.dumps({"mcpServers": {"cmd": {"command": sys.executable, "args": [str(SERVER_FILE)]}}}).encode()
r = client.post(base + "/upload", headers=HU, files={"file": ("s.json", stdio_cfg, "application/json")})
check("خادم stdio من المالك ← مرفوض (تشغيل أوامر للمطوّر فقط)", r.status_code == 400 and "HTTP" in r.text, r.text[:150])

_real_check = EM.check_public_url
EM.check_public_url = lambda url: None          # محاكاة خادم عام (الخادم التجريبي محلي)
r = client.post(base, headers=HU, json={"name": "lookup", "url": URL, "headers": {"Authorization": "Bearer tok-123"},
                                         "description": "أداة خاصة"})
srv_name = r.json().get("server", {}).get("name", "") if r.status_code == 200 else ""
check("المالك يضيف أداة HTTP لموظفه ← تُكتشف أدواتها", r.status_code == 200
      and r.json()["server"]["tools"][0]["name"] == "lookup", r.text[:200])
check("اسمها في السجل يحمل معرّف الموظف", srv_name.startswith("ext_e") and EM.belongs_to(srv_name, emp_cfg.id), srv_name)
emp_now = BOOT._platform.repository.get(emp_cfg.id)
check("تُربط بالموظف تلقائياً", srv_name in (emp_now.mcp_servers or []))
check("قيمة الترويسة السرية لا تُعاد", "tok-123" not in r.text)
check("الأسرار في data/employee_mcps خارج git", "tok-123" in (EM._DIR / f"{EM._tag(emp_cfg.id)}.json").read_text(encoding="utf-8"))
res = TS.call_tool(emp_now, srv_name, "lookup", {"query": "نفط"})
check("الموظف يستدعي أداته الخاصة (والترويسة تصل للخادم)", "found:نفط|Bearer tok-123" in res.get("text", ""), res)
mine = client.get(f"/api/aeos/tools?employee_id={emp_cfg.id}", headers=HU).json()
other = client.get(f"/api/aeos/tools?employee_id={emp_a.id}", headers=HA).json()
check("تظهر في أدوات موظفها فقط", any(p["name"] == srv_name for p in mine["private"])
      and not any(p["name"] == srv_name for p in other["private"] + other["external"]))
r = client.post(f"/api/aeos/employees/{emp_a.id}/tools", headers=HA, json={"server": srv_name, "attach": True})
check("مالك آخر لا يربط أداة موظف غيره بموظفه (400)", r.status_code == 400, r.text[:120])
try:
    TS.call_tool(BOOT._platform.repository.get(emp_a.id), srv_name, "lookup", {})
    leaked = True
except ValueError:
    leaked = False
check("موظف آخر لا يستدعيها", not leaked)
check("مستخدم آخر لا يحذفها (404 — الموظف ليس له)",
      client.delete(f"{base}/{srv_name}", headers=HA).status_code == 404)
check("ليست من أدوات المنسّق النظامية", not any(t["server"] == srv_name for t in O.system_mcp_tools().values()))
up = json.dumps({"mcpServers": {"docs": {"type": "http", "url": URL}}}).encode()
r = client.post(base + "/upload", headers=HU, files={"file": ("m.json", up, "application/json")})
check("رفع ملف إعداد JSON (http) للموظف", r.status_code == 200 and len(r.json()["saved"]) == 1, r.text[:150])
try:
    EM.save(emp_cfg.id, {"name": "redir", "url": f"http://127.0.0.1:{PORT}/redirect"})
    redirected = True
except Exception as exc:  # noqa: BLE001
    redirected = "تحويل" not in str(exc) and "302" not in str(exc)
check("تحويل (302) من الخادم ← مرفوض (لا SSRF عبر redirect)", not redirected)
EM.check_public_url = _real_check
cfg_x = EM.get(emp_cfg.id, srv_name)
r = EM.EmployeeMCPServer(cfg_x).call(__import__("app.aeos.mcp.base", fromlist=["MCPRequest"]).MCPRequest(
    employee_id=emp_cfg.id, tool=f"{srv_name}.lookup", params={"query": "x"}))
check("فحص العنوان يتكرر عند كل استدعاء (تغيّر DNS)", not r.success and "داخلية" in (r.error or ""), r.error)
r = client.delete(f"{base}/{srv_name}", headers=HU)
emp_now = BOOT._platform.repository.get(emp_cfg.id)
check("المالك يحذف أداته ← تُفصل وتُلغى من السجل", r.status_code == 200 and srv_name not in (emp_now.mcp_servers or [])
      and not BOOT._platform.registry.has(srv_name))
n = EM.purge(emp_cfg.id, BOOT._platform.registry)
check("حذف الموظف ← تُمحى أدواته الخاصة وأسرارها", n == 1 and not (EM._DIR / f"{EM._tag(emp_cfg.id)}.json").exists())
_srv.shutdown()

print()
shutil.rmtree(TMP, ignore_errors=True)
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص لوحة تحكم النظام نجحت")
