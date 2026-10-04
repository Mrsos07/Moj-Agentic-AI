"""
تحقّق المنسّق العام (app/aeos/services/orchestrator.py) — بلا شبكة وبلا LLM حقيقي
وبلا أي فعل على X: عميل LLM مزيّف يصدر نداءات أدوات مُبرمجة، وكل ما يكتب أو
ينشر مُستبدَل بمسجّل.

    .\\venv\\Scripts\\python.exe verify_coordinator.py
"""
from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("PYTHONUTF8", "1")
sys.stdout.reconfigure(encoding="utf-8")

from app.aeos.employee.models import EmployeeConfig, EmployeeStatus, Persona  # noqa: E402
from app.aeos.services import orchestrator as O  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# ── فريق مزيّف ──
TEAM = [
    EmployeeConfig(id="e1", user_id=1, platform="x", account_username="alpha",
                   persona=Persona(display_name="أحمد", interests=["tech"])),
    EmployeeConfig(id="e2", user_id=None, platform="x", account_username="beta",
                   persona=Persona(display_name="سارة", interests=["history"])),
    EmployeeConfig(id="e3", user_id=2, platform="x", account_username="gamma"),
    EmployeeConfig(id="e4", user_id=1, platform="x", account_username="old",
                   status=EmployeeStatus.ARCHIVED),
]
O.employees_for.__globals__["_platform"] = lambda: SimpleNamespace(
    repository=SimpleNamespace(list_all=lambda: TEAM, get=lambda i: next(c for c in TEAM if c.id == i)),
    llm=None,
)

print("1) الفريق والملكية")
handles = [c.account_username for c in O.employees_for(1)]
check("موظفو المستخدم + بلا مالك، بلا المؤرشف وبلا موظفي غيره", handles == ["alpha", "beta"], handles)
check("حل بالاسم المعروض", O.resolve_employee("سارة", 1).account_username == "beta")
check("موظف مستخدم آخر غير قابل للحل", O.resolve_employee("gamma", 1) is None)

print("2) التوجيه الهجين")
m = O.mentioned_employees("@alpha و @beta انشروا عن الذكاء", 1)
check("ذِكر موظفَين ⇒ رسالة فريق", O.is_fleet_message("x", m))
check("«كل الموظفين» ⇒ رسالة فريق", O.is_fleet_message("خل كل الموظفين ينشرون", []))
check("«حالة الموظف» (مفرد) ليست رسالة فريق", not O.is_fleet_message("حالة الموظف", []))
one = O.mentioned_employees("@alpha انشر عن الطقس", 1)
check("@ لموظف واحد يُزال من نص الأمر",
      O.strip_mentions("@alpha انشر عن الطقس", one) == "انشر عن الطقس")
check("@ لحساب ليس موظفاً لا يُحتسب", O.mentioned_employees("رد على @someone", 1) == [])


# ── عميل LLM مزيّف ──
def _call(i, name, args):
    return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=name, arguments=json.dumps(args)))


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


dispatched = []
O._Tools._dispatch = lambda self, cfg, payload, caps, label: (
    dispatched.append((cfg.account_username, payload, caps)), self._mark(cfg))

print("3) حلقة الأدوات")
check("بلا LLM ⇒ None (يكمل المسار القديم)",
      O.handle("مرحبا", 1, client_factory=lambda: (None, None)) is None)

fake = FakeClient([
    [_call(1, "order_publish", {"employee": "alpha", "topic": "الذكاء الاصطناعي"}),
     _call(2, "order_publish", {"employee": "@beta", "topic": "الذكاء الاصطناعي"}),
     _call(3, "order_publish", {"employee": "ghost", "topic": "x"})],
    "أسندت المهمة لأحمد وسارة.",
])
out = O.handle("خل الفريق ينشر عن الذكاء الاصطناعي", 1, client_factory=lambda: (fake, "m"))
check("يعيد رد المنسّق النهائي", out and out["message"] == "أسندت المهمة لأحمد وسارة.")
check("أُسند الأمر للموظفَين", [d[0] for d in dispatched] == ["alpha", "beta"], dispatched)
check("النشر يُتحقّق منه بقدرتي النشر",
      dispatched[0][2] == ["publish_content", "publish_thread"])
check("dispatched يعيد الموظفين المكلّفين", out["dispatched"] == ["alpha", "beta"])
tool_msgs = [m for m in fake.seen[-1]["messages"] if m["role"] == "tool"]
check("موظف مجهول ⇒ خطأ يعود للـLLM بقائمة الفريق",
      "error" in tool_msgs[2]["content"] and "@alpha" in tool_msgs[2]["content"])
check("موجّه النظام يحوي الفريق",
      "@alpha" in fake.seen[0]["messages"][0]["content"]
      and "@gamma" not in fake.seen[0]["messages"][0]["content"])
check("لا أداة حذف", not any("delete" in t["function"]["name"] for t in O.TOOLS))

print("4) التعديل يعيد قراءة الإعداد بين الخطوات")
from app.aeos.services import employee_commands as C  # noqa: E402

calls = []
C._handle_update_persona = lambda p, cfg, e: calls.append(("persona", e)) or "p"
C._handle_set_schedule = lambda p, cfg, e: calls.append(("schedule", e)) or "s"
C._handle_teach = lambda p, cfg, e, raw: calls.append(("teach", e)) or "t"
msg = "خلّ اهتمامات أحمد الذكاء الاصطناعي وخلّه يشتغل كل ٣٠ دقيقة"
res = json.loads(O._Tools(1, msg).call("update_employee", json.dumps(
    {"employee": "alpha", "interests": ["ai"], "interval_minutes": 30, "add_instruction": "ركّز"})))
check("كل الحقول طُبّقت بالترتيب", [c[0] for c in calls] == ["persona", "schedule", "teach"], calls)
check("حقل فارغ ⇒ خطأ",
      "error" in json.loads(O._Tools(1, msg).call("update_employee", '{"employee": "alpha"}')))

# الحالة المقاسة فعلاً: الموديل ملأ كل حقول المخطط لطلب «كل 45 دقيقة»
calls.clear()
res = json.loads(O._Tools(1, "خل أحمد يشتغل كل 45 دقيقة").call("update_employee", json.dumps({
    "employee": "alpha", "interests": ["tech"], "tone": "كاتب", "dialect": "خليجية",
    "interval_minutes": 45, "active_hours_start": 0, "active_hours_end": 23,
    "daily_post_target": 0, "add_instruction": ""})))
check("حقول لم يطلبها المالك تُتجاهل (لا شخصية، لا ساعات، لا هدف=0)",
      calls == [("schedule", {"interval_minutes": 45})], calls)
check("المُتجاهَل يُبلَّغ للموديل", "dialect" in res.get("ignored_unrequested_fields", []), res)
check("أداة غير معروفة مرفوضة", "error" in json.loads(O._Tools(1).call("__init__", "{}")))

print("5) فشل الشبكة بعد تنفيذ أداة لا يُخفي ما نُفّذ")


class Boom(FakeClient):
    def _create(self, **kw):
        if self.seen:
            raise RuntimeError("network")
        return super()._create(**kw)


dispatched.clear()
boom = Boom([[_call(1, "order_publish", {"employee": "alpha", "topic": "x"})]])
out = O.handle("انشر", 1, client_factory=lambda: (boom, "m"))
check("يعيد تنبيهاً بما نُفّذ", out and "order_publish" in out["message"] and out["dispatched"] == ["alpha"])

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص المنسّق نجحت")
