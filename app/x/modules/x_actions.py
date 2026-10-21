import os
import re
import time
from typing import Optional

from playwright.sync_api import Page, sync_playwright

from .x_captcha import guard as _captcha_guard


DEFAULT_TIMEOUT = 30_000


def _launch_browser(p, headless: bool):
    """Launch Chromium with optional Chrome channel (defaults to installed Chrome)."""
    chrome_channel = os.getenv("XSUITE_CHROME_CHANNEL", "").strip() or "chrome"
    try:
        return p.chromium.launch(channel=chrome_channel, headless=headless)
    except Exception:
        # Fallback if channel isn't available
        return p.chromium.launch(headless=headless)


def _norm_tweet_url(url: str) -> str:
    url = (url or "").strip()
    # Accept x.com or twitter.com
    if url.startswith("https://twitter.com/"):
        url = url.replace("https://twitter.com/", "https://x.com/", 1)
    return url


def _goto_tweet(page: Page, tweet_url: str):
    tweet_url = _norm_tweet_url(tweet_url)
    if not tweet_url:
        raise ValueError("tweet_url required")
    page.goto(tweet_url, wait_until="domcontentloaded")
    # تحقق «Verify you are human» يظهر أحياناً قبل التغريدة — بدونه كل تفاعل يفشل
    _captcha_guard(page, log=print)
    # Ensure tweet page is ready
    page.wait_for_timeout(6200)
    # Sometimes content loads after initial DOMContentLoaded
    page.locator("article").first.wait_for(state="visible", timeout=60_000)


def _click_first_visible(page: Page, selectors: list[str], timeout_ms: int = 30_000):
    deadline = time.time() + (timeout_ms / 1000.0)
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    loc.first.scroll_into_view_if_needed()
                    try:
                        loc.first.click(timeout=3_000)
                    except Exception:
                        loc.first.click(timeout=3_000, force=True)
                    return
            except Exception as e:
                last_err = e
        page.wait_for_timeout(250)
    if last_err:
        raise last_err
    raise TimeoutError("Could not find a clickable element")


def _wait_any(page: Page, selectors: list[str], timeout_ms: int = 30_000):
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible():
                    return
            except Exception:
                pass
        page.wait_for_timeout(200)
    raise TimeoutError("Timeout waiting for expected UI")


def _get_scope(page: Page):
    dlg = page.locator("div[role='dialog']")
    return dlg.first if dlg.count() else page


def _pick_file_input(scope: Page):
    # Prefer testid fileInput when present
    loc = scope.locator("input[type='file'][data-testid='fileInput']")
    if loc.count():
        return loc.first
    loc = scope.locator("input[type='file']")
    if loc.count():
        for i in range(min(loc.count(), 10)):
            item = loc.nth(i)
            try:
                if item.is_visible():
                    return item
            except Exception:
                pass
        return loc.first
    return None


def _media_preview_visible(scope: Page) -> bool:
    candidates = [
        'div[data-testid="attachments"]',
        'div[data-testid="tweetPhoto"]',
        'div[data-testid="tweetPhotoContainer"]',
        'div[data-testid="mediaContainer"]',
        'div[data-testid="videoPlayer"]',
        'div[data-testid="videoComponent"]',
        'video',
        '#layers img',
        '#layers video',
        'img[src*="twimg"]',
    ]
    for sel in candidates:
        try:
            loc = scope.locator(sel)
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            pass
    return False


def _media_uploading_indicator_visible(scope: Page) -> bool:
    # Avoid general countdown-circle; focus inside attachments containers when possible
    candidates = [
        'div[data-testid="mediaUploadProgress"]',
        '#layers div[data-testid="mediaUploadProgress"]',
        'div[data-testid="attachments"] div[role="progressbar"]',
        'div[data-testid="mediaContainer"] div[role="progressbar"]',
        'div[data-testid="tweetPhotoContainer"] div[role="progressbar"]',
        # Localized aria labels
        'div[aria-label*="Uploading"]',
        'div[aria-label*="Processing"]',
        'div[aria-label*="رفع"]',
        'div[aria-label*="معالجة"]',
        'svg[aria-label*="Uploading"]',
        'svg[aria-label*="Processing"]',
        'svg[aria-label*="رفع"]',
        'svg[aria-label*="معالجة"]',
    ]
    for sel in candidates:
        try:
            loc = scope.locator(sel)
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            pass
    return False


def _wait_media_uploaded_in_composer(page: Page, timeout_ms: int = 180_000):
    """ديناميكي: ينتظر preview للميديا ثم ينتظر انتهاء الرفع/المعالجة (خصوصًا للفيديو)."""
    deadline = time.time() + (timeout_ms / 1000.0)
    scope = _get_scope(page)

    # Remove button is a strong hint media is attached
    remove_btn = scope.locator(
        '#layers [aria-label="Remove"], #layers [aria-label="Remove media"], '
        '#layers [aria-label="إزالة"], #layers [aria-label="حذف"], '
        '#layers [data-testid*="remove"]'
    )

    saw_preview = False
    stable_ticks = 0

    while time.time() < deadline:
        if _media_preview_visible(scope) or (remove_btn.count() and remove_btn.first.is_visible()):
            saw_preview = True

        if not saw_preview:
            page.wait_for_timeout(250)
            continue

        # If still uploading/processing -> keep waiting
        if _media_uploading_indicator_visible(scope):
            stable_ticks = 0
            page.wait_for_timeout(450)
            continue

        # No indicator: require a short stable window (video often flips states)
        stable_ticks += 1
        if stable_ticks >= 4:  # ~1.2s stable
            page.wait_for_timeout(700)
            return
        page.wait_for_timeout(300)

    raise TimeoutError("انتهت مهلة انتظار رفع/معالجة الميديا.")


def _wait_button_enabled(scope: Page, selectors: list[str], timeout_ms: int = 120_000):
    deadline = time.time() + (timeout_ms / 1000.0)
    while time.time() < deadline:
        for sel in selectors:
            try:
                btn = scope.locator(sel)
                if btn.count() and btn.first.is_visible():
                    aria = btn.first.get_attribute("aria-disabled")
                    try:
                        enabled = btn.first.is_enabled()
                    except Exception:
                        enabled = (aria != "true")
                    if enabled and aria != "true":
                        return True
            except Exception:
                pass
        scope.page.wait_for_timeout(250)
    return False

def _testid_visible(page, testid: str, timeout_ms: int = 8_000) -> bool:
    """انتظر ظهور عنصر بـ data-testid محدّد خلال المهلة (إشارة تأكيد للفعل)."""
    deadline = time.time() + (timeout_ms / 1000.0)
    loc = page.locator(f"[data-testid='{testid}']")
    while time.time() < deadline:
        try:
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            pass
        page.wait_for_timeout(200)
    return False


def _success_toast_sent(page) -> bool:
    """توست تأكيد إرسال (رد/منشور) بنصوص X المعتادة عربي/إنجليزي."""
    try:
        toast = page.get_by_test_id("toast")
        if not (toast.count() and toast.first.is_visible()):
            return False
        txt = (toast.first.inner_text() or "").lower()
        markers = ["your post was sent", "your reply was sent", "was sent", "sent",
                   "تم إرسال", "تم الرد", "أُرسل", "ارسل", "ردّك", "ردك"]
        return any(m in txt for m in markers)
    except Exception:
        return False


def _reply_submitted(page, filled_text: str = "", timeout_ms: int = 20_000) -> bool:
    """تأكيد فعلي أن الرد أُرسل.

    إشارتان موثوقتان بعد الضغط على زر النشر:
      1) ظهور توست نجاح ("Your reply was sent" / "تم إرسال ردك").
      2) إفراغ/اختفاء محرّر الرد (tweetTextarea_0) بعد أن كان يحوي النص.
    إن بقي المحرّر ممتلئاً بالنص ولم يظهر توست => الرد لم يُرسل.
    """
    deadline = time.time() + (timeout_ms / 1000.0)
    tb = page.locator("div[data-testid='tweetTextarea_0']")
    while time.time() < deadline:
        if _success_toast_sent(page):
            return True
        try:
            if tb.count() == 0 or not tb.first.is_visible():
                return True
            cur = (tb.first.inner_text() or "").strip()
            # أُفرغ المحرّر بعد أن كان يحوي نصّنا => تم الإرسال
            if filled_text and cur == "":
                return True
        except Exception:
            return True
        page.wait_for_timeout(300)
    return False


def like_tweet(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int = 2_000):
    """Like a tweet by URL."""
    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)

            # Prefer stable testids
            selectors = [
                "div[data-testid='like']",
                "button[data-testid='like']",
                "div[role='button'][data-testid='like']",
                # Fallback to aria-label (EN/AR)
                "[aria-label*='Like']",
                "[aria-label*='إعجاب']",
                "[aria-label*='أعجب']",
            ]
            _click_first_visible(page, selectors, timeout_ms=60_000)

            # تأكيد فعلي: تحوّل الزر إلى "unlike" يعني أن الإعجاب سُجّل بالفعل.
            if not _testid_visible(page, "unlike", timeout_ms=8_000):
                try:
                    page.screenshot(path="debug_like.png", full_page=True)
                except Exception:
                    pass
                raise RuntimeError("لم يتم تأكيد الإعجاب (لم يتحوّل الزر إلى unlike).")

            page.wait_for_timeout(wait_after_ms)
        finally:
            context.close()
            browser.close()


def repost_tweet(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int = 2_000):
    """Repost (retweet) a tweet by URL."""
    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)

            # سبق نشرها؟ زر unretweet ظاهر يعني «معاد نشرها أصلاً» — نجاح
            # idempotent. بدون هذا الفحص تنفتح قائمة «Undo repost» ويطابقها
            # محدد has-text('Repost') فيُلغى الرتويت بالخطأ ثم يفشل التأكيد.
            if _testid_visible(page, "unretweet", timeout_ms=1_500):
                page.wait_for_timeout(wait_after_ms)
                return

            # Step 1: open repost menu
            open_menu_selectors = [
                "div[data-testid='retweet']",
                "button[data-testid='retweet']",
                "div[role='button'][data-testid='retweet']",
                # recorded: aria label contains reposts. Repost
                "[aria-label*='reposts'][aria-label*='Repost']",
                "[aria-label*='Repost']",
                "[aria-label*='إعادة النشر']",
                "[aria-label*='إعاده النشر']",
                "[aria-label*='إعادة نشر']",
            ]
            # واجهة X تتجاهل بعض النقرات البرمجية بصمت (نفس علّة الكومبوزر):
            # النقرة «تنجح» ولا تنفتح القائمة، فكان انتظارٌ واحد 15 ثانية
            # يفشل بـ Timeout waiting for expected UI. الحل: أعد النقر حتى
            # تظهر القائمة فعلاً بدل انتظار نقرة واحدة قد تكون ضاعت.
            menu_sel = ["div[role='menu']", "div[role='menuitem']"]
            for attempt in range(3):
                _click_first_visible(page, open_menu_selectors, timeout_ms=20_000)
                try:
                    _wait_any(page, menu_sel, timeout_ms=5_000)
                    break
                except TimeoutError:
                    if attempt == 2:
                        raise
                    page.wait_for_timeout(800)

            # Step 2: choose "Repost" from menu — داخل القائمة المفتوحة حصراً،
            # وإلا طابق span عاماً في الصفحة (عدّاد Reposts مثلاً).
            menu_item_selectors = [
                "div[role='menuitem']:has-text('Repost')",
                "div[role='menuitem']:has-text('إعادة النشر')",
                "div[role='menuitem']:has-text('إعاده النشر')",
                "div[role='menuitem']:has-text('إعادة نشر')",
                "div[role='menu'] span:has-text('Repost')",
                "div[role='menu'] span:has-text('إعادة النشر')",
                "div[role='menu'] span:has-text('إعاده النشر')",
                "div[role='menu'] span:has-text('إعادة نشر')",
            ]
            _click_first_visible(page, menu_item_selectors, timeout_ms=30_000)

            # تأكيد فعلي: تحوّل الزر إلى "unretweet" يعني أن إعادة النشر تمّت.
            if not _testid_visible(page, "unretweet", timeout_ms=8_000):
                try:
                    page.screenshot(path="debug_repost.png", full_page=True)
                except Exception:
                    pass
                raise RuntimeError("لم يتم تأكيد إعادة النشر (لم يتحوّل الزر إلى unretweet).")

            page.wait_for_timeout(wait_after_ms)
        finally:
            context.close()
            browser.close()


def focal_tweet(page: Page, tweet_url: str):
    """التغريدة الرئيسية في صفحة ``/status/<id>`` (رابط وقتها ينتهي بالمعرّف).

    ``page.locator(...).first`` خطأ هنا: صفحة الرد تعرض التغريدة الأصلية فوقه، فكان
    التراجع عن إعجاب/ريتويت يقع على تغريدة أخرى. إن لم نجد المعرّف نعود لأول تغريدة."""
    m = re.search(r"/status/(\d+)", tweet_url or "")
    if m:
        loc = page.locator(f"article[data-testid='tweet']:has(a[href$='/status/{m.group(1)}'] time)")
        try:
            loc.first.wait_for(state="visible", timeout=15_000)
            return loc.first
        except Exception:
            pass
    return page.locator("article").first


def _state_in(scope, testid: str, timeout_ms: int = 8_000) -> bool:
    """انتظر ظهور زر بحالة معيّنة (like/unlike/retweet/unretweet...) داخل النطاق."""
    deadline = time.time() + (timeout_ms / 1000.0)
    loc = scope.locator(f"[data-testid='{testid}']")
    while time.time() < deadline:
        try:
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def undo_repost_tweet(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int = 2_000) -> str:
    """Undo repost (unretweet). Returns ``"done"`` (verified) or ``"already"`` (was not reposted).

    كان يضغط نص «Undo repost» الإنجليزي فقط، بلا أي تحقق، على أول تغريدة في الصفحة."""
    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)
            tweet = focal_tweet(page, tweet_url)
            if not _state_in(tweet, "unretweet", timeout_ms=3_000):
                if _state_in(tweet, "retweet", timeout_ms=3_000):
                    return "already"
                raise RuntimeError("لم أجد زر إعادة النشر في التغريدة")
            tweet.locator("[data-testid='unretweet']").first.click()
            confirm = page.locator("[data-testid='unretweetConfirm']")
            if not _state_in(page, "unretweetConfirm", timeout_ms=5_000):
                confirm = page.locator("div[role='menu'] [role='menuitem']").filter(
                    has_text=re.compile(r"Undo repost|التراجع عن إعادة النشر|إلغاء إعادة النشر", re.I))
            confirm.first.click(timeout=8_000)
            if not _state_in(tweet, "retweet", timeout_ms=8_000):
                raise RuntimeError("لم يتأكد التراجع عن إعادة النشر (لم يعد الزر إلى retweet)")
            page.wait_for_timeout(wait_after_ms)
            return "done"
        finally:
            context.close()
            browser.close()


def undo_like_tweet(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int = 2_000) -> str:
    """Undo like. Returns ``"done"`` (verified) or ``"already"`` (was not liked).

    كان يضغط ``like`` أيضاً إن لم يجد ``unlike`` — أي أنه **يُعجب** بتغريدة لم تكن
    معجَباً بها بدل أن يتراجع."""
    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)
            tweet = focal_tweet(page, tweet_url)
            if not _state_in(tweet, "unlike", timeout_ms=3_000):
                if _state_in(tweet, "like", timeout_ms=3_000):
                    return "already"
                raise RuntimeError("لم أجد زر الإعجاب في التغريدة")
            tweet.locator("[data-testid='unlike']").first.click()
            if not _state_in(tweet, "like", timeout_ms=8_000):
                raise RuntimeError("لم يتأكد إلغاء الإعجاب (لم يعد الزر إلى like)")
            page.wait_for_timeout(wait_after_ms)
            return "done"
        finally:
            context.close()
            browser.close()


def reply_to_tweet(
    storage_state_path: str,
    tweet_url: str,
    reply_text: str,
    headless: bool,
    media_path: Optional[str] = None,
    media_timeout_ms: int = 180_000,
    wait_after_ms: int = 5_000,
):
    """Reply to a tweet by URL."""
    reply_text = (reply_text or "").strip()
    if not reply_text:
        raise ValueError("reply_text required")

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)

            # Open reply composer (if needed)
            reply_btn_selectors = [
                "div[data-testid='reply']",
                "button[data-testid='reply']",
                "div[role='button'][data-testid='reply']",
                "[aria-label*='Reply']",
                "[aria-label*='رد']",
            ]
            # If textbox already visible, skip clicking reply
            textbox = page.locator("div[data-testid='tweetTextarea_0']")
            if not (textbox.count() and textbox.first.is_visible()):
                _click_first_visible(page, reply_btn_selectors, timeout_ms=30_000)

            # Textbox
            tb = page.locator("div[data-testid='tweetTextarea_0'][role='textbox']")
            if not tb.count():
                tb = page.locator("div[data-testid='tweetTextarea_0']")
            tb.first.wait_for(state="visible", timeout=60_000)
            tb.first.click()
            try:
                tb.first.fill(reply_text)
            except Exception:
                tb.first.press("Control+A")
                

            # Attach media (optional) for reply
            if media_path and str(media_path).strip():
                scope = _get_scope(page)
                file_input = _pick_file_input(scope)
                if file_input is None:
                    raise RuntimeError("Could not find file input for reply composer.")
                file_input.wait_for(state="attached", timeout=60_000)
                file_input.set_input_files(media_path)
                _wait_media_uploaded_in_composer(page, timeout_ms=media_timeout_ms)
            
            
            # Publish reply: tweetButtonInline preferred
            publish_selectors = [
                "button[data-testid='tweetButtonInline']",
                "button[data-testid='tweetButton']",
                # fallback by visible text (EN/AR)
                "button:has-text('Reply')",
                "button:has-text('رد')",
                "button:has-text('نشر')",
                "button:has-text('Post')",
            ]

            # Wait until enabled (no eval/CSP-safe). For video replies this matters.
            scope = _get_scope(page)
            _wait_button_enabled(scope, ["button[data-testid='tweetButtonInline']", "button[data-testid='tweetButton']", "button:has-text('Reply')", "button:has-text('رد')", "button:has-text('نشر')", "button:has-text('Post')"], timeout_ms=120_000)

            # If it never becomes enabled, we'll still attempt the click (X UI can be flaky)
            _click_first_visible(page, publish_selectors, timeout_ms=30_000)
            page.wait_for_timeout(1500)

            # تأكيد فعلي أن الرد أُرسل قبل اعتبار العملية ناجحة (يمنع النجاح الكاذب).
            if not _reply_submitted(page, filled_text=reply_text, timeout_ms=20_000):
                try:
                    page.screenshot(path="debug_reply.png", full_page=True)
                except Exception:
                    pass
                raise RuntimeError(
                    "لم يتم تأكيد إرسال الرد: بقي المحرّر ممتلئاً ولم يظهر تأكيد الإرسال."
                )

            # انتظار قصير بعد التأكيد
            page.wait_for_timeout(wait_after_ms)
        finally:
            context.close()
            browser.close()


def bookmark_tweet(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int = 2_000):
    """Bookmark a tweet by URL."""
    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)
            selectors = [
                "div[data-testid='bookmark']",
                "button[data-testid='bookmark']",
                "div[role='button'][data-testid='bookmark']",
                "[aria-label*='Bookmark']",
                "[aria-label*='Bookmarks']",
                "[aria-label*='إشارة مرجعية']",
                "[aria-label*='الإشارات المرجعية']",
            ]
            _click_first_visible(page, selectors, timeout_ms=60_000)

            # تأكيد فعلي: تحوّل الزر إلى "removeBookmark" يعني أن الحفظ تمّ.
            # بدونه كان «ملف الإلهام» يملأ بمراجع لم تُحفظ فعلاً.
            if not _testid_visible(page, "removeBookmark", timeout_ms=8_000):
                try:
                    page.screenshot(path="debug_bookmark.png", full_page=True)
                except Exception:
                    pass
                raise RuntimeError(
                    "لم يتم تأكيد الحفظ (لم يتحوّل الزر إلى removeBookmark)."
                )
            page.wait_for_timeout(wait_after_ms)
        finally:
            context.close()
            browser.close()


def quote_tweet(
    storage_state_path: str,
    tweet_url: str,
    text: str,
    headless: bool,
    media_path: Optional[str] = None,
    media_timeout_ms: int = 180_000,
    wait_after_ms: int = 5_000,
):
    """Quote a tweet (Repost with comment) by URL."""
    text = (text or '').strip()
    if not text:
        raise ValueError("text required for quote")

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)

            # Open repost menu
            open_menu_selectors = [
                "div[data-testid='retweet']",
                "button[data-testid='retweet']",
                "div[role='button'][data-testid='retweet']",
                "[aria-label*='Repost']",
                "[aria-label*='إعادة النشر']",
                "[aria-label*='إعاده النشر']",
                "[aria-label*='إعادة نشر']",
            ]
            # نفس علّة النقرة الصامتة في repost_tweet: أعد النقر حتى تنفتح القائمة
            menu_sel = ["div[role='menu']", "div[role='menuitem']"]
            for attempt in range(3):
                _click_first_visible(page, open_menu_selectors, timeout_ms=20_000)
                try:
                    _wait_any(page, menu_sel, timeout_ms=5_000)
                    break
                except TimeoutError:
                    if attempt == 2:
                        raise
                    page.wait_for_timeout(800)

            # Click Quote (EN/AR + تنويعات همزة القطع/الوصل في واجهة X العربية)
            quote_selectors = [
                "div[role='menuitem']:has-text('Quote')",
                "div[role='menuitem']:has-text('اقتباس')",
                "div[role='menuitem']:has-text('إقتباس')",
                "div[role='menuitem']:has-text('اقتبس')",
                "div[role='menuitem']:has-text('مع تعليق')",
                "a[href*='/compose/post']",
                "span:has-text('Quote')",
                "span:has-text('اقتباس')",
                "span:has-text('إقتباس')",
                "span:has-text('اقتبس')",
            ]
            _click_first_visible(page, quote_selectors, timeout_ms=30_000)

            scope = _get_scope(page)
            tb = scope.locator("div[data-testid='tweetTextarea_0'][role='textbox']")
            if not tb.count():
                tb = scope.locator("div[data-testid='tweetTextarea_0']")
            tb.first.wait_for(state="visible", timeout=60_000)
            tb.first.click()
            try:
                tb.first.fill(text)
            except Exception:
                tb.first.press("Control+A")
                tb.first.type(text, delay=10)

            if media_path and str(media_path).strip():
                file_input = _pick_file_input(scope)
                if file_input is None:
                    raise RuntimeError("Could not find file input for quote composer.")
                file_input.wait_for(state="attached", timeout=60_000)
                file_input.set_input_files(media_path)
                _wait_media_uploaded_in_composer(page, timeout_ms=media_timeout_ms)

            # ── النشر مع تأكيد فعلي (كان هنا «نجاح كاذب») ──
            # المشكلة السابقة: نضغط الزر ثم ننتظر وننهي بنجاح دون أي تحقّق. ومحرّر
            # X الجديد (Lexical) لا يُرسل دائماً بالنقر البرمجي، فكان الاقتباس
            # يُبلّغ بالنجاح ولا شيء يُنشر. نستخدم الآن نفس المسار المُجرَّب في
            # النشر: انتظار تفعيل الزر → نقر داخل المودال → تأكيد → فولباك
            # Ctrl+Enter → فشل صريح إن لم يتأكّد الإرسال.
            from .x_post import (
                _click_publish, _composer_dialog_open, _confirm_submitted,
                _submit_via_keyboard, _wait_publish_button_enabled,
            )

            # يرفع TimeoutError إن لم يُفعّل الزر (كان يُتجاهل فيُنقر زر معطّل)
            _wait_publish_button_enabled(page, timeout_ms=120_000)
            composer_open_before = _composer_dialog_open(page)

            _click_publish(page)
            page.wait_for_timeout(1_500)
            # ممنوع الاعتماد على وجود «/status/» في الرابط: صفحة الاقتباس تبدأ
            # عليه أصلاً، فيصير التأكيد كاذباً دائماً.
            sent = _confirm_submitted(page, timeout_ms=12_000,
                                      saw_dialog_before=composer_open_before,
                                      allow_status_url_fallback=False)
            if not sent:
                sent = _submit_via_keyboard(page, timeout_ms=15_000,
                                            allow_status_url_fallback=False)
            if not sent:
                try:
                    page.screenshot(path="debug_quote.png", full_page=True)
                except Exception:
                    pass
                raise RuntimeError(
                    "لم يتم تأكيد نشر الاقتباس: بقي المحرّر مفتوحاً ولم يظهر "
                    "تأكيد الإرسال من X."
                )
            page.wait_for_timeout(wait_after_ms)
        finally:
            context.close()
            browser.close()


def share_copy_link(storage_state_path: str, tweet_url: str, headless: bool, wait_after_ms: int = 2_000):
    """Open share menu and click 'Copy link' (share via OS sheet is not reliable in automation)."""
    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        try:
            _goto_tweet(page, tweet_url)

            share_btn_selectors = [
                "div[data-testid='share']",
                "button[data-testid='share']",
                "div[role='button'][data-testid='share']",
                "[aria-label*='Share']",
                "[aria-label*='Share post']",
                "[aria-label*='مشاركة']",
                "[aria-label*='شارك']",
            ]
            _click_first_visible(page, share_btn_selectors, timeout_ms=60_000)

            # Copy link option (EN/AR)
            copy_selectors = [
                "div[role='menuitem']:has-text('Copy link')",
                "div[role='menuitem']:has-text('Copy link to post')",
                "div[role='menuitem']:has-text('نسخ الرابط')",
                "div[role='menuitem']:has-text('نسخ رابط')",
                "span:has-text('Copy link')",
                "span:has-text('نسخ الرابط')",
            ]
            _wait_any(page, ["div[role='menu']", "div[role='menuitem']"], timeout_ms=15_000)
            _click_first_visible(page, copy_selectors, timeout_ms=30_000)
            page.wait_for_timeout(wait_after_ms)
        finally:
            context.close()
            browser.close()


def follow_user(storage_state_path: str, profile_url: str, headless: bool = True, wait_after_ms: int = 2000):
    """
    Follow user by visiting their profile and clicking Follow.
    Supports Arabic/English because it relies on data-testid patterns when possible.
    """
    from playwright.sync_api import sync_playwright

    chrome_channel = os.getenv("XSUITE_CHROME_CHANNEL", "").strip() or "chrome"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=chrome_channel, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(60_000)

        try:
            page.goto(profile_url, wait_until="domcontentloaded")
            _captcha_guard(page, log=print)
            page.wait_for_timeout(6200)

            # Prefer testid like "*-follow"
            btn = page.locator('[data-testid$="-follow"]')
            if not (btn.count() and btn.first.is_visible()):
                # fallback: any button containing Arabic "متابعة" or English "Follow"
                btn = page.locator('button:has-text("متابعة"), button:has-text("Follow")')

            btn.first.wait_for(state="visible", timeout=30_000)
            btn.first.scroll_into_view_if_needed()

            # التقط معرّف الزر بعينه قبل النقر: صفحة الملف تحوي أزرار متابعة
            # لحسابات مقترحة أيضاً، فالبحث في الصفحة كلها قد يؤكّد زراً آخر.
            try:
                clicked_testid = btn.first.get_attribute("data-testid") or ""
            except Exception:
                clicked_testid = ""

            try:
                btn.first.click(timeout=8_000)
            except Exception:
                btn.first.click(timeout=8_000, force=True)

            # تأكيد فعلي: يتحوّل نفس الزر إلى "<id>-unfollow" بعد نجاح المتابعة
            # (أو إلى إلغاء طلب المتابعة في الحسابات المحمية).
            expected = []
            if clicked_testid.endswith("-follow"):
                base = clicked_testid[: -len("-follow")]
                expected = [f'[data-testid="{base}-unfollow"]',
                            f'[data-testid="{base}-cancelFollowRequest"]']
            expected += ['[data-testid$="-unfollow"]',
                         '[data-testid$="-cancelFollowRequest"]',
                         'button:has-text("إلغاء المتابعة")',
                         'button:has-text("Following")']
            confirmed = False
            for sel in expected:
                try:
                    loc = page.locator(sel)
                    if loc.count():
                        loc.first.wait_for(state="visible", timeout=6_000)
                        confirmed = True
                        break
                except Exception:
                    continue
            if not confirmed:
                try:
                    page.screenshot(path="debug_follow.png", full_page=True)
                except Exception:
                    pass
                raise RuntimeError("لم يتم تأكيد المتابعة (لم يتحوّل الزر إلى unfollow).")

            page.wait_for_timeout(wait_after_ms)
        finally:
            context.close()
            browser.close()


def unfollow_user(storage_state_path: str, profile_url: str, headless: bool = True, wait_after_ms: int = 2000):
    """
    Unfollow user by visiting their profile and clicking Following -> confirm.
    Uses data-testid '*-unfollow' and confirmationSheetConfirm.
    """
    from playwright.sync_api import sync_playwright

    chrome_channel = os.getenv("XSUITE_CHROME_CHANNEL", "").strip() or "chrome"

    with sync_playwright() as p:
        browser = p.chromium.launch(channel=chrome_channel, headless=headless)
        context = browser.new_context(storage_state=storage_state_path)
        page = context.new_page()
        page.set_default_timeout(60_000)

        try:
            page.goto(profile_url, wait_until="domcontentloaded")
            _captcha_guard(page, log=print)
            page.wait_for_timeout(6200)

            btn = page.locator('[data-testid$="-unfollow"]')
            if not (btn.count() and btn.first.is_visible()):
                # Sometimes shows "Following" or "متابَع" etc.
                btn = page.locator('button:has-text("إلغاء المتابعة"), button:has-text("Unfollow"), button:has-text("Following"), button:has-text("متابَع")')

            btn.first.wait_for(state="visible", timeout=30_000)
            btn.first.scroll_into_view_if_needed()

            try:
                btn.first.click(timeout=8_000)
            except Exception:
                btn.first.click(timeout=8_000, force=True)

            # confirm sheet
            confirm = page.locator('[data-testid="confirmationSheetConfirm"]')
            if confirm.count():
                confirm.first.wait_for(state="visible", timeout=15_000)
                try:
                    confirm.first.click(timeout=8_000)
                except Exception:
                    confirm.first.click(timeout=8_000, force=True)

            page.wait_for_timeout(wait_after_ms)
        finally:
            context.close()
            browser.close()
