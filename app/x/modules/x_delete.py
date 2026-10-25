import json
import re
import time
from typing import Optional

from .x_captcha import guard as _captcha_guard
from .x_captcha import goto as _goto
from .x_captcha import launch_browser as _launch_browser
from .x_captcha import new_context as _captcha_context
from .x_captcha import sync_playwright
from .sync_bridge import sync_safe

DEFAULT_TIMEOUT = 30_000

#: عنصر قائمة «حذف» بالضبط — لا أي نص يحوي «حذف» في الصفحة
_DELETE_ITEM = re.compile(r"^\s*(Delete|حذف)\s*$", re.I)


def build_tweet_url_by_id(tweet_id: str) -> str:
    """بناء رابط التغريدة من ID فقط باستخدام الرابط العام."""
    tweet_id = str(tweet_id).strip()
    return f"https://x.com/i/status/{tweet_id}"


def _tweet_id(url: str) -> str:
    m = re.search(r"/status/(\d+)", url or "")
    return m.group(1) if m else ""


@sync_safe
def delete_tweet_by_id(storage_state_path: str, tweet_id: str, headless: bool = True,
                       wait_after_ms: int = 3000, expected_handle: str = "") -> bool:
    """حذف تغريدة من X باستخدام ID التغريدة فقط (انظر ``delete_tweet``)."""
    tweet_url = build_tweet_url_by_id(tweet_id)
    return delete_tweet(storage_state_path, tweet_url, headless, wait_after_ms,
                        expected_handle=expected_handle)


def _delete_confirmed(resp_box: list) -> Optional[bool]:
    """نتيجة طلب DeleteTweet الملتقَط: True نجح، False رفضه X، None لم يصل بعد."""
    for resp in resp_box:
        try:
            if resp.status != 200:
                return False
            body = json.loads(resp.text() or "{}")
        except Exception:
            continue
        if body.get("errors"):
            return False
        return True
    return None


@sync_safe
def delete_tweet(storage_state_path: str, tweet_url: str, headless: bool = True,
                 wait_after_ms: int = 3000, expected_handle: str = "") -> bool:
    """حذف تغريدة يملكها الحساب، مع تأكيد فعلي.

    - الهدف هو التغريدة **الرئيسية** في الصفحة (رابط وقتها ينتهي بـ ``/status/<id>``)
      لا أول تغريدة: صفحة الرد تعرض التغريدة الأصلية فوقه.
    - ``expected_handle``: إن مُرّر نرفض الحذف إن لم يكن كاتب التغريدة هو الحساب نفسه.
    - النجاح = ردّ X على طلب ``DeleteTweet`` بلا أخطاء، أو اختفاء التغريدة بعد إعادة
      التحميل. إغلاق نافذة التأكيد وحده ليس دليلاً.

    يعيد True عند التأكد فقط، ويرمي RuntimeError بسبب واضح في كل فشل.
    """
    tweet_id = _tweet_id(tweet_url)
    if not tweet_id:
        raise RuntimeError("رابط/معرّف التغريدة غير صالح")
    expected = (expected_handle or "").strip().lstrip("@").lower()

    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = _captcha_context(
            browser, storage_state_path,
            page_url=tweet_url,
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)
        delete_responses: list = []
        page.on("response", lambda r: delete_responses.append(r)
                if "/DeleteTweet" in r.url and r.request.method == "POST" else None)
        focal_sel = f"article[data-testid='tweet']:has(a[href$='/status/{tweet_id}'] time)"

        try:
            _goto(page, tweet_url, wait_until="domcontentloaded")
            _captcha_guard(page, log=print)
            focal = page.locator(focal_sel)
            try:
                focal.first.wait_for(state="visible", timeout=25_000)
            except Exception:
                raise RuntimeError("التغريدة غير موجودة أو محذوفة مسبقاً أو لا يمكن عرضها")
            focal = focal.first

            href = focal.locator(f"a[href$='/status/{tweet_id}']").first.get_attribute("href") or ""
            author = href.strip("/").split("/")[0].lower() if href else ""
            if expected and author and author != expected:
                raise RuntimeError(f"رفض الحذف: التغريدة لحساب @{author} وليست لـ @{expected}")

            focal.get_by_test_id("caret").first.click()
            item = page.locator("div[role='menu'] [role='menuitem']").filter(has_text=_DELETE_ITEM)
            try:
                item.first.wait_for(state="visible", timeout=10_000)
            except Exception:
                page.keyboard.press("Escape")
                raise RuntimeError("لا يوجد خيار «حذف» — التغريدة ليست من منشورات هذا الحساب")
            item.first.click()

            confirm = page.get_by_test_id("confirmationSheetConfirm")
            confirm.wait_for(state="visible", timeout=10_000)
            confirm.click()

            deadline = time.time() + 15
            verdict = None
            while time.time() < deadline and verdict is None:
                page.wait_for_timeout(300)
                verdict = _delete_confirmed(delete_responses)
            if verdict is False:
                raise RuntimeError("رفض X طلب الحذف")
            if verdict is None:
                # لم نلتقط ردّ الشبكة: التحقق بإعادة فتح التغريدة
                _goto(page, tweet_url, wait_until="domcontentloaded")
                page.wait_for_timeout(6000)
                _captcha_guard(page, log=print)
                if page.locator(focal_sel).count():
                    raise RuntimeError("لم يتأكد الحذف: التغريدة ما زالت ظاهرة")
            page.wait_for_timeout(min(wait_after_ms, 1500))
            return True

        except RuntimeError:
            raise
        except Exception as e:
            try:
                page.screenshot(path="debug_delete.png", full_page=True)
            except Exception:
                pass
            raise RuntimeError(f"فشل حذف التغريدة: {e}")
        finally:
            context.close()
            browser.close()
