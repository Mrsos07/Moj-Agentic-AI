"""
تشغيل دوال Playwright المتزامنة من داخل asyncio بأمان.

Playwright Sync API يرفض العمل داخل event loop:
    «It looks like you are using Playwright Sync API inside the asyncio loop.»
وهذا ما حدث عند إنشاء الموظف: نقطة API غير متزامنة استدعت ``update_profile_on_x``
مباشرة. الحل هنا (طبقة واحدة) بدل تكرار الحماية في كل نقطة استدعاء:

- داخل loop ⇒ ننفّذ الدالة في **خيط مستقل** وننتظر النتيجة (وقفل المتصفح المشترك
  إن كان متاحاً حتى لا يتزامن متصفحان في العملية نفسها).
- خارج loop (سيرفر xsuite / scheduler) ⇒ استدعاء مباشر بلا أي تغيير.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools


def _browser_lock():
    """قفل المتصفح المشترك إن كان متاحاً (backend)، وإلا None (سيرفر xsuite)."""
    try:
        from app.services.x_bridge import browser_lock

        return browser_lock
    except Exception:  # noqa: BLE001 - xsuite يعمل بلا حزمة app
        return None


def run_sync(fn, *args, **kwargs):
    """ينفّذ ``fn`` ويضمن ألا تكون داخل event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    def call():
        lock = _browser_lock()
        if lock is None:
            return fn(*args, **kwargs)
        with lock:
            return fn(*args, **kwargs)

    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(call).result()
    return call()


def sync_safe(fn):
    """مُزيّن للدوال العامة المتزامنة (نشر/تفاعل/ملف/حذف)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return run_sync(fn, *args, **kwargs)

    return wrapper
