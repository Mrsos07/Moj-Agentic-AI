import os
from playwright.sync_api import sync_playwright

from .x_unfollow import unfollow_user  # noqa: F401 - المسار القديم لإلغاء المتابعة
from .x_captcha import guard as _captcha_guard
from .x_captcha import new_context as _captcha_context
from .x_captcha import launch_proxy_kwargs as _launch_proxy_kwargs
from .sync_bridge import sync_safe

DEFAULT_TIMEOUT = 30_000

#: رأس الملف الشخصي فقط. ``page.locator('[data-testid$="-follow"]').first`` كان يطابق
#: اقتراحات الشريط الجانبي («من تتابع»، 3 على كل ملف — قيس): إن كان الحساب متابَعاً
#: أصلاً أو تأخر تحميل الرأس، كان الموظف **يتابع حساباً آخر** ويُحسب ذلك نجاحاً.
_HEADER = '[data-testid="primaryColumn"] [data-testid="placementTracking"]'


def _visible(page, selector: str, timeout_ms: int) -> bool:
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


@sync_safe
def follow_user(storage_state_path: str, profile_url: str, headless: bool, wait_after_ms: int = 3000) -> str:
    """Follow a profile. Returns ``"done"`` (verified: header button became ``-unfollow``)
    or ``"already"`` (was already followed)."""
    chrome_channel = os.getenv("XSUITE_CHROME_CHANNEL", "").strip() or "chrome"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=chrome_channel, headless=headless,
                                            **_launch_proxy_kwargs())
        except Exception:
            browser = p.chromium.launch(headless=headless, **_launch_proxy_kwargs())
        context = _captcha_context(browser, storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            page.goto(profile_url, wait_until="domcontentloaded")
            _captcha_guard(page, log=print)
            follow_sel = f'{_HEADER} [data-testid$="-follow"]'
            unfollow_sel = f'{_HEADER} [data-testid$="-unfollow"]'
            if not _visible(page, follow_sel, 20_000):
                if _visible(page, unfollow_sel, 3_000):
                    return "already"
                raise RuntimeError("لم أجد زر المتابعة في رأس الملف الشخصي (الحساب غير موجود أو لم تُحمّل الصفحة)")
            btn = page.locator(follow_sel).first
            btn.scroll_into_view_if_needed()
            try:
                btn.click(timeout=8000)
            except Exception:
                btn.click(timeout=8000, force=True)
            if not _visible(page, unfollow_sel, 10_000):
                raise RuntimeError("لم تتأكد المتابعة (لم يتحوّل الزر إلى «إلغاء المتابعة»)")
            page.wait_for_timeout(wait_after_ms)
            return "done"
        finally:
            context.close()
            browser.close()
