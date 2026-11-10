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
      and ov["skills"]["coordinator"] == 4 and ov["skills"]["employee"] == 14, ov.get("skills"))

print("9) المجلد الرئيسي الموحّد: كل الخوادم والمهارات في system/")
check("الخوادم المدمجة التسعة تُحمَّل من system/mcps (لا تسجيل يدوي)",
      LOADED_BUILTINS == ["x", "playwright_x", "content", "memory", "browser", "news", "pixabay", "search", "image"],
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
check("مهارات الموظفين الـ14 تُحمَّل من system/skills بترتيبها (+ مشغّل المخصّصة)",
      len(skills_loaded) == 15 and "system_tools" in skills_loaded
      and "unslop_guard" in skills_loaded and "stock_media" in skills_loaded, skills_loaded)
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
import queue  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

import app.aeos.mcp.employee_mcp as EM  # noqa: E402

EM._DIR = TMP / "data" / "employee_mcps"


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        # نقل SSE القديم: GET يفتح بثاً يرسل endpoint، والردود تصل أحداثاً على البث
        if self.path != "/legacy/sse":
            self.send_response(405)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"event: endpoint\ndata: /legacy/messages?sid=1\n\n")
        self.wfile.flush()
        while True:
            try:
                out = SSE_Q.get(timeout=20)
            except queue.Empty:
                return
            self.wfile.write(f"event: message\ndata: {json.dumps(out, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

    def do_POST(self):
        if self.path.startswith("/legacy/messages"):
            msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            mid, method = msg.get("id"), msg.get("method")
            if mid is not None:
                if method == "initialize":
                    res = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "sse-legacy"}}
                elif method == "tools/list":
                    res = {"tools": [{"name": "sse_lookup", "description": "بحث", "inputSchema": {
                        "type": "object", "properties": {"query": {"type": "string"}}}}]}
                else:
                    res = {"content": [{"type": "text", "text": f"sse:{msg['params']['arguments'].get('query')}"}]}
                SSE_Q.put({"jsonrpc": "2.0", "id": mid, "result": res})
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "mode=keyed" in self.path and self.headers.get("X-API-Key") != "good-key":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if "mode=oauth" in self.path:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer resource_metadata="https://x/.well-known/oauth"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/redirect":
            # اقرأ الجسم أولاً: الرد قبل قراءته يجعل ويندوز يرسل RST أحياناً فيصل العميلَ
            # «ConnectionReset» بدل 302 — فحص متقطّع الفشل بلا علاقة بالحماية نفسها
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{PORT}/mcp")
            self.end_headers()
            return
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mid, method = msg.get("id"), msg.get("method")
        mode = next((p[5:] for p in self.path.split("?")[-1].split("&") if p.startswith("mode=")), "")
        if mode in ("rest", "html"):   # ليس خادم MCP: واجهة REST / صفحة موقع ترد 200 على أي POST
            body = b'{"ok": true, "data": []}' if mode == "rest" else b"<html><body>Docs</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if mode == "rest" else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        res = {}
        if method == "initialize":
            res = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "http-echo"}}
            if mode == "notools":
                res["capabilities"] = {"resources": {}}
            elif mode == "noinit":
                res = {}
        elif method == "tools/list" and mode in ("empty", "notools"):
            res = {"tools": []}
        elif method == "tools/list":
            tools = [{"name": "lookup", "description": "بحث", "inputSchema": {
                "type": "object", "properties": {"query": {"type": "string"}}}}]
            if "full=1" in self.path:      # خادم بأدوات متعددة لاختبار التصنيف التلقائي
                tools += [
                    {"name": "fetch_page", "description": "Extract the content of a web page",
                     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}}},
                    {"name": "delete_everything", "description": "search and delete records",
                     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}},
                ]
            res = {"tools": tools}
        elif method == "tools/call":
            CALLS.append((msg["params"]["name"], msg["params"]["arguments"]))
            auth = self.headers.get("Authorization", "")
            xk = self.headers.get("X-API-Key", "")
            args = msg["params"]["arguments"]
            res = {"content": [{"type": "text", "text":
                                f"found:{args.get('query') or args.get('url')}|{auth}|xk={xk}|path={self.path}"}]}
        body = json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}).encode() if mid is not None else b""
        self.send_response(200 if mid is not None else 202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


CALLS: list = []
SSE_Q: "queue.Queue" = queue.Queue()
_srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
_srv.daemon_threads = True
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
redir_err = ""
try:
    EM.save(emp_cfg.id, {"name": "redir", "url": f"http://127.0.0.1:{PORT}/redirect"})
    redirected = True
except Exception as exc:  # noqa: BLE001
    redir_err = f"{type(exc).__name__}: {exc}"
    redirected = "تحويل" not in str(exc) and "302" not in str(exc)
check("تحويل (302) من الخادم ← مرفوض (لا SSRF عبر redirect)", not redirected, redir_err)
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

print("11) لوحة التحكم: إضافة سريعة بالمفتاح ونوع الاتصال + خادم داخلي يبنيه المطوّر")


def _sys_call(slug, query="q"):
    return O._Tools(U, "x").call(f"mcp__{slug}__lookup", json.dumps({"query": query}))


form = {"name": "quick_bearer", "transport": "http", "url": URL, "auth": {"mode": "bearer", "key": "k-bearer-1"}}
r = client.post("/api/admin/system/mcps/test-connection", headers=HD, json=form)
check("اختبار الاتصال قبل الحفظ ← يكتشف الأدوات ولا يحفظ", r.status_code == 200
      and [t["name"] for t in r.json()["tools"]] == ["lookup"] and not (X._CATALOG_DIR / "quick_bearer").exists(), r.text[:150])
check("المسؤول لا يضيف ولا يختبر (403)",
      client.post("/api/admin/system/mcps", headers=HA, json=form).status_code == 403
      and client.post("/api/admin/system/mcps/test-connection", headers=HA, json=form).status_code == 403)
r = client.post("/api/admin/system/mcps", headers=HD, json={**form, "allowed_tools": ["lookup"], "research_tool": "lookup"})
check("إضافة سريعة (Bearer) ← مثبّت ومدمج فوراً", r.status_code == 200 and BOOT._platform.registry.has("ext_quick_bearer"), r.text[:150])
check("المنسّق يستخدمه والمفتاح يصل في Authorization", "Bearer k-bearer-1" in _sys_call("quick_bearer"))
check("المفتاح لا يُعاد للواجهة ولا يُكتب في mcp.json",
      "k-bearer-1" not in r.text and "k-bearer-1" not in (X._CATALOG_DIR / "quick_bearer" / "mcp.json").read_text(encoding="utf-8"))

r = client.post("/api/admin/system/mcps", headers=HD, json={"name": "quick_header", "url": URL,
                                                             "auth": {"mode": "header", "name": "X-API-Key", "key": "k-hdr-2"}})
check("ترويسة مخصّصة (X-API-Key)", r.status_code == 200 and "xk=k-hdr-2" in _sys_call("quick_header"), r.text[:150])

r = client.post("/api/admin/system/mcps", headers=HD, json={"name": "quick_query", "url": URL,
                                                             "auth": {"mode": "query", "name": "apiKey", "key": "k-q-3"}})
man = (X._CATALOG_DIR / "quick_query" / "mcp.json").read_text(encoding="utf-8")
check("معامل في الرابط ← يصل للخادم ولا يُحفظ في الرابط", r.status_code == 200 and "apiKey=k-q-3" in _sys_call("quick_query")
      and "k-q-3" not in man and '"query_keys": [\n    "apiKey"' in man, man[:300])
r = client.post("/api/admin/system/mcps", headers=HD, json={"name": "pasted", "url": URL + "?tavilyApiKey=k-paste-4&lang=ar",
                                                             "auth": {"mode": "none"}})
man = (X._CATALOG_DIR / "pasted" / "mcp.json").read_text(encoding="utf-8")
out = _sys_call("pasted")
check("رابط بمفتاح ملصوق (مثل Tavily) ← يُنزع المفتاح تلقائياً ويُحفظ كسرّ",
      r.status_code == 200 and "k-paste-4" not in man and "lang=ar" in man and "tavilyApiKey=k-paste-4" in out, man[:250])
r = client.patch("/api/admin/system/mcps/quick_query", headers=HD, json={"query_secrets": {"apiKey": "k-q-NEW"}})
check("تحديث مفتاح معامل الرابط من اللوحة", r.status_code == 200 and "apiKey=k-q-NEW" in _sys_call("quick_query"))
r = client.post("/api/admin/system/mcps", headers=HD, json={"name": "bad", "url": URL, "auth": {"mode": "bearer", "key": ""}})
check("طريقة مفتاح بلا مفتاح ← رسالة واضحة (400)", r.status_code == 400 and "المفتاح" in r.json()["detail"])
r = client.post("/api/admin/system/mcps/test-connection", headers=HD, json={"name": "dead", "url": "http://127.0.0.1:1/mcp"})
check("خادم لا يرد ← 502 بسبب واضح ولا يُحفظ", r.status_code == 502 and not (X._CATALOG_DIR / "dead").exists())

tpl = client.get("/api/admin/system/mcps/template", headers=HD)
check("قالب الخادم الداخلي يُنزَّل", tpl.status_code == 200 and "tools/list" in tpl.text and "HANDLERS" in tpl.text)
r = client.post("/api/admin/system/mcps/upload", headers=HD, files={"file": ("my_tool.py", tpl.text.encode("utf-8"), "text/x-python")})
check("القالب نفسه خادم يعمل: يُرفع ويُثبّت ويستجيب", r.status_code == 200 and r.json()["saved"][0]["tools"][0]["name"] == "hello"
      and "أهلاً سعود" in O._Tools(U, "x").call("mcp__my_tool__hello", '{"name": "سعود"}'), r.text[:200])
r = client.post("/api/admin/system/mcps/upload", headers=HD, data={"env": "ECHO_KEY=pkg-secret-5"},
                files={"file": ("env_srv.py", SERVER_PY.encode(), "text/x-python")})
check("خادم داخلي بمفاتيح (متغيّرات بيئة) ← تصله وقت التشغيل ولا تُكتب في mcp.json",
      r.status_code == 200 and "env:pkg-secret-5" in O._Tools(U, "x").call("mcp__env_srv__secret_env", "{}")
      and "pkg-secret-5" not in (X._CATALOG_DIR / "env_srv" / "mcp.json").read_text(encoding="utf-8"))
from app.core.log_redaction import redact  # noqa: E402

check("مفتاح معامل الرابط محجوب من السجلات", "k-paste-4" not in redact(f"POST {URL}?tavilyApiKey=k-paste-4"))

print("12) الاتصال عبر مقتطف curl / Python / CLI كما تنشره المزوّدات")


def _snip(text, headers=HD):
    return client.post("/api/admin/system/mcps/parse-snippet", headers=headers, json={"text": text})


check("مستخدم عادي لا يستخدم الاستيراد (403)", _snip("curl https://x.com/mcp", HA).status_code == 403)
f = _snip('curl -X POST https://api.acme.io/mcp \\\n  -H "Authorization: Bearer real-tok-1" \\\n  -H "Accept: application/json, text/event-stream" '
          "-d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\"}'").json()
check("curl (Bearer + أسطر متصلة بـ\\) ← رابط ومفتاح", f["detected"] == "curl" and f["forms"][0]["url"] == "https://api.acme.io/mcp"
      and f["forms"][0]["auth"] == {"mode": "bearer", "name": "", "key": "real-tok-1"} and not f["forms"][0]["headers"], f)
f = _snip('curl https://api.acme.io/mcp -H "X-API-Key: sk_live_9"').json()["forms"][0]
check("curl بترويسة مخصّصة", f["auth"] == {"mode": "header", "name": "X-API-Key", "key": "sk_live_9"}, f["auth"])
f = _snip('curl.exe -X POST https://a.io/mcp `\n -H "Authorization: Bearer ps-1"').json()
check("curl.exe بأسطر PowerShell (`)", f["detected"] == "curl" and f["forms"][0]["url"] == "https://a.io/mcp"
      and f["forms"][0]["auth"]["key"] == "ps-1", f)
f = _snip('headers = {"Authorization": "Bearer py-1"}\nasync with streamablehttp_client("https://p.io/mcp", headers=headers) as (r, w, _):\n    pass').json()["forms"][0]
check("Python streamablehttp_client + متغيّر headers", f["url"] == "https://p.io/mcp" and f["auth"]["key"] == "py-1", f)
r = _snip('import os\nAPI_KEY = os.environ["ACME_KEY"]\nasync with httpx2.AsyncClient(headers={"Authorization": f"Bearer {API_KEY}"}) as hc:\n'
          '    t = streamable_http_client("https://n.io/mcp", http_client=hc)').json()
check("Python الحديث (httpx + مفتاح من البيئة) ← يطلب المفتاح ويوضح السبب",
      r["forms"][0]["url"] == "https://n.io/mcp" and r["forms"][0]["needs_key"] and any("ACME_KEY" in w for w in r["warnings"]), r)
r = _snip('client = MultiServerMCPClient({"math": {"command": "python", "args": ["m.py"], "transport": "stdio"},'
          ' "web": {"url": "https://w.io/mcp", "transport": "streamable_http", "headers": {"X-Api-Key": "w-1"}}})').json()
check("MultiServerMCPClient بخادمين (stdio + http)", [x["transport"] for x in r["forms"]] == ["stdio", "http"]
      and r["forms"][0]["name"] == "math" and r["forms"][1]["auth"]["key"] == "w-1", r["forms"])
f = _snip('Client("https://mcp.tavily.com/mcp/?tavilyApiKey=tvly-REAL1")').json()["forms"][0]
check("رابط بمفتاح في المعاملات ← طريقة «معامل في الرابط»",
      f["url"] == "https://mcp.tavily.com/mcp/" and f["auth"] == {"mode": "query", "name": "tavilyApiKey", "key": "tvly-REAL1"}, f)
f = _snip('claude mcp add --transport http zz https://z.io/mcp --header "Authorization: Bearer cl-1"').json()["forms"][0]
check("claude mcp add", f["name"] == "zz" and f["auth"]["key"] == "cl-1", f)
r = _snip('npx -y mcp-remote https://mcp.linear.app/sse --header "Authorization:Bearer lin-1"').json()
check("mcp-remote ← اتصال مباشر بالرابط بنقل SSE (صار مدعوماً)", r["forms"][0]["url"].endswith("/sse")
      and r["forms"][0]["auth"]["key"] == "lin-1" and r["forms"][0]["http_mode"] == "sse", r)
f = _snip("GITHUB_TOKEN=ghp_x1 uvx mcp-server-github").json()["forms"][0]
check("أمر stdio بمتغيّر قبله", f["transport"] == "stdio" and f["command"] == "uvx"
      and f["auth"] == {"mode": "env", "name": "GITHUB_TOKEN", "key": "ghp_x1"} and f["name"] == "github", f)
r = _snip('curl https://e.io/mcp -H "Authorization: Bearer your_acme_api_key"').json()
check("مفتاح المثال في التوثيق لا يُعتبر مفتاحاً", r["forms"][0]["needs_key"] and r["forms"][0]["auth"]["key"] == ""
      and any("مثال" in w for w in r["warnings"]), r)
marker = TMP / "pwned.txt"
evil = f'import os\nos.system("echo x > {marker.as_posix()}")\nopen("{marker.as_posix()}", "w").write("x")\nClient("https://safe.io/mcp")'
r = _snip(evil)
check("المقتطف لا يُنفَّذ أبداً (قراءة نحوية فقط)", r.status_code == 200 and not marker.exists())
check("مقتطف بلا رابط ولا أمر ← رسالة واضحة (400)", _snip("print('hello')").status_code == 400)

# من البداية للنهاية: لصق curl ← استخراج ← تثبيت ← المنسّق يستخدمه بالمفتاح
form = _snip(f'curl -X POST {URL} -H "Authorization: Bearer e2e-key-7"').json()["forms"][0]
r = client.post("/api/admin/system/mcps", headers=HD, json={**form, "name": "from_curl"})
check("لصق curl ← تثبيت ← المنسّق يتصل بالمفتاح الصحيح", r.status_code == 200 and "Bearer e2e-key-7" in _sys_call("from_curl"), r.text[:150])

print("12.5) «اتصال ناجح — 0 أداة» لم يعد ممكناً: تشخيص واضح بدل نجاح زائف")


def _tc(mode):
    return client.post("/api/admin/system/mcps/test-connection", headers=HD,
                       json={"name": "diag", "url": f"{URL}?mode={mode}"})


r = client.post("/api/admin/system/mcps/test-connection", headers=HD,
                json={"name": "diag", "url": f"http://127.0.0.1:{PORT}/api?mode=rest"})
check("رابط REST (/api) ← يُصحَّح تلقائياً إلى /mcp ويعمل، وتُبلَّغ الواجهة بالتعديل", r.status_code == 200
      and r.json()["form"]["url"] == f"http://127.0.0.1:{PORT}/mcp" and r.json()["tools"]
      and any("الرابط" in a for a in r.json()["adjusted"]), r.text[:250])
try:
    X.discover(X.normalize_config({"name": "d", "url": f"http://127.0.0.1:{PORT}/api?mode=rest"}))
    rest_err = ""
except Exception as exc:  # noqa: BLE001
    rest_err = str(exc)
check("وبلا اكتشاف تلقائي: رسالة «ليس JSON-RPC» مع اقتراح /mcp", "ليس JSON-RPC" in rest_err
      and f"127.0.0.1:{PORT}/mcp" in rest_err, rest_err)
r = client.post("/api/admin/system/mcps/test-connection", headers=HD, json={
    "name": "keyed", "url": f"{URL}?mode=keyed", "auth": {"mode": "bearer", "key": "good-key"}})
check("مكان المفتاح خاطئ (Bearer والخادم يريد X-API-Key) ← يُكتشف ويُصحَّح تلقائياً", r.status_code == 200
      and r.json()["form"]["auth"] == {"mode": "header", "name": "X-API-Key"}
      and any("X-API-Key" in a for a in r.json()["adjusted"]), r.text[:300])
r = client.post("/api/admin/system/mcps/test-connection", headers=HD, json={
    "name": "keyed", "url": f"{URL}?mode=keyed", "auth": {"mode": "bearer", "key": "wrong"}})
check("مفتاح خاطئ في كل الأماكن ← 401 واضح (المفتاح…) لا نجاح", r.status_code == 502 and "401" in r.text
      and "المفتاح" in r.text, r.text[:250])
r = client.post("/api/admin/system/mcps", headers=HD, json={
    "name": "keyedsrv", "url": f"{URL}?mode=keyed", "auth": {"mode": "bearer", "key": "good-key"}})
check("«تثبيت» مباشرة بلا اختبار ← يُحفظ بالإعداد المصحَّح", r.status_code == 200
      and X.get("keyedsrv")["headers"].get("X-API-Key") == "good-key", r.text[:200])
try:
    X.discover(X.normalize_config({"name": "o", "url": f"{URL}?mode=oauth"}))
    oauth_err = ""
except Exception as exc:  # noqa: BLE001
    oauth_err = str(exc)
check("رابط يتطلب OAuth ← رسالة تشرح ذلك وتطلب رابط مفتاح API", "OAuth" in oauth_err, oauth_err)
r = client.post("/api/admin/system/mcps/test-connection", headers=HD,
                json={"name": "legacy", "url": f"http://127.0.0.1:{PORT}/legacy/sse"})
check("خادم SSE القديم (/sse) ← يعمل الآن (كان مرفوضاً)", r.status_code == 200
      and [t["name"] for t in r.json()["tools"]] == ["sse_lookup"] and r.json()["form"]["http_mode"] == "sse", r.text[:250])
r = client.post("/api/admin/system/mcps", headers=HD,
                json={"name": "legacysse", "url": f"http://127.0.0.1:{PORT}/legacy/sse"})
sse_out = O._Tools(U, "x").call("mcp__legacysse__sse_lookup", json.dumps({"query": "نفط"})) if r.status_code == 200 else ""
check("تثبيت خادم SSE + المنسّق يستدعي أداته", "sse:نفط" in sse_out, (r.text[:150], sse_out[:150]))
# حالة TrendsMCP الحقيقية: التوثيق يعرض curl لواجهة REST (/api) — خادم MCP على /mcp
r = _snip('curl -X POST https://api.trendsmcp.ai/api -H "Authorization: Bearer YOUR_API_KEY" '
          '-H "Content-Type: application/json" -d \'{"mode":"get_time_series","source":"google search"}\'').json()
check("مقتطف curl لواجهة REST ← الرابط يصير /mcp مع تنبيه، والمفتاح يُطلب",
      r["forms"][0]["url"] == "https://api.trendsmcp.ai/mcp" and r["forms"][0]["needs_key"]
      and any("REST" in w for w in r["warnings"]), r)
r = _snip('import requests\nres = requests.post("https://api.trendsmcp.ai/api", headers={"Authorization": "Bearer k1"},'
          ' json={"mode": "get_time_series"})').json()
check("ونفسه بكود Python (requests.post)", r["forms"][0]["url"] == "https://api.trendsmcp.ai/mcp"
      and r["forms"][0]["auth"]["key"] == "k1", r["forms"])
f = _snip('curl -X POST https://m.io/mcp -d \'{"jsonrpc":"2.0","id":1,"method":"initialize"}\'').json()["forms"][0]
check("curl لخادم MCP حقيقي (جسم JSON-RPC) لا يُغيَّر رابطه", f["url"] == "https://m.io/mcp")
r = _tc("html")
check("صفحة ويب ← «أعاد صفحة ويب (HTML)… غالباً ينتهي بـ /mcp»", r.status_code == 502 and "HTML" in r.text
      and "/mcp" in r.text, r.text[:200])
r = _tc("noinit")
check("رد initialize بلا protocolVersion/capabilities ← لا يتكلم MCP", r.status_code == 502
      and "protocolVersion" in r.text, r.text[:200])
r = _tc("notools")
check("خادم MCP بلا قدرة tools ← يوضح أنه يقدّم موارد فقط", r.status_code == 400 and "لا يقدّم أدوات" in r.text, r.text[:200])
r = _tc("empty")
check("خادم MCP أعاد 0 أداة ← 400 بالأسباب (المفتاح/طريقة الإرسال/أدوات الحساب)", r.status_code == 400
      and "0 أداة" in r.text and "المفتاح" in r.text, r.text[:200])
r = client.post("/api/admin/system/mcps", headers=HD, json={"name": "emptysrv", "url": f"{URL}?mode=empty"})
check("ولا يُثبَّت خادم بلا أدوات", r.status_code == 400 and not any(c["slug"] == "emptysrv" for c in X.list_configs()))
check("خادم سليم ما زال يعمل", _tc("").status_code == 200 and _tc("").json()["tools"])

print("13) مهارة تلقائية: الموظف يستخدم أي أداة تُضاف للنظام (مثل Tavily) دون إعداد")
from app.aeos.employee import research as RS  # noqa: E402
from app.aeos.employee import tool_use as TU  # noqa: E402
from verify_creator import make_config, make_context  # noqa: E402

C = TU.classify
check("تصنيف تلقائي: tavily_search ← بحث", C({"name": "tavily_search", "inputSchema": {"properties": {"query": {}}}})
      == {"role": "search", "arg": "query"})
check("تصنيف تلقائي: tavily_extract ← قراءة روابط", C({"name": "tavily_extract", "inputSchema": {"properties": {"urls": {}}}})
      == {"role": "extract", "arg": "urls"})
check("أدوات الكتابة/الحذف لا يستدعيها الموظف أبداً",
      C({"name": "delete_everything", "description": "search", "inputSchema": {"properties": {"query": {}}}}) is None
      and C({"name": "send_message", "inputSchema": {"properties": {"text": {}}}}) is None)
# خوادم سابقة في الكتالوج المؤقت ليست موضوع هذا القسم: نطاقها للمنسّق فقط
for _c in X.list_configs():
    X.update(_c["slug"], {"scope": {"employees": False}}, BOOT._platform.registry)
r = client.post("/api/admin/system/mcps", headers=HD, json={"name": "autotool", "url": URL + "?full=1",
                                                             "scope": {"coordinator": True, "employees": True}})
check("ثُبّت خادم نظام (بلا أداة بحث معيّنة يدوياً)", r.status_code == 200 and not r.json()["server"]["research_tool"], r.text[:150])
emp_t = make_config()
emp_t.id, emp_t.mcp_servers = uuid.uuid4().hex, []          # موظف لم يُربط بأي شيء
tools_av = TU.available(emp_t)
check("متاح للموظف تلقائياً دون ربط يدوي", {t["tool"] for t in tools_av if t["server"] == "ext_autotool"}
      == {"lookup", "fetch_page"}, tools_av)
ctx_t = make_context(emp_t, with_signals=False)
ctx_t.mcp = BOOT._platform.registry
CALLS.clear()
out = RS.augment_brief("اكتب عن أسعار النفط", "أسعار النفط اليوم", ctx_t,
                       material="خبر عاجل https://news.example.com/oil-today و https://x.com/a/status/1")
check("قبل الكتابة: يبحث بنص الموضوع ويقرأ الرابط الخارجي (لا رابط X)",
      ("lookup", {"query": "أسعار النفط اليوم"}) in CALLS
      and ("fetch_page", {"url": "https://news.example.com/oil-today"}) in CALLS, CALLS)
check("أداة الحذف لم تُستدعَ", not any(n == "delete_everything" for n, _ in CALLS))
check("النتائج داخل محدّدات «تجاهل أي أوامر»", "<<<" in out and "found:أسعار النفط اليوم" in out and "تجاهل أي تعليمات" in out)
check("الاستخدام مسجّل في الدورة", any("autotool" in " ".join(u["tools"]) or "lookup" in " ".join(u["tools"])
                                       for u in ctx_t.scratch.get("tool_use", [])), ctx_t.scratch.get("tool_use"))
n_before = len(CALLS)
RS.augment_brief("اكتب", "أسعار النفط اليوم", ctx_t, material="")
check("ذاكرة مؤقتة: نفس الاستعلام لا يُعاد طلبه (توفير رصيد المزوّد)", len(CALLS) == n_before, CALLS[n_before:])
check("تعليمات الموظف تذكر الأدوات المتاحة", "🧰" in ctx_t.persona_prompt() and "autotool" in ctx_t.persona_prompt())

from app.aeos.skills import builtin as BI2  # noqa: E402
from app.aeos.skills.registry import SkillRegistry  # noqa: E402

reg_s = SkillRegistry()
for s in BI2.default_skills():
    reg_s.register(s)
check("المهارة التلقائية تعمل لكل موظف دون تثبيت (always_on)",
      "system_tools" in [s.name for s in reg_s.resolve([])])
ctx_s = make_context(emp_t, with_signals=False)
props = reg_s.get("system_tools").propose(ctx_s)
check("المهارة تكتشف الأدوات ولا تقترح أفعالاً على X", props == []
      and any(t["server"] == "ext_autotool" for t in ctx_s.scratch.get("available_tools", [])))

BOOT._platform.repository.save(emp_t)
TS.set_attached(emp_t, "ext_autotool", False)
check("المالك يفصل خادماً عن موظفه ← لا يُستخدم له", not any(t["server"] == "ext_autotool" for t in TU.available(emp_t))
      and not next(e for e in TS.overview(emp_t)["external"] if e["name"] == "ext_autotool")["attached"])
TS.set_attached(emp_t, "ext_autotool", True)
check("وإعادة ربطه ← يعود", any(t["server"] == "ext_autotool" for t in TU.available(emp_t)))
X.update("autotool", {"scope": {"employees": False}}, BOOT._platform.registry)
check("نطاق «الموظفين» مُطفأ في اللوحة ← لا يُستخدم لأي موظف", not TU.available(emp_t))
X.update("autotool", {"scope": {"employees": True}}, BOOT._platform.registry)
from app.aeos.capabilities.handlers import _with_tool_research  # noqa: E402

CALLS.clear()
qp = _with_tool_research("PROMPT", "@someone عاجل: ارتفاع النفط 5% https://news.example.com/x #نفط", ctx_t)
check("الاقتباس/الرد: بحث بنص التغريدة بلا منشن/هاشتاق/رابط + قراءة رابطها",
      qp.startswith("PROMPT") and ("lookup", {"query": "عاجل: ارتفاع النفط 5%"}) in CALLS
      and any(n == "fetch_page" for n, _ in CALLS), CALLS)
_srv.shutdown()

print()
shutil.rmtree(TMP, ignore_errors=True)
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص لوحة تحكم النظام نجحت")
