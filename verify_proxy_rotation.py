"""
verify_proxy_rotation.py — تدوير IP سعودي لكل جلسة/موظف لمنصة X.

بلا شبكة وبلا اشتراك: بروكسي وهمي محلي يعيد نتيجة geo، ويتحقق من:
  • قوالب اسم المستخدم لكل مزوّد (Bright Data/Oxylabs/Decodo/IPRoyal/SOAX/قالب عام)
  • ثبات الجلسة داخل النافذة وتغيّرها بين النوافذ/الموظفين وسياسة per-request
  • **الضمان السعودي**: IP غير سعودي ⇒ رفض وإعادة محاولة بجلسة جديدة، وبعد
    PROXY_MAX_TRIES فشل صريح (لا نكمل بIP غير سعودي)
  • كاش التحقق، إخفاء كلمة المرور، صيغة Playwright، والتكامل مع new_context

    .\\venv\\Scripts\\python.exe verify_proxy_rotation.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

os.environ["PROXY_DOTENV"] = "0"          # معزول عن .env (قد يحمل مزوّداً حقيقياً)

from app.core import captcha_browser as CB  # noqa: E402
from app.core import proxy_rotation as PR  # noqa: E402

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


@contextmanager
def env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    os.environ.update({k: str(v) for k, v in kw.items()})
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


BASE_ENV = dict(
    PROXY_ENABLED=1, PROXY_USERNAME="base-user", PROXY_PASSWORD="secret-pw",
    PROXY_COUNTRY="sa", PROXY_CITY="riyadh", PROXY_ROTATE="session",
    PROXY_SESSION_TTL=600, PROXY_VERIFY=1, PROXY_MAX_TRIES=3, PROXY_VERIFY_TTL=900,
    PROXY_HOST="127.0.0.1", PROXY_PORT=1,
    PROXY_VERIFY_URL="http://geo.test/json", CAPTCHA_PROXY="",
    CAPTCHA_CLOUDFLARE_PRESOLVE="0",
)


# ── بروكسي وهمي يعيد نتيجة geo ويجمع أسماء المستخدمين ──────────────────────
class FakeGeoProxy:
    def __init__(self, country="SA", ip="5.5.5.5"):
        self.country = country
        self.ip = ip
        self.usernames = []
        self.hits = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                auth = self.headers.get("Proxy-Authorization", "")
                if auth.startswith("Basic "):
                    raw = base64.b64decode(auth[6:]).decode("utf-8", "replace")
                    outer.usernames.append(raw.split(":", 1)[0])
                outer.hits += 1
                body = json.dumps({
                    "status": "success", "country": "Saudi Arabia",
                    "countryCode": outer.country, "city": "Riyadh",
                    "isp": "stc", "query": outer.ip,
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_port

    def stop(self):
        self.server.shutdown()


# ═══════════════════════════════════════════════════════════════════════════
print("1) قوالب اسم المستخدم لكل مزوّد (الشكل الذي يقرأه المزوّد فعلاً)")
cases = [
    ("brightdata", "brd.superproxy.io", 33335, "-country-sa-city-riyadh-session-{sid}"),
    ("oxylabs", "pr.oxylabs.io", 7777, "-cc-SA-city-riyadh-sessid-{sid}"),    ("decodo", "gate.decodo.com", 7000, "-country-sa-city-riyadh-session-{sid}"),
    ("iproyal", "geo.iproyal.com", 12321, "_country-sa_city-riyadh_session-{sid}_lifetime-10"),
    ("soax", "proxy.soax.com", 5000, "-country-sa-region-riyadh-sessionid-{sid}"),
]
for provider, host, port, suffix in cases:
    with env(**{**BASE_ENV, "PROXY_PROVIDER": provider, "PROXY_HOST": host,
                "PROXY_PORT": port}):
        cfg = PR.load_config()
        sid = PR.session_id("emaad5079", cfg, bucket=123)
        url = PR.build_url(cfg, sid)
        user = url.split("://", 1)[1].split(":", 1)[0]
        expected_base = "customer-base-user" if provider == "oxylabs" else "base-user"
        expected = expected_base + suffix.format(sid=sid)
        check(f"{provider}: {expected[:44]}…", user == expected, f"got {user}")
        check(f"{provider}: المنفذ والبروتوكول", f":{port}" in url and url.startswith("http://"))

with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_SESSION_TTL": 1800}):
    cfg = PR.load_config()
    sid = PR.session_id("e", cfg, bucket=1)
    user = PR.build_url(cfg, sid).split("://", 1)[1].split(":", 1)[0]
    check("oxylabs: sesstime يمدّ الجلسة لـ30 دقيقة (الافتراضي عندهم 10)",
          user.endswith(f"-sessid-{sid}-sesstime-30"), user)

with env(**{**BASE_ENV, "PROXY_PROVIDER": "generic",
            "PROXY_USERNAME_TEMPLATE": "{base}-c-{country}-s-{sid}"}):
    cfg = PR.load_config()
    sid = PR.session_id("x", cfg, bucket=1)
    user = PR.build_url(cfg, sid).split("://", 1)[1].split(":", 1)[0]
    check("قالب عام: {base}/{country}/{sid}", user == f"base-user-c-sa-s-{sid}", user)

# ═══════════════════════════════════════════════════════════════════════════
print("\n2) الجلسات: ثبات داخل النافذة، وتغيّر بين الموظفين/النوافذ")
with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs"}):
    cfg = PR.load_config()
    a1 = PR.session_id("emaad5079", cfg, bucket=10)
    a2 = PR.session_id("emaad5079", cfg, bucket=10)
    a3 = PR.session_id("emaad5079", cfg, bucket=11)
    b1 = PR.session_id("other_emp", cfg, bucket=10)
    check("نفس الموظف/النافذة ⇒ نفس الجلسة (sticky)", a1 == a2, f"{a1} vs {a2}")
    check("نافذة زمنية جديدة ⇒ جلسة جديدة (IP جديد)", a1 != a3)
    check("موظف مختلف ⇒ جلسة مختلفة", a1 != b1)
    check("طول المعرّف ≤ 16 (حد Bright Data/Oxylabs)", len(a1) <= 16, a1)

with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_ROTATE": "request"}):
    cfg = PR.load_config()
    check("per-request: كل نداء جلسة مختلفة",
          PR.session_id("e", cfg) != PR.session_id("e", cfg))

with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_ROTATE": "sticky"}):
    cfg = PR.load_config()
    check("sticky: ثابتة حتى بين النوافذ",
          PR.session_id("e", cfg) == PR.session_id("e", cfg))

# ═══════════════════════════════════════════════════════════════════════════
print("\n3) إخفاء الأسرار وصيغة Playwright")
with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs"}):
    url = PR.build_url(PR.load_config(), PR.session_id("e", bucket=1))
    check("mask يحجب كلمة المرور", "secret-pw" not in PR.mask(url) and "***" in PR.mask(url),
          PR.mask(url))
    pw = PR.playwright_proxy(url)
    check("playwright: server بلا بيانات دخول",
          pw["server"].startswith("http://") and "@" not in pw["server"], str(pw))
    # كلمة مرور فيها +: تُبنى %2B في الرابط — تمريرها مشفّرة إلى Playwright = 407
    with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_PASSWORD": "p+a~s"}):
        u2 = PR.build_url(PR.load_config(), "mojsid")
        check("playwright: يفك ترميز كلمة المرور (%2B → +)",
              PR.playwright_proxy(u2)["password"] == "p+a~s", PR.playwright_proxy(u2)["password"])
        check("captcha_browser._proxy_dict يفك الترميز أيضاً (كان 407 حقيقياً)",
              CB._proxy_dict(u2)["password"] == "p+a~s", CB._proxy_dict(u2)["password"])
    check("playwright: username يحمل geo/الجلسة وكلمة المرور منفصلة",
          pw["username"].startswith("customer-base-user-cc-SA") and pw["password"] == "secret-pw",
          str(pw))

# ═══════════════════════════════════════════════════════════════════════════
print("\n4) الضمان السعودي: IP غير سعودي ⇒ رفض وإعادة محاولة")
sa = FakeGeoProxy("SA", "5.5.5.5")
us = FakeGeoProxy("US", "8.8.8.8")

with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_PORT": sa.port}):
    PR.reset_cache()
    got = PR.acquire("emaad5079")
    check("IP سعودي ⇒ يُقبل", bool(got) and "base-user" in got)
    check("الفحص يمرّ من البروكسي نفسه (اسم المستخدم وصل)",
          bool(sa.usernames) and sa.usernames[0].startswith("customer-base-user-cc-SA"),
          str(sa.usernames[:1]))
    hits = sa.hits
    PR.acquire("emaad5079")
    check("كاش التحقق: لا طلب geo ثانٍ لنفس الجلسة", sa.hits == hits, f"{hits}->{sa.hits}")

with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_PORT": us.port}):
    PR.reset_cache()
    try:
        PR.acquire("emaad5079")
        err = ""
    except PR.ProxyError as e:
        err = f"{e.code}|{e}"
    check("IP أمريكي ⇒ رفض صريح NOT_SAUDI", err.startswith("NOT_SAUDI"), err)
    check("أعاد المحاولة بجلسات جديدة (3 محاولات)",
          len(us.usernames) == 3 and len(set(us.usernames)) == 3, str(us.usernames))

with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_PORT": us.port,
            "PROXY_MAX_TRIES": 1}):
    PR.reset_cache()
    before = len(us.usernames)
    try:
        PR.acquire("emaad5079")
    except PR.ProxyError:
        pass
    check("MAX_TRIES=1: محاولة واحدة فقط",
          len(us.usernames) - before == 1, str(us.usernames))

with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_PORT": sa.port,
            "PROXY_VERIFY": 0}):
    PR.reset_cache()
    before = sa.hits
    got = PR.acquire("e")
    check("PROXY_VERIFY=0: لا فحص geo (سرعة، بلا ضمان)",
          bool(got) and sa.hits == before, f"hits {before}->{sa.hits}")

with env(**{**BASE_ENV, "PROXY_USERNAME": ""}):
    check("بلا اسم مستخدم ⇒ الخدمة معطّلة", PR.is_enabled() is False)
    check("المعطّلة ترجع None (سلوك قديم بلا تغيير)", PR.acquire("e") is None)

# ═══════════════════════════════════════════════════════════════════════════
print("\n5) الحالة والتكامل مع المتصفح")
with env(**{**BASE_ENV, "PROXY_PROVIDER": "oxylabs", "PROXY_PORT": sa.port}):
    PR.reset_cache()
    st = PR.status(check=True)
    check("status: المزوّد/الدولة/التدوير", st["provider"] == "oxylabs"
          and st["country"] == "sa" and st["rotate"] == "session", str(st))
    check("status: فحص فعلي يعيد SA",
          (st.get("last_check") or {}).get("countryCode") == "SA", str(st.get("last_check")))
    check("status: بلا كلمة مرور", "secret-pw" not in json.dumps(st, ensure_ascii=False))
    check("launch_proxy_kwargs: عنصر نائب لبروكسي كل سياق",
          CB.launch_proxy_kwargs() == {"proxy": {"server": "http://per-context"}},
          str(CB.launch_proxy_kwargs()))

    class Boom:
        @staticmethod
        def is_enabled():
            return True

        @staticmethod
        def acquire(_scope):
            raise RuntimeError("المزوّد لا يرد")

    orig = CB._proxy_module
    CB._proxy_module = lambda: Boom
    try:
        CB.new_context(None, None)
        raised = ""
    except RuntimeError as e:
        raised = str(e)
    finally:
        CB._proxy_module = orig
    check("new_context: يفشل صريحاً إن تعذّر البروكسي (لا IP حقيقي)",
          "تدوير IP مفعّل" in raised, raised)

# بلا تدوير: لا عنصر نائب ولا بروكسي
with env(**{**BASE_ENV, "PROXY_USERNAME": ""}):
    check("بلا تدوير: launch بلا proxy", CB.launch_proxy_kwargs() == {},
          str(CB.launch_proxy_kwargs()))

sa.stop()
us.stop()

print("\n" + ("✅ كل فحوص تدوير IP نجحت" if not FAILED else f"❌ فشل {len(FAILED)}: {FAILED}"))
sys.exit(1 if FAILED else 0)
