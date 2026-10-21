import os
import re
from typing import List, Optional, Tuple

from playwright.sync_api import sync_playwright

from .x_captcha import guard as _captcha_guard
from .x_captcha import new_context as _captcha_context
from .x_captcha import launch_proxy_kwargs as _launch_proxy_kwargs


def _file_inputs_in_edit_dialog(page):
    loc = page.locator("#layers input[type='file']")
    if loc.count() > 0:
        return loc
    return page.locator("input[type='file']")


def _try_click_by_patterns(page, patterns: List[str]) -> bool:
    for pat in patterns:
        try:
            btn = page.get_by_role("button", name=re.compile(pat, re.I))
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click(timeout=8000)
                return True
        except Exception:
            pass
    return False


def _try_click_banner_button(page) -> bool:
    patterns = [
        r"Add banner photo", r"Add header photo", r"Header photo", r"Banner",
        r"إضافة.*(صورة|صوره).*(غلاف|بانر|بنر|هيدر|رأس|عنوان)",
        r"(صورة|صوره).*(غلاف|بانر|بنر|هيدر|رأس|عنوان)"
    ]
    return _try_click_by_patterns(page, patterns)


def _try_click_avatar_button(page) -> bool:
    patterns = [
        r"Add avatar photo", r"Add profile photo", r"Profile photo", r"Avatar",
        r"إضافة.*(صورة|صوره).*(الملف|بروفايل|شخصية|شخصيه)",
        r"(صورة|صوره).*(الملف|بروفايل|شخصية|شخصيه)"
    ]
    return _try_click_by_patterns(page, patterns)


def _handle_crop_if_any(page):
    names = ["Apply","Save","Done","Next","تطبيق","حفظ","تم","التالي","إنهاء","قص","تأكيد"]
    for n in names:
        try:
            b = page.get_by_role("button", name=re.compile(rf"^{re.escape(n)}$", re.I))
            if b.count() > 0 and b.first.is_visible():
                b.first.click(timeout=6000)
                page.wait_for_timeout(600)
                return True
        except Exception:
            pass
    for sel in ["[data-testid='applyButton']", "[data-testid='ocfApplyButton']"]:
        try:
            el = page.locator(sel)
            if el.count() > 0 and el.first.is_visible():
                el.first.click(timeout=6000)
                page.wait_for_timeout(600)
                return True
        except Exception:
            pass
    return False


def _fill_first_match(page, candidates: List[Tuple[str, str]], value: str):
    for kind, sel in candidates:
        try:
            if kind == 'css':
                loc = page.locator(sel)
            else:
                loc = page.get_by_role("textbox", name=re.compile(sel, re.I))
            if loc.count() > 0:
                loc.first.fill(value, timeout=8000)
                return True
        except Exception:
            continue
    return False


def _ensure_logged_in_or_raise(page):
    page.wait_for_timeout(1000)
    _captcha_guard(page, log=print)
    for txt in ["Log in", "تسجيل الدخول", "Login"]:
        try:
            if page.get_by_role("link", name=re.compile(txt, re.I)).count() > 0:
                raise RuntimeError("لم يتم تسجيل الدخول بالكوكيز. استخدم storage_state صحيح.")
        except Exception:
            continue


def update_profile_on_x(
    storage_state_path: str,
    name: str = "",
    bio: str = "",
    location: str = "",
    website: str = "",
    avatar_path: Optional[str] = None,
    banner_path: Optional[str] = None,
    headless: bool = True,
):
    # كبقية الوحدات (x_actions/x_follow/…): كروم المثبَّت افتراضياً — كروميوم
    # Playwright المرفق غير منزَّل هنا، وكان غيابه يُفشل التطبيق بصمت.
    chrome_channel = os.getenv('XSUITE_CHROME_CHANNEL', '').strip() or "chrome"
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel=chrome_channel, headless=headless,
                                            **_launch_proxy_kwargs())
        except Exception:
            browser = p.chromium.launch(headless=headless, **_launch_proxy_kwargs())
        context = _captcha_context(browser, storage_state_path)
        page = context.new_page()

        page.goto("https://x.com/settings/profile", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        _ensure_logged_in_or_raise(page)

        inputs = _file_inputs_in_edit_dialog(page)

        if banner_path:
            _try_click_banner_button(page)
            page.wait_for_timeout(400)
            if inputs.count() >= 1:
                inputs.nth(0).set_input_files(banner_path)
            else:
                page.locator("input[type='file']").first.set_input_files(banner_path)
            page.wait_for_timeout(1200)
            _handle_crop_if_any(page)

        if avatar_path:
            _try_click_avatar_button(page)
            page.wait_for_timeout(400)
            if inputs.count() >= 2:
                inputs.nth(1).set_input_files(avatar_path)
            elif inputs.count() == 1:
                inputs.first.set_input_files(avatar_path)
            else:
                page.locator("input[type='file']").first.set_input_files(avatar_path)
            page.wait_for_timeout(1200)
            _handle_crop_if_any(page)

        if name:
            filled = _fill_first_match(page, [
                ("css", "input[name='displayName']"),
                ("role", r"^Name\\b"),
                ("role", r"^الاسم\\b"),
            ], name)
            if not filled:
                raise RuntimeError("لم أجد حقل الاسم في نموذج تعديل البروفايل — لم يُحفظ شيء.")

        if bio:
            filled = False
            try:
                loc = page.locator("textarea[name='description'], textarea")
                if loc.count() > 0:
                    loc.first.fill(bio, timeout=8000)
                    filled = True
            except Exception:
                pass
            if not filled:
                filled = _fill_first_match(page, [("role", r"^Bio\\b"), ("role", r"^النبذة|^نبذة|^نبذه")], bio)
            if not filled:
                raise RuntimeError("لم أجد حقل النبذة في نموذج تعديل البروفايل — لم يُحفظ شيء.")

        if location:
            _fill_first_match(page, [
                ("css", "input[name='location']"),
                ("role", r"الموقع الجغرافي"),
                ("role", r"^Location\\b"),
            ], location)

        if website:
            _fill_first_match(page, [
                ("css", "input[name='url']"),
                ("role", r"الموقع الإلكتروني"),
                ("role", r"(Website|URL|Link)\\b"),
            ], website)

        page.get_by_test_id("Profile_Save_Button").click(timeout=30_000)
        page.wait_for_timeout(2500)

        # تحقق فعلي: النقر على «حفظ» قد يمر بلا أثر (خطأ تحقق في X، حقل لم
        # يُعبّأ، جلسة منتهية) — أعد فتح النموذج واقرأ القيم المحفوظة فعلاً.
        # إبلاغ نجاح زائف أسوأ من فشل صريح: النظام يزامن البرسونا على أساسه.
        if name or bio:
            page.goto("https://x.com/settings/profile", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            _captcha_guard(page, log=print)
            problems = []
            if name:
                current = None
                try:
                    current = page.locator("input[name='displayName']").first.input_value(timeout=10_000)
                except Exception:
                    problems.append("تعذّر قراءة حقل الاسم للتحقق")
                if current is not None and current.strip() != name.strip():
                    problems.append(f"الاسم لم يُحفظ (الحالي: {current.strip()!r})")
            if bio:
                current = None
                try:
                    current = page.locator("textarea[name='description'], textarea").first.input_value(timeout=10_000)
                except Exception:
                    problems.append("تعذّر قراءة حقل النبذة للتحقق")
                if current is not None and current.strip() != bio.strip():
                    problems.append("النبذة لم تُحفظ")
            if problems:
                raise RuntimeError("الحفظ لم يكتمل: " + "؛ ".join(problems))

        context.close()
        browser.close()
