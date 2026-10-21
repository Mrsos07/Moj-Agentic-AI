import os
from playwright.sync_api import sync_playwright

from .x_captcha import guard as _captcha_guard

DEFAULT_TIMEOUT = 30_000


def _visible(page, selector: str, timeout_ms: int) -> bool:
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


def unfollow_user(storage_state_path: str, profile_url: str, headless: bool, wait_after_ms: int = 3000) -> str:
    """Unfollow a profile. Returns ``"done"`` (verified) or ``"already"`` (was not followed).

    كان ينقر ثم يعتبر ذلك نجاحاً، ومحدد ``has-text("Following")`` الاحتياطي قد يطابق
    تبويب «Following» في الصفحة. النجاح الآن = عودة زر ``<id>-follow``."""
    chrome_channel = os.getenv("XSUITE_CHROME_CHANNEL", "").strip() or "chrome"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=chrome_channel, headless=headless)
        except Exception:
            browser = p.chromium.launch(headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            page.goto(profile_url, wait_until="domcontentloaded")
            _captcha_guard(page, log=print)
            # زر رأس الملف فقط: الشريط الجانبي («من تتابع») فيه دائماً أزرار ‎-follow‎ لحسابات
            # أخرى — قيس: 3 منها على كل ملف، فكان «ظهور زر متابعة» سيؤكد إلغاءً لم يحدث.
            header = '[data-testid="primaryColumn"] [data-testid="placementTracking"]'
            unfollow_sel = f'{header} [data-testid$="-unfollow"]'
            follow_sel = f'{header} [data-testid$="-follow"]'
            if not _visible(page, unfollow_sel, 20_000):
                if _visible(page, follow_sel, 3_000):
                    return "already"
                raise RuntimeError("لم أجد زر المتابعة في الملف الشخصي (الحساب غير موجود أو لم تُحمّل الصفحة)")

            btn = page.locator(unfollow_sel).first
            btn.scroll_into_view_if_needed()
            try:
                btn.click(timeout=8000)
            except Exception:
                btn.click(timeout=8000, force=True)

            confirm = page.locator('[data-testid="confirmationSheetConfirm"]')
            if _visible(page, '[data-testid="confirmationSheetConfirm"]', 8_000):
                try:
                    confirm.first.click(timeout=8000)
                except Exception:
                    confirm.first.click(timeout=8000, force=True)

            if not _visible(page, follow_sel, 10_000):
                raise RuntimeError("لم يتأكد إلغاء المتابعة (لم يظهر زر «متابعة»)")
            page.wait_for_timeout(wait_after_ms)
            return "done"
        finally:
            context.close()
            browser.close()
