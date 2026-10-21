"""جسر كابتشا لسيرفر X Suite.

سيرفر ``app/x`` يعمل بـcwd=app/x فلا يستطيع استيراد حزمة ``app``؛ نحمّل الوحدة
المشتركة بالمسار بدل نسخ المنطق هنا (نسخة واحدة تُصلَح مرة واحدة).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable, Optional

_BROWSER = Path(__file__).resolve().parents[2] / "core" / "captcha_browser.py"


def _load():
    name = "moj_captcha_browser"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, _BROWSER)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_browser = _load()

CaptchaError = _browser.CaptchaError
detect = _browser.detect
solve = _browser.solve
cloudflare_clearance = _browser.cloudflare_clearance
new_context = _browser.new_context
launch_proxy_kwargs = _browser.launch_proxy_kwargs


def guard(page, log: Optional[Callable[[str], None]] = None) -> None:
    """يحل كابتشا الصفحة إن ظهرت، أو يرفع ``CaptchaError`` برسالة عربية واضحة."""
    _browser.guard_page(page, log=log)
