"""
كشف الكابتشا في متصفح Playwright وحلها قبل أن تُفشل العملية.

لماذا هنا وليس في x_post/x_actions؟ لأن كل مسارات X (نشر، رد، إعجاب، متابعة، حذف)
تفتح متصفحاً مستقلاً؛ لو كُتب الحل في كل ملف لتكرّر 7 مرات ونسي أحدها — وهي
بالضبط الطريقة التي يفشل بها «النشر» بصمت بينما «الإعجاب» يعمل.

الاستخدام من أي مسار:
    from captcha_browser import guard_page   # عبر app/x/modules/x_captcha.py
    page.goto(...)
    guard_page(page, log=print)              # يرفع CaptchaError إن لم يُحلّ
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_LOG = Callable[[str], None]


def _solver_module():
    """تحميل الحلّال بالمسار — هذا الملف يُستورد من app/x أيضاً (لا حزمة app)."""
    name = "moj_captcha_solver"
    mod = sys.modules.get(name)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("captcha_solver.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_solver = _solver_module()
CaptchaError = _solver.CaptchaError


# ── الكشف ───────────────────────────────────────────────────────────────────
_DETECT_JS = r"""
() => {
  const out = {url: location.href, kinds: []};
  const tag = (el) => {
    const id = 'mojcap' + Math.random().toString(36).slice(2, 9);
    el.setAttribute('data-moj-captcha', id);
    return id;
  };
  const iframes = [...document.querySelectorAll('iframe')].map(f => f.src || '');
  const joined = iframes.join(' ');
  const bodyText = ((document.body && document.body.innerText) || '').slice(0, 6000);

  // reCAPTCHA v2 / Enterprise
  let el = document.querySelector('.g-recaptcha[data-sitekey]');
  let sitekey = el ? el.getAttribute('data-sitekey') : null;
  if (!sitekey) {
    const f = iframes.find(s => /recaptcha/i.test(s));
    if (f) {
      const m = f.match(/[?&]k=([^&]+)/);
      if (m) sitekey = decodeURIComponent(m[1]);
    }
  }
  if (sitekey) {
    out.kinds.push({kind: 'recaptcha_v2', sitekey,
                    enterprise: /recaptcha\/enterprise|enterprise\.js/i.test(joined)});
  }

  // hCaptcha
  el = document.querySelector('.h-captcha[data-sitekey], [data-hcaptcha-sitekey]');
  if (el) {
    out.kinds.push({kind: 'hcaptcha',
                    sitekey: el.getAttribute('data-sitekey') || el.getAttribute('data-hcaptcha-sitekey'),
                    callback: el.getAttribute('data-callback') || ''});
  }

  // Cloudflare Turnstile
  el = document.querySelector('.cf-turnstile[data-sitekey], [data-sitekey^="0x"]');
  if (el) {
    out.kinds.push({kind: 'turnstile', sitekey: el.getAttribute('data-sitekey'),
                    callback: el.getAttribute('data-callback') || ''});
  }

  // Arkose / FunCaptcha
  const arkoseFrame = [...document.querySelectorAll('iframe')]
      .find(f => /arkoselabs|funcaptcha/i.test(f.src || ''));
  if (arkoseFrame) out.kinds.push({kind: 'funcaptcha', el: tag(arkoseFrame)});

  // كابتشا صورة (X يعرض صورة حروف مع حقل إدخال)
  const imgs = [...document.images].filter(i => i.naturalWidth >= 80 && i.naturalHeight >= 28);
  const img = imgs.find(i => /captcha|challenge/i.test(i.src)) ||
              imgs.find(i => (i.src || '').startsWith('data:image'));
  const input = document.querySelector(
      'input[name*="captcha" i], input[aria-label*="captcha" i], input[placeholder*="character" i],' +
      'input[placeholder*="رمز" i], input[placeholder*="الأحرف" i]');
  if (img && input) out.kinds.push({kind: 'image', el: tag(img), input: tag(input)});

  // Cloudflare «Just a moment…» — صفحة تحقق تحل محل الموقع، ليست كابتشا Arkose
  const cfTitle = /just a moment|checking your browser|performing security verification|لحظة|جاري التحقق|تحقق أمني/i
      .test(document.title || '');
  const cfUrl = /\/cdn-cgi\/challenge-platform\//.test(location.href);
  const cfForm = !!document.querySelector('#challenge-form, #cf-challenge-running, .cf-browser-verification');
  if (cfTitle || cfUrl || cfForm) out.kinds.push({kind: 'cloudflare'});

  // حجب لا يحلّه مزوّد كابتشا (لا تُخفِه كأنه كابتشا)
  const block = [
    [/whoa there/i, 'X حجب الطلب مؤقتاً (Whoa there)'],
    [/this request looks like it might be automated/i, 'X يعتبر الطلب آلياً (error 226)'],
    [/something went wrong\.?\s*try reloading/i, 'X أظهر «Something went wrong» — أعد المحاولة'],
    [/unusual activity/i, 'X رصد نشاطاً غير معتاد على الحساب'],
  ].find(([re]) => re.test(bodyText));
  if (block) out.kinds.push({kind: 'blocked', message: block[1]});

  return out.kinds.length ? out : null;
}
"""


def detect(page) -> Optional[Dict[str, Any]]:
    """أول عائق ظاهر في الصفحة (كابتشا أو حجب)، أو ``None``."""
    try:
        return page.evaluate(_DETECT_JS)
    except Exception:  # noqa: BLE001 - صفحة أُغلقت/انتقلت: لا عائق
        return None


def _kind(info: Optional[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    for item in (info or {}).get("kinds", []):
        if item.get("kind") == name:
            return item
    return None


# ── حقن التوكن ──────────────────────────────────────────────────────────────
_INJECT_RECAPTCHA = r"""
(token) => {
  let called = false;
  document.querySelectorAll('textarea#g-recaptcha-response, textarea[name="g-recaptcha-response"]')
    .forEach(el => { el.style.display = 'block'; el.value = token;
                     el.dispatchEvent(new Event('change', {bubbles: true})); });
  const cfg = window.___grecaptcha_cfg;
  if (cfg && cfg.clients) {
    for (const key of Object.keys(cfg.clients)) {
      const client = cfg.clients[key] || {};
      for (const k of Object.keys(client)) {
        const cb = client[k] && client[k].callback;
        if (typeof cb === 'function') {
          try { cb(token); called = true; } catch (e) {}
        }
      }
    }
  }
  return called;
}
"""

_INJECT_HCAPTCHA = r"""
(arg) => {
  const token = arg[0], cbName = arg[1];
  let called = false;
  document.querySelectorAll('textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]')
    .forEach(el => { el.value = token; el.dispatchEvent(new Event('change', {bubbles: true})); });
  if (cbName && typeof window[cbName] === 'function') {
    try { window[cbName](token); called = true; } catch (e) {}
  }
  return called;
}
"""

_INJECT_TURNSTILE = r"""
(arg) => {
  const token = arg[0], cbName = arg[1];
  let called = false;
  document.querySelectorAll('input[name="cf-turnstile-response"]')
    .forEach(el => { el.value = token; el.dispatchEvent(new Event('change', {bubbles: true})); });
  if (cbName && typeof window[cbName] === 'function') {
    try { window[cbName](token); called = true; } catch (e) {}
  }
  return called;
}
"""

_INJECT_ARKOSE = r"""
(token) => {
  let ok = false;
  const el = document.getElementById('FunCaptcha-Token');
  if (el) {
    el.value = token;
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    ok = true;
  }
  // بعض الصفحات تنتظر رسالة من إطار Arkose
  try {
    window.parent.postMessage({eventId: 'challenge-complete',
                               payload: {sessionToken: token}}, '*');
  } catch (e) {}
  return ok;
}
"""


def _wait_gone(page, kind: str, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        info = detect(page)
        if not info or not _kind(info, kind):
            return True
        page.wait_for_timeout(700)
    return False


def _click_submit(page, log: _LOG) -> None:
    """زر المتابعة بعد ملء كابتشا الصورة (X: Next/Continue/التالي)."""
    for sel in ('button:has-text("Next")', 'button:has-text("Continue")',
                'button:has-text("Verify")', 'button:has-text("التالي")',
                'button:has-text("متابعة")', 'button:has-text("تحقق")'):
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                loc.first.click(timeout=5_000)
                return
        except Exception:  # noqa: BLE001 - جرّب الزر التالي
            continue
    log("⚠️ لم أجد زر متابعة بعد كابتشا الصورة — أرسل Enter")
    try:
        page.keyboard.press("Enter")
    except Exception:  # noqa: BLE001
        pass


# ── حل Arkose بالتصنيف الصوري (شبكة 6 بلاطات) ──────────────────────────────
def _arkose_question(page) -> str:
    """سؤال اللغز بالإنجليزية كما يظهر داخل إطار Arkose (المزوّد يحتاجه حرفياً)."""
    for frame in page.frames:
        url = (frame.url or "").lower()
        if "arkoselabs" not in url and "funcaptcha" not in url:
            continue
        try:
            text = frame.evaluate("() => (document.body && document.body.innerText) || ''")
        except Exception:  # noqa: BLE001 - إطار لم يُحمَّل
            continue
        for line in (text or "").splitlines():
            line = line.strip()
            if len(line) >= 12 and ("?" in line or line.split(" ")[0].lower() in
                                    ("pick", "select", "choose", "find", "use", "click")):
                return line
    return ""


def _arkose_grid_click(page, info: Dict[str, Any], solver, log: _LOG,
                       rounds: int) -> bool:
    """يفتح صورة اللغز، يسأل المزوّد أي بلاطة، ينقرها، ثم يتحقق. محاولة محدودة."""
    if os.getenv("CAPTCHA_ARKOSE_GRID", "1").lower() in ("0", "false", "no", "off"):
        return False
    try:
        cols = max(1, int(os.getenv("CAPTCHA_ARKOSE_COLS", "3")))
        rows = max(1, int(os.getenv("CAPTCHA_ARKOSE_ROWS", "2")))
        crop_top = float(os.getenv("CAPTCHA_ARKOSE_CROP_TOP", "0.18"))
        crop_bottom = float(os.getenv("CAPTCHA_ARKOSE_CROP_BOTTOM", "0.16"))
    except ValueError:
        cols, rows, crop_top, crop_bottom = 3, 2, 0.18, 0.16

    from io import BytesIO
    try:
        from PIL import Image
    except ImportError:  # noqa: BLE001
        log("⚠️ Pillow غير مثبّت — لا يمكن قص صورة Arkose")
        return False

    for attempt in range(1, rounds + 1):
        question = _arkose_question(page)
        if not question:
            log("⚠️ تعذّر قراءة سؤال Arkose — لا يمكن تصنيف البلاطة")
            return False
        if "arrow" in question.lower():
            # لغز الأسهم: المزوّد يرجع «أي صورة» لا «أي بلاطة»؛ النقر على شبكة هنا
            # خطأ صريح (يقيسه المزوّد: عيّنة q62 الحقيقية رجعت objects=[1]).
            log("⚠️ لغز Arkose من نوع الأسهم — لا ننقر شبكة (تخطٍّ آمن)")
            return False
        try:
            handle = page.locator(f'[data-moj-captcha="{info["el"]}"]').first
            box = handle.bounding_box()
            raw = handle.screenshot()
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ تعذّر تصوير إطار Arkose: {e}")
            return False
        if not box or not raw:
            return False

        img = Image.open(BytesIO(raw))
        w, h = img.size
        top, bottom = int(h * crop_top), int(h * (1 - crop_bottom))
        if bottom - top < 40:
            top, bottom = 0, h
        puzzle = img.crop((0, top, w, bottom))
        buf = BytesIO()
        puzzle.save(buf, format="PNG")

        try:
            picks = solver.classify_funcaptcha(buf.getvalue(), question)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ تصنيف Arkose فشل: {e}")
            return False

        idx = picks[0]
        if idx < 0 or idx >= cols * rows:
            log(f"⚠️ بلاطة خارج الشبكة ({idx}) — تخطّي")
            return False
        col, row = idx % cols, idx // cols
        tile_w = (box["width"]) / cols
        tile_h = (bottom - top) / rows
        x = box["x"] + col * tile_w + tile_w / 2
        y = box["y"] + top + row * tile_h + tile_h / 2
        log(f"🖱️ Arkose: أنقر البلاطة {idx + 1} من {cols * rows} («{question[:60]}»)")
        try:
            page.mouse.click(x, y)
            page.wait_for_timeout(900)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ فشل النقر: {e}")
            return False

        # زر التحقق داخل الإطار (Verify/Next) — إن وُجد
        for frame in page.frames:
            if "arkoselabs" not in (frame.url or "").lower():
                continue
            for sel in ('#fc-verify', 'button:has-text("Verify")', 'button:has-text("Next")',
                        'button:has-text("Submit")'):
                try:
                    loc = frame.locator(sel)
                    if loc.count() and loc.first.is_visible():
                        loc.first.click(timeout=4_000)
                        break
                except Exception:  # noqa: BLE001
                    continue
        page.wait_for_timeout(1_500)
        if not detect(page) or not _kind(detect(page), "funcaptcha"):
            log("✅ نجح لغز Arkose")
            return True
        log(f"↻ جولة Arkose {attempt}/{rounds} لم تكتمل — نعيد")
    return False


# ── الواجهة ─────────────────────────────────────────────────────────────────
def solve(page, *, log: Optional[_LOG] = None, rounds: int = 3,
          wait_s: float = 60.0) -> bool:
    """يحل أول عائق كابتشا في الصفحة. ``False`` = لم يُحل (أو حجب غير كابتشا)."""
    log = log or (lambda _m: None)
    solver_mod = _solver
    info = detect(page)
    if not info:
        return True

    # صفحة الحجب قد تحمل كابتشا أيضاً («Whoa there» + Arkose): نحاول الحل أولاً،
    # ولا نرمي رسالة الحجب إلا إذا لم يكن في الصفحة ما يُحل.
    solvable = [k for k in ("recaptcha_v2", "hcaptcha", "turnstile", "funcaptcha",
                            "image", "cloudflare")
                if _kind(info, k)]
    if not solvable:
        blocked = _kind(info, "blocked")
        if blocked:
            log(f"⛔ {blocked.get('message')}")
        return False

    solver = solver_mod.get_solver()
    if solver is None:
        log("⚠️ كابتشا ظاهرة لكن لا مفتاح مزوّد في .env — لن تُحل آلياً")
        return False

    page_url = (info.get("url") or "").split("#")[0]
    user_agent = ""
    try:
        user_agent = page.evaluate("() => navigator.userAgent") or ""
    except Exception:  # noqa: BLE001
        pass

    cloudflare = _kind(info, "cloudflare")
    if cloudflare:
        proxy = (os.getenv("CAPTCHA_PROXY", "").strip()
                 or solver_mod.load_config().proxy)
        if not proxy:
            log("⛔ Cloudflare «Just a moment» — ليس كابتشا؛ يحتاج CAPTCHA_PROXY عاماً")
            return False
        log("🧩 Cloudflare — طلب cf_clearance عبر البروكسي…")
        try:
            sol = solver.solve_cloudflare(page_url, proxy=proxy)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ فشل Cloudflare: {e}")
            return False
        ua_solved = (sol.get("user_agent") or "").strip()
        if ua_solved and user_agent and ua_solved != user_agent:
            # cf_clearance مربوط بالـUA والـIP؛ حقنه في جلسة بUA آخر يُرفض
            log("⚠️ حُلّ Cloudflare بـUA مختلف — أعد تشغيل المتصفح بالـUA الناتج "
                "(cf_clearance مربوط بالـUA والـIP)")
            return False
        cookies = sol.get("cookies") or {}
        if not cookies:
            log("⚠️ خدمة الحل لم تُرجع كوكيز Cloudflare")
            return False
        try:
            page.context.add_cookies([
                {"name": str(k), "value": str(v), "url": page_url}
                for k, v in cookies.items()])
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ تعذّر ضبط كوكيز Cloudflare: {e}")
            return False
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        gone = not _kind(detect(page) or {}, "cloudflare")
        log("✅ زال تحدّي Cloudflare" if gone
            else "⚠️ تحدّي Cloudflare ما زال قائماً بعد الحقن")
        return gone

    recaptcha = _kind(info, "recaptcha_v2")
    if recaptcha and recaptcha.get("sitekey"):
        log("🧩 reCAPTCHA v2 — طلب توكن…")
        try:
            token = solver.solve_recaptcha_v2(page_url, recaptcha["sitekey"])
            called = page.evaluate(_INJECT_RECAPTCHA, token)
            log(f"✅ حُقن توكن reCAPTCHA (callback: {called})")
            return _wait_gone(page, "recaptcha_v2", wait_s)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ فشل reCAPTCHA: {e}")
            return False

    hcap = _kind(info, "hcaptcha")
    if hcap and hcap.get("sitekey"):
        log("🧩 hCaptcha — طلب توكن…")
        try:
            token = solver.solve_hcaptcha(page_url, hcap["sitekey"], user_agent=user_agent)
            page.evaluate(_INJECT_HCAPTCHA, [token, hcap.get("callback") or ""])
            log("✅ حُقن توكن hCaptcha")
            return _wait_gone(page, "hcaptcha", wait_s)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ فشل hCaptcha: {e}")
            return False

    turnstile = _kind(info, "turnstile")
    if turnstile and turnstile.get("sitekey"):
        log("🧩 Turnstile — طلب توكن…")
        try:
            token = solver.solve_turnstile(page_url, turnstile["sitekey"], user_agent=user_agent)
            page.evaluate(_INJECT_TURNSTILE, [token, turnstile.get("callback") or ""])
            log("✅ حُقن توكن Turnstile")
            return _wait_gone(page, "turnstile", wait_s)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ فشل Turnstile: {e}")
            return False

    arkose = _kind(info, "funcaptcha")
    if arkose:
        log("🧩 Arkose/FunCaptcha — طلب توكن…")
        token = ""
        try:
            token = solver.solve_funcaptcha(page_url=page_url, user_agent=user_agent)
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ توكن Arkose فشل: {e}")
        if token:
            try:
                for frame in page.frames:
                    if "arkoselabs" in (frame.url or "").lower():
                        frame.evaluate(_INJECT_ARKOSE, token)
                        break
                if _wait_gone(page, "funcaptcha", 20):
                    log("✅ حُقن توكن Arkose")
                    return True
            except Exception as e:  # noqa: BLE001
                log(f"⚠️ حقن توكن Arkose فشل: {e}")
        if _arkose_grid_click(page, arkose, solver, log, rounds):
            return True
        log("⛔ تعذّر حل Arkose آلياً (المزوّد لا يُرجع توكناً مستقراً لهذا النوع)")
        return False

    image = _kind(info, "image")
    if image:
        log("🧩 كابتشا صورة — قراءة النص…")
        try:
            raw = page.locator(f'[data-moj-captcha="{image["el"]}"]').first.screenshot()
            text = solver.solve_image(raw)
            field = page.locator(f'[data-moj-captcha="{image["input"]}"]').first
            field.click(timeout=5_000)
            field.fill(text)
            log(f"✅ قُرئ نص الكابتشا ({len(text)} حرفاً)")
            _click_submit(page, log)
            page.wait_for_timeout(2_500)
            return not detect(page) or not _kind(detect(page), "image")
        except Exception as e:  # noqa: BLE001
            log(f"⚠️ فشل كابتشا الصورة: {e}")
            return False

    return False


def guard_page(page, *, log: Optional[_LOG] = None) -> None:
    """بوابة موحّدة: يحل الكابتشا إن ظهرت، ويرفع خطأً واضحاً إن لم يستطع.

    الرفع مقصود: العملية التي تفشل بصمت تُسجّل «نجاحاً» كاذباً (قاعدة المشروع).
    """
    log = log or (lambda _m: None)
    info = detect(page)
    if not info:
        return
    blocked = _kind(info, "blocked")
    if blocked and not any(_kind(info, k) for k in
                           ("recaptcha_v2", "hcaptcha", "turnstile", "funcaptcha",
                            "image", "cloudflare")):
        raise CaptchaError(blocked.get("message") or "X حجب الطلب")
    if not solve(page, log=log):
        blocked = _kind(info, "blocked")
        if blocked:
            raise CaptchaError(blocked.get("message") or "X حجب الطلب")
        if _kind(info, "cloudflare"):
            raise CaptchaError(
                "Cloudflare يطلب تحققاً أمنياً (Just a moment) — ليس كابتشا Arkose. "
                "يحتاج بروكسي عاماً في CAPTCHA_PROXY ليُحل عبر YesCaptcha")
        raise CaptchaError(
            "ظهرت كابتشا ولم تُحل آلياً — تحقق من مفتاح المزوّد في .env "
            "(YESCAPTCHA_KEY / TWOCAPTCHA_KEY / CAPMONSTER_KEY / ANTICAPTCHA_KEY)")

    if detect(page):
        raise CaptchaError("كابتشا ما زالت ظاهرة بعد محاولة الحل")
