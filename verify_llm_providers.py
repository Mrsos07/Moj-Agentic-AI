"""
verify_llm_providers.py — مزوّد النماذج لكل مستخدم: المفاتيح المشفّرة، العزل، الاختيار المنفصل
للشات والموظفين، الرجوع لإعداد النظام، وتوجيه نداءات دورة الموظف لنموذج مالكه.

على قاعدة بيانات مؤقتة، والشبكة مُحاكاة (لا مفاتيح ولا رصيد). ``--live`` يضيف فحصاً حياً
لـ Gemini (GEMINI_API_KEY من .env) وOllama المحلي إن كانا متاحين.

    .\\venv\\Scripts\\python.exe verify_llm_providers.py [--live]
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TOPIC_SEMANTIC", "0")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import app.db.database as DB  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="moj_llm_"))
from app.db.testing import use_temp_database  # noqa: E402

_engine = use_temp_database(TMP)  # Alembic head، SQLite مؤقت أو PG إن ضُبط MOJ_TEST_DATABASE_URL
from app.db import models as M  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import app.core.llm_catalog as CAT  # noqa: E402
import app.services.user_llm as UL  # noqa: E402
from app.auth.security import create_access_token  # noqa: E402
from app.core.llm_provider import LLM_OWNER, resolve_llm  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def _user(email):
    db = DB.SessionLocal()
    u = M.User(email=email, password_hash="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    return u.id, {"Authorization": f"Bearer {create_access_token({'sub': str(u.id), 'email': email})}"}


from app.main import app  # noqa: E402

client = TestClient(app)
A, HA = _user("a@t.local")
B, HB = _user("b@t.local")
SYSTEM = resolve_llm()

print("1) الواجهة والعزل")
check("بلا تسجيل دخول ← 401", client.get("/api/llm/providers").status_code == 401)
r = client.get("/api/llm/providers", headers=HA).json()
check("المزوّدون الخمسة: OpenRouter وOpenAI وClaude وGoogle وOllama",
      [p["id"] for p in r["providers"]] == ["openrouter", "openai", "anthropic", "google", "ollama"], r)
check("بلا اختيار ← الشات والموظفون على إعداد النظام", not r["chat"]["provider"] and not r["employee"]["provider"])
KEY = "sk-test-SECRET-1234567890abcd"
r = client.put("/api/llm/providers/openai/key", headers=HA, json={"api_key": KEY})
po = next(p for p in r.json()["providers"] if p["id"] == "openai")
check("حفظ المفتاح ← مربوط، ولا يُعاد إلا آخر 4 أحرف", r.status_code == 200 and po["has_key"]
      and po["key_last4"] == "abcd" and KEY not in r.text, r.text[:200])
db = DB.SessionLocal()
row = db.query(M.UserLLMSettings).filter_by(user_id=A).one()
db.close()
check("المفتاح مشفّر في القاعدة (لا نص صريح)", KEY not in (row.keys_json or "")
      and UL._v().decrypt(json.loads(row.keys_json)["openai"]) == KEY)
check("مستخدم آخر لا يرى مفتاح غيره", not next(p for p in client.get("/api/llm/providers", headers=HB).json()
                                              ["providers"] if p["id"] == "openai")["has_key"])
check("ولا يستطيع اختيار مزوّد بمفتاح غيره (400)", client.put("/api/llm/selection/chat", headers=HB, json={
    "provider": "openai", "model": "gpt-4o"}).status_code == 400)
check("مفتاح بصيغة خاطئة (مسافات) ← 400", client.put("/api/llm/providers/google/key", headers=HA,
                                                      json={"api_key": "abc def ghi jkl"}).status_code == 400)
check("Ollama لا يُخزَّن له مفتاح ولا عنوان", client.put("/api/llm/providers/ollama/key", headers=HA,
                                                       json={"api_key": "http://10.0.0.1:80"}).status_code == 400)

print("2) سرد النماذج (شبكة مُحاكاة)")
calls = []


class _R:
    def __init__(self, data, code=200):
        self._d, self.status_code = data, code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)

    def json(self):
        return self._d


def fake_get(url, headers=None, timeout=None):
    calls.append((url, headers or {}))
    if "api.openai.com" in url:
        return _R({"data": [{"id": i} for i in ("gpt-4o", "gpt-4o-mini", "whisper-1", "text-embedding-3-small",
                                                  "dall-e-3", "o3-mini", "babbage-002", "gpt-4o-realtime-preview")]})
    if "generativelanguage" in url:
        return _R({"data": [{"id": "models/gemini-2.5-flash"}, {"id": "models/gemini-2.5-flash-image"},
                            {"id": "models/text-embedding-004"}, {"id": "models/gemini-2.5-pro"}]})
    if "anthropic.com" in url:
        return _R({"data": [{"id": "claude-sonnet-4-5"}, {"id": "claude-haiku-4-5"}]})
    if "11434/api/tags" in url:
        return _R({"models": [{"name": "qwen3:8b"}, {"name": "llama3.2:latest"}]})
    if "openrouter.ai" in url:
        return _R({}, 401)
    return _R({}, 404)


CAT.requests.get, _real_get = fake_get, CAT.requests.get
r = client.get("/api/llm/providers/openai/models", headers=HA).json()
check("OpenAI: نماذج الدردشة فقط (بلا صوت/تضمين/صور/realtime)", r["models"] == ["gpt-4o", "gpt-4o-mini", "o3-mini"], r)
check("والمفتاح يُرسل في Authorization", calls[-1][1].get("Authorization") == f"Bearer {KEY}")
client.put("/api/llm/providers/google/key", headers=HA, json={"api_key": "AIza-test-google-key-1"})
check("Google: البادئة models/ تُنزع والصور والتضمين تُستبعد",
      client.get("/api/llm/providers/google/models", headers=HA).json()["models"] == ["gemini-2.5-flash", "gemini-2.5-pro"])
client.put("/api/llm/providers/anthropic/key", headers=HA, json={"api_key": "sk-ant-test-123456789"})
r = client.get("/api/llm/providers/anthropic/models", headers=HA).json()
check("Claude: واجهته الأصلية (x-api-key + anthropic-version)", r["models"] == ["claude-haiku-4-5", "claude-sonnet-4-5"]
      and calls[-1][1].get("x-api-key") == "sk-ant-test-123456789" and calls[-1][1].get("anthropic-version"), calls[-1])
r = client.get("/api/llm/providers/ollama/models", headers=HA).json()
check("Ollama: من جهاز الخادم فقط (127.0.0.1:11434) بلا مفتاح", r["models"] == ["llama3.2:latest", "qwen3:8b"]
      and calls[-1][0].startswith("http://127.0.0.1:11434/"), calls[-1])
check("مزوّد بلا مفتاح ← رسالة واضحة (400)", "مفتاح" in client.get("/api/llm/providers/openrouter/models",
                                                                   headers=HA).json().get("detail", ""))
CAT.requests.get = _real_get

print("3) اختبار الاتصال (نداء صغير فعلي — مُحاكى)")
import openai  # noqa: E402

_RealOpenAI = openai.OpenAI


class _FakeErr(Exception):
    def __init__(self, msg, status):
        super().__init__(msg)
        self.status_code = status


class _FakeOpenAI:
    mode = "ok"

    def __init__(self, **kw):
        self.kw = kw
        outer = self

        class _C:
            def create(self, model, messages, max_tokens=None, **_):
                if _FakeOpenAI.mode == "quota":
                    raise _FakeErr("You have no credits remaining (insufficient_quota)", 429)
                from types import SimpleNamespace as NS

                return NS(choices=[NS(message=NS(content="ok"))])

        self.chat = type("X", (), {"completions": _C()})()


openai.OpenAI = _FakeOpenAI
_FakeOpenAI.mode = "quota"
r = client.post("/api/llm/providers/openai/test", headers=HA, json={"model": "gpt-4o"})
check("حساب بلا رصيد (429) ← رسالة «بلا رصيد» لا «نجاح» (حالة مفتاح OpenAI الحقيقي)",
      r.status_code == 400 and "رصيد" in r.text, r.text[:200])
_FakeOpenAI.mode = "ok"
r = client.post("/api/llm/providers/openai/test", headers=HA, json={"model": "gpt-4o"})
check("اختبار ناجح ← يُسجَّل في حالة المزوّد", r.status_code == 200 and next(
    p for p in client.get("/api/llm/providers", headers=HA).json()["providers"] if p["id"] == "openai")["last_test"]["ok"])
openai.OpenAI = _RealOpenAI

print("4) الاختيار المنفصل والرجوع لإعداد النظام")
r = client.put("/api/llm/selection/chat", headers=HA, json={"provider": "openai", "model": "gpt-4o"})
check("اختيار نموذج الشات", r.status_code == 200 and r.json()["model"] == "gpt-4o")
ep = resolve_llm(user_id=A, purpose="chat")
check("resolve_llm للشات ← OpenAI بمفتاح المستخدم ونموذجه", ep.provider == "openai" and ep.model == "gpt-4o"
      and ep.api_key == KEY and ep.base_url == "https://api.openai.com/v1")
check("والتضمين والصور يبقيان من إعداد النظام", ep.embedding_model == SYSTEM.embedding_model
      and ep.image_model == SYSTEM.image_model)
check("الموظفون بلا اختيار ← إعداد النظام (منفصل عن الشات)",
      resolve_llm(user_id=A, purpose="employee").provider == SYSTEM.provider)
check("مستخدم آخر ← إعداد النظام", resolve_llm(user_id=B, purpose="chat").provider == SYSTEM.provider)
check("بلا user_id (كل المواضع القديمة) ← إعداد النظام بلا تغيير", resolve_llm().provider == SYSTEM.provider
      and resolve_llm().model == SYSTEM.model)
r = client.put("/api/llm/selection/employee", headers=HA, json={"provider": "ollama", "model": "qwen3:8b"})
ep = resolve_llm(user_id=A, purpose="employee")
check("نموذج الموظفين: Ollama المحلي بلا مفتاح", r.status_code == 200 and ep.provider == "ollama"
      and ep.base_url == "http://127.0.0.1:11434/v1" and ep.model == "qwen3:8b")
check("اختيار غرض غير معروف ← 400", client.put("/api/llm/selection/admin", headers=HA,
                                                json={"provider": "openai"}).status_code == 400)
from app.aeos.services import orchestrator as O  # noqa: E402

c, model = O._client(A)
check("المنسّق يستخدم نموذج شات المستخدم", model == "gpt-4o" and str(c.base_url).startswith("https://api.openai.com"))
c, model = O._client(B)
check("ومنسّق مستخدم آخر على إعداد النظام", c is None or model == SYSTEM.model)
client.put("/api/llm/providers/openai/key", headers=HA, json={"api_key": ""})
check("حذف المفتاح ← يسقط الاختيار المعتمد عليه ويرجع الشات لإعداد النظام",
      resolve_llm(user_id=A, purpose="chat").provider == SYSTEM.provider
      and not client.get("/api/llm/providers", headers=HA).json()["chat"]["provider"])

print("5) دورة الموظف: كل نداءاتها لنموذج مالكه")
from app.aeos.core.interfaces import ILLMProvider  # noqa: E402
from app.aeos.core import llm as LLMMOD  # noqa: E402


class _Default(ILLMProvider):
    calls = 0

    @property
    def available(self):
        return True

    def complete(self, system, prompt, *, temperature=0.7, max_tokens=1000):
        _Default.calls += 1
        return "SYSTEM"

    def complete_json(self, system, prompt, *, temperature=0.3):
        return {"by": "system"}


seen_models = []
_real_complete = LLMMOD.OpenAICompatibleLLMProvider.complete


def _fake_complete(self, system, prompt, *, temperature=0.7, max_tokens=1000):
    seen_models.append((self._provider, self._model))
    return "" if self._model == "broken-model" else f"USER:{self._model}"


LLMMOD.OpenAICompatibleLLMProvider.complete = _fake_complete
routed = LLMMOD.RoutedLLMProvider(_Default())
check("بلا مالك ← النموذج الافتراضي", routed.complete("s", "p") == "SYSTEM")
tok = LLM_OWNER.set((A, "employee"))
out = routed.complete("s", "p")
check("دورة موظف المستخدم A ← نموذج «الموظفين» الذي اختاره (Ollama qwen3:8b)",
      out == "USER:qwen3:8b" and seen_models[-1] == ("ollama", "qwen3:8b"), (out, seen_models))
LLM_OWNER.reset(tok)
tok = LLM_OWNER.set((B, "employee"))
check("دورة موظف مستخدم بلا اختيار ← الافتراضي", routed.complete("s", "p") == "SYSTEM")
LLM_OWNER.reset(tok)
client.put("/api/llm/selection/employee", headers=HA, json={"provider": "google", "model": "broken-model"})
tok = LLM_OWNER.set((A, "employee"))
before = _Default.calls
check("فشل نموذج المستخدم (مفتاح/رصيد) ← هذا النداء بالافتراضي بدل محتوى فارغ",
      routed.complete("s", "p") == "SYSTEM" and _Default.calls == before + 1)
LLM_OWNER.reset(tok)
tok = LLM_OWNER.set((A, "chat"))
check("الغرض «chat» لا يستخدم نموذج الموظفين (الشات بلا اختيار الآن ← الافتراضي)", routed.complete("s", "p") == "SYSTEM")
LLM_OWNER.reset(tok)
LLMMOD.OpenAICompatibleLLMProvider.complete = _real_complete

print("6) أمان التخزين")
_real_vault = UL._vault
from app.aeos.security.vault import FernetVault  # noqa: E402

UL._vault = FernetVault("a-different-master-key")
UL.invalidate(A)
v = client.get("/api/llm/providers", headers=HA)
check("تغيّر مفتاح التشفير ← المفاتيح القديمة تُعامل كغير موجودة بلا انهيار", v.status_code == 200
      and not next(p for p in v.json()["providers"] if p["id"] == "google")["has_key"])
UL._vault = _real_vault
UL.invalidate(A)

print("6b) OpenRouter من الشات: قائمته نفسها، وعلى رصيد النظام للمدير/المطوّر فقط")
FAKE_OR = [
    {"id": "anthropic/claude-sonnet-4.5", "name": "Anthropic: Claude Sonnet 4.5", "context": 1000000,
     "price_in": 3.0, "price_out": 15.0, "free": False},
    {"id": "google/gemini-2.5-flash", "name": "Google: Gemini 2.5 Flash", "context": 1048576,
     "price_in": 0.3, "price_out": 2.5, "free": False},
    {"id": "openai/gpt-4o-mini", "name": "OpenAI: GPT-4o-mini", "context": 128000,
     "price_in": 0.15, "price_out": 0.6, "free": False},
]
_real_catalog = CAT.openrouter_catalog
CAT.openrouter_catalog = lambda force=False: FAKE_OR
C, HC = _user("dev@t.local")
db = DB.SessionLocal()
from app.auth.roles import set_role  # noqa: E402

set_role(db.query(M.User).get(C), "developer")
db.commit()
db.close()
UL.invalidate(C)
UL.invalidate(B)
try:
    if SYSTEM.provider == "openrouter":
        vb = client.get("/api/llm/providers", headers=HB).json()
        orb = next(p for p in vb["providers"] if p["id"] == "openrouter")
        check("مستخدم عادي بلا مفتاح: لا رصيد نظام ولا OpenRouter", not vb["system_models"] and not orb["connected"])
        check("ولا يرى قائمة OpenRouter (400)", client.get("/api/llm/openrouter/models", headers=HB).status_code == 400)
        r = client.put("/api/llm/selection/chat", headers=HB, json={"provider": "openrouter",
                                                                     "model": "anthropic/claude-sonnet-4.5"})
        check("ولا يختار نموذجاً على رصيد النظام (400)", r.status_code == 400, r.text[:120])

        vc = client.get("/api/llm/providers", headers=HC).json()
        orc = next(p for p in vc["providers"] if p["id"] == "openrouter")
        check("المطوّر: OpenRouter متاح على رصيد النظام بلا مفتاح", vc["system_models"] and orc["connected"]
              and orc["via_system"] and not orc["has_key"])
        lst = client.get("/api/llm/openrouter/models", headers=HC).json()
        check("القائمة الغنية: اسم وسعر وسياق", lst["via_system"] and lst["models"][0]["price_out"] == 15.0
              and lst["models"][0]["context"] == 1000000)
        r = client.put("/api/llm/selection/chat", headers=HC, json={"provider": "openrouter",
                                                                     "model": "anthropic/claude-sonnet-4.5"})
        check("يختار نموذجاً من القائمة", r.status_code == 200 and r.json()["model"] == "anthropic/claude-sonnet-4.5")
        r = client.put("/api/llm/selection/chat", headers=HC, json={"provider": "openrouter", "model": "made/up-model"})
        check("نموذج خارج القائمة (أو بلا أدوات) ← 400", r.status_code == 400, r.text[:120])
        ep = resolve_llm(user_id=C, purpose="chat")
        check("الشات يذهب للنموذج المختار بمفتاح النظام وعنوانه",
              ep.model == "anthropic/claude-sonnet-4.5" and ep.api_key == SYSTEM.api_key
              and ep.base_url == SYSTEM.base_url and ep.provider == "openrouter", (ep.model, ep.provider))
        check("الموظفون يبقون على إعداد النظام (اختيار مستقل)",
              resolve_llm(user_id=C, purpose="employee").model == SYSTEM.model)

        from app.aeos.services.orchestrator import TOOLS, _Tools  # noqa: E402

        check("أداة المنسّق chat_model مسجّلة", any(t["function"]["name"] == "chat_model" for t in TOOLS))
        tools = _Tools(C)
        found = tools.chat_model({"action": "search", "query": "gemini flash"})
        check("من الشات: بحث «gemini flash» يجد النموذج", found["count"] == 1
              and found["models"][0]["id"] == "google/gemini-2.5-flash", found)
        res = tools.chat_model({"action": "set", "model": "google/gemini-2.5-flash"})
        check("من الشات: set يغيّر نموذج الشات", res["ok"] and resolve_llm(user_id=C).model == "google/gemini-2.5-flash")
        try:
            tools.chat_model({"action": "set", "model": "gemini"})
            ok = False
        except LookupError:
            ok = True
        check("من الشات: اسم غير كامل لا يُحفظ (يجب search أولاً)", ok)
        check("من الشات: current", tools.chat_model({"action": "current"})["model"] == "google/gemini-2.5-flash")
        tools.chat_model({"action": "reset"})
        check("من الشات: reset ← افتراضي النظام", resolve_llm(user_id=C).model == SYSTEM.model)
        try:
            _Tools(B).chat_model({"action": "search", "query": "claude"})
            ok = False
        except ValueError:
            ok = True
        check("أداة الشات تحترم الصلاحية (مستخدم عادي بلا مفتاح)", ok)
        print("6c) نموذج لكل موظف من بطاقته")
        from app.aeos.employee.repository import SqlEmployeeRepository  # noqa: E402
        from verify_creator import make_config  # noqa: E402

        repo = SqlEmployeeRepository()
        emp1, emp2 = make_config(), make_config()
        emp1.id, emp1.user_id, emp1.account_username, emp1.cookie_label = "emp-one", C, "emp_one", "emp_one"
        emp2.id, emp2.user_id, emp2.account_username, emp2.cookie_label = "emp-two", C, "emp_two", "emp_two"
        repo.save(emp1)
        repo.save(emp2)
        v = client.get("/api/aeos/employees/emp-one/llm", headers=HC).json()
        check("بلا اختيار: الموظف يتبع العام ← النظام", v["source"] == "system" and not v["selection"]["provider"], v)
        r = client.put("/api/aeos/employees/emp-one/llm", headers=HC,
                       json={"provider": "openrouter", "model": "anthropic/claude-sonnet-4.5"})
        check("المالك يختار نموذجاً لهذا الموظف من بطاقته", r.status_code == 200 and r.json()["source"] == "employee"
              and r.json()["effective"]["model"] == "anthropic/claude-sonnet-4.5", r.text[:160])
        saved = repo.get("emp-one")
        check("يُحفظ في business_rules.llm (بلا ترحيل) ولا يمسح بقية القواعد",
              saved.business_rules.get("llm", {}).get("model") == "anthropic/claude-sonnet-4.5"
              and saved.business_rules.get("daily_post_target") == 2, saved.business_rules.get("llm"))
        client.put("/api/llm/selection/employee", headers=HC, json={"provider": "openrouter", "model": "openai/gpt-4o-mini"})
        sel1 = (saved.business_rules or {}).get("llm")
        sel2 = (repo.get("emp-two").business_rules or {}).get("llm")
        check("داخل الدورة: الموظف المختار بنموذجه هو", UL.endpoint_for(C, "employee", sel1).model == "anthropic/claude-sonnet-4.5")
        check("وزميله بلا اختيار يتبع نموذج الموظفين العام",
              UL.endpoint_for(C, "employee", sel2).model == "openai/gpt-4o-mini")
        tok = LLM_OWNER.set((C, "employee", sel1))
        picked = LLMMOD.RoutedLLMProvider(_Default())._pick()
        LLM_OWNER.reset(tok)
        check("RoutedLLMProvider يوجّه نداءات الدورة لنموذج الموظف (LLM_OWNER بثلاثة عناصر)",
              getattr(picked, "_model", "") == "anthropic/claude-sonnet-4.5", getattr(picked, "_model", picked))
        r = client.put("/api/aeos/employees/emp-one/llm", headers=HC, json={"provider": "openrouter", "model": "made/up"})
        check("نموذج خارج القائمة ← 400", r.status_code == 400)
        check("موظف مستخدم آخر ← 404", client.get("/api/aeos/employees/emp-one/llm", headers=HB).status_code == 404)
        r = client.put("/api/aeos/employees/emp-one/llm", headers=HC, json={"provider": "", "model": ""})
        check("«يتبع العام» يزيل الاختيار الخاص", r.json()["source"] == "owner"
              and "llm" not in repo.get("emp-one").business_rules, r.json())
        res = _Tools(C).chat_model({"action": "set", "employee": "emp_two", "model": "google/gemini-2.5-flash"})
        check("من الشات: «خلّ @emp_two يستخدم Gemini» ← لهذا الموظف وحده",
              res["ok"] and repo.get("emp-two").business_rules["llm"]["model"] == "google/gemini-2.5-flash"
              and "llm" not in repo.get("emp-one").business_rules)
        client.put("/api/llm/selection/employee", headers=HC, json={"provider": "", "model": ""})
    else:
        print("  ⚠️ النظام ليس على OpenRouter — تخطّيت فحوص رصيد النظام")
finally:
    CAT.openrouter_catalog = _real_catalog

if "--live" in sys.argv:
    print("7) فحص حي (مفاتيح .env، بلا حفظ في القاعدة الحقيقية)")
    from dotenv import dotenv_values

    env = dotenv_values(".env")
    gk = env.get("GEMINI_API_KEY") or ""
    if gk:
        ms = CAT.list_models("google", gk)
        check("Gemini حي: سرد النماذج", "gemini-2.5-flash" in ms, ms[:6])
        check("Gemini حي: نداء دردشة", CAT.test("google", gk, "gemini-2.5-flash")["ok"])
    try:
        om = CAT.list_models("ollama")
        check("Ollama حي: النماذج المحلية", bool(om), om)
        if om:
            check("Ollama حي: نداء دردشة", CAT.test("ollama", "", om[0])["ok"])
    except ValueError as exc:
        print(f"  ⚠️ Ollama غير متاح: {exc}")

print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص مزوّد النماذج نجحت")
