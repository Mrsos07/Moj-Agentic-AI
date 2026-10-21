"""
verify_captcha.py — حل الكابتشا بلا حل يدوي: المزوّدون، ArkoseLogin، بوابة المتصفح، والمحاكي.

بلا شبكة وبلا رصيد: خوادم مزوّدين وهمية على 127.0.0.1 تتحقق من **شكل الطلب**،
وPlaywright يفتح صفحات محلية فيها أدوات كابتشا مزيّفة، والمحاكي كائن وهمي
(بلا LDPlayer). الاستدعاءات المدفوعة الحقيقية في ``--live`` فقط.

    .\\venv\\Scripts\\python.exe verify_captcha.py
    .\\venv\\Scripts\\python.exe verify_captcha.py --live   # رصيد + OCR بمفتاح حقيقي
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO
from urllib.parse import parse_qs

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
# ترتيب مهم: جذر المستودع أولاً، وإلا فإن app/x/loginx/app.py يحجب حزمة app
sys.path.insert(0, os.path.join(ROOT, "app", "x", "loginx"))
sys.path.insert(0, os.path.join(ROOT, "app", "x", "modules"))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from app.core import captcha_browser as CB  # noqa: E402
from app.core import captcha_solver as CS  # noqa: E402

# الفحوص المحلية معزولة عن .env (قد يحمل مفتاحاً حقيقياً)؛ قسم --live يعيد قراءته
os.environ["CAPTCHA_DOTENV"] = "0"

FAILED = []


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def _try(fn):
    try:
        fn()
        return "NO_ERROR"
    except CS.CaptchaError as e:
        return e.code or str(e)
    except Exception as e:  # noqa: BLE001
        return type(e).__name__


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


# ── خادم مزوّد وهمي ─────────────────────────────────────────────────────────
SYNC_SOLUTIONS = {
    "ImageToTextTaskOcrBase": {"text": "LMResm"},
    "FunCaptchaClassification": {"objects": [4], "labels": ["a", "b", "c", "d", "bread", "f"]},
    "ReCaptchaV2Classification": {"objects": [1, 5]},
}
ASYNC_SOLUTIONS = {
    "NoCaptchaTaskProxyless": {"gRecaptchaResponse": "recaptcha-token"},
    "RecaptchaV3TaskProxyless": {"gRecaptchaResponse": "v3-token"},
    "HCaptchaTaskProxyless": {"gRecaptchaResponse": "hcaptcha-token"},
    "TurnstileTaskProxyless": {"token": "turnstile-token"},
    "FunCaptchaTaskProxylessM1": {"token": "arkose-token"},
    "FunCaptchaTaskProxyless": {"token": "arkose-token"},
    "ImageToTextTask": {"text": "AbC12"},
    "CloudFlareTaskS3": {"cookies": {"cf_clearance": "CF-CLEAR"},
                         "user_agent": "UA-Linux-Chrome",
                         "content": "<html>ok</html>"},
    "CloudFlareTaskS2": {"cookies": {"cf_clearance": "CF-CLEAR"},
                         "user_agent": "UA-Linux-Chrome"},
}


class FakeProvider:
    """خادم HTTP يقلّد createTask/getTaskResult/in.php/res.php ويتذكر كل طلب."""

    def __init__(self, mode="yescaptcha"):
        self.mode = mode
        self.requests = []
        self.tasks = {}
        self.counter = 0
        self.fail = None            # كود خطأ يُعاد من getTaskResult/res.php
        self.ready_after = 1        # عدد استعلامات processing قبل ready
        self.polls = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n).decode("utf-8", "replace")
                if "json" in (self.headers.get("Content-Type") or ""):
                    payload = json.loads(raw or "{}")
                else:
                    payload = {k: v[0] for k, v in parse_qs(raw).items()}
                outer.requests.append((self.path, payload))
                status, body = outer.handle(self.path, payload)
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def stop(self):
        self.server.shutdown()

    def handle(self, path, payload):
        if self.mode == "2captcha":
            return self._two(path, payload)
        return self._json(path, payload)

    # YesCaptcha / CapMonster / Anti-Captcha
    def _json(self, path, payload):
        if path.endswith("/getBalance"):
            return 200, {"errorId": 0, "balance": 12.5}
        if path.endswith("/createTask"):
            task = payload.get("task") or {}
            kind = task.get("type")
            if kind in SYNC_SOLUTIONS:
                return 200, {"errorId": 0, "status": "ready",
                             "solution": SYNC_SOLUTIONS[kind], "taskId": "sync-1"}
            self.counter += 1
            task_id = f"t{self.counter}"
            self.tasks[task_id] = kind
            return 200, {"errorId": 0, "taskId": task_id}
        if path.endswith("/getTaskResult"):
            task_id = payload.get("taskId")
            self.polls[task_id] = self.polls.get(task_id, 0) + 1
            if self.fail:
                return 200, {"errorId": 1, "errorCode": self.fail,
                             "errorDescription": "fake failure"}
            if self.polls[task_id] <= self.ready_after:
                return 200, {"errorId": 0, "status": "processing"}
            kind = self.tasks.get(task_id, "")
            return 200, {"errorId": 0, "status": "ready",
                         "solution": ASYNC_SOLUTIONS.get(kind, {"token": "generic-token"})}
        return 404, {"errorId": 1, "errorCode": "NOT_FOUND"}

    # 2Captcha
    def _two(self, path, payload):
        if path.endswith("/in.php"):
            if payload.get("method") == "getbalance":
                return 200, {"status": 1, "request": "9.5"}
            self.counter += 1
            rid = str(self.counter)
            self.tasks[rid] = payload.get("method")
            return 200, {"status": 1, "request": rid}
        if path.endswith("/res.php"):
            if payload.get("action") == "getbalance":
                return 200, {"status": 1, "request": "9.5"}
            rid = payload.get("id")
            self.polls[rid] = self.polls.get(rid, 0) + 1
            if self.fail:
                return 200, {"status": 0, "request": self.fail}
            if self.polls[rid] <= self.ready_after:
                return 200, {"status": 0, "request": "CAPCHA_NOT_READY"}
            return 200, {"status": 1, "request": "two-token"}
        return 404, {"status": 0, "request": "NOT_FOUND"}


def tasks_of(server):
    return [p for path, p in server.requests if path.endswith("/createTask") or path.endswith("/in.php")]


class PageServer:
    """صفحة محلية: أول طلب تحدي Cloudflare، ثم صفحة عادية (لمحاكاة زوال التحدي)."""

    CHALLENGE = ("<html><head><title>Just a moment...</title></head>"
                 "<body>Performing security verification</body></html>")
    NORMAL = "<html><head><title>X</title></head><body>home</body></html>"

    def __init__(self):
        self.challenge = True
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = (outer.CHALLENGE if outer.challenge else outer.NORMAL).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/"

    def stop(self):
        self.server.shutdown()


# ═══════════════════════════════════════════════════════════════════════════
print("1) الإعداد واختيار المزوّد")
with env(CAPTCHA_ENABLED=1, CAPTCHA_PROVIDER="yescaptcha", YESCAPTCHA_KEY="yc-12345678",
         CAPTCHA_KEY="", TWOCAPTCHA_KEY="", CAPMONSTER_KEY="", ANTICAPTCHA_KEY=""):
    cfg = CS.load_config()
    check("المزوّد الصريح يُحترم", cfg.provider == "yescaptcha" and cfg.enabled)
    check("لا مفتاح كامل في status (آخر 4 فقط)", CS.status()["key"] == "…5678"
          and "yc-12345678" not in json.dumps(CS.status(), ensure_ascii=False))

with env(CAPTCHA_PROVIDER="", YESCAPTCHA_KEY="", CAPTCHA_KEY="",
         CAPMONSTER_KEY="cm-key-9999", TWOCAPTCHA_KEY="", ANTICAPTCHA_KEY=""):
    check("بلا CAPTCHA_PROVIDER: أول مفتاح موجود يفوز",
          CS.load_config().provider == "capmonster")

with env(CAPTCHA_ENABLED=0, YESCAPTCHA_KEY="yc-12345678"):
    check("CAPTCHA_ENABLED=0 يعطّل الحل", CS.is_enabled() is False and CS.get_solver() is None)

with env(CAPTCHA_ENABLED=1, CAPTCHA_KEY="", YESCAPTCHA_KEY="", TWOCAPTCHA_KEY="",
         CAPMONSTER_KEY="", ANTICAPTCHA_KEY=""):
    try:
        CS.require_solver()
        raised = False
    except CS.CaptchaError as e:
        raised = "YESCAPTCHA_KEY" in str(e)
    check("بلا مفاتيح: رسالة عربية تشرح ما ينقص", raised)

# ═══════════════════════════════════════════════════════════════════════════
print("\n2) YesCaptcha: شكل الطلب، الاستقصاء، والأخطاء")
yc = FakeProvider("yescaptcha")
with env(CAPTCHA_ENABLED=1, CAPTCHA_PROVIDER="yescaptcha", YESCAPTCHA_KEY="yc-test",
         CAPTCHA_BASE_URL=yc.base, CAPTCHA_POLL=1, CAPTCHA_TIMEOUT=60, CAPTCHA_PROXY=""):
    solver = CS.Solver()
    token = solver.solve_funcaptcha(blob="BLOB123", user_agent="UA/1")
    task = tasks_of(yc)[0]["task"]
    check("Funcaptcha: النوع الحالي M1", task["type"] == "FunCaptchaTaskProxylessM1", str(task))
    check("Funcaptcha: عنوان صفحة X لا نطاق Arkose",
          task["websiteURL"] == CS.X_LOGIN_URL and "arkoselabs" not in task["websiteURL"])
    check("Funcaptcha: مفتاح X العام الافتراضي",
          task["websitePublicKey"] == CS.X_ARKOSE_LOGIN_KEY, task.get("websitePublicKey"))
    check("Funcaptcha: blob يُرسل كسلسلة JSON", task.get("data") == '{"blob": "BLOB123"}')
    check("Funcaptcha: User-Agent ممرَّر", task.get("userAgent") == "UA/1")
    check("التوكن يُستخرج من solution.token", token == "arkose-token", token)
    check("الاستقصاء مرّ على processing ثم ready", yc.polls.get("t1") == 2, str(yc.polls))

    check("reCAPTCHA v2 يُرسل sitekey", solver.solve_recaptcha_v2("https://x.com/a", "KEY1") == "recaptcha-token")
    rec = tasks_of(yc)[-1]["task"]
    check("reCAPTCHA v2: النوع والموقع",
          rec["type"] == "NoCaptchaTaskProxyless" and rec["websiteKey"] == "KEY1")

    solver.solve_recaptcha_v3("https://x.com/a", "KEY3", action="login", min_score=0.9)
    v3 = tasks_of(yc)[-1]["task"]
    check("reCAPTCHA v3: pageAction وminScore",
          v3["type"] == "RecaptchaV3TaskProxyless" and v3["pageAction"] == "login"
          and v3["minScore"] == 0.9)

    solver.solve_hcaptcha("https://x.com/a", "HKEY")
    hc = tasks_of(yc)[-1]["task"]
    check("hCaptcha: userAgent مطلوب في YesCaptcha",
          hc["type"] == "HCaptchaTaskProxyless" and hc.get("userAgent", "").startswith("Mozilla"))

    solver.solve_turnstile("https://x.com/a", "0xKEY")
    ts = tasks_of(yc)[-1]["task"]
    check("Turnstile: النوع الصحيح", ts["type"] == "TurnstileTaskProxyless")

    img = b"\x89PNG-fake-bytes"
    check("OCR متزامن يرجع النص", solver.solve_image(img) == "LMResm")
    ocr = tasks_of(yc)[-1]["task"]
    check("OCR: النوع المتزامن وbody بلا data: prefix",
          ocr["type"] == "ImageToTextTaskOcrBase"
          and ocr["body"] == base64.b64encode(img).decode())
    check("تصنيف FunCaptcha يرجع البلاطة",
          solver.classify_funcaptcha(img, "Pick the bread") == [4])
    cls = tasks_of(yc)[-1]["task"]
    check("التصنيف: image + question حرفياً",
          cls["type"] == "FunCaptchaClassification" and cls["question"] == "Pick the bread")
    check("الرصيد يُقرأ", solver.balance() == 12.5)

    yc.fail = "ERROR_CAPTCHA_UNSOLVABLE"
    try:
        solver.solve_funcaptcha()
        err = ""
    except CS.CaptchaError as e:
        err = f"{e.code}|{e.retryable}"
    check("خطأ المزوّد يُرفع بكوده ويُعتبر قابلاً للإعادة",
          err.startswith("ERROR_CAPTCHA_UNSOLVABLE") and err.endswith("True"), err)

    yc.fail = "ERROR_TASK_TIMEOUT"
    try:
        solver.solve_funcaptcha()
        code = ""
    except CS.CaptchaError as e:
        code = e.code
    check("مهلة المهمة تُرفع صريحة", code == "ERROR_TASK_TIMEOUT", code)

    yc.fail = None
    check("تصنيف على مزوّد لا يدعمه يُرفض بوضوح",
          _try(lambda: CS.Solver(CS.Config(**{**CS.load_config().__dict__,
                                              "provider": "capmonster"})).classify_funcaptcha(img, "x"))
          .startswith("UNSUPPORTED"))

    # صفحة تحدي Cloudflare: بروكسي إلزامي + cf_clearance مطلوب صراحةً
    sol = solver.solve_cloudflare("https://x.com/home", proxy="http://u:p@1.2.3.4:8080")
    cf_task = tasks_of(yc)[-1]["task"]
    check("Cloudflare: النوع S3 (الأرخص) والبروكسي يمرّان",
          cf_task["type"] == "CloudFlareTaskS3"
          and cf_task["proxy"] == "http://u:p@1.2.3.4:8080", str(cf_task))
    check("Cloudflare: requiredCookies=[cf_clearance] حتى لا يُحتسب بلا الكوكي",
          cf_task.get("requiredCookies") == ["cf_clearance"])
    check("Cloudflare: يعيد الكوكيز والـUA",
          sol.get("cookies", {}).get("cf_clearance") == "CF-CLEAR"
          and sol.get("user_agent") == "UA-Linux-Chrome")
    check("Cloudflare بلا بروكسي: رفض صريح قبل أي دفع",
          _try(lambda: solver.solve_cloudflare("https://x.com/home", proxy="")) == "PROXY_REQUIRED")
yc.stop()


# ═══════════════════════════════════════════════════════════════════════════
print("\n3) 2Captcha: in.php/res.php وشكل المعاملات")
two = FakeProvider("2captcha")
with env(CAPTCHA_ENABLED=1, CAPTCHA_PROVIDER="2captcha", TWOCAPTCHA_KEY="two-key",
         CAPTCHA_BASE_URL=two.base, CAPTCHA_POLL=1, CAPTCHA_TIMEOUT=60, CAPTCHA_KEY=""):
    solver = CS.Solver()
    check("Funcaptcha 2Captcha يرجع التوكن", solver.solve_funcaptcha(blob="B") == "two-token")
    req = tasks_of(two)[0]
    check("Funcaptcha: method/publickey/surl",
          req.get("method") == "funcaptcha" and req.get("publickey") == CS.X_ARKOSE_LOGIN_KEY
          and req.get("surl") == "https://client-api.arkoselabs.com", str(req))
    check("Funcaptcha: pageurl صفحة X", req.get("pageurl") == CS.X_LOGIN_URL)
    check("Funcaptcha: blob داخل data", req.get("data") == '{"blob": "B"}')
    check("reCAPTCHA v2: userrecaptcha/googlekey",
          solver.solve_recaptcha_v2("https://x.com/a", "K2") == "two-token"
          and tasks_of(two)[-1].get("googlekey") == "K2")
    solver.solve_turnstile("https://x.com/a", "0xK")
    check("Turnstile: method=turnstile", tasks_of(two)[-1].get("method") == "turnstile")
    check("OCR: method=base64", solver.solve_image(b"img") == "two-token"
          and tasks_of(two)[-1].get("method") == "base64")
    check("الرصيد من getbalance", solver.balance() == 9.5)
    two.fail = "ERROR_CAPTCHA_UNSOLVABLE"
    check("خطأ 2Captcha يصل بكوده",
          _try(lambda: solver.solve_funcaptcha()).startswith("ERROR_CAPTCHA_UNSOLVABLE"))
two.stop()

# ═══════════════════════════════════════════════════════════════════════════
print("\n4) CapMonster وAnti-Captcha: نفس الغلاف")
for prov, key_env in (("capmonster", "CAPMONSTER_KEY"), ("anticaptcha", "ANTICAPTCHA_KEY")):
    srv = FakeProvider("yescaptcha")
    with env(CAPTCHA_ENABLED=1, CAPTCHA_PROVIDER=prov, CAPTCHA_BASE_URL=srv.base,
             CAPTCHA_POLL=1, CAPTCHA_TIMEOUT=60, CAPTCHA_KEY="k", **{key_env: "k"}):
        solver = CS.Solver()
        tok = solver.solve_funcaptcha()
        task = tasks_of(srv)[0]["task"]
        check(f"{prov}: النوع FunCaptchaTaskProxyless", task["type"] == "FunCaptchaTaskProxyless")
        check(f"{prov}: data كائن لا سلسلة", task.get("data") == {"blob": ""} or "data" not in task)
        check(f"{prov}: التوكن يعود", tok == "arkose-token")
        check(f"{prov}: الرصيد", solver.balance() == 12.5)
    srv.stop()

# ═══════════════════════════════════════════════════════════════════════════
print("\n5) تدفق الدخول: ArkoseLogin ← web_modal deeplink")
import x_auth.login as XL  # noqa: E402


class FakeFlow(XL.LoginFlow):
    def __init__(self, steps):
        self.steps = steps
        self.index = 0
        self.subtask_inputs = {}
        self.flow_token = "ft"
        self.subtasks = [{"subtask_id": s} for s in steps[0]]
        self.submitted = []

    async def execute_subtasks(self):
        self.submitted.append(dict(self.subtask_inputs))
        self.subtask_inputs.clear()
        self.index = min(self.index + 1, len(self.steps) - 1)
        self.subtasks = [{"subtask_id": s} for s in self.steps[self.index]]


captured = {}
XL.captcha_bridge.enabled = lambda: True
XL.captcha_bridge.solve_arkose = lambda **kw: captured.update(kw) or "TOKEN-ABC"

flow = FakeFlow([["ArkoseLogin"], ["LoginSuccessSubtask"]])
flow.subtasks = [{"subtask_id": "ArkoseLogin",
                  "web_modal": {"arkose_blob": "BLOB-X", "sitekey": "KEY-X"}}]
asyncio.run(XL._complete_login(flow, ["user"], "pass"))
payload = flow.submitted[0]["ArkoseLogin"]["web_modal"]
check("التوكن يُسلَّم في completion_deeplink",
      payload["completion_deeplink"] ==
      "twitter://onboarding/web_modal/next_link?access_token=TOKEN-ABC", str(payload))
check("الرابط next_link مطلوب أيضاً", payload.get("link") == "next_link")
check("blob يُقرأ من الحمولة المتداخلة", captured.get("blob") == "BLOB-X", str(captured))
check("المفتاح العام يُقرأ من الحمولة", captured.get("public_key") == "KEY-X", str(captured))
check("User-Agent الحقيقي يمرّر للمزوّد", captured.get("user_agent", "").startswith("Mozilla"),
      str(captured.get("user_agent")))

XL.captcha_bridge.enabled = lambda: False
flow2 = FakeFlow([["ArkoseLogin"], ["LoginSuccessSubtask"]])
try:
    asyncio.run(XL._complete_login(flow2, ["user"], "pass"))
    msg = ""
except RuntimeError as e:
    msg = str(e)
check("بلا مفتاح: فشل صريح يشرح المطلوب (لا صمت)", "YESCAPTCHA_KEY" in msg, msg)

# ═══════════════════════════════════════════════════════════════════════════
print("\n6) بوابة المتصفح: كشف، حقن توكن، ورفض عند الفشل")
from playwright.sync_api import sync_playwright  # noqa: E402


class FakeSolver:
    def __init__(self):
        self.calls = []

    def solve_recaptcha_v2(self, url, sitekey, **_kw):
        self.calls.append(("recaptcha_v2", sitekey))
        return "RECAP-TOKEN"

    def solve_hcaptcha(self, url, sitekey, **_kw):
        self.calls.append(("hcaptcha", sitekey))
        return "HCAP-TOKEN"

    def solve_turnstile(self, url, sitekey, **_kw):
        self.calls.append(("turnstile", sitekey))
        return "TS-TOKEN"

    def solve_image(self, image, **_kw):
        self.calls.append(("image", len(image)))
        return "AB12C"

    def solve_funcaptcha(self, **_kw):
        self.calls.append(("funcaptcha", "token"))
        return ""


_img_buf = BytesIO()
Image.new("RGB", (100, 50), "white").save(_img_buf, format="PNG")
IMG_URI = "data:image/png;base64," + base64.b64encode(_img_buf.getvalue()).decode()

PAGES = {
    "recaptcha": """<html><body>
      <div class="g-recaptcha" data-sitekey="SITE-R"></div>
      <textarea id="g-recaptcha-response" name="g-recaptcha-response"></textarea>
      <script>
        window.___grecaptcha_cfg = {clients: {0: {x: {callback: function (t) {
          window.__cb = t; document.querySelector('.g-recaptcha').remove();
        }}}}};
      </script></body></html>""",
    "hcaptcha": """<html><body>
      <div class="h-captcha" data-sitekey="SITE-H" data-callback="onHcap"></div>
      <textarea name="h-captcha-response"></textarea>
      <script>window.onHcap = function (t) {
        window.__cb = t; document.querySelector('.h-captcha').remove();
      };</script></body></html>""",
    "turnstile": """<html><body>
      <div class="cf-turnstile" data-sitekey="0xSITE" data-callback="onTs"></div>
      <input type="hidden" name="cf-turnstile-response">
      <script>window.onTs = function (t) {
        window.__cb = t; document.querySelector('.cf-turnstile').remove();
      };</script></body></html>""",
    "arkose": """<html><body>
      <iframe src="https://client-api.arkoselabs.com/fc/api/nojs/?pkey=KEY"></iframe>
      </body></html>""",
    "image": f"""<html><body>
      <img src="{IMG_URI}" width="100" height="50">
      <input placeholder="Enter the characters">
      <button onclick="document.querySelector('img').remove()">Next</button>
      </body></html>""",
    "blocked": "<html><body>Whoa there! Something went wrong. Try reloading.</body></html>",
    "clean": "<html><body>Hello timeline</body></html>",
}


def page_html(pw, html):
    browser = pw.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page()
    page.set_content(html)
    return browser, page


try:
    with sync_playwright() as pw:
        for name, kind, sitekey in (("recaptcha", "recaptcha_v2", "SITE-R"),
                                    ("hcaptcha", "hcaptcha", "SITE-H"),
                                    ("turnstile", "turnstile", "0xSITE"),
                                    ("arkose", "funcaptcha", ""),
                                    ("image", "image", "")):
            browser, page = page_html(pw, PAGES[name])
            info = CB.detect(page) or {"kinds": []}
            got = CB._kind(info, kind)
            check(f"كشف {name}", got is not None, json.dumps(info, ensure_ascii=False)[:200])
            if sitekey and got:
                check(f"sitekey لـ{name}", got.get("sitekey") == sitekey, str(got))
            browser.close()

        fake = FakeSolver()
        CB._solver.get_solver = lambda: fake

        browser, page = page_html(pw, PAGES["recaptcha"])
        ok = CB.solve(page, wait_s=5)
        check("reCAPTCHA: الحل نجح بعد الحقن", ok)
        check("reCAPTCHA: التوكن في textarea",
              page.evaluate("() => document.querySelector('textarea#g-recaptcha-response').value")
              == "RECAP-TOKEN")
        check("reCAPTCHA: callback استُدعي", page.evaluate("() => window.__cb") == "RECAP-TOKEN")
        browser.close()

        browser, page = page_html(pw, PAGES["hcaptcha"])
        check("hCaptcha: الحقن + callback", CB.solve(page, wait_s=5)
              and page.evaluate("() => window.__cb") == "HCAP-TOKEN")
        browser.close()

        browser, page = page_html(pw, PAGES["turnstile"])
        check("Turnstile: الحقن + callback", CB.solve(page, wait_s=5)
              and page.evaluate("() => window.__cb") == "TS-TOKEN")
        browser.close()

        browser, page = page_html(pw, PAGES["image"])
        check("كابتشا الصورة: OCR يملأ الحقل ويضغط التالي", CB.solve(page, wait_s=5)
              and page.evaluate("() => document.querySelector('input').value") == "AB12C")
        browser.close()

        browser, page = page_html(pw, PAGES["clean"])
        check("صفحة سليمة: لا عائق", CB.detect(page) is None)
        CB.guard_page(page)
        check("صفحة سليمة: guard يمر بلا استثناء", True)
        browser.close()

        browser, page = page_html(pw, PAGES["blocked"])
        try:
            CB.guard_page(page)
            msg = ""
        except CB.CaptchaError as e:
            msg = str(e)
        check("حجب X يُرفع برسالة (لا يُخفى ككابتشا)", "Whoa there" in msg or "حجب" in msg, msg)
        browser.close()

        browser, page = page_html(pw, PAGES["recaptcha"])
        CB._solver.get_solver = lambda: None
        try:
            CB.guard_page(page)
            msg = ""
        except CB.CaptchaError as e:
            msg = str(e)
        check("كابتشا بلا مفتاح: guard يرفع ولا يكمل بصمت", "YESCAPTCHA_KEY" in msg, msg)
        browser.close()

        # ── Cloudflare «Just a moment» (ليس كابتشا Arkose) ──
        cf_srv = PageServer()
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(cf_srv.base)
        check("كشف صفحة Cloudflare",
              CB._kind(CB.detect(page) or {"kinds": []}, "cloudflare") is not None,
              str(CB.detect(page)))

        called = {"n": 0}
        fake2 = FakeSolver()
        fake2.solve_cloudflare = lambda url, proxy="": (
            called.__setitem__("n", called["n"] + 1)
            or {"cookies": {"cf_clearance": "CF"}, "user_agent": ""})
        CB._solver.get_solver = lambda: fake2
        with env(CAPTCHA_PROXY=""):
            check("Cloudflare بلا بروكسي: لا استدعاء مدفوع",
                  CB.solve(page, log=lambda _m: None) is False and called["n"] == 0)

        with env(CAPTCHA_PROXY="http://u:p@1.2.3.4:8080"):
            cf_srv.challenge = False          # بعد الحقن يعود الموقع الطبيعي
            ok = CB.solve(page, log=lambda _m: None)
        check("Cloudflare: حل + حقن cf_clearance + زوال التحدّي",
              ok and called["n"] == 1
              and any(c["name"] == "cf_clearance" for c in page.context.cookies()))
        browser.close()
        cf_srv.stop()

        cf_srv2 = PageServer()
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.goto(cf_srv2.base)
        with env(CAPTCHA_PROXY=""):
            try:
                CB.guard_page(page)
                msg = ""
            except CB.CaptchaError as e:
                msg = str(e)
        check("guard: رسالة Cloudflare صريحة تطلب بروكسياً",
              "Cloudflare" in msg and "CAPTCHA_PROXY" in msg, msg)
        browser.close()
        cf_srv2.stop()
except Exception as e:  # noqa: BLE001
    check("Playwright/Chrome متاح لاختبار المتصفح", False, f"{type(e).__name__}: {e}")

# ═══════════════════════════════════════════════════════════════════════════
print("\n7) المحاكي: قراءة السؤال ونقر البلاطة الصحيحة")
from PIL import Image  # noqa: E402
import j4  # noqa: E402

QUESTION = "Pick the image that is the correct way up"


class FakeEl:
    def __init__(self, dev, kwargs):
        self.dev = dev
        self.kwargs = kwargs

    def exists(self, timeout=0):
        text = self.kwargs.get("textContains") or self.kwargs.get("text") or ""
        return any(text.lower() in t.lower() for t in self.dev.texts)

    def wait(self, timeout=0):
        return self.exists(timeout)

    def click(self):
        self.dev.clicks.append(("el", self.kwargs))

    @property
    def info(self):
        return {"bounds": self.dev.bounds}


class FakeDevice:
    def __init__(self):
        self.texts = ["Verify you are human", QUESTION]
        self.bounds = {"left": 90, "top": 500, "right": 990, "bottom": 560}
        self.clicks = []
        self.solved = False

    def __call__(self, **kwargs):
        return FakeEl(self, kwargs)

    def dump_hierarchy(self):
        return (f'<?xml version="1.0"?><hierarchy><node text="{QUESTION}" '
                f'bounds="[90,500][990,560]"/></hierarchy>')

    def screenshot(self):
        return Image.new("RGB", (1080, 1920), "white")

    def click(self, x, y):
        self.clicks.append((x, y))
        self.solved = True
        self.texts = []


class FakeModule:
    @staticmethod
    def is_enabled():
        return True

    @staticmethod
    def get_solver():
        class S:
            def classify_funcaptcha(self, image, question):
                assert question == QUESTION, question
                assert len(image) > 100
                return [4]
        return S()


auto = j4.LDPlayerChromeAutomation(log_callback=lambda *_a: None)
auto.d = FakeDevice()
check("كشف الكابتشا من نص الشاشة", auto._captcha_present())
check("قراءة سؤال اللغز من شجرة الإمكانية", auto._challenge_question() == QUESTION)
auto._captcha_module = lambda: FakeModule
check("حل المحاكي نجح", auto.solve_captcha())
coords = [c for c in auto.d.clicks if isinstance(c, tuple)]
check("نقر بلاطة واحدة فقط", len(coords) == 1, str(auto.d.clicks))
if coords:
    x, y = coords[0]
    # شبكة 3×2: البلاطة 4 (فهرس) = العمود 1، الصف 1
    check("إحداثيات النقر = مركز البلاطة 5",
          (abs(x - 540) <= 2 and abs(y - 1436) <= 2), f"({x}, {y})")

auto2 = j4.LDPlayerChromeAutomation(log_callback=lambda *_a: None)
auto2.d = FakeDevice()
auto2._captcha_module = lambda: type("M", (), {
    "is_enabled": staticmethod(lambda: False), "get_solver": staticmethod(lambda: None)})()
check("بلا مفتاح: المحاكي يرجع False (لا يدّعي النجاح)", auto2.solve_captcha() is False)

# لغز الأسهم: المزوّد يرجع «أي صورة» لا «أي بلاطة» (عيّنة حقيقية: objects=[1]) —
# النقر على شبكة هنا خطأ صريح، فنرفض بدل التخمين.
ARROW_Q = "Use the arrows to change the number of objects until it matches the left image"
arrow = FakeDevice()
arrow.texts = ["Verify you are human", ARROW_Q]
arrow.dump_hierarchy = lambda: (f'<?xml version="1.0"?><hierarchy><node text="{ARROW_Q}" '
                                f'bounds="[90,500][990,560]"/></hierarchy>')
auto3 = j4.LDPlayerChromeAutomation(log_callback=lambda *_a: None)
auto3.d = arrow
auto3._captcha_module = lambda: FakeModule
check("لغز الأسهم: رفض بلا نقر أعمى", auto3.solve_captcha() is False and not arrow.clicks)

# ═══════════════════════════════════════════════════════════════════════════
print("\n8) الربط: كل مسار نشر/تفاعل يمر على البوابة")
import inspect  # noqa: E402
import app.x.modules.x_post as XP  # noqa: E402
import app.x.modules.x_actions as XA  # noqa: E402
import app.x.modules.x_delete as XD  # noqa: E402
import app.x.modules.x_follow as XF  # noqa: E402
import app.x.modules.x_unfollow as XU  # noqa: E402
import app.x.modules.x_profile as XPR  # noqa: E402

for mod, label in ((XP, "x_post"), (XA, "x_actions"), (XD, "x_delete"),
                   (XF, "x_follow"), (XU, "x_unfollow"), (XPR, "x_profile")):
    src = inspect.getsource(mod)
    check(f"{label} يستدعي _captcha_guard", "_captcha_guard(page" in src,
          "البوابة غير مربوطة — الكابتشا ستُفشل المسار بصمت")

# ═══════════════════════════════════════════════════════════════════════════
if "--live" in sys.argv:
    print("\n9) حقيقي (--live): الرصيد وصورة OCR")
    os.environ["CAPTCHA_DOTENV"] = "1"      # اقرأ المفتاح الحقيقي من .env الآن
    CS._DOTENV_CACHE = None
    st = CS.status(check_balance=True)
    print(f"  المزوّد: {st['provider']}  المفتاح: {st['key']}  الرصيد: {st.get('balance')}")
    check("المفتاح الحقيقي يعمل (رصيد مقروء)", st.get("balance") is not None,
          str(st.get("balance_error", "")))
    if st.get("balance") is not None:
        img = Image.new("RGB", (220, 70), "white")
        from PIL import ImageDraw
        ImageDraw.Draw(img).text((10, 15), "ABCD5", fill="black")
        buf = BytesIO()
        img.save(buf, format="PNG")
        try:
            text = CS.Solver().solve_image(buf.getvalue())
            print(f"  OCR: {text!r}")
            check("OCR حقيقي يرجع نصاً", bool(text.strip()))
        except CS.CaptchaError as e:
            check("OCR حقيقي", False, str(e))

print("\n" + ("✅ كل الفحوص نجحت" if not FAILED else f"❌ فشل {len(FAILED)}: {FAILED}"))
sys.exit(1 if FAILED else 0)
