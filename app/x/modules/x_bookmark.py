"""حفظ تغريدة (Bookmark) والتراجع عنه — بتحقق فعلي من تبدّل حالة الزر.

كانت النسختان تضغطان أول زر تجدانه ثم تعتبران ذلك نجاحاً؛ والتراجع كان يضغط
``bookmark`` إن لم يجد ``removeBookmark`` — أي أنه **يحفظ** تغريدة غير محفوظة.
"""
from playwright.sync_api import sync_playwright

from .x_actions import _goto_tweet, _launch_browser, _state_in, focal_tweet

DEFAULT_TIMEOUT = 30_000


def _toggle(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int,
            press: str, result: str, label: str) -> str:
    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)
            tweet = focal_tweet(page, tweet_url)
            if not _state_in(tweet, press, timeout_ms=3_000):
                if _state_in(tweet, result, timeout_ms=3_000):
                    return "already"
                raise RuntimeError(f"لم أجد زر {label} في التغريدة")
            btn = tweet.locator(f"[data-testid='{press}']").first
            btn.scroll_into_view_if_needed()
            try:
                btn.click(timeout=5000)
            except Exception:
                btn.click(timeout=5000, force=True)
            if not _state_in(tweet, result, timeout_ms=8_000):
                raise RuntimeError(f"لم يتأكد {label} (لم يتحوّل الزر إلى {result})")
            page.wait_for_timeout(wait_after_ms)
            return "done"
        finally:
            context.close()
            browser.close()


def bookmark_tweet(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int = 3000) -> str:
    """Bookmark a tweet. ``"done"`` (verified) or ``"already"``."""
    return _toggle(storage_state_path, tweet_url, headless, wait_after_ms,
                   "bookmark", "removeBookmark", "الحفظ")


def undo_bookmark_tweet(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int = 3000) -> str:
    """Remove a bookmark. ``"done"`` (verified) or ``"already"`` (was not bookmarked)."""
    return _toggle(storage_state_path, tweet_url, headless, wait_after_ms,
                   "removeBookmark", "bookmark", "إلغاء الحفظ")
