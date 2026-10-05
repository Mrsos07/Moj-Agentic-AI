"""
تحقّق ذاكرة الوكلاء (قصيرة + طويلة المدى) — قاعدة بيانات مؤقتة، LLM وتضمينات
مزيّفة، بلا شبكة وبلا مساس بالبيانات الحية.

    .\\venv\\Scripts\\python.exe verify_memory.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("PYTHONUTF8", "1")
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.database import Base  # noqa: E402
from app.db import models as DBM  # noqa: E402
from app.aeos.memory import agent_memory as AM  # noqa: E402
from app.aeos.employee import topic_intel  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# ── قاعدة مؤقتة ──
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
engine = create_engine(f"sqlite:///{tmp.name}", connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)
AM._session = Session

# ── تضمين مزيّف: محاور مفاهيم + بصمة كلمات خفيفة (نصّان مختلفان ≠ متجه واحد) ──
AXES = {"سياس": 0, "حكوم": 0, "جمعة": 1, "إجاز": 1, "اقتصاد": 2, "نفط": 4, "إيموجي": 3, "رموز": 3}


def fake_embed(texts):
    import zlib

    out = []
    for t in texts:
        v = [0.0] * 5 + [0.0] * 16
        for cue, ax in AXES.items():
            if cue in t:
                v[ax] += 3.0
        for w in t.split():
            v[5 + zlib.crc32(w.encode()) % 16] += 0.25
        out.append(v)
    return out


fake_embed.model = "fake"
topic_intel.embedder_factory = lambda: fake_embed


class FakeLLM:
    available = True

    def __init__(self, json_reply=None, text="ملخّص: طلب المالك نشراً عن النفط وأُسند لعماد."):
        self.json_reply, self.text, self.calls = json_reply or {}, text, []

    def complete(self, system, prompt, **kw):
        self.calls.append(("complete", prompt))
        return self.text

    def complete_json(self, system, prompt, **kw):
        self.calls.append(("json", prompt))
        return self.json_reply


C = AM.coordinator_scope(2)
E1, E2 = AM.employee_scope("emp1"), AM.employee_scope("emp2")

print("1) الكتابة والدمج")
a = AM.add(C, "المالك لا يريد منشورات سياسية يوم الجمعة", "preference", 3)
b = AM.add(C, "المالك لا يريد منشورات سياسية يوم الجمعة", "preference", 5)
check("نص متطابق يُدمج لا يتكرر", b["merged"] and b["id"] == a["id"])
check("الدمج يرفع الأهمية للأعلى", b["importance"] == 5)
c = AM.add(C, "ممنوع السياسة في إجازة الجمعة", "preference", 2)
check("تشابه دلالي ≥0.9 يُدمج ويُحدّث النص للأحدث", c["merged"] and c["content"].startswith("ممنوع"))
AM.add(C, "المالك يفضّل منشورات بلا إيموجي ولا رموز", "preference", 4)
AM.add(E1, "عماد يركّز على الاقتصاد والنفط", "instruction", 4)
AM.add(E2, "سارة تكتب عن التاريخ", "instruction", 4)
try:
    AM.add(C, "  ", "fact")
    check("ذكرى فارغة مرفوضة", False)
except ValueError:
    check("ذكرى فارغة مرفوضة", True)

print("2) الاسترجاع")
r = AM.search([C], "هل أنشر عن السياسة اليوم الجمعة؟", 3)
check("الأكثر صلة أولاً", r and "السياسة" in r[0]["content"], r)
check("غير المرتبط لا يُسترجع (عتبة الصلة)", all("إيموجي" not in m["content"] for m in r))
check("الاسترجاع يُحدّث عدّاد الاستخدام", AM.search([C], "السياسة الجمعة", 1)[0]["hits"] >= 1)
check("عزل الموظفين: نطاق عماد لا يرى ذاكرة سارة",
      all(m["scope"] == E1 for m in AM.search([E1], "التاريخ الاقتصاد", 5)))
check("الجوهرية (أهمية ≥4) تحضر بلا استعلام", len(AM.core([C])) == 2)

print("3) الحذف المقيّد بالنطاق")
sid = AM.search([E2], "التاريخ", 1, min_relevance=0)[0]["id"]
check("لا يحذف ذكرى خارج نطاقه", not AM.delete(sid, [E1]))
check("يحذف ضمن نطاقه", AM.delete(sid, [E2]))

print("4) الذاكرة قصيرة المدى (الملخّص المتدحرج)")
db = Session()
conv = DBM.Conversation(user_id=2, session_id="s1")
db.add(conv)
db.commit()
base = datetime.utcnow() - timedelta(hours=1)
for i in range(30):
    db.add(DBM.Message(conversation_id=conv.id, role="user" if i % 2 == 0 else "assistant",
                       content=f"رسالة رقم {i}", created_at=base + timedelta(seconds=i)))
db.commit()
llm = FakeLLM()
check("أول تلخيص يحدث (18 رسالة خارج النافذة)", AM.compact(conv.id, llm))
summ = AM.get_summary(conv.id)
check("الملخّص محفوظ ومعه حدّ آخر رسالة مُلخّصة", summ.get("content", "").startswith("ملخّص")
      and summ["meta"]["upto"] > 0)
check("لا تلخيص جديد بلا رسائل جديدة كافية", not AM.compact(conv.id, llm) and len(llm.calls) == 1)
db.add(DBM.Message(conversation_id=conv.id, role="user", content="رسالة رقم 30",
                   created_at=base + timedelta(seconds=30)))
db.commit()
ctx = AM.chat_context(db, conv.id, exclude_last_user="رسالة رقم 30")
check("السياق = ملخّص + نافذة حرفية بلا تكرار الرسالة الحالية",
      ctx["summary"] and len(ctx["turns"]) == 12 and ctx["turns"][-1]["content"] != "رسالة رقم 30")
hist = AM.history_messages(ctx)
check("الملخّص يُقدَّم رسالة نظام في المقدمة", hist[0]["role"] == "system" and "ملخّص" in hist[0]["content"])
db.close()

print("5) الاستخراج طويل المدى")
team = [SimpleNamespace(id="emp1", account_username="emaad"),
        SimpleNamespace(id="emp2", account_username="sara")]
quiet = FakeLLM()
check("بلا مؤشر دوام لا يُنفق نداء استخراج", AM.extract_memories("تمام شكراً لك", "", quiet, ["emaad"]) == []
      and not quiet.calls)
ext = FakeLLM({"memories": [
    {"content": "عماد ينشر عن النفط كل صباح", "kind": "instruction", "importance": 4, "about": "@Emaad"},
    {"content": "اسم شركة المالك موج", "kind": "fact", "importance": 3, "about": "owner"},
    {"content": "x", "kind": "fact"},
    {"content": "نوع غير معروف هنا", "kind": "hack"},
    {"content": "رابع زائد عن الحد", "kind": "fact"}, {"content": "خامس زائد عن الحد", "kind": "fact"},
]})
stats = AM.after_turn(2, None, "تذكّر أن عماد لازم ينشر عن النفط كل صباح وشركتي اسمها موج", "تم", ext, team)
by_content = {s["content"]: s["scope"] for s in stats["stored"]}
check("ما يخص موظفاً يذهب لنطاقه، وما يخص المالك لنطاق المنسّق",
      by_content.get("عماد ينشر عن النفط كل صباح") == E1 and by_content.get("اسم شركة المالك موج") == C, stats)
check("تنقية: القصير والنوع المجهول يسقطان والحد 3",
      len(stats["stored"]) == 3 and "خامس زائد عن الحد" not in by_content and "x" not in by_content)
check("لا دمج خاطئ: ذكريات مختلفة تبقى منفصلة",
      not any(s["merged"] for s in stats["stored"]) and
      any("موج" in m["content"] for m in AM.list_memories([C])))
emp_chat = FakeLLM({"memories": [{"content": "خلّك مختصر في ردودك دائماً", "kind": "instruction",
                                  "importance": 4, "about": "self"}]})
st2 = AM.after_turn(2, None, "خلك دايماً مختصر في ردودك", "حاضر", emp_chat, team, employee=team[1])
check("في دردشة موظف: «self» تذهب لنطاقه", st2["stored"] and st2["stored"][0]["scope"] == E2)
check("الموجّه يعرّف الاستخراج بالموظف المخاطَب", "@sara" in emp_chat.calls[0][1])

print("6) كتل الموجّه")
AM.record_event(2, "أسندت نشراً إلى @emaad: أسعار النفط")
cb = AM.coordinator_brief(2, "وش أنشر عن النفط؟", team)
check("بريف المنسّق: ثوابت + أحداث + وسم الموظف", "ثوابت المالك" in cb and "أسعار النفط" in cb
      and "[@emaad]" in cb, cb)
AM.add(E1, "قلّل الهاشتاقات إلى واحد", "lesson", 3)
eb = AM.employee_brief("emp1", "النفط")
check("بريف الموظف: تعليمات المالك عنه + دروس التقييم", "الاقتصاد" in eb and "الهاشتاقات" in eb, eb)
check("بريف موظف لا يتسرّب فيه شيء من ذاكرة المنسّق", "الجمعة" not in eb)

print("7) أدوات المنسّق والأحداث")
from app.aeos.services import orchestrator as O  # noqa: E402

TEAM_CFG = [SimpleNamespace(id="emp1", account_username="emaad", user_id=2,
                            status=SimpleNamespace(value="active"),
                            persona=SimpleNamespace(display_name="عماد", interests=[]))]
O.employees_for.__globals__["employees_for"] = lambda uid: TEAM_CFG
O._Tools._dispatch = lambda self, cfg, payload, caps, label: self._mark(cfg)
tools = O._Tools(2, "انشر عن الذهب")
res = json.loads(tools.call("order_publish", json.dumps({"employee": "emaad", "topic": "أسعار الذهب"})))
events = AM.recent(C, ("event",), 3)
check("الإسناد يُسجَّل حدثاً تلقائياً من الأداة", res.get("dispatched") and "أسعار الذهب" in events[0]["content"])
bad = json.loads(tools.call("order_publish", json.dumps({"employee": "ghost", "topic": "x"})))
check("أداة فاشلة لا تُسجّل حدثاً", "error" in bad and len(AM.recent(C, ("event",), 10)) == 2)
rem = json.loads(tools.call("remember", json.dumps({"content": "عماد لا يكتب عن الرياضة أبداً",
                                                      "about": "emaad", "importance": 5})))
check("remember عن موظف يُحفظ في نطاقه", rem["scope"] == "@emaad"
      and any("الرياضة" in m["content"] for m in AM.core([E1])))
rec = json.loads(tools.call("recall_memory", json.dumps({"query": "وش طلبت عن الذهب؟", "include_events": True})))
check("recall_memory يعيد الأحداث", any("الذهب" in e["content"] for e in rec["recent_events"]))
foreign = AM.add(AM.coordinator_scope(99), "ذاكرة مستخدم آخر", "fact")
check("forget لا يحذف ذاكرة مستخدم آخر",
      "error" in json.loads(tools.call("forget", json.dumps({"memory_id": foreign["id"]}))))

print("8) الاحتفاظ المحدود بذاكرة الدورة")
db = Session()
old = datetime.utcnow() - timedelta(days=30)
for i in range(170):
    db.add(DBM.EmployeeMemoryRecord(employee_id="emp1", layer="reflection", content=f"درس {i}",
                                    importance=1, created_at=old + timedelta(minutes=i)))
db.commit()
db.close()
removed = AM.prune_employee_memory("emp1", keep_reflection=150)
db = Session()
left = db.query(DBM.EmployeeMemoryRecord).filter_by(employee_id="emp1", layer="reflection").count()
db.close()
check("الدروس القديمة منخفضة الأهمية تُحذف والعدد مسقوف", left == 0 and removed == 170, (left, removed))

engine.dispose()
os.unlink(tmp.name)
print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص الذاكرة نجحت")
