"""
verify_isolation.py — فحص لياقة معمارية للعزل والذاكرة، على قاعدة بيانات مؤقتة.

لا يلمس بياناتك ولا X ولا الشبكة. ثلاث طبقات:

1. **كل نقطة في التطبيق** (يُعدَّد تلقائياً من ``app.routes``) ترفض الطلب المجهول —
   إلا قائمة عامة صريحة. نقطة جديدة تُنسى حمايتها تُسقط هذا الفحص فوراً.
2. **العزل بين مستخدمَين**: B لا يقرأ ولا يعدّل ولا يحذف موظفي A أو حساباته أو
   محادثاته أو ذاكرته، ولا ينتحله عبر الـWebSocket.
3. **دورة حياة الذاكرة**: حذف موظف/محادثة/مستخدم لا يترك بيانات يتيمة.

    .\\venv\\Scripts\\python.exe verify_isolation.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid

os.environ.setdefault("TOPIC_SEMANTIC", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine  # noqa: E402

import app.db.database as DB  # noqa: E402

# ── قاعدة مؤقتة: كل SessionLocal في التطبيق هو الكائن نفسه، فإعادة ربطه تكفي ──
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
_engine = create_engine(f"sqlite:///{_tmp.name}", connect_args={"check_same_thread": False})
DB.engine = _engine
DB.SessionLocal.configure(bind=_engine)
from app.db import models as M  # noqa: E402

DB.Base.metadata.create_all(bind=_engine)

from fastapi.testclient import TestClient  # noqa: E402

import app.aeos.bootstrap as BOOT  # noqa: E402
from app.aeos.employee.models import EmployeeConfig, Persona  # noqa: E402
from app.aeos.employee.repository import SqlEmployeeRepository  # noqa: E402
from app.auth.security import create_access_token  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


class _Graph:
    def __init__(self):
        self.cleared = []

    def clear(self, eid):
        self.cleared.append(eid)

    def stats(self, eid):
        return {"episodes": 0}

    def search(self, *a, **k):
        return []

    def recent_facts(self, *a, **k):
        return []


class FakePlatform:
    """منصة خفيفة: المستودع الحقيقي على القاعدة المؤقتة، بلا متصفح/رسم/جدولة."""

    def __init__(self):
        self.repository = SqlEmployeeRepository()
        self.graph_memory = _Graph()
        self.llm = None

    def delete_employee(self, eid):
        self.graph_memory.clear(eid)
        return self.repository.delete(eid)

    def stats(self):
        return {"skills": [], "mcp_servers": [], "capabilities": []}

    def agents_overview(self):
        agents = [{"account_username": c.account_username, "enabled": True}
                  for c in self.repository.list_all()]
        return {"total": len(agents), "running": len(agents), "agents": agents}


BOOT._platform = FakePlatform()
from app.main import app  # noqa: E402

client = TestClient(app)


# ── بيانات: مستخدمان، لكلٍّ موظف وحساب ومحادثة وذكريات ──
def _user(email, admin=False):
    db = DB.SessionLocal()
    u = M.User(email=email, password_hash="x", is_admin=admin)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    # نفس صيغة تسجيل الدخول الحقيقي (app.auth.routes.login)
    return u.id, {"Authorization": f"Bearer {create_access_token({'sub': str(u.id), 'email': email})}"}


A, HA = _user("a@test.local")
B, HB = _user("b@test.local")
db = DB.SessionLocal()
db.add(M.SocialAccount(user_id=A, platform="x", username="acct_a", account_label="acct_a",
                       cookie_filename="acct_a.json"))
db.commit()
db.close()
emp_a = EmployeeConfig(id=uuid.uuid4().hex, user_id=A, platform="x", account_username="acct_a",
                       cookie_label="acct_a", persona=Persona(display_name="A"))
BOOT._platform.repository.save(emp_a)

from app.aeos.memory import agent_memory as AM  # noqa: E402
from app.aeos.memory import lifecycle as LC  # noqa: E402
from app.services.memory_service import memory_service  # noqa: E402

db = DB.SessionLocal()
conv_a = memory_service.get_or_create_conversation(db=db, user_id=A, session_id="emp_acct_a")
memory_service.add_message(db=db, conversation_id=conv_a.id, role="user", content="سر المالك A")
db.add(M.AgentMemory(scope=AM.employee_scope(emp_a.id), kind="instruction", content="تعليمة A"))
db.add(M.AgentMemory(scope=AM.coordinator_scope(A), kind="preference", content="تفضيل A"))
db.add(M.AgentMemory(scope="x", kind="summary", key=AM._summary_key(conv_a.id), content="ملخّص A"))
db.add(M.EmployeeMemoryRecord(employee_id=emp_a.id, layer="working", content="طبقة A"))
db.add(M.ExecutionLogRecord(employee_id=emp_a.id, cycle_id="c1"))
db.commit()
CONV_A = conv_a.id
db.close()

# ─────────────────────────── 1) fitness: every route rejects anonymous ───────────────────────────
print("1) كل نقطة ترفض المجهول (تعداد تلقائي من التطبيق)")
#: عامة عن قصد — كل إضافة هنا قرار يُراجَع
PUBLIC = {
    ("GET", "/"), ("GET", "/health"), ("GET", "/api/aeos/health"), ("GET", "/api/x/health"),
    ("GET", "/api/agent/health"), ("POST", "/api/auth/register"), ("POST", "/api/auth/login"),
    ("GET", "/api/auth/verify"),
    # قوائم ثابتة بلا بيانات مستخدم (أسماء النوايا واقتراحات نصية)
    ("POST", "/api/intent/suggestions"), ("GET", "/api/intent/list"),
    ("GET", "/{full_path:path}"),
}
#: محمية بتوقيع/سرّ لا بتوكن مستخدم (تُفحص في القسم 4)
SIGNED = {("POST", "/api/internal/login-callback"), ("POST", "/api/webhooks/schedule_callback"),
          ("POST", "/api/telegram/webhook/{webhook_token}")}
open_routes, checked = [], 0
for r in app.routes:
    methods = getattr(r, "methods", None) or set()
    path = getattr(r, "path", "")
    if not methods or path.startswith(("/docs", "/redoc", "/openapi", "/static", "/assets")):
        continue
    for m in sorted(methods - {"HEAD", "OPTIONS"}):
        if (m, path) in PUBLIC or (m, path) in SIGNED:
            continue
        url = path
        for seg in [s for s in path.split("/") if s.startswith("{")]:
            url = url.replace(seg, "1")
        resp = client.request(m, url, json={})
        checked += 1
        if resp.status_code not in (401, 403):
            open_routes.append(f"{m} {path} → {resp.status_code}")
check(f"{checked} نقطة محمية: كلها 401/403 بلا توكن", not open_routes, "; ".join(open_routes[:8]))

# ─────────────────────────── 2) cross-tenant ───────────────────────────
print("2) المستخدم B لا يصل لأي شيء يخص A")
E = emp_a.id
for method, url, body in [
    ("GET", f"/api/aeos/employees/{E}", None), ("GET", f"/api/aeos/employees/{E}/logs", None),
    ("GET", f"/api/aeos/employees/{E}/insights", None), ("POST", f"/api/aeos/employees/{E}/ask", {"question": "?"}),
    ("GET", f"/api/aeos/employees/{E}/graph-memory", None), ("GET", f"/api/aeos/employees/{E}/critique", None),
    ("PATCH", f"/api/aeos/employees/{E}/status?status=paused", None),
    ("POST", f"/api/aeos/employees/{E}/trigger", {}), ("GET", f"/api/aeos/memory/overview?employee_id={E}", None),
    ("GET", f"/api/aeos/memory?employee_id={E}", None), ("DELETE", f"/api/aeos/employees/{E}", None),
    ("DELETE", "/api/aeos/agent?account_username=acct_a", None),
    ("GET", f"/api/conversations/{CONV_A}", None), ("GET", f"/api/conversations/{CONV_A}/context", None),
    ("DELETE", f"/api/conversations/{CONV_A}", None),
    ("POST", "/api/x/post", {"label": "acct_a", "text": "hijack"}),
    ("DELETE", "/api/x/accounts/acct_a", None),
    ("POST", "/api/schedule-event", {"platform": "x", "username": "acct_a", "category": "c", "content": "x"}),
]:
    resp = client.request(method, url, json=body, headers=HB)
    check(f"B ← {method} {url.split('?')[0].replace(E, '{A}')} = 404", resp.status_code == 404,
          f"{resp.status_code} {resp.text[:120]}")
check("الموظف ما زال موجوداً بعد محاولة B حذفه", BOOT._platform.repository.get(E) is not None)
check("قائمة موظفي B لا تحوي موظف A",
      client.get("/api/aeos/employees", headers=HB).json()["employees"] == [])
check("نظرة الوكلاء لـB فارغة", client.get("/api/aeos/agents", headers=HB).json()["agents"] == [])
r = client.post("/api/aeos/agent/toggle", json={"account_username": "acct_a", "enabled": True}, headers=HB)
check("B لا يشغّل وكيلاً على حساب A", r.status_code == 404, r.status_code)
r = client.post("/api/aeos/employees", json={"account_username": "acct_a", "user_id": B}, headers=HB)
check("B لا ينشئ موظفاً على كوكيز A (ولو أرسل user_id)", r.status_code == 404, r.status_code)
check("A يصل لموظفه (المالك ليس محجوباً)",
      client.get(f"/api/aeos/employees/{E}", headers=HA).status_code == 200)
check("A يرى ملخّص ذاكرة موظفه الموحّد",
      client.get(f"/api/aeos/memory/overview?employee_id={E}", headers=HA).json()
      .get("long_term", {}).get("total") == 1)
from app.auth.ownership import visible_labels  # noqa: E402

ua = DB.SessionLocal().query(M.User).get(A)
ub = DB.SessionLocal().query(M.User).get(B)
check("حساباتي: كل مستخدم يرى ملفاته فقط", visible_labels(["acct_a", "orphan"], ua) == ["acct_a"]
      and visible_labels(["acct_a", "orphan"], ub) == [])

print("3) الـWebSocket يوثّق بالتوكن ولا يقبل الانتحال")
from starlette.websockets import WebSocketDisconnect  # noqa: E402

for label, url in [("بلا توكن", "/ws/chat"), ("توكن مزوّر", "/ws/chat?token=not-a-jwt")]:
    try:
        with client.websocket_connect(url) as ws:
            ws.receive_json()
        check(f"{label} ← يُغلق 4401", False, "لم يُغلق")
    except WebSocketDisconnect as exc:
        check(f"{label} ← يُغلق 4401", exc.code == 4401, exc.code)
import app.main as MAIN  # noqa: E402

tok_a = HA["Authorization"].split()[1]
with client.websocket_connect(f"/ws/chat?token={tok_a}") as ws:
    ws.send_json({"type": "ping", "user_id": B})
    pong = ws.receive_json()
    owners = set(MAIN.manager.user_ids.values())
check("رسالة تدّعي user_id=B على اتصال A تُنسب لـA", pong.get("type") == "pong" and owners == {A}, owners)

print("4) الذاكرة: محادثات الجلسة معزولة بالمالك، والـcallbacks موقّعة")
db = DB.SessionLocal()
cb = memory_service.get_or_create_conversation(db=db, user_id=B, session_id="emp_acct_a")
check("نفس session_id لمستخدم آخر ← محادثة مختلفة (لا يرث سجل A)", cb.id != CONV_A and cb.user_id == B)
db.close()
from app.auth.internal import sign, signed_url, verify  # noqa: E402

check("توقيع callback صحيح يُقبل والمعدَّل يُرفض",
      verify("login", 5, sign("login", 5)) and not verify("login", 6, sign("login", 5))
      and not verify("schedule", 5, sign("login", 5)))
check("signed_url يضيف التوقيع", "sig=" in signed_url("http://h/x", "login", 1))
r = client.post("/api/webhooks/schedule_callback", json={"event_id": "e1", "cookie_label": "a",
                                                          "content": "x", "success": True})
check("webhook الجدولة بلا توقيع ← 403", r.status_code == 403, r.status_code)
r = client.post("/api/internal/login-callback", json={"user_id": A, "success": True})
check("callback الدخول بلا توقيع ← 403 (لا تزوير إشعارات في شات أحد)", r.status_code == 403, r.status_code)

print("5) دورة حياة الذاكرة: لا بيانات يتيمة")
check("قبل الحذف: لا أيتام", LC.orphan_report() == {"layer_memories_dead_employee": 0}, LC.orphan_report())
client.delete(f"/api/conversations/{CONV_A}", headers=HA)
db = DB.SessionLocal()
check("حذف المحادثة يحذف ملخّصها (لا يرثه معرّف مُعاد استخدامه)",
      db.query(M.AgentMemory).filter(M.AgentMemory.key == AM._summary_key(CONV_A)).count() == 0)
db.close()
r = client.delete(f"/api/aeos/employees/{E}", headers=HA)
db = DB.SessionLocal()
left = {
    "agent_memories": db.query(M.AgentMemory).filter(M.AgentMemory.scope == AM.employee_scope(E)).count(),
    "layers": db.query(M.EmployeeMemoryRecord).filter(M.EmployeeMemoryRecord.employee_id == E).count(),
    "logs": db.query(M.ExecutionLogRecord).filter(M.ExecutionLogRecord.employee_id == E).count(),
}
db.close()
check("حذف الموظف يمحو ذاكرته طويلة المدى وطبقاته وسجلاته", r.status_code == 200 and not any(left.values()), left)
check("وحذف رسمه المعرفي", E in BOOT._platform.graph_memory.cleared)
emp_b = EmployeeConfig(id=uuid.uuid4().hex, user_id=B, platform="x", account_username="acct_b",
                       cookie_label="acct_b", persona=Persona(display_name="B"))
BOOT._platform.repository.save(emp_b)
db = DB.SessionLocal()
db.add(M.AgentMemory(scope=AM.employee_scope(emp_b.id), kind="fact", content="B"))
db.add(M.AgentMemory(scope=AM.coordinator_scope(B), kind="fact", content="B"))
db.commit()
counts = LC.purge_user(B, db)
db.commit()
db.close()
check("purge_user يمحو موظفي المستخدم ومحادثاته وذاكرة منسّقه",
      counts.get("employees") == 1 and counts.get("conversations", 0) >= 1
      and counts.get("coordinator_memories") == 1, counts)
check("بعد كل الحذف: صفر أيتام", not any(LC.orphan_report().values()), LC.orphan_report())

print()
os.unlink(_tmp.name) if os.path.exists(_tmp.name) and not _engine.dispose() else None
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص العزل والذاكرة نجحت")
