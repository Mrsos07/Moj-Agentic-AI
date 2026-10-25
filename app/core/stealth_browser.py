"""
متصفح X الخفي: Patchright (Playwright مُرقَّع ضد الكشف) يقود Google Chrome المثبَّت.

قيس على هذا الجهاز (2026-10-25، Chrome 155) قبل التغيير — Playwright 1.46 + UA سياق ثابت:
  deviceandbrowserinfo.com «You are a bot!» (hasWebdriverTrue, isAutomatedWithCDP,
  isAutomatedWithCDPInWebWorker) · sannysoft: WebDriver present · CreepJS 67% headless، و
  «HeadlessChrome/155» يتسرّب من الـWorkers · رأس User-Agent يقول Chrome/142 (النشر/الحذف
  Chrome/120) بينما sec-ch-ua يقول 155.
بعده — Patchright + Chrome + UA على مستوى المتصفح مشتق من نسخته الفعلية:
  «You are human!» بلا أي علامة · sannysoft كله ناجح · CreepJS 0% headless و0% stealth.

القواعد:
- كل متصفح لـX يُطلق من ``launch()`` هنا، وكل سياق يمر بـ``context_options()``.
- لا UA مكتوب يدوياً: يُشتق من نسخة Chrome الفعلية ويُمرَّر بـ``--user-agent`` فيصل
  الـWorkers وService Worker (UA السياق لا يصلها). في الوضع المرئي لا يُلمس UA أصلاً.
- لا أعلام تُضعف الأمان: ``--disable-web-security`` يلغي سياسة نفس المصدر (أي صفحة تقرأ
  x.com بكوكيز الحساب)، و``--no-sandbox`` يلغي عزل العارض — إلا root على لينكس (Docker)
  حيث لا يعمل Chrome بدونه.
- ``evaluate`` في Patchright يعمل في عالم معزول (يرى DOM لا متغيرات الصفحة)؛ ما يحتاج
  متغيرات الصفحة نفسها (callbacks الكابتشا) يمر عبر ``main_world_evaluate``.

مكتبة قياسية فقط (+ patchright/playwright): يُحمَّل بالمسار من عمليات لا ترى حزمة app.
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import sys
import threading
from typing import Any, Dict, List, Optional

_log = logging.getLogger("moj.stealth_browser")

#: أعطال شبكة عابرة قبل أي رد (نفق البروكسي السكني ينقطع أحياناً) — إعادة التنقّل آمنة
TRANSIENT_NET_ERRORS = (
    "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET", "ERR_EMPTY_RESPONSE",
    "ERR_TUNNEL_CONNECTION_FAILED", "ERR_PROXY_CONNECTION_FAILED", "ERR_TIMED_OUT",
    "ERR_CONNECTION_TIMED_OUT", "ERR_NETWORK_CHANGED", "ERR_SOCKET_NOT_CONNECTED",
)

#: شاشات شائعة؛ تُختار أصغرها التي تتسع للنافذة مع شريط المتصفح (Playwright يجعل الشاشة
#: = نافذة العرض افتراضياً، وهذا لا يحدث في متصفح حقيقي)
_SCREENS = ((1920, 1080), (2560, 1440), (3840, 2160))

_versions: Dict[tuple, str] = {}
_lock = threading.Lock()
_warned: List[bool] = []


def engine() -> str:
    """``patchright`` (الافتراضي متى كان مثبّتاً) أو ``playwright`` (``X_BROWSER_ENGINE``)."""
    if (os.getenv("X_BROWSER_ENGINE") or "").strip().lower() == "playwright":
        return "playwright"
    if importlib.util.find_spec("patchright") is not None:
        return "patchright"
    if not _warned:
        _warned.append(True)
        _log.warning("patchright غير مثبّت — متصفح X يعمل بـPlaywright العادي وهو قابل للكشف. "
                     "ثبّته: pip install -r requirements.txt")
    return "playwright"


def sync_playwright():
    """``sync_playwright()`` للمحرّك الفعّال — نفس واجهة Playwright حرفياً."""
    if engine() == "patchright":
        from patchright.sync_api import sync_playwright as factory
    else:
        from playwright.sync_api import sync_playwright as factory
    return factory()


def chrome_channel() -> str:
    """Chrome المثبَّت افتراضياً؛ ``XSUITE_CHROME_CHANNEL=chromium`` = المتصفح المرفق."""
    channel = (os.getenv("XSUITE_CHROME_CHANNEL") or "").strip() or "chrome"
    return "" if channel.lower() == "chromium" else channel


def user_agent_for(version: str, channel: str = "chrome") -> str:
    """UA كما يرسله هذا الـChrome في الوضع المرئي (المختزل: ``Chrome/<major>.0.0.0`` ونظام
    مُجمّد) — فيطابق ``sec-ch-ua`` و``navigator.userAgentData`` اللذين لا نلمسهما."""
    major = str(version or "").split(".")[0]
    if not major.isdigit():
        return ""
    system = {"win32": "Windows NT 10.0; Win64; x64",
              "darwin": "Macintosh; Intel Mac OS X 10_15_7"}.get(sys.platform, "X11; Linux x86_64")
    ua = (f"Mozilla/5.0 ({system}) AppleWebKit/537.36 (KHTML, like Gecko) "
          f"Chrome/{major}.0.0.0 Safari/537.36")
    return ua + f" Edg/{major}.0.0.0" if (channel or "").startswith("msedge") else ua


def launch_args() -> List[str]:
    """الأعلام الوحيدة المسموحة — كلها لازمة للتشغيل، لا شيء يُضعف الأمان أو يكشف الأتمتة."""
    args: List[str] = []
    if engine() == "playwright":
        args.append("--disable-blink-features=AutomationControlled")   # Patchright يضيفها
    if sys.platform.startswith("linux"):
        args.append("--disable-dev-shm-usage")         # /dev/shm في Docker صغير فيتعطّل العارض
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")                 # Chrome يرفض العمل كـroot مع العزل
    return args


def _launch(p, channel: str, options: Dict[str, Any]):
    opts = dict(options, channel=channel) if channel else dict(options)
    return p.chromium.launch(**opts)


def _version(p, channel: str, base: Dict[str, Any]) -> str:
    """نسخة المتصفح الفعلية (تُقاس مرة لكل عملية): منها يُبنى UA قبل الإطلاق الحقيقي."""
    key = (engine(), channel or "chromium")
    with _lock:
        if key not in _versions:
            probe = _launch(p, channel, {k: v for k, v in base.items() if k != "proxy"})
            try:
                _versions[key] = probe.version
            finally:
                probe.close()
        return _versions[key]


def _mark(browser, ua: str) -> None:
    try:
        browser._moj_stealth = {"engine": engine(), "user_agent": ua}
    except Exception:  # noqa: BLE001 - علامة اختيارية
        pass


def is_stealth(browser) -> bool:
    """أُطلق من ``launch()`` (فالـUA محسوم على مستوى المتصفح)؟"""
    return isinstance(getattr(browser, "_moj_stealth", None), dict)


def _launch_consistent(p, channel: str, base: Dict[str, Any], headless: bool, retry: bool = True):
    if not headless:
        browser = _launch(p, channel, base)        # UA الأصلي صحيح أصلاً
        _mark(browser, "")
        return browser
    version = _version(p, channel, base)
    ua = user_agent_for(version, channel or "chromium")
    args = base["args"] + ([f"--user-agent={ua}"] if ua else [])
    browser = _launch(p, channel, dict(base, args=args))
    if retry and browser.version.split(".")[0] != version.split(".")[0]:
        # Chrome حدّث نفسه بعد قياس نسخته: UA قديم يناقض sec-ch-ua — أعد الإطلاق بالصحيح
        with _lock:
            _versions[(engine(), channel or "chromium")] = browser.version
        browser.close()
        return _launch_consistent(p, channel, base, headless, retry=False)
    _mark(browser, ua)
    return browser


def launch(p, headless: bool = True, *, proxy: Optional[Dict[str, str]] = None):
    """أطلق Chrome بلا آثار أتمتة. ``p`` من ``sync_playwright()`` في هذه الوحدة.

    القناة المثبّتة أولاً (``chrome_channel()``) ثم Chromium المرفق احتياطياً؛ يُرفع خطأ
    يجمع سببي الفشل إن تعذّر الاثنان.
    """
    base: Dict[str, Any] = {"headless": headless, "args": launch_args()}
    if proxy:
        base["proxy"] = proxy
    if engine() == "playwright":
        base["ignore_default_args"] = ["--enable-automation"]
    errors = []
    for channel in ([chrome_channel()] if chrome_channel() else []) + [""]:
        try:
            return _launch_consistent(p, channel, base, headless)
        except Exception as e:  # noqa: BLE001 - جرّب المتصفح التالي
            errors.append(f"{channel or 'chromium'}: {str(e).splitlines()[0][:200]}")
    raise RuntimeError("تعذّر تشغيل المتصفح — " + " | ".join(errors))


def context_options(browser, options: Dict[str, Any]) -> Dict[str, Any]:
    """خيارات سياق متّسقة مع المتصفح نفسه.

    - UA: لا شيء إن أُطلق المتصفح من ``launch()``؛ وإلا (مسار قديم/اختبار) UA بنفس نسخة
      ``sec-ch-ua`` بدل «HeadlessChrome» الذي يرد عليه X بـ403.
    - الشاشة: أكبر من نافذة العرض كما في أي جهاز حقيقي.
    """
    out = dict(options)
    if not out.get("user_agent") and not is_stealth(browser):
        ua = user_agent_for(str(getattr(browser, "version", "") or ""))
        if ua:
            out["user_agent"] = ua
    if not out.get("no_viewport") and not out.get("screen"):
        vp = out.get("viewport") or {"width": 1280, "height": 720}
        w, h = int(vp.get("width") or 1280), int(vp.get("height") or 720)
        out["screen"] = next(({"width": sw, "height": sh} for sw, sh in _SCREENS
                              if sw >= w and sh >= h + 80), {"width": w, "height": h + 80})
    return out


def main_world_evaluate(target, expression: str, arg: Any = None):
    """``evaluate`` في عالم الصفحة نفسه (متغيراتها وcallbacks الكابتشا).

    Patchright يعزل ``evaluate`` افتراضياً، وPlaywright العادي يعمل في عالم الصفحة أصلاً
    ولا يعرف ``isolated_context`` — فيُفحص التوقيع لا المحرّك (صفحة الاختبار قد تكون من أيهما).
    """
    try:
        isolated = "isolated_context" in inspect.signature(target.evaluate).parameters
    except (TypeError, ValueError):
        isolated = False
    if isolated:
        return target.evaluate(expression, arg, isolated_context=False)
    return target.evaluate(expression, arg)


def goto(page, url: str, *, retries: int = 1, wait_ms: int = 2500, **kwargs):
    """``page.goto`` بإعادة واحدة على عطل شبكة عابر فقط (``TRANSIENT_NET_ERRORS``).

    قيس 2026-10-25: ``ERR_CONNECTION_CLOSED`` في قراءة الخط الزمني عبر بروكسي Oxylabs
    السعودي، ثم نجحت 4 تحميلات متتالية بنفس المسار — انقطاع نفق، لا حجب. أي خطأ آخر
    (مهلة/403/…) يُرفع كما هو: لا نُخفي فشلاً حقيقياً.
    """
    for attempt in range(retries + 1):
        try:
            return page.goto(url, **kwargs)
        except Exception as e:  # noqa: BLE001
            if attempt >= retries or not any(code in str(e) for code in TRANSIENT_NET_ERRORS):
                raise
            _log.warning("goto %s: %s — إعادة المحاولة", url, str(e).splitlines()[0][:120])
            page.wait_for_timeout(wait_ms)
