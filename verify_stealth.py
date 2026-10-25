"""
verify_stealth.py — متصفح X الخفي (Patchright + Google Chrome) بلا آثار أتمتة.

بلا شبكة افتراضياً: صفحة فحص تُخدَم عبر route (سكربتها يعمل في عالم الصفحة كأي موقع)
ومعها Worker وSharedWorker من Blob:
  1) ثابت: وحدات X تطلق متصفحها من stealth_browser فقط — لا chromium.launch مباشر، لا
     sync_playwright من Playwright العادي، لا UA مكتوب يدوياً، لا أعلام تُضعف الأمان
  2) المحرّك patchright مفعّل وملف تشغيله موجود، والأعلام آمنة، وصيغة UA المختزلة
  3) البصمة: webdriver=false، لا HeadlessChrome في UA الصفحة/الـWorkers/رأس الطلب، النسخة
     الكبرى في UA = sec-ch-ua = userAgentData، المنصة متّسقة، الشاشة أكبر من نافذة العرض
  4) evaluate يقرأ DOM، وmain_world_evaluate يرى متغيرات الصفحة (حقن callback الكابتشا)
  5) goto: إعادة واحدة على عطل شبكة عابر فقط
  6) new_context: لا UA سياق لمتصفح خفي، UA متّسق لمتصفح خارجي، جلسة التدوير لا تُجهَّز
     مسبقاً لـCloudflare (لا دفع ولا UA لينكس) وCAPTCHA_PROXY الصريح يبقى كما كان
--live:
  7) deviceandbrowserinfo «You are human!»، sannysoft بلا فشل، CreepJS 0% headless
  8) x.com/explore عبر جلسة البروكسي السعودي: 200 بلا كابتشا/حجب
--account <label|path.json>: الخط الزمني والملف الشخصي بكوكيز الحساب (قراءة فقط)

    .\\venv\\Scripts\\python.exe verify_stealth.py [--live] [--account nxjrjrjdj]
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

LIVE = "--live" in sys.argv
ACCOUNT = sys.argv[sys.argv.index("--account") + 1] if "--account" in sys.argv else ""
if not (LIVE or ACCOUNT):
    os.environ["PROXY_DOTENV"] = "0"        # محلي: معزول عن مزوّد البروكسي الحقيقي في .env
os.environ["CAPTCHA_DOTENV"] = "0"          # لا مفتاح كابتشا حقيقي في أي قسم ⇒ لا دفع أبداً
os.environ["CAPTCHA_PROXY"] = ""
os.environ["CAPTCHA_CLOUDFLARE_PRESOLVE"] = "0"

from app.core import captcha_browser as CB  # noqa: E402

ST = CB._stealth
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


VIEWPORT = {"width": 1280, "height": 900}

# ═══════════════════════════════════════════════════════════════════════════
print("1) ثابت: مُطلِق واحد لكل مسارات X")
X_FILES = sorted((Path(ROOT) / "app" / "x" / "modules").glob("*.py")) + [
    Path(ROOT) / "system" / "mcps" / "playwright_x" / "server.py",
    Path(ROOT) / "app" / "core" / "captcha_browser.py",
]
RULES = (
    (r"\.chromium\.launch\(", "إطلاق مباشر يتجاوز stealth_browser"),
    (r"from playwright\.sync_api import [^\n]*sync_playwright", "sync_playwright من Playwright العادي"),
    (r"--disable-web-security", "يلغي سياسة نفس المصدر"),
    (r"--no-sandbox", "يلغي عزل العارض"),
    (r"--disable-gpu", "WebGL برمجي = علامة VM/headless"),
)
#: للمتصفح فقط: عميل HTTP (curl_cffi impersonate=chromeNNN) يجب أن يحمل نسخة بصمته نفسها
BROWSER_UA_RULE = (r"Chrome/\d{2,3}\.0\.0\.0", "UA مكتوب يدوياً يناقض sec-ch-ua بعد أول تحديث لـChrome")
for path in X_FILES:
    src = path.read_text(encoding="utf-8")
    rules = RULES + ((BROWSER_UA_RULE,) if "sync_playwright" in src else ())
    bad = [f"{why} ({pat})" for pat, why in rules if re.search(pat, src)]
    check(str(path.relative_to(ROOT)), not bad, "; ".join(bad))

# ═══════════════════════════════════════════════════════════════════════════
print("\n2) المحرّك والأعلام وصيغة UA")
check("المحرّك المفعّل = patchright", ST.engine() == "patchright", ST.engine())
try:
    from patchright._impl._driver import compute_driver_executable

    driver = Path(compute_driver_executable()[0])
    check("ملف تشغيل Patchright موجود", driver.exists(), f"{driver} — pip install --force-reinstall patchright")
except Exception as e:  # noqa: BLE001
    check("ملف تشغيل Patchright موجود", False, f"{type(e).__name__}: {e}")
args = ST.launch_args()
root_linux = sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0
check("أعلام الإطلاق آمنة", not ({"--disable-web-security", "--disable-gpu"} & set(args))
      and ("--no-sandbox" not in args or root_linux), str(args))
with env(X_BROWSER_ENGINE="playwright"):
    check("الاحتياطي (Playwright العادي) يخفي webdriver بالعلَم",
          ST.engine() == "playwright" and "--disable-blink-features=AutomationControlled" in ST.launch_args())
_orig_driver = ST._driver_present
try:
    ST._driver_present = lambda pkg: False if pkg == "patchright" else _orig_driver(pkg)
    ST._warned.clear()
    fell_back = ST.engine()
finally:
    ST._driver_present = _orig_driver
check("ملف تشغيل Patchright مفقود ⇒ احتياطي Playwright (لا انهيار)", fell_back == "playwright", fell_back)
ua155 = ST.user_agent_for("155.0.8059.85")
check("UA مختزل من النسخة: Chrome/<major>.0.0.0", ua155.endswith("Chrome/155.0.0.0 Safari/537.36")
      and "Headless" not in ua155, ua155)
if sys.platform == "win32":
    check("UA ويندوز بالصيغة المجمّدة", "(Windows NT 10.0; Win64; x64)" in ua155, ua155)
check("Edge يحمل Edg/<major>", ST.user_agent_for("155.0.1.2", "msedge").endswith(" Edg/155.0.0.0"))
check("نسخة مجهولة ⇒ لا UA (لا تخمين)", ST.user_agent_for("") == "")
shot = ST.context_options(None, {"viewport": {"width": 1920, "height": 1080}})["screen"]
check("نافذة 1920x1080 ⇒ شاشة أكبر منها", shot["width"] >= 1920 and shot["height"] > 1080, str(shot))

# ═══════════════════════════════════════════════════════════════════════════
PROBE_HTML = r"""<!doctype html><html><head><title>probe</title><script>
window.__probe_secret = 42;
(async () => {
  const r = {};
  r.webdriver = navigator.webdriver;
  r.ua = navigator.userAgent;
  const brands = (navigator.userAgentData && navigator.userAgentData.brands) || [];
  r.brands = brands.map(b => b.brand + '/' + b.version);
  r.ch_platform = navigator.userAgentData ? navigator.userAgentData.platform : '';
  r.platform = navigator.platform;
  r.screen = [screen.width, screen.height];
  r.inner = [innerWidth, innerHeight];
  const src = "postMessage({ua: navigator.userAgent, wd: navigator.webdriver})";
  const url = URL.createObjectURL(new Blob([src], {type: 'text/javascript'}));
  r.worker = await new Promise(res => {
    const w = new Worker(url); w.onmessage = e => res(e.data); setTimeout(() => res(null), 5000);
  });
  const ssrc = "onconnect = e => e.ports[0].postMessage({ua: navigator.userAgent, wd: navigator.webdriver})";
  const surl = URL.createObjectURL(new Blob([ssrc], {type: 'text/javascript'}));
  r.shared = await new Promise(res => {
    try { const s = new SharedWorker(surl); s.port.onmessage = e => res(e.data); s.port.start(); }
    catch (err) { res({error: String(err)}); }
    setTimeout(() => res(null), 5000);
  });
  document.body.setAttribute('data-r', JSON.stringify(r));
  document.title = 'DONE';
})();
</script></head><body>probe</body></html>"""

HCAPTCHA_HTML = """<html><body>
  <div class="h-captcha" data-sitekey="SITE-H" data-callback="onHcap"></div>
  <textarea name="h-captcha-response"></textarea>
  <script>window.onHcap = function (t) {
    window.__cb = t; document.querySelector('.h-captcha').remove();
  };</script></body></html>"""


def serve(ctx, pages, seen=None):
    """صفحات محلية على أصل https وهمي — لا شبكة (route يجيب قبل أي اتصال)."""
    def handler(route):
        if seen is not None:
            seen.append(route.request)
        body = pages.get(route.request.url.split("?")[0], "<html><body>404</body></html>")
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
    ctx.route("https://probe.moj.test/**", handler)


def probe(page):
    page.goto("https://probe.moj.test/", wait_until="domcontentloaded")
    deadline = time.time() + 15
    while time.time() < deadline and page.title() != "DONE":
        page.wait_for_timeout(200)
    return json.loads(page.locator("body").get_attribute("data-r") or "{}")


class FakePage:
    def __init__(self, errors):
        self.errors, self.calls, self.waits = list(errors), 0, 0

    def goto(self, url, **_kw):
        self.calls += 1
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        return "ok"

    def wait_for_timeout(self, _ms):
        self.waits += 1


class FakeRotation:
    @staticmethod
    def is_enabled():
        return True

    @staticmethod
    def acquire(_scope, **_kw):
        return "http://user:pw@127.0.0.1:9"


class FakeSolver:
    def __init__(self):
        self.cf_calls = []

    def solve_cloudflare(self, url, proxy=""):
        self.cf_calls.append(url)
        return {"cookies": {"cf_clearance": "CF"}, "user_agent": "UA-Linux-Test"}

    def solve_hcaptcha(self, url, sitekey, **_kw):
        return "HCAP-TOKEN"


try:
    with ST.sync_playwright() as p:
        # نفس شكل الإنتاج مع التدوير: بروكسي إطلاق نائب، والصفحات محلية عبر route فلا شبكة
        browser = ST.launch(p, headless=True, proxy={"server": "http://per-context"})
        major = browser.version.split(".")[0]
        flag_ua = ST.user_agent_for(browser.version, ST.chrome_channel())

        print(f"\n3) البصمة في صفحة فحص محلية (Chrome {browser.version}، {ST.engine()}، headless)")
        reqs = []
        ctx = CB.new_context(browser, None, proxy="", viewport=VIEWPORT)
        serve(ctx, {"https://probe.moj.test/": PROBE_HTML,
                    "https://probe.moj.test/hcaptcha": HCAPTCHA_HTML}, reqs)
        page = ctx.new_page()
        r = probe(page)
        hdr = reqs[0].all_headers() if reqs else {}
        worker, shared = r.get("worker") or {}, r.get("shared") or {}
        check("navigator.webdriver = false", r.get("webdriver") is False, str(r.get("webdriver")))
        check("UA الصفحة بلا HeadlessChrome", "Headless" not in r.get("ua", "Headless"), r.get("ua"))
        check("UA الـWorker بلا HeadlessChrome (UA السياق لا يصل الـWorkers)",
              bool(worker.get("ua")) and "Headless" not in worker["ua"], str(worker))
        check("UA الـSharedWorker بلا HeadlessChrome",
              bool(shared.get("ua")) and "Headless" not in shared["ua"], str(shared))
        # WorkerNavigator في Chrome بلا webdriver أصلاً (undefined يسقطه JSON) — المهم ألّا يكون true
        check("webdriver في الـWorker ليس true", worker.get("wd") is not True, str(worker))
        check("رأس User-Agent بلا HeadlessChrome", "Headless" not in hdr.get("user-agent", "Headless"),
              hdr.get("user-agent"))
        ua_major = (re.search(r"Chrome/(\d+)", r.get("ua", "")) or [None, ""])[1]
        check("نسخة UA = نسخة المتصفح الفعلية", ua_major == major, f"{r.get('ua')} ≠ {browser.version}")
        check("sec-ch-ua بنفس النسخة الكبرى", f'v="{major}"' in hdr.get("sec-ch-ua", ""), hdr.get("sec-ch-ua"))
        check("userAgentData.brands بنفس النسخة وبلا Headless",
              any(b.endswith("/" + major) for b in r.get("brands", []))
              and not any("Headless" in b for b in r.get("brands", [])), str(r.get("brands")))
        if sys.platform == "win32":
            check("المنصة متّسقة: UA/navigator.platform/userAgentData = ويندوز",
                  "Windows" in r.get("ua", "") and r.get("platform") == "Win32"
                  and r.get("ch_platform") == "Windows", f"{r.get('platform')} | {r.get('ch_platform')}")
        scr, inner = r.get("screen") or [0, 0], r.get("inner") or [0, 0]
        check("الشاشة أكبر من نافذة العرض (التساوي علامة headless)",
              scr[0] >= inner[0] and scr[1] > inner[1], f"screen={scr} inner={inner}")
        check("سياق المتصفح الخفي بلا UA سياق: UA = علَم المتصفح نفسه", r.get("ua") == flag_ua,
              f"{r.get('ua')} ≠ {flag_ua}")

        print("\n4) evaluate: DOM معزول، ومتغيرات الصفحة عبر main_world_evaluate")
        check("evaluate يقرأ DOM", page.evaluate("() => document.body.getAttribute('data-r') !== null") is True)
        check("main_world_evaluate يرى متغيرات الصفحة",
              ST.main_world_evaluate(page, "() => window.__probe_secret") == 42)
        if ST.engine() == "patchright":
            check("evaluate العادي معزول عن متغيرات الصفحة (Patchright)",
                  page.evaluate("() => typeof window.__probe_secret") == "undefined")
        check("detect: صفحة سليمة بلا عائق", CB.detect(page) is None, str(CB.detect(page)))
        page.goto("https://probe.moj.test/hcaptcha", wait_until="domcontentloaded")
        fake = FakeSolver()
        orig_solver = CB._solver.get_solver
        CB._solver.get_solver = lambda: fake
        try:
            solved = CB.solve(page, wait_s=5)
        finally:
            CB._solver.get_solver = orig_solver
        check("حقن hCaptcha يستدعي callback الصفحة (عالم الصفحة لا المعزول)",
              solved and ST.main_world_evaluate(page, "() => window.__cb") == "HCAP-TOKEN")
        ctx.close()

        print("\n5) goto: إعادة واحدة على عطل شبكة عابر فقط")
        fp = FakePage(["Page.goto: net::ERR_CONNECTION_CLOSED at https://x.com/home"])
        check("ERR_CONNECTION_CLOSED ⇒ إعادة ثم نجاح", ST.goto(fp, "https://x.com/home") == "ok"
              and fp.calls == 2 and fp.waits == 1, f"calls={fp.calls}")
        for err in ("Timeout 60000ms exceeded.", "net::ERR_HTTP_RESPONSE_CODE_FAILURE"):
            fp = FakePage([err])
            try:
                ST.goto(fp, "https://x.com/home")
                raised = False
            except RuntimeError:
                raised = True
            check(f"«{err[:36]}» يُرفع فوراً بلا إعادة", raised and fp.calls == 1, f"calls={fp.calls}")
        fp = FakePage(["net::ERR_CONNECTION_RESET"] * 2)
        try:
            ST.goto(fp, "https://x.com/home")
            raised = False
        except RuntimeError:
            raised = True
        check("عطلان متتاليان ⇒ يُرفع الثاني (لا إخفاء)", raised and fp.calls == 2, f"calls={fp.calls}")

        print("\n6) new_context: متصفح خارجي، وجلسة التدوير بلا تجهيز Cloudflare")
        raw = p.chromium.launch(headless=True, **({"channel": ST.chrome_channel()} if ST.chrome_channel() else {}))
        ctx = CB.new_context(raw, None, proxy="", viewport=VIEWPORT)
        ua_raw = ctx.new_page().evaluate("() => navigator.userAgent")
        check("متصفح لم يُطلق من stealth: UA سياق بنفس نسخته بلا Headless",
              "Headless" not in ua_raw and f"Chrome/{raw.version.split('.')[0]}." in ua_raw, ua_raw)
        ctx.close()
        raw.close()

        fake = FakeSolver()
        orig_pm, orig_solver = CB._proxy_module, CB._solver.get_solver
        CB._proxy_module = lambda: FakeRotation
        CB._solver.get_solver = lambda: fake
        CB._CLEARANCE_CACHE.clear()
        try:
            with env(CAPTCHA_CLOUDFLARE_PRESOLVE="1", CAPTCHA_PROXY=""):
                ctx = CB.new_context(browser, None, viewport=VIEWPORT)
                ua_rot = ctx.new_page().evaluate("() => navigator.userAgent")
                ctx.close()
            check("جلسة التدوير: لا تجهيز Cloudflare مسبق (لا 20 نقطة لكل جلسة)", not fake.cf_calls,
                  str(fake.cf_calls))
            check("جلسة التدوير: لا UA لينكس مستعار على Chrome ويندوز", "UA-Linux" not in ua_rot, ua_rot)
            with env(CAPTCHA_CLOUDFLARE_PRESOLVE="1", CAPTCHA_PROXY="http://u:p@127.0.0.1:9"):
                ctx = CB.new_context(browser, None, proxy="", viewport=VIEWPORT)
                ua_cf = ctx.new_page().evaluate("() => navigator.userAgent")
                ctx.close()
            check("CAPTCHA_PROXY الصريح: التجهيز المسبق كما كان (UA الحل شرط cf_clearance)",
                  len(fake.cf_calls) == 1 and ua_cf == "UA-Linux-Test", f"{fake.cf_calls} {ua_cf}")
        finally:
            CB._proxy_module, CB._solver.get_solver = orig_pm, orig_solver
        browser.close()
except Exception as e:  # noqa: BLE001
    check("Patchright/Chrome متاح لاختبار المتصفح", False, f"{type(e).__name__}: {e}")


def _text(page):
    try:
        return page.locator("body").inner_text(timeout=10_000)
    except Exception as e:  # noqa: BLE001
        return f"<no text: {e}>"


# ═══════════════════════════════════════════════════════════════════════════
if LIVE:
    print("\n7) حقيقي: مواقع كشف البوتات العامة (اتصال مباشر)")
    with ST.sync_playwright() as p:
        browser = ST.launch(p, headless=True)
        ctx = browser.new_context(**ST.context_options(browser, {"viewport": VIEWPORT}))
        page = ctx.new_page()
        try:
            page.goto("https://deviceandbrowserinfo.com/are_you_a_bot", wait_until="domcontentloaded",
                      timeout=60_000)
            page.wait_for_timeout(8000)
            t = _text(page)
            flags = re.findall(r'"(\w+)":\s*true', t)
            check("deviceandbrowserinfo: «You are human!» بلا أي علامة",
                  bool(re.search(r"you are human", t, re.I)) and not flags, f"علامات: {flags}")
        except Exception as e:  # noqa: BLE001
            check("deviceandbrowserinfo متاح", False, str(e).splitlines()[0][:120])
        try:
            page.goto("https://bot.sannysoft.com/", wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(5000)
            failed = page.evaluate("""() => Array.from(document.querySelectorAll('td.failed, td.warn'))
                .map(td => (td.parentElement.querySelector('td') || {}).innerText)""")
            check("sannysoft: لا اختبار فاشل", not failed, str(failed))
        except Exception as e:  # noqa: BLE001
            check("sannysoft متاح", False, str(e).splitlines()[0][:120])
        try:
            page.goto("https://abrahamjuliot.github.io/creepjs/", wait_until="domcontentloaded",
                      timeout=90_000)
            page.wait_for_timeout(12_000)
            t = _text(page)
            hits = [ln.strip() for ln in t.splitlines() if re.search(r"(headless|stealth):", ln, re.I)]
            print(f"     CreepJS: {hits[:4]}")
            check("CreepJS: 0% headless و0% stealth وبلا HeadlessChrome",
                  any(re.match(r"0% headless", h) for h in hits)
                  and any(re.match(r"0% stealth", h) for h in hits) and "HeadlessChrome" not in t, str(hits))
        except Exception as e:  # noqa: BLE001
            check("CreepJS متاح", False, str(e).splitlines()[0][:120])
        ctx.close()
        browser.close()

    print("\n8) حقيقي: x.com عبر جلسة البروكسي السعودي")
    if CB._proxy_module().is_enabled():
        with ST.sync_playwright() as p:
            browser = CB.launch_browser(p, headless=True)
            ctx = CB.new_context(browser, None, scope="verify-stealth", viewport=VIEWPORT)
            page = ctx.new_page()
            try:
                resp = ST.goto(page, "https://x.com/explore", wait_until="domcontentloaded", timeout=60_000)
                check("x.com/explore: 200 (لا 403 للبصمة)", bool(resp) and resp.status == 200,
                      str(resp.status if resp else None))
                page.wait_for_timeout(3000)
                check("x.com: بلا كابتشا/حجب", CB.detect(page) is None, str(CB.detect(page)))
            except Exception as e:  # noqa: BLE001
                check("x.com/explore عبر البروكسي", False, str(e).splitlines()[0][:160])
            ctx.close()
            browser.close()
    else:
        print("  (تدوير IP غير مفعّل في .env — تخطّي)")

if ACCOUNT:
    cookie = Path(ACCOUNT) if ACCOUNT.endswith(".json") else Path(ROOT) / "app" / "x" / "cookies" / f"{ACCOUNT}.json"
    handle = cookie.stem
    print(f"\n9) حساب حقيقي @{handle} (قراءة فقط، نفس جلسة بروكسي الحساب)")
    if not cookie.exists():
        check(f"ملف الكوكيز موجود: {cookie}", False)
    else:
        with ST.sync_playwright() as p:
            browser = CB.launch_browser(p, headless=True)
            ctx = CB.new_context(browser, str(cookie), viewport=VIEWPORT)
            page = ctx.new_page()
            page.set_default_timeout(60_000)
            try:
                for url in ("https://x.com/home", f"https://x.com/{handle}"):
                    started = time.time()
                    ST.goto(page, url, wait_until="domcontentloaded")
                    CB.guard_page(page, log=print)
                    page.locator('article[data-testid="tweet"]').first.wait_for(state="visible", timeout=45_000)
                    n = page.locator('article[data-testid="tweet"]').count()
                    check(f"{url}: {n} تغريدة ظاهرة ({time.time() - started:.0f}s)", n > 0)
                js = page.evaluate("() => ({wd: navigator.webdriver, ua: navigator.userAgent})")
                check("على x.com نفسه: webdriver=false وUA بلا Headless",
                      js.get("wd") is False and "Headless" not in js.get("ua", "Headless"), str(js))
            except Exception as e:  # noqa: BLE001
                check(f"قراءة @{handle}", False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
            finally:
                ctx.close()
                browser.close()

print("\n" + ("✅ كل الفحوص نجحت" if not FAILED else f"❌ فشل {len(FAILED)}: {FAILED}"))
sys.exit(1 if FAILED else 0)
