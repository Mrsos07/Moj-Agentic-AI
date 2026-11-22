"""
verify_runtime.py — وقت تشغيل AEOS الدائم (AEOS_RUNTIME=durable): دورة حياة المهمة، الطابور في القاعدة،
العقود ورموز السياج، النبض والاستعادة، إعادة المحاولة وDLQ، الأولوية والعدالة والحصص، الضغط العكسي،
الإلغاء والمهل وحدود الدورة، سجل الأفعال قبل/بعد كل أثر خارجي، الحارس المغلق عند الفشل، حدود
Agent-to-Agent، قاطع الدائرة، الإيقاف النظيف، القياسات — والانقطاع عند كل مرحلة من المراحل الثماني عشرة.

لا شبكة ولا X ولا نموذج: القدرات والنموذج مزيّفان، والقاعدة مؤقتة (SQLite، أو PostgreSQL إن ضُبط
MOJ_TEST_DATABASE_URL).

    .\\venv\\Scripts\\python.exe verify_runtime.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TOPIC_SEMANTIC", "0")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("AEOS_RUNTIME", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import app.db.database as DB  # noqa: E402
from app.db.testing import use_temp_database  # noqa: E402

_auth = tempfile.mkdtemp()
import app.auth.token_guard as _TG  # noqa: E402

_TG._PATH = Path(_auth) / "rev.json"
_TG._state = None
use_temp_database()

from sqlalchemy import select  # noqa: E402

from app.db import models as M  # noqa: E402
from app.services import task_checkpoints as TC  # noqa: E402
from app.aeos.capabilities.base import CapabilityResult  # noqa: E402
from app.aeos.cognition.cycle import CognitiveCycle  # noqa: E402
from app.aeos.cognition.phases import ExecutePhase, PersonaGuardPhase, SavePhase  # noqa: E402
from app.aeos.core.events import Event, EventType  # noqa: E402
from app.aeos.core.interfaces import ILLMProvider  # noqa: E402
from app.aeos.core.llm import RoutedLLMProvider  # noqa: E402
from app.aeos.decision.engine import Decision  # noqa: E402
from app.aeos.employee.models import EmployeeConfig  # noqa: E402
from app.aeos.employee.repository import SqlEmployeeRepository  # noqa: E402
from app.aeos.memory.manager import MemoryManager  # noqa: E402
from app.aeos.runtime import budget as BG  # noqa: E402
from app.aeos.runtime import journal as JR  # noqa: E402
from app.aeos.runtime.config import WorkerConfig, runtime_mode  # noqa: E402
from app.aeos.runtime.durable_queue import (CURRENT_JOB, DbJobQueue, DbLock, Lease, QueueFull,  # noqa: E402
                                            StaleLease, TaskLimitExceeded)
from app.aeos.runtime.durable_workers import SHUTDOWN, DurableWorkerPool  # noqa: E402
from app.aeos.runtime.manager import RuntimeManager  # noqa: E402
from app.aeos.runtime.metrics import CircuitBreaker  # noqa: E402
from app.aeos.runtime.states import InvalidTransition, JobState as S, TRANSITIONS, check as tcheck  # noqa: E402
from app.aeos.skills.base import ProposedAction  # noqa: E402
from verify_creator import make_config  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:  # noqa: BLE001
        return e
    return None


def later(seconds):
    return datetime.utcnow() + timedelta(seconds=seconds)


ENV = {"AEOS_LEASE_SECONDS": "4", "AEOS_HEARTBEAT_SECONDS": "1", "AEOS_RECLAIM_EVERY_SECONDS": "1",
       "AEOS_RETRY_BASE_SECONDS": "0", "AEOS_POLL_SECONDS": "0.1", "AEOS_WORKER_ID": "w-test",
       "AEOS_TENANT_MAX_ACTIVE": "2", "AEOS_CYCLE_DEADLINE_SECONDS": "60", "AEOS_DRAIN_SECONDS": "2"}


def cfg(**over):
    return WorkerConfig.from_env({**ENV, **{f"AEOS_{k.upper()}": str(v) for k, v in over.items()}})


repo = SqlEmployeeRepository()


def fresh():
    """قاعدة جديدة لكل قسم + ثلاثة مالكين."""
    use_temp_database()
    with DB.SessionLocal() as s:
        users = [M.User(email=f"u{i}-{uuid.uuid4().hex[:6]}@example.com", password_hash="h") for i in range(3)]
        s.add_all(users)
        s.commit()
        return [u.id for u in users]


def emp(user_id, real=False):
    c = make_config() if real else EmployeeConfig(id="", user_id=user_id, platform="x",
                                                  account_username="a" + uuid.uuid4().hex[:8])
    c.id = uuid.uuid4().hex
    c.user_id = user_id
    c.account_username = "a" + uuid.uuid4().hex[:8]
    return repo.save(c).id


def ev(eid, etype=EventType.SCHEDULER, payload=None, source="verify"):
    return Event(etype, employee_id=eid, payload=payload or {}, source=source)


def job_row(job_id):
    with DB.SessionLocal() as s:
        return s.execute(select(M.ExecutionJobRecord).where(M.ExecutionJobRecord.job_id == job_id)).scalar()


# ═════════════════════════ 1) الإعداد ودورة الحياة ═════════════════════════
print("1) الإعداد من البيئة ودورة حياة المهمة")
check("الافتراضي legacy (السلوك القديم)", runtime_mode({}) == "legacy" and runtime_mode({"AEOS_RUNTIME": "durable"}) == "durable")
check("وضع غير معروف يُرفض", raises(lambda: runtime_mode({"AEOS_RUNTIME": "x"}), ValueError))
c0 = cfg(workers=7, guard_fail_closed="0")
check("WorkerConfig من البيئة (أرقام وأعلام)", c0.workers == 7 and c0.lease_seconds == 4 and c0.guard_fail_closed is False)
check("نبض أطول من نصف العقد مرفوض", raises(lambda: cfg(heartbeat_seconds=3), ValueError))
check("الانتقالات المحكومة: QUEUED→LEASED→RUNNING⇄WAITING→نهائية",
      tcheck("QUEUED", "LEASED") is None and tcheck("RUNNING", "WAITING") is None and tcheck("WAITING", "RUNNING") is None)
check("لا انتقال من حالة نهائية ولا قفز QUEUED→COMPLETED",
      all(not TRANSITIONS[t] for t in (S.COMPLETED, S.FAILED, S.CANCELLED, S.TIMED_OUT))
      and isinstance(raises(lambda: tcheck("QUEUED", "COMPLETED")), InvalidTransition))

# ═════════════════════════ 2) الإدراج ═════════════════════════
print("2) الإدراج: منع التكرار، الدمج، الضغط العكسي")
A, B, C = fresh()
q = DbJobQueue(cfg(queue_max_depth=3))
e1 = emp(A)
j1, new1 = q.submit(e1, ev(e1), idempotency_key="k-1", tenant_id=A)
j1b, new1b = q.submit(e1, ev(e1), idempotency_key="k-1", tenant_id=A)
check("مفتاح منع التكرار نفسه ⇒ المهمة نفسها", j1 == j1b and new1 and not new1b)
j2, new2 = q.submit(e1, ev(e1), droppable=True, tenant_id=A)
check("مجدولة لموظف له مهمة منتظرة ⇒ تُدمج", j2 == j1 and not new2)
q.submit(emp(A), ev(e1), tenant_id=A)
q.submit(emp(A), ev(e1), tenant_id=A)
dropped, _ = q.submit(emp(B), ev(e1), droppable=True, tenant_id=B)
check("فوق الحد: المجدولة تُسقط (الدورة التالية تعيدها)", dropped is None)
check("فوق الحد: الطلب المباشر يُرفض بـQueueFull", isinstance(raises(lambda: q.submit(emp(B), ev(e1))), QueueFull))

# ═════════════════════════ 3) الاستلام ═════════════════════════
print("3) الاستلام: ذرّي تحت التنافس، موظف واحد = دورة نشطة واحدة، الحصص والعدالة والأولوية")
A, B, C = fresh()
q = DbJobQueue(cfg(tenant_max_active=100))
emps = [emp([A, B, C][i % 3]) for i in range(24)]
for e in emps:
    q.submit(e, ev(e), tenant_id=None)
got, lock = [], threading.Lock()


def claimer(i):
    while True:
        ls = q.claim(f"w{i}")
        if ls is None:
            return
        with lock:
            got.append(ls.job_id)


ts = [threading.Thread(target=claimer, args=(i,)) for i in range(12)]
[t.start() for t in ts]
[t.join() for t in ts]
check("24 مهمة × 12 عاملاً متنافساً ⇒ كل مهمة استُلمت مرة واحدة", len(got) == 24 and len(set(got)) == 24, len(got))
same = emp(A)
q.submit(same, ev(same, EventType.MANUAL))
q.submit(same, ev(same, EventType.MENTION))
first = q.claim("wA")
check("موظف له دورة نشطة ⇒ مهمته الثانية لا تُستلم (فهرس فريد جزئي)", first and first.employee_id == same and q.claim("wB") is None)

A, B, C = fresh()
q = DbJobQueue(cfg(tenant_max_active=1))
for _ in range(4):
    e = emp(A)
    q.submit(e, ev(e), tenant_id=A)
eb = emp(B)
q.submit(eb, ev(eb), tenant_id=B)
c1, c2, c3 = q.claim("w"), q.claim("w"), q.claim("w")
check("حصة المالك: A لا يتجاوز دورة نشطة واحدة، وB يُخدم رغم أن مهامه أحدث",
      c1.tenant_id == A and c2 and c2.tenant_id == B and c3 is None, (c1.tenant_id, getattr(c2, "tenant_id", None)))
A, B, C = fresh()
q = DbJobQueue(cfg())
lo, hi = emp(A), emp(B)
q.submit(lo, ev(lo), priority=3, tenant_id=A)
time.sleep(0.01)
q.submit(hi, ev(hi, EventType.MENTION), priority=1, tenant_id=B)
check("الأولوية: منشن (1) قبل المجدولة (3) الأقدم", q.claim("w").employee_id == hi)

# ═════════════════════════ 4) العقود والسياج ═════════════════════════
print("4) العقد: النبض، الانتهاء، الاستعادة، رمز السياج يرفض العامل القديم")
A, B, C = fresh()
q = DbJobQueue(cfg())
e = emp(A)
jid, _ = q.submit(e, ev(e), tenant_id=A)
la = q.claim("worker-A")
exp0 = job_row(jid).lease_expires_at
time.sleep(1.1)
q.heartbeat(la)
check("النبض يمدّد العقد", job_row(jid).lease_expires_at > exp0)
rec = q.reclaim_expired(now=later(30))
lb = q.claim("worker-B", now=later(31))
check("عقد منتهٍ لمهمة لم تبدأ ⇒ يعود للطابور ويستلمه عامل آخر برمز أعلى",
      rec and rec[0]["action"] == "requeued" and lb and lb.job_id == jid and lb.token > la.token, rec)
check("العامل القديم: النبض مرفوض (StaleLease)", isinstance(raises(lambda: q.heartbeat(la)), StaleLease))
check("العامل القديم: الإنهاء مرفوض ولا يغيّر الحالة",
      isinstance(raises(lambda: q.complete(la, {"x": 1})), StaleLease) and job_row(jid).status == "LEASED")
old_journal = JR.ActionJournal(q, la, "c-old")
check("العامل القديم: لا يبدأ أثراً خارجياً (سجل الأفعال مسيَّج)",
      isinstance(raises(lambda: old_journal.begin("like_post", "u")), StaleLease))
q.start(lb)
q.wait(lb)
q.resume_running(lb)
q.complete(lb, {"ok": 1})
check("العامل الجديد: RUNNING ⇄ WAITING ثم COMPLETED", job_row(jid).status == "COMPLETED")
check("انتقال من حالة نهائية مرفوض", isinstance(raises(lambda: q.start(lb)), StaleLease))
q.submit(e, ev(e, EventType.MANUAL), tenant_id=A)
lc = q.claim("w")
check("طلب إلغاء جارٍ يصل في النبض", q.request_cancel(employee_id=e)["signalled_running"] == 1 and q.heartbeat(lc) is True)

lk1, lk2 = DbLock("leader"), DbLock("leader")
t1 = lk1.acquire("p1", 5)
check("قفل موزّع: مالك واحد", t1 and lk2.acquire("p2", 5) is None and lk1.acquire("p1", 5) > t1)
t2 = lk2.acquire("p2", 5, now=later(60))
check("قفل منتهٍ ينتقل برمز سياج أعلى", t2 and t2 > t1 and lk2.holder()["owner"] == "p2")

# ═════════════════════════ 5) إعادة المحاولة وDLQ ═════════════════════════
print("5) إعادة المحاولة بتأخير أسّي ثم DLQ؛ لا إعادة بعد أثر خارجي")
A, B, C = fresh()
q = DbJobQueue(cfg(max_attempts=2, retry_base_seconds=30))
e = emp(A)
jid, _ = q.submit(e, ev(e), tenant_id=A)
l1 = q.claim("w")
st = q.fail(l1, "عطل عابر")
row = job_row(jid)
check("فشل عابر ⇒ QUEUED بعد تأخير (لا استلام فوري)",
      st == "QUEUED" and row.available_at > datetime.utcnow() + timedelta(seconds=20) and q.claim("w") is None)
l2 = q.claim("w", now=later(120))
st = q.fail(l2, "عطل عابر")
row = job_row(jid)
check("استنفاد المحاولات ⇒ FAILED في DLQ", st == "FAILED" and row.dead_letter and row.attempts == 2)
check("التأخير أسّي ومسقوف", q.backoff(1) < q.backoff(3) <= q.cfg.retry_max_seconds * 1.2)
e2 = emp(A)
jid2, _ = q.submit(e2, ev(e2), tenant_id=A)
l3 = q.claim("w")
with DB.SessionLocal() as s:
    q.mark_effects(s, l3)
    s.commit()
check("فشل بعد أثر خارجي ⇒ FAILED بلا إعادة (لا تكرار على X)",
      q.fail(l3, "فشل") == "FAILED" and not job_row(jid2).dead_letter)

# ═════════════════════════ بيئة الدورات ═════════════════════════
NAMES = ["observe", "topic_intel", "performance", "emotion", "analyze", "think", "strategy", "prioritize",
         "plan", "decide", "guard", "execute", "evaluate", "learn", "critique", "graph_memory", "support", "save"]


class KillWorker(BaseException):
    """موت العامل (يتجاوز كل except Exception)."""


def like(n):
    return Decision(ProposedAction(capability="like_post",
                                   params={"tweet_url": f"https://x.com/edu/status/{n}", "handle": "@edu"},
                                   rationale="اختبار"), score=0.8, explanation="اختبار")


class FakeLLM(ILLMProvider):
    def __init__(self, reply="ok", fail=False):
        self.reply, self.fail, self.calls = reply, fail, 0

    @property
    def available(self):
        return True

    def complete(self, system, prompt, *, temperature=0.7, max_tokens=1000):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return self.reply

    def complete_json(self, system, prompt, *, temperature=0.3):
        self.calls += 1
        return {}


def build(hooks=None, decisions=None, cap=None, llm=None, kill_before=None):
    hooks, executed = hooks or {}, []
    decisions = decisions if decisions is not None else [like(1), like(2)]

    def execute(request, context):
        executed.append(request.params.get("tweet_url"))
        if cap:
            return cap(request, context)
        return CapabilityResult.ok(request.name, output={"tweet_url": request.params.get("tweet_url")})

    def phases():
        out = []
        for name in NAMES:
            if name == "decide":
                ph = SimpleNamespace(name=name, run=lambda ctx: ctx.scratch.__setitem__("decisions", list(decisions)))
            elif name == "execute":
                ph = ExecutePhase()
            elif name == "save":
                ph = SavePhase()
            else:
                ph = SimpleNamespace(name=name, run=(lambda n: lambda ctx: hooks.get(n, lambda c: None)(ctx))(name))
            if name == kill_before:
                inner = ph
                ph = SimpleNamespace(name=name, always_run=getattr(inner, "always_run", False),
                                     run=lambda ctx: (_ for _ in ()).throw(KillWorker()))
            out.append(ph)
        return out

    rt = RuntimeManager(repository=repo, memory_manager=MemoryManager(), mcp_registry=None,
                        capability_registry=SimpleNamespace(execute=execute, has=lambda n: False),
                        workspace_manager=SimpleNamespace(get=lambda eid: None, destroy_tmp=lambda eid: None),
                        cycle_factory=lambda: CognitiveCycle(phases()), llm=llm)
    return rt, executed


def one(q, rt, e, etype=EventType.SCHEDULER, c=None, payload=None, source="verify"):
    pool = DurableWorkerPool(q, rt, c or q.cfg)
    jid, _ = q.submit(e, ev(e, etype, payload, source), tenant_id=None)
    lease = q.claim(pool.cfg.worker_id)
    return jid, pool, lease


# ═════════════════════════ 6) دورة كاملة ═════════════════════════
print("6) دورة كاملة عبر العامل: اعتراف بعد الحفظ، سجل الأفعال، مخرجات المراحل وأسباب التخطّي")
A, B, C = fresh()
q = DbJobQueue(cfg())
e = emp(A, real=True)
rt, executed = build()
jid, pool, lease = one(q, rt, e)
out = pool.run_lease(lease)
row = job_row(jid)
entries = JR.entries(job_id=jid)
rep = q.get(jid)["result"]
check("COMPLETED بعد الحفظ، والتقرير محفوظ في المهمة", out == "completed" and row.status == "COMPLETED" and rep.get("cycle_id"))
check("سجل الأفعال: SUCCEEDED لكل فعل بسياج العقد",
      [x["status"] for x in entries] == ["SUCCEEDED", "SUCCEEDED"] and all(x["fence_token"] == lease.token for x in entries))
check("مخرجات كل مرحلة وأسباب التخطّي في التقرير وسجل التنفيذ",
      "decisions" in rep["phase_outputs"].get("decide", []) and "execution_results" in rep["phase_outputs"].get("execute", [])
      and rep["runtime"]["budget"]["tool_calls"] == 2)
with DB.SessionLocal() as s:
    log = s.execute(select(M.ExecutionLogRecord).where(M.ExecutionLogRecord.cycle_id == rep["cycle_id"])).scalar()
check("سجل التنفيذ يحفظ runtime وphase_outputs", log and '"runtime"' in log.outcome and '"phase_outputs"' in log.outcome)
rt2, ex2 = build(decisions=[])
jid2, pool2, l2 = one(q, rt2, e)
pool2.run_lease(l2)
check("الحارس يُتخطّى بسبب صريح حين لا قرارات", q.get(jid2)["result"]["phase_skips"].get("guard") == "لا قرارات لمراجعتها")
task = TC.get(TC.cycle_task_id(rep["cycle_id"]))
check("نقطة الاستعادة ناجحة", task and task["status"] == TC.SUCCEEDED)

print("6b) الوضع القديم بلا عقد: لا ميزانية ولا سجل ولا runtime (السلوك السابق)")
rt3, ex3 = build()
r3 = rt3.run_cycle(e, ev(e))
check("legacy: الدورة كما كانت", ex3 and "runtime" not in r3 and not JR.entries(cycle_id=r3["cycle_id"]))

# ═════════════════════════ 7) الإلغاء والمهلة والحدود ═════════════════════════
print("7) الإلغاء ينتشر عبر النبض، المهلة، حدود النموذج والأدوات")
A, B, C = fresh()
q = DbJobQueue(cfg())
e = emp(A, real=True)
holder = {}
rt, executed = build(hooks={"think": lambda ctx: (q.request_cancel(job_id=holder["jid"]), time.sleep(2.5))})
jid, pool, lease = one(q, rt, e, EventType.MANUAL)
holder["jid"] = jid
out = pool.run_lease(lease)
rep = q.get(jid)["result"]
check("طلب المالك أثناء «think» ⇒ CANCELLED قبل أي فعل على X",
      out == "cancelled" and job_row(jid).status == "CANCELLED" and executed == [], (out, executed))
check("المراحل الباقية متخطّاة بالسبب والحفظ يعمل",
      "cancelled" in (rep["phase_skips"].get("execute") or "") and "save" not in rep["phase_skips"])
check("نقطة الاستعادة «أُلغيت»", TC.get(TC.cycle_task_id(rep["cycle_id"]))["status"] == TC.CANCELLED)

rt, executed = build(hooks={"think": lambda ctx: q.request_cancel(job_id=holder["jid"])})
jid, pool, lease = one(q, rt, e, EventType.MANUAL, c=cfg(heartbeat_seconds=30, lease_seconds=120))
holder["jid"] = jid
t0 = time.monotonic()
out = pool.run_lease(lease)
check("الإلغاء لا ينتظر النبض (30 ث): يصل عند نقطة الفحص التالية بين المراحل",
      out == "cancelled" and executed == [] and time.monotonic() - t0 < 10, (out, executed))

rt, executed = build(hooks={"observe": lambda ctx: time.sleep(1.6)})
jid, pool, lease = one(q, rt, e, c=cfg(cycle_deadline_seconds=1))
out = pool.run_lease(lease)
check("المهلة ⇒ TIMED_OUT ولا تنفيذ", out == "timed_out" and job_row(jid).status == "TIMED_OUT" and executed == [])

llm = RoutedLLMProvider(FakeLLM())
calls = []
rt, executed = build(llm=llm, hooks={"think": lambda ctx: calls.extend(
    [raises(lambda: ctx.llm.complete("s", "p"), BG.BudgetExceeded) for _ in range(3)])})
jid, pool, lease = one(q, rt, e, c=cfg(max_llm_calls=2))
pool.run_lease(lease)
rep = q.get(jid)["result"]
check("حد نداءات النموذج: الثالث مرفوض", calls[:2] == [None, None] and isinstance(calls[2], BG.BudgetExceeded))
check("ميزانية مستنفدة ⇒ المراحل التحسينية تُتخطّى بالسبب، والحارس والتنفيذ لا",
      "emotion" not in rep["phase_skips"]                       # قبل الاستنفاد ⇒ عملت
      and all("llm_calls" in rep["phase_skips"].get(n, "") for n in ("critique", "graph_memory"))
      and "execute" not in rep["phase_skips"] and rep["runtime"]["budget"]["exhausted"] == "llm_calls")
rt, executed = build(decisions=[like(i) for i in range(5)])
jid, pool, lease = one(q, rt, e, c=cfg(max_tool_calls=2))
pool.run_lease(lease)
check("حد الأدوات: فعلان ثم توقف التنفيذ", len(executed) == 2, executed)
os.environ["AEOS_MAX_LLM_CALLS"] = "1"
b = BG.CycleBudget(max_llm_calls=1)
tok = BG.CURRENT.set(b)
try:
    RoutedLLMProvider(FakeLLM()).complete("s", "p")
    second = raises(lambda: RoutedLLMProvider(FakeLLM()).complete_json("s", "p"), BG.BudgetExceeded)
finally:
    BG.CURRENT.reset(tok)
    os.environ.pop("AEOS_MAX_LLM_CALLS", None)
check("الميزانية تشمل complete_json وكل مستدعٍ في خيط الدورة", isinstance(second, BG.BudgetExceeded))
pb = BG.CycleBudget.from_config(cfg(cycle_deadline_seconds=60))
check("«الضغط» نسبي للمهلة: 60 ث كاملة ليست ضغطاً، وأقل من 15% منها ضغط",
      not pb.under_pressure() and BG.CycleBudget(deadline=time.monotonic() + 5, total_seconds=60).under_pressure())

# ═════════════════════════ 8) الحارس المغلق ═════════════════════════
print("8) PersonaGuard: تعذّر التدقيق ⇒ رفض في الوضع الدائم، وتمرير كما كان في القديم")
from app.aeos.cognition.context import ExecutionContext  # noqa: E402

gcfg = repo.get(e)
for fail_closed, label in ((True, "دائم"), (False, "قديم")):
    for llm_kind in ("error", "empty"):
        ctx = ExecutionContext(config=gcfg, event=ev(e), memory=MemoryManager(), mcp=None,
                               capabilities=SimpleNamespace(has=lambda n: False), workspace=None,
                               llm=FakeLLM(reply="", fail=llm_kind == "error"), cycle_id="g")
        ctx.fail_closed = fail_closed
        reasons = {}
        rej = PersonaGuardPhase()._llm_audit(ctx, [like(1), like(2)], [0, 1], reasons)
        if fail_closed:
            check(f"[{label}] نموذج {llm_kind} ⇒ كل الإجراءات العامة مرفوضة بالسبب",
                  rej == {0, 1} and "الفشل مغلق" in reasons.get(0, ""), (rej, reasons))
        else:
            check(f"[{label}] نموذج {llm_kind} ⇒ تمرير (fail-open كما كان)", rej == set(), rej)

# ═════════════════════════ 9) الانقطاع عند كل مرحلة ═════════════════════════
print("9) موت العامل قبل كل مرحلة من 18 ⇒ الاستعادة الصحيحة")
A, B, C = fresh()
q = DbJobQueue(cfg())
results = {}
for i, name in enumerate(NAMES):
    e = emp(A, real=True)
    rt, executed = build(kill_before=name)
    jid, pool, lease = one(q, rt, e)
    try:
        pool.run_lease(lease)
        died = False
    except KillWorker:
        died = True
    rec = {r["job_id"]: r for r in q.reclaim_expired(now=later(30))}.get(jid, {})
    row = job_row(jid)
    after_exec = i > NAMES.index("execute")
    expected = "interrupted" if after_exec else "requeued"
    ok = died and rec.get("action") == expected and (row.status == "FAILED" if after_exec else row.status == "QUEUED")
    if after_exec:
        ok = ok and TC.get(rec.get("task_id") or "")["status"] == TC.INTERRUPTED and len(executed) == 2
    results[name] = ok
    if not ok:
        print("     ", name, died, rec, row.status, executed)
check("قبل التنفيذ (observe…guard، execute): بلا أثر ⇒ تعود للطابور تلقائياً",
      all(results[n] for n in NAMES[:NAMES.index("execute") + 1]))
check("بعد التنفيذ (evaluate…save): لها أثر ⇒ FAILED «مقطوعة» بلا إعادة تلقائية (قرار المالك)",
      all(results[n] for n in NAMES[NAMES.index("execute") + 1:]))
requeued = [r for r in q.stats()["by_state"].items()]
rt_ok, ex_ok = build()
pool = DurableWorkerPool(q, rt_ok, cfg(worker_id="w-after-restart"))
done = 0
while True:
    ls = q.claim("w-after-restart", now=later(60))
    if ls is None:
        break
    done += pool.run_lease(ls) == "completed"
check("بعد «إعادة التشغيل»: عامل جديد يكمل كل ما عاد للطابور", done == NAMES.index("execute") + 1, done)

print("9b) موت العامل داخل فعل X (بعد INTENT وقبل التأكيد) ⇒ UNKNOWN ولا تكرار عند الاستئناف")
e = emp(A, real=True)


def dying_cap(request, context):
    if request.params.get("tweet_url", "").endswith("/2"):
        raise KillWorker()
    return CapabilityResult.ok(request.name, output={"tweet_url": request.params.get("tweet_url")})


rt, executed = build(cap=dying_cap)
jid, pool, lease = one(q, rt, e, EventType.MANUAL)
try:
    pool.run_lease(lease)
except KillWorker:
    pass
rec = q.reclaim_expired(now=later(30))
statuses = [x["status"] for x in JR.entries(job_id=jid)]
check("الفعل الأول SUCCEEDED والثاني UNKNOWN، والمهمة «مقطوعة» لا تُعاد",
      statuses == ["SUCCEEDED", "UNKNOWN"] and job_row(jid).status == "FAILED"
      and rec and rec[0]["unknown_actions"] == 1, (statuses, rec))
task_id = rec[0]["task_id"]
claimed = TC.claim_resume(task_id, A, TC.RESUMED)
rt2, executed2 = build(decisions=[like(1), like(2), like(3)])
jid2, pool2, l2 = one(q, rt2, e, EventType.MANUAL, source=RuntimeManager.resume_source(task_id))
pool2.run_lease(l2)
res = {r.get("target"): r for r in q.get(jid2)["result"]["results"]}
check("الاستئناف: الناجح لا يتكرر، والمجهول لا يتكرر (الفشل مغلق)، والجديد يُنفَّذ",
      executed2 == ["https://x.com/edu/status/3"] and res["https://x.com/edu/status/2"]["status"] == "skipped",
      executed2)
check("سطح الاستعادة: عدد الأفعال المجهولة في القياسات", q.stats()["unknown_actions"] >= 1)

# ═════════════════════════ 10) التكرار والتنافس الحي ═════════════════════════
print("10) عمال حقيقيون متزامنون: لا دورتان للموظف نفسه، والإدراج المكرر مهمة واحدة")
A, B, C = fresh()
q = DbJobQueue(cfg(workers=6, tenant_max_active=10))
emps = [emp(A, real=True) for _ in range(4)]
rt, executed = build(hooks={"think": lambda ctx: time.sleep(0.3)})
active, peak, plock = {}, {}, threading.Lock()
orig = rt.run_cycle


def tracking(employee_id, event, **kw):
    with plock:
        active[employee_id] = active.get(employee_id, 0) + 1
        peak[employee_id] = max(peak.get(employee_id, 0), active[employee_id])
    try:
        return orig(employee_id, event, **kw)
    finally:
        with plock:
            active[employee_id] -= 1


rt.run_cycle = tracking
pool = DurableWorkerPool(q, rt, q.cfg)
for e in emps:
    for k in range(3):
        q.submit(e, ev(e, EventType.MANUAL), idempotency_key=f"{e}:{k}")
        q.submit(e, ev(e, EventType.MANUAL), idempotency_key=f"{e}:{k}")
pool.start()
end = time.time() + 40
while time.time() < end and q.stats()["by_state"].get("COMPLETED", 0) < 12:
    time.sleep(0.2)
stop = pool.stop(drain=True, timeout=5)
st = q.stats()["by_state"]
check("12 مهمة (24 إدراجاً مكرراً) ⇒ 12 دورة مكتملة بالضبط", st.get("COMPLETED") == 12 and len(executed) == 24, st)
check("لا دورتان متزامنتان للموظف نفسه أبداً", max(peak.values()) == 1, peak)
snap = pool.metrics.snapshot(q.stats())
check("القياسات: الإنتاجية، p50/p95، نسبة الفشل، تأخر الطابور",
      snap["throughput_per_min"] > 0 and snap["cycle_p95_s"] and snap["failure_rate"] == 0.0
      and "queue_lag_s" in snap["queue"], snap)

# ═════════════════════════ 11) الإيقاف النظيف ═════════════════════════
print("11) الإيقاف النظيف (drain): لا استلام جديد، والجاري يُلغى تعاونياً ويعود للطابور بلا أثر")
A, B, C = fresh()
q = DbJobQueue(cfg(workers=1))
e = emp(A, real=True)
rt, executed = build(hooks={"observe": lambda ctx: time.sleep(3)})
jid, _ = q.submit(e, ev(e))
e2 = emp(A, real=True)
jid_wait, _ = q.submit(e2, ev(e2))
pool = DurableWorkerPool(q, rt, q.cfg)
pool.start()
time.sleep(0.8)
res = pool.stop(drain=True, timeout=0.5)
row, row2 = job_row(jid), job_row(jid_wait)
check("الجاري أُلغي بسبب الإيقاف، ولم يُنفَّذ شيء على X، وعاد للطابور",
      res["cancelled"] == 1 and executed == [] and row.status == "QUEUED", (res, row.status))
check("المنتظر لم يُستلم أثناء الإيقاف", row2.status == "QUEUED")
check("نقطة الاستعادة «مقطوعة» لا «فاشلة»",
      any(t["status"] == TC.INTERRUPTED for t in TC.for_user(A)), [t["status"] for t in TC.for_user(A)])

# ═════════════════════════ 12) Agent-to-Agent ═════════════════════════
print("12) المهام الفرعية: العمق، الأبناء، الشجرة، ومنع الحلقة")
A, B, C = fresh()
q = DbJobQueue(cfg(max_task_depth=2, max_fanout=2, max_tree=5))
ea, eb = emp(A), emp(A)
parent_id, _ = q.submit(ea, ev(ea, EventType.MANUAL, {"x": 1}))
pl = q.claim("w")
tok = CURRENT_JOB.set(pl)
try:
    kid, _ = q.submit(eb, ev(eb, EventType.MANUAL, {"y": 1}))
    loop = raises(lambda: q.submit(ea, ev(ea, EventType.MANUAL, {"x": 1})), TaskLimitExceeded)
    q.submit(eb, ev(eb, EventType.MANUAL, {"y": 2}))
    fan = raises(lambda: q.submit(eb, ev(eb, EventType.MANUAL, {"y": 3})), TaskLimitExceeded)
finally:
    CURRENT_JOB.reset(tok)
k = job_row(kid)
check("الابن يرث الأب والجذر والعمق تلقائياً من المهمة الجارية",
      k.parent_job_id == parent_id and k.root_job_id == parent_id and k.depth == 1)
check("حلقة (الموظف نفسه بالمهمة نفسها في السلسلة) مرفوضة", loop is not None and "حلقة" in str(loop))
check("حد الأبناء المباشرين", fan is not None)
deep = Lease(job_id=kid, token=1, employee_id=eb, event=ev(eb), depth=2, root_job_id=parent_id)
tok = CURRENT_JOB.set(deep)
try:
    too_deep = raises(lambda: q.submit(emp(A), ev(ea, EventType.MANUAL, {"z": 1})), TaskLimitExceeded)
finally:
    CURRENT_JOB.reset(tok)
check("حد العمق", too_deep is not None and "عمق" in str(too_deep))

# ═════════════════════════ 13) قاطع الدائرة والطريق المتزامن ═════════════════════════
print("13) قاطع الدائرة، والطلب المتزامن (run_cycle_now) عبر الطابور")
br = CircuitBreaker(failures=2, cooldown_s=0.3)
br.record(False)
o1 = br.allow()
br.record(False)
o2 = br.allow()
time.sleep(0.35)
probe, second = br.allow(), br.allow()
br.record(True)
check("مغلق ⇒ مفتوح بعد إخفاقين ⇒ نصف مفتوح (تجربة واحدة) ⇒ مغلق بالنجاح",
      o1 and not o2 and probe and not second and br.state == "closed")

from app.aeos.bootstrap import AEOSPlatform  # noqa: E402

A, B, C = fresh()
q = DbJobQueue(cfg())
e = emp(A, real=True)
rt, executed = build()
pool = DurableWorkerPool(q, rt, q.cfg)
P = AEOSPlatform.__new__(AEOSPlatform)
P._mode, P._repo, P._queue, P._workers, P._rt_cfg, P._runtime = "durable", repo, q, pool, q.cfg, rt
pool.start()
try:
    rep = P.run_cycle_now(e, "manual", {"directed_topic": "اختبار"})
finally:
    pool.stop()
check("run_cycle_now في الوضع الدائم: عبر الطابور ويعيد التقرير نفسه",
      rep.get("cycle_id") and rep["job"]["status"] == "COMPLETED" and len(rep.get("results") or []) == 2, rep.get("job"))
check("إلغاء الموظف من الواجهة يصل للطابور", P.cancel_employee(e)["supported"] is True)

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص وقت التشغيل الدائم نجحت")
