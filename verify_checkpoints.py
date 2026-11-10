"""
verify_checkpoints.py — نقاط الاستعادة عند انقطاع الاتصال وإعادة التشغيل، على قاعدة مؤقتة.

لا يلمس بياناتك ولا X ولا الشبكة ولا أي نموذج. ما يفحصه (كل بند خلل قيس قبل الإصلاح):
1. السجل الدائم: المهمة المكررة لا تُنفَّذ مرتين، الاستئناف ذرّي، والإقلاع يعلّم الجاري «مقطوعاً».
2. الصندوق الصادر: أرقام متزايدة، الإعادة بعد آخر رقم فقط، معزول بين المستخدمين.
3. التسليم الحي: رد مهمة مات سوكتها يذهب للاتصال الذي عاد، ولا يُدفع لغير صاحبه أبداً
   (كان يُدفع لكل الاتصالات المفتوحة إن لم يكن صاحبه متصلاً).
4. دورة الموظف: نقطة لكل مرحلة، والأفعال الناجحة تُحفظ فوراً، والاستئناف لا يكرّر فعلاً على X
   ويحتسب الأفعال السابقة في ميزانية اليوم.
5. الـWebSocket: مهمة الشات + منع التكرار + إعادة ما فات + استئناف/تجاهل + الملكية + إشعار الإقلاع.

    .\\venv\\Scripts\\python.exe verify_checkpoints.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TOPIC_SEMANTIC", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine  # noqa: E402

import app.db.database as DB  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
_auth_dir = tempfile.mkdtemp()
import app.auth.internal as _INT  # noqa: E402
import app.auth.token_guard as _TG  # noqa: E402

_TG._PATH = Path(_auth_dir) / "auth_revocations.json"
_TG._state = None
_INT._USED_PATH = os.path.join(_auth_dir, "nonces.json")
_engine = create_engine(f"sqlite:///{_tmp.name}", connect_args={"check_same_thread": False})
DB.engine = _engine
DB.SessionLocal.configure(bind=_engine)
from app.db import models as M  # noqa: E402

DB.Base.metadata.create_all(bind=_engine)

from app.services import task_checkpoints as TC  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def _user(email):
    from app.auth.security import create_access_token

    db = DB.SessionLocal()
    u = M.User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    return u.id, create_access_token({"sub": str(u.id), "email": email})


A, TOK_A = _user("a@cp.local")
B, TOK_B = _user("b@cp.local")

# ─────────────────────────────────────────────────────────────────────
print("1) السجل الدائم للمهام")
t1, created = TC.begin("task-aaaa-0001", "chat", A, title="انشر عن الطقس", session_id="s1",
                       request={"message": "انشر عن الطقس", "session_id": "s1"})
check("مهمة جديدة ← running", created and t1["status"] == TC.RUNNING, t1)
again, created2 = TC.begin("task-aaaa-0001", "chat", A, title="x")
check("نفس المعرّف لنفس المستخدم ← لا تسجيل ثانٍ (منع التنفيذ المكرر)",
      not created2 and again["id"] == "task-aaaa-0001")
foreign, created3 = TC.begin("task-aaaa-0001", "chat", B, title="y")
check("معرّف يخص مستخدماً آخر ← مهمة جديدة بمعرّف مختلف لا تكشف مهمة A",
      created3 and foreign["id"] != "task-aaaa-0001" and TC.get(foreign["id"])["user_id"] == B)
check("معرّف غير صالح من المتصفح يُستبدل", TC.clean_id("../../x") != "../../x"
      and TC.clean_id("ok-id_123456") == "ok-id_123456")
check("claim_resume لا يعمل على مهمة غير مقطوعة", TC.claim_resume("task-aaaa-0001", A, TC.RUNNING) is None)

boot = TC.mark_interrupted_on_boot()
check("الإقلاع يعلّم كل running ← interrupted",
      {t["id"] for t in boot} >= {"task-aaaa-0001", foreign["id"]}
      and TC.get("task-aaaa-0001")["status"] == TC.INTERRUPTED)
check("B لا يستأنف مهمة A", TC.claim_resume("task-aaaa-0001", B, TC.RUNNING) is None)
claimed = TC.claim_resume("task-aaaa-0001", A, TC.RUNNING)
check("استئناف المالك ← running بطلبه الأصلي ومحاولة ثانية",
      claimed and claimed["status"] == TC.RUNNING and claimed["attempts"] == 2
      and claimed["request"]["message"] == "انشر عن الطقس", claimed)
check("ضغطة استئناف ثانية ← لا شيء (ذرّي)", TC.claim_resume("task-aaaa-0001", A, TC.RUNNING) is None)
done = TC.finish("task-aaaa-0001", TC.SUCCEEDED)
check("finish يغلق المهمة", done["status"] == TC.SUCCEEDED and done["finished_at"])
snap = TC.for_user(A)
check("لقطة المستخدم: مهامه فقط", snap and all(TC.get(t["id"])["user_id"] == A for t in snap))
check("لقطة المتصفح بلا الطلب الخام ولا الحالة الداخلية",
      all(not ({"request", "state", "user_id"} & set(v)) for v in TC.iter_payloads(snap)))

# ─────────────────────────────────────────────────────────────────────
print("2) الصندوق الصادر")
m1 = TC.outbox_record(A, {"type": "assistant_message", "message": "أ1"})
m2 = TC.outbox_record(A, {"type": "assistant_message", "message": "أ2"})
TC.outbox_record(B, {"type": "assistant_message", "message": "ب1"})
typing = TC.outbox_record(A, {"type": "typing", "status": True})
check("أرقام متزايدة", m2["seq"] > m1["seq"] > 0, (m1, m2))
check("مؤشر الكتابة لا يُخزّن (لا يعيد «يكتب…» قديماً)", "seq" not in typing)
since = TC.outbox_since(A, m1["seq"])
check("الإعادة بعد آخر رقم فقط", [p["message"] for p in since] == ["أ2"] and since[0]["replayed"])
check("معزول بين المستخدمين", all(p["message"] != "ب1" for p in TC.outbox_since(A, 0)))
check("آخر رقم للمستخدم", TC.outbox_latest(A) == m2["seq"])
from datetime import datetime, timedelta  # noqa: E402

check("التقليم يحذف الأقدم من 48 ساعة", TC.prune_outbox(datetime.utcnow() + timedelta(hours=49)) >= 3
      and TC.outbox_latest(A) == 0)

# ─────────────────────────────────────────────────────────────────────
print("3) التسليم الحي عبر ConnectionManager")
import app.aeos.bootstrap as BOOT  # noqa: E402
from app.aeos.employee.models import EmployeeConfig, Persona  # noqa: E402
from app.aeos.employee.repository import SqlEmployeeRepository  # noqa: E402


class FakePlatform:
    def __init__(self):
        self.repository = SqlEmployeeRepository()
        self.llm = None
        self.notified = []
        self.resumed = []
        self.graph_memory = SimpleNamespace(clear=lambda eid: None)

    def notify_owner(self, employee_id, text, intent="aeos_cycle_progress"):
        self.notified.append((employee_id, text, intent))
        return True

    def resume_cycle(self, task):
        self.resumed.append(task["id"])
        return {}

    def delete_employee(self, eid):
        return self.repository.delete(eid)


BOOT._platform = FakePlatform()
import app.main as MAIN  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeWS:
    def __init__(self, dead=False):
        self.dead, self.sent = dead, []

    async def send_json(self, msg):
        if self.dead:
            raise RuntimeError("socket closed")
        self.sent.append(msg)


cm = MAIN.ConnectionManager()
old_ws, new_ws, b_ws = FakeWS(dead=True), FakeWS(), FakeWS()
for ws, uid in ((old_ws, A), (new_ws, A), (b_ws, B)):
    cm.active_connections.append(ws)
    cm.set_user(ws, uid)
    cm.register_user(uid, ws)


async def _send_in_task():
    token = MAIN._CURRENT_TASK.set({"id": "task-live-0001", "session_id": "s1"})
    try:
        await cm.send_message({"type": "assistant_message", "message": "النتيجة"}, old_ws)
    finally:
        MAIN._CURRENT_TASK.reset(token)


asyncio.run(_send_in_task())
got = [m for m in new_ws.sent if m.get("type") == "assistant_message"]
check("رد مهمة مات سوكتها يصل لاتصال المستخدم الذي عاد",
      got and got[0]["task_id"] == "task-live-0001" and got[0]["session_id"] == "s1"
      and isinstance(got[0].get("seq"), int), new_ws.sent)
check("ولا يصل لمستخدم آخر", not b_ws.sent, b_ws.sent)
try:
    asyncio.run(cm.send_message({"type": "pong"}, old_ws))
    check("خارج المهام: فشل الإرسال يُرفع كما كان (الحلقة تُغلق الاتصال)", False)
except RuntimeError:
    check("خارج المهام: فشل الإرسال يُرفع كما كان (الحلقة تُغلق الاتصال)", True)
cm2 = MAIN.ConnectionManager()
stranger = FakeWS()
cm2.active_connections.append(stranger)
cm2.set_user(stranger, B)
asyncio.run(cm2.broadcast_to_user(A, {"type": "assistant_message", "message": "تقرير موظف A"}))
check("تقرير موظف A لا يُدفع لاتصال B حين يكون A غير متصل (كان يُدفع للجميع)", not stranger.sent,
      stranger.sent)

# ─────────────────────────────────────────────────────────────────────
print("4) دورة الموظف: نقاط المراحل والأفعال والاستئناف بلا تكرار")
from verify_creator import make_config  # noqa: E402

from app.aeos.capabilities.base import CapabilityResult  # noqa: E402
from app.aeos.cognition.cycle import CognitiveCycle  # noqa: E402
from app.aeos.cognition.phases import ExecutePhase  # noqa: E402
from app.aeos.core.events import Event, EventType  # noqa: E402
from app.aeos.decision.engine import Decision  # noqa: E402
from app.aeos.memory.manager import MemoryManager  # noqa: E402
from app.aeos.runtime.manager import RuntimeManager  # noqa: E402
from app.aeos.skills.base import ProposedAction  # noqa: E402

cfg = make_config()
cfg.id = uuid.uuid4().hex
cfg.user_id = A
BOOT._platform.repository.save(cfg)


def like(n):
    return Decision(ProposedAction(capability="like_post",
                                   params={"tweet_url": f"https://x.com/edu/status/{n}", "handle": "@edu"},
                                   rationale="اختبار"), score=0.8, explanation="اختبار")


executed = []


def execute(request, context):
    executed.append(request.params.get("tweet_url"))
    return CapabilityResult.ok(request.name, output={"tweet_url": request.params.get("tweet_url")})


def decide_phase(chosen):
    return SimpleNamespace(name="decide", run=lambda ctx: ctx.scratch.__setitem__("decisions", list(chosen)))


plan = {"decisions": [like(1), like(2)]}
runtime = RuntimeManager(
    repository=BOOT._platform.repository, memory_manager=MemoryManager(), mcp_registry=None,
    capability_registry=SimpleNamespace(execute=execute),
    workspace_manager=SimpleNamespace(get=lambda eid: None, destroy_tmp=lambda eid: None),
    cycle_factory=lambda: CognitiveCycle([decide_phase(plan["decisions"]), ExecutePhase()]))
pushed = []
from app.services import realtime_bus  # noqa: E402

realtime_bus.register_sink(lambda uid, payload: pushed.append((uid, payload)))

report = runtime.run_cycle(cfg.id, Event(EventType.SCHEDULER, employee_id=cfg.id, source="verify"))
task1 = TC.get(TC.cycle_task_id(report["cycle_id"]), with_internals=True)
check("الدورة تُسجَّل وتُغلق ناجحة", task1 and task1["status"] == TC.SUCCEEDED, task1)
check("نقطة لكل مرحلة", task1["state"].get("phases") == ["decide", "execute"], task1["state"])
check("الأفعال الناجحة محفوظة بمفاتيحها",
      TC.done_action_keys(task1) == ["like_post:https://x.com/edu/status/1",
                                     "like_post:https://x.com/edu/status/2"], task1["state"])
updates = [p["task"] for uid, p in pushed if p.get("type") == "task_update"]
check("تحديثات حيّة للمالك (بدء ← مراحل ← نهاية)",
      updates and updates[0]["status"] == TC.RUNNING and updates[-1]["status"] == TC.SUCCEEDED
      and all(uid == A for uid, p in pushed if p.get("type") == "task_update"))

# انقطاع: دورة سجّلت فعلين نجحا (إعجاب + نشر) ثم مات الخادم قبل الحفظ
cut_id = TC.cycle_task_id("cut" + uuid.uuid4().hex[:10])
TC.begin(cut_id, "cycle", A, employee_id=cfg.id, title="دورة",
         request={"event_type": "manual", "payload": {"directed_topic": "الطقس"}})
from app.aeos.decision.engine import local_now  # noqa: E402

today = local_now(cfg).strftime("%Y-%m-%d")
TC.record_action(cut_id, "like_post:https://x.com/edu/status/7",
                 {"capability": "like_post", "day": today})
TC.record_action(cut_id, "publish", {"capability": "publish_content", "day": today})
TC.mark_interrupted_on_boot()
check("الدورة المقطوعة ← interrupted بفعلين محفوظين",
      TC.get(cut_id)["status"] == TC.INTERRUPTED and TC.get(cut_id)["done_actions"] == 2)
claimed = TC.claim_resume(cut_id, A, TC.RESUMED)
fresh = BOOT._platform.repository.get(cfg.id)
likes_before = fresh.runtime_state.counters.get("likes_today", 0)
posts_before = fresh.runtime_state.counters.get("posts_today", 0)
executed.clear()
plan["decisions"] = [like(7), like(8)]
r2 = runtime.run_cycle(cfg.id, Event(EventType.MANUAL, employee_id=cfg.id,
                                     payload=claimed["request"]["payload"],
                                     source=RuntimeManager.resume_source(cut_id)))
check("الاستئناف لا يكرّر الإعجاب المنفّذ قبل الانقطاع وينفّذ الجديد فقط",
      executed == ["https://x.com/edu/status/8"], executed)
skipped = [r for r in r2.get("results") or [] if r.get("status") == "skipped"]
check("المتخطّى معلَن بسببه", skipped and "قبل انقطاع" in skipped[0]["error"], skipped)
after = BOOT._platform.repository.get(cfg.id).runtime_state.counters
check("الأفعال السابقة تُحتسب في ميزانية اليوم (لم تصل للحفظ قبل الانقطاع)",
      after.get("likes_today", 0) == likes_before + 2 and after.get("posts_today", 0) == posts_before + 1,
      (likes_before, posts_before, after))
check("المهمة القديمة «استُؤنفت» والجديدة ناجحة",
      TC.get(cut_id)["status"] == TC.RESUMED
      and TC.get(TC.cycle_task_id(r2["cycle_id"]))["status"] == TC.SUCCEEDED)
from app.aeos.cognition.phases import resume_key  # noqa: E402

check("مفتاح النشر واحد (المحتوى يُولَّد من جديد عند الاستئناف)",
      resume_key("publish_content", "") == resume_key("publish_thread", "x") == "publish"
      and resume_key("wait", "") == "")

# تعذّر الاستئناف (موظف مشغول/موقوف) ← تعود مقطوعة ويُبلَّغ المالك
busy_id = TC.cycle_task_id("busy" + uuid.uuid4().hex[:10])
TC.begin(busy_id, "cycle", A, employee_id=cfg.id, request={"event_type": "manual", "payload": {}})
TC.mark_interrupted_on_boot()
busy = TC.claim_resume(busy_id, A, TC.RESUMED)
notes = []
fake_self = SimpleNamespace(
    _runtime=SimpleNamespace(run_cycle=lambda eid, ev: {"skipped": True, "reason": "cycle_already_running"}),
    _repo=BOOT._platform.repository,
    notify_owner=lambda eid, text, intent="": notes.append(text))
BOOT.AEOSPlatform.resume_cycle(fake_self, busy)
check("تعذّر الاستئناف ← تعود «مقطوعة» مع السبب ويُبلَّغ المالك",
      TC.get(busy_id)["status"] == TC.INTERRUPTED and "منتصف دورة" in (TC.get(busy_id)["error"] or "")
      and notes and "تعذّر استئناف" in notes[0], (TC.get(busy_id), notes))
TC.finish(busy_id, TC.CANCELLED)
realtime_bus.register_sink(None)

# ─────────────────────────────────────────────────────────────────────
print("5) الـWebSocket: مهمة الشات، منع التكرار، إعادة ما فات، الاستئناف والتجاهل")
calls = []


async def fake_coordinator(message, user_id, pinned, db, session_id, employee=None):
    calls.append(message)
    return {"message": f"تم: {message}", "dispatched": []}


MAIN._run_coordinator = fake_coordinator
MAIN.orchestrator.is_fleet_message = lambda message, mentioned: True
MAIN._remember_turn = lambda *a, **k: None
client = TestClient(MAIN.app)


def _recv(ws, timeout=20):
    box = {}
    th = threading.Thread(target=lambda: box.setdefault("m", ws.receive_json()), daemon=True)
    th.start()
    th.join(timeout)
    if "m" not in box:
        raise TimeoutError("no message")
    return box["m"]


def recv_until(ws, pred, limit=25):
    got = []
    for _ in range(limit):
        msg = _recv(ws)
        got.append(msg)
        if pred(msg):
            return msg, got
    return None, got


def connect(token, since=None):
    ws = client.websocket_connect("/ws/chat")
    sock = ws.__enter__()
    sock.send_json({"type": "auth", "token": token})
    assert _recv(sock)["type"] == "auth_ok"
    sock.send_json({"type": "resume", "since": since})
    snap, _ = recv_until(sock, lambda m: m.get("type") == "tasks_snapshot")
    return ws, sock, snap


def is_done(tid):
    return lambda m: (m.get("type") == "task_update" and (m.get("task") or {}).get("id") == tid
                      and m["task"]["status"] != TC.RUNNING)


ws, sock, snap = connect(TOK_A)
check("عودة الاتصال ← لقطة مهام برقم آخر رسالة", snap and "tasks" in snap and isinstance(snap["seq"], int))
base_seq = snap["seq"]
sock.send_json({"message": "كل الموظفين انشروا عن الطقس", "task_id": "chat-task-0001", "session_id": "s9"})
final, got = recv_until(sock, is_done("chat-task-0001"))
reply = next((m for m in got if m.get("type") == "assistant_message"), None)
check("المهمة تُعلن running ثم تنتهي succeeded",
      any(m.get("type") == "task_update" and m["task"]["status"] == TC.RUNNING for m in got)
      and final and final["task"]["status"] == TC.SUCCEEDED, got)
check("الرد موسوم بالمهمة والجلسة ورقم تسلسلي",
      reply and reply["task_id"] == "chat-task-0001" and reply["session_id"] == "s9"
      and reply["seq"] > base_seq, reply)
sock.send_json({"message": "كل الموظفين انشروا عن الطقس", "task_id": "chat-task-0001", "session_id": "s9"})
dup, _ = recv_until(sock, lambda m: m.get("type") == "task_update")
check("إعادة إرسال الرسالة نفسها بعد انقطاع ← لا تنفيذ ثانٍ",
      dup.get("duplicate") and calls.count("كل الموظفين انشروا عن الطقس") == 1, (dup, calls))
ws.__exit__(None, None, None)

ws = client.websocket_connect("/ws/chat")
sock = ws.__enter__()
sock.send_json({"type": "auth", "token": TOK_A})
_recv(sock)
sock.send_json({"type": "resume", "since": base_seq})
replayed, got = recv_until(sock, lambda m: m.get("type") == "tasks_snapshot")
replays = [m for m in got if m.get("replayed")]
check("الإعادة تحمل الرد الفائت فقط وبعلامة replayed",
      replays and replays[0]["message"] == "تم: كل الموظفين انشروا عن الطقس"
      and all(m["seq"] > base_seq for m in replays), got)
check("اللقطة تحوي المهمة المنتهية", any(t["id"] == "chat-task-0001" for t in replayed["tasks"]))

# مهمة شات قطعها إعادة تشغيل ← استئناف بنصّها الأصلي
TC.begin("chat-cut-00001", "chat", A, title="الفريق ينشر عن الرياضة", session_id="s9",
         request={"message": "الفريق ينشر عن الرياضة", "session_id": "s9"})
TC.mark_interrupted_on_boot()
sock.send_json({"type": "resume_task", "task_id": "chat-cut-00001"})
final, got = recv_until(sock, is_done("chat-cut-00001"))
check("استئناف طلب الشات يعيده بنصّه الأصلي وينتهي",
      "الفريق ينشر عن الرياضة" in calls and final and final["task"]["status"] == TC.SUCCEEDED
      and final["task"]["attempts"] == 2, got)
sock.send_json({"type": "resume_task", "task_id": "chat-cut-00001"})
dup, _ = recv_until(sock, lambda m: m.get("type") == "task_update")
check("ضغطة استئناف ثانية لا تنفّذ مرة أخرى",
      dup.get("duplicate") and calls.count("الفريق ينشر عن الرياضة") == 1)

# دورة مقطوعة ← الاستئناف يذهب للمنصة في الخلفية
cyc = TC.cycle_task_id("ws" + uuid.uuid4().hex[:10])
TC.begin(cyc, "cycle", A, employee_id=cfg.id, request={"event_type": "manual", "payload": {}})
TC.mark_interrupted_on_boot()
sock.send_json({"type": "resume_task", "task_id": cyc})
upd, _ = recv_until(sock, lambda m: m.get("type") == "task_update" and m["task"]["id"] == cyc)
import time  # noqa: E402

for _ in range(50):
    if cyc in BOOT._platform.resumed:
        break
    time.sleep(0.05)
check("استئناف دورة ← «استُؤنفت» وتُشغَّل في الخلفية",
      upd["task"]["status"] == TC.RESUMED and cyc in BOOT._platform.resumed)

dis = TC.cycle_task_id("dis" + uuid.uuid4().hex[:10])
TC.begin(dis, "cycle", A, employee_id=cfg.id, request={})
TC.mark_interrupted_on_boot()
sock.send_json({"type": "dismiss_task", "task_id": dis})
upd, _ = recv_until(sock, lambda m: m.get("type") == "task_update" and m["task"]["id"] == dis)
check("التجاهل ← أُلغيت بلا تشغيل", upd["task"]["status"] == TC.CANCELLED and dis not in BOOT._platform.resumed)
ws.__exit__(None, None, None)

TC.begin("chat-a-own-001", "chat", A, title="خاصة بـA", request={"message": "خاصة"})
TC.mark_interrupted_on_boot()
ws, sock, _ = connect(TOK_B)
sock.send_json({"type": "resume_task", "task_id": "chat-a-own-001"})
err, _ = recv_until(sock, lambda m: m.get("type") in ("error", "task_update"))
check("B لا يستأنف مهمة A ولا يعرف وجودها",
      err.get("type") == "error" and "خاصة" not in calls and TC.get("chat-a-own-001")["status"] == TC.INTERRUPTED)
ws.__exit__(None, None, None)

print("6) إشعار الإقلاع للمهام المقطوعة")
TC.finish("chat-a-own-001", TC.CANCELLED)
TC.begin("chat-boot-0001", "chat", A, title="طلب قبل الإقلاع", session_id="s-boot", request={})
TC.begin(TC.cycle_task_id("boot1"), "cycle", A, employee_id=cfg.id, request={})
TC.phase(TC.cycle_task_id("boot1"), "execute", 18)
interrupted = TC.mark_interrupted_on_boot()
BOOT._platform.notified.clear()
MAIN._announce_interrupted(interrupted)
from app.services.memory_service import memory_service  # noqa: E402

db = DB.SessionLocal()
conv = memory_service.get_or_create_conversation(db=db, user_id=A, session_id="s-boot")
texts = [m.content for m in conv.messages]
db.close()
check("طلب الشات المقطوع: إشعار محفوظ في محادثته", any("انقطع تنفيذ طلبك" in t for t in texts), texts)
check("الدورة المقطوعة: الموظف يبلّغ مالكه بالمرحلة",
      any("التنفيذ على X" in text for _, text, _ in BOOT._platform.notified), BOOT._platform.notified)

print("7) حذف المستخدم يحذف مهامه ورسائله المحفوظة")
from app.aeos.memory.lifecycle import purge_user  # noqa: E402

TC.outbox_record(B, {"type": "assistant_message", "message": "ب"})
counts = purge_user(B)
check("purge_user يمسح task_checkpoints وrealtime_outbox لـB",
      counts.get("task_checkpoints", 0) >= 1 and counts.get("realtime_outbox", 0) >= 1
      and not TC.for_user(B) and TC.outbox_latest(B) == 0, counts)

print(f"\n{'❌ فشل ' + str(len(FAILED)) if FAILED else '✅ كل الفحوص نجحت'}")
for name in FAILED:
    print("  -", name)
sys.exit(1 if FAILED else 0)
