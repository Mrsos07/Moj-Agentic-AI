"""
verify_security.py — تأمين الأسرار والخوادم الداخلية والدخول، بلا شبكة وبلا X.

كل فحص هنا لثغرة قيست فعلاً في الكود قبل الإصلاح:
- توكن الدخول يُكتب كاملاً في سجل الخادم (رابط WebSocket).
- خادم X Suite (كوكيز كل الحسابات) محمي بقيم منشورة على GitHub.
- loginx: كل المسارات مفتوحة ومنها تنزيل ملف كوكيز أي حساب، وcallback إلى أي عنوان.
- الدخول بلا حدّ لمحاولات التخمين، والحساب المعطّل يستطيع الدخول.

    .\\venv\\Scripts\\python.exe verify_security.py
"""
from __future__ import annotations

import importlib
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


print("1) حجب الأسرار من السجلات")
from app.core.log_redaction import RedactSecretsFilter, redact  # noqa: E402

# توكن مزيّف بصيغة JWT (توقيع وهمي) — لا يصلح لأي شيء
jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwIiwiZmFrZSI6dHJ1ZX0.FAKEsignatureFAKEsignature00"
line = f'127.0.0.1:58733 - "WebSocket /ws/chat?token={jwt}" [accepted]'
check("رابط WebSocket بتوكن ← محجوب", jwt not in redact(line) and "token=***" in redact(line), redact(line))
check("ترويسة Bearer ← محجوبة", "abc.def.ghi" not in redact("Authorization: Bearer abc.def.ghi"))
check("JWT ظاهر في أي نص ← محجوب", jwt not in redact(f"user token is {jwt} ok"))
check("مفتاح مزوّد ← محجوب", "1234567890abcdef" not in redact("key sk-or-v1-1234567890abcdef"))
check("نص عادي لا يتغيّر", redact("GET /api/aeos/team 200 OK") == "GET /api/aeos/team 200 OK")
rec = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1,
                        '%s - "WebSocket %s" [accepted]', ("127.0.0.1:1", f"/ws/chat?token={jwt}"), None)
RedactSecretsFilter().filter(rec)
check("مرشّح السجل يحجب داخل وسائط uvicorn الفعلية", jwt not in rec.getMessage(), rec.getMessage())

print("2) أسرار الخدمات الداخلية: قوية، فريدة، خارج git")
import app.core.internal_secrets as IS  # noqa: E402

DEFAULT_FILE = IS.FILE
tmp = Path(tempfile.mkdtemp()) / "internal_secrets.env"
IS.FILE = tmp
saved = {k: os.environ.pop(k, None) for k in IS.KEYS}
os.environ["XSUITE_API_TOKENS"] = "your-secure-token-here"      # القيمة المنشورة
os.environ["XSUITE_ADMIN_PASS"] = "Mm112233@@"
gen = IS.ensure()
check("القيم المنشورة في المستودع تُستبدل", os.environ["XSUITE_API_TOKENS"] != "your-secure-token-here"
      and os.environ["XSUITE_ADMIN_PASS"] != "Mm112233@@")
check("كل الأسرار الخمسة مولَّدة وقوية (≥40 حرفاً)", set(gen) == set(IS.KEYS)
      and all(len(os.environ[k]) >= 40 for k in IS.KEYS), gen)
first = {k: os.environ[k] for k in IS.KEYS}
for k in IS.KEYS:
    os.environ.pop(k)
IS.ensure()
check("ثابتة عبر إعادة التشغيل (تُقرأ من الملف)", all(os.environ[k] == first[k] for k in IS.KEYS))
os.environ["LOGINX_API_KEY"] = "my-own-strong-value-1234567890"
IS.ensure()
check("قيمة ضبطها المالك صراحةً تُحترم", os.environ["LOGINX_API_KEY"] == "my-own-strong-value-1234567890")
check("ملف الأسرار الافتراضي داخل data/", DEFAULT_FILE.parent.name == "data"
      and DEFAULT_FILE.parent.parent == Path(__file__).resolve().parent, DEFAULT_FILE)
import subprocess  # noqa: E402

ignored = subprocess.run(["git", "check-ignore", "data/internal_secrets.env"],
                         capture_output=True, text=True).returncode == 0
check("git يتجاهل data/internal_secrets.env", ignored)

print("3) خادم X Suite (5789) لا يقبل القيم المنشورة")
sys.path.insert(0, str(Path("app/x").resolve()))
from modules import auth as XA  # noqa: E402

os.environ["XSUITE_API_TOKENS"] = "your-secure-token-here"
check("التوكن المنشور مرفوض ولو ضُبط صراحةً", "your-secure-token-here" not in XA.get_tokens())
os.environ.pop("XSUITE_API_TOKENS")
check("بلا سرّ موروث ← لا توكن صالح (رفض الكل لا قبول الافتراضي)", XA.get_tokens() == set())
src = Path("app/x/app.py").read_text(encoding="utf-8")
check("لا مفتاح جلسات 'change-me' ولا كلمة مرور افتراضية في اللوحة",
      "app.secret_key = os.getenv('SECRET_KEY', 'change-me')" not in src
      and "os.getenv('XSUITE_ADMIN_PASS', 'Mm112233@@')" not in src)
check("مقارنة كلمة المرور ثابتة الزمن", "compare_digest(p, ADMIN_PASS)" in src)

print("4) loginx (5000): مفتاح على كل المسارات + callback محلي فقط")
loginx_dir = str(Path("app/x/loginx").resolve())
sys.path.insert(0, loginx_dir)
os.environ["LOGINX_API_KEY"] = "test-loginx-key-abcdefghijklmnop"
cwd = os.getcwd()
try:
    os.chdir(loginx_dir)
    import importlib.util as _u

    spec = _u.spec_from_file_location("loginx_app", os.path.join(loginx_dir, "app.py"))
    LX = _u.module_from_spec(spec)
    spec.loader.exec_module(LX)
finally:
    os.chdir(cwd)
c = LX.app.test_client()
check("فحص الصحة مفتوح (يستخدمه الجسر)", c.get("/api/health").status_code == 200)
check("تنزيل ملف كوكيز بلا مفتاح ← 401 (كان استيلاءً كاملاً على الحساب)",
      c.get("/cookies/download/emaad5079.json").status_code == 401)
check("بدء دخول بلا مفتاح ← 401", c.post("/api/login", json={"username": "a", "password": "b"}).status_code == 401)
check("مفتاح خاطئ ← 401", c.get("/api/status/x", headers={"X-API-Key": "wrong"}).status_code == 401)
check("المفتاح الصحيح يُقبل", c.get("/api/status/unknown", headers={
    "X-API-Key": "test-loginx-key-abcdefghijklmnop"}).status_code == 404)
check("callback لعنوان خارجي ← يُسقط (SSRF)", LX._safe_callback("http://evil.example/steal") is None
      and LX._safe_callback("http://169.254.169.254/latest") is None)
check("callback لهذا الجهاز ← يُقبل",
      LX._safe_callback("http://127.0.0.1:8000/api/internal/login-callback?sig=x") is not None)
check("مفتاح الجلسات ليس القيمة المنشورة", LX.app.secret_key != "x_automation_2026")

print("5) الدخول: حدّ للتخمين، والمعطّل لا يدخل")
from app.auth.routes import _LoginThrottle  # noqa: E402

t = _LoginThrottle()
for _ in range(5):
    t.fail("a@x.com", "1.1.1.1")
check("5 محاولات فاشلة للبريد ← حظر مؤقت", t.retry_after("a@x.com", "1.1.1.1") > 0)
check("بريد آخر من عنوان آخر غير متأثر", t.retry_after("b@x.com", "2.2.2.2") == 0)
for i in range(20):
    t.fail(f"u{i}@x.com", "9.9.9.9")
check("20 محاولة من عنوان واحد على بُرُد مختلفة ← حظر العنوان", t.retry_after("new@x.com", "9.9.9.9") > 0)
t2 = _LoginThrottle()
for _ in range(4):
    t2.fail("c@x.com", "3.3.3.3")
t2.success("c@x.com")
check("النجاح يمسح عدّاد البريد", t2.retry_after("c@x.com", "4.4.4.4") == 0)
rsrc = Path("app/auth/routes.py").read_text(encoding="utf-8")
check("الحساب المعطّل (is_active=False) يُرفض بنفس رسالة الخطأ", 'is_active", True) is False' in rsrc)

print("6) CORS والـWebSocket")
mainsrc = Path("app/main.py").read_text(encoding="utf-8")
check("لا نمط ngrok عام افتراضياً", 'os.getenv("ALLOWED_ORIGIN_REGEX", "")' in mainsrc)
check("الـWebSocket لا يقرأ التوكن من الرابط", 'query_params.get("token"' not in mainsrc)
fe = Path("frontend/src/components/ChatInterface.jsx").read_text(encoding="utf-8")
check("الواجهة لا تضع التوكن في رابط السوكت", "ws/chat?token" not in fe and "type: 'auth'" in fe)

for k, v in saved.items():
    if v is not None:
        os.environ[k] = v
print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص الأمان نجحت")
