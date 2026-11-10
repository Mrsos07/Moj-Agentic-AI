import re
import time
from typing import Dict, List, Optional, Tuple

from .x_captcha import guard as _captcha_guard
from .x_captcha import goto as _goto
from .x_captcha import launch_browser as _launch_browser
from .x_captcha import new_context as _captcha_context
from .x_captcha import sync_playwright
from .sync_bridge import sync_safe

# نقاط X التي تحفظ الصورة/الغلاف بعد «حفظ» (عميل الويب يرفع الوسائط ثم يستدعيها؛
# rettiwt-api يستخدم …/i/api/1.1/account/update_profile_banner.json نفسها).
_MEDIA_ENDPOINTS = (("avatar", "/account/update_profile_image"),
                    ("banner", "/account/update_profile_banner"))
_MEDIA_LABELS = {"avatar": "الصورة الشخصية", "banner": "صورة الغلاف"}
_CROP_WAIT_S = 8     # ظهور نافذة القص بعد اختيار الصورة
_SAVE_WAIT_S = 90    # رفع صورتين (~1.5MB لكل منهما) عبر بروكسي سكني يأخذ ثوانيَ عديدة
_CLOSED_GRACE_S = 20  # النموذج أُغلق ولا ردّ صريح بعد ⇒ لا ننتظر أكثر


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


def _click_visible(loc) -> bool:
    """ينقر أول عنصر ظاهر — إلا زر حفظ نموذج البروفايل (لا يُضغط إلا عمداً)."""
    try:
        for i in range(loc.count()):
            el = loc.nth(i)
            if el.is_visible() and el.get_attribute("data-testid") != "Profile_Save_Button":
                el.click(timeout=6000)
                return True
    except Exception:
        pass
    return False


def _handle_crop_if_any(page, wait_s: float = _CROP_WAIT_S) -> bool:
    """يعتمد نافذة قص الصورة. ينتظر ظهورها بدل محاولة واحدة بعد مهلة ثابتة: إن تأخرت
    كانت قائمة الأسماء تصل إلى «حفظ» فتضغط زر حفظ البروفايل نفسه قبل اعتماد الصورة."""
    names = ["Apply","Save","Done","Next","تطبيق","حفظ","تم","التالي","إنهاء","قص","تأكيد"]
    deadline = time.monotonic() + wait_s
    while True:
        for sel in ("[data-testid='applyButton']", "[data-testid='ocfApplyButton']"):
            if _click_visible(page.locator(sel)):
                page.wait_for_timeout(600)
                return True
        for n in names:
            if _click_visible(page.get_by_role("button", name=re.compile(rf"^{re.escape(n)}$", re.I))):
                page.wait_for_timeout(600)
                return True
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(400)


def _wait_saved(page, seen: Dict[str, List[int]], wanted: List[str]) -> None:
    """ينتظر اكتمال الحفظ قبل أي تنقّل: الانتقال بعد 2.5ث ثابتة كان يقطع رفع الصور
    الجاري (يتباطأ عبر البروكسي) — فيُحفظ الاسم والنبذة وتضيع الصورة بصمت."""
    page.wait_for_timeout(2500)
    deadline = time.monotonic() + _SAVE_WAIT_S
    closed_at = None
    while wanted and time.monotonic() < deadline and not all(seen.get(k) for k in wanted):
        try:
            still_open = page.get_by_test_id("Profile_Save_Button").first.is_visible()
        except Exception:
            still_open = False
        if still_open:
            closed_at = None
        elif closed_at is None:
            closed_at = time.monotonic()
        elif time.monotonic() - closed_at > _CLOSED_GRACE_S:
            return
        page.wait_for_timeout(500)


def _media_result(seen: Dict[str, List[int]], wanted: List[str]) -> Dict[str, str]:
    """confirmed = ردّ X بنجاح على حفظ الصورة؛ unconfirmed = لم يُلتقط ردّ (لا نزعم نجاحاً)؛
    ردّ خطأ ⇒ استثناء: صورة رفضها X لا تُبلَّغ كأنها طُبّقت."""
    out = {}
    for kind in wanted:
        codes = seen.get(kind) or []
        if any(200 <= c < 300 for c in codes):
            out[kind] = "confirmed"
        elif codes:
            raise RuntimeError(f"X رفض حفظ {_MEDIA_LABELS[kind]} (HTTP {codes[-1]}) — لم تُطبَّق.")
        else:
            out[kind] = "unconfirmed"
    return out


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


@sync_safe
def update_profile_on_x(
    storage_state_path: str,
    name: str = "",
    bio: str = "",
    location: str = "",
    website: str = "",
    avatar_path: Optional[str] = None,
    banner_path: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, str]:
    """يعيد تأكيد كل صورة طُلبت: ``{"avatar"|"banner": "confirmed"|"unconfirmed"}``."""
    # كبقية الوحدات (x_actions/x_follow/…): كروم المثبَّت افتراضياً — كروميوم
    # Playwright المرفق غير منزَّل هنا، وكان غيابه يُفشل التطبيق بصمت.
    with sync_playwright() as p:
        browser = _launch_browser(p, headless=headless)
        context = _captcha_context(browser, storage_state_path)
        page = context.new_page()

        # ردود X على حفظ الصورة/الغلاف — إلحاق فقط داخل المعالج (لا قراءة أجسام)
        seen: Dict[str, List[int]] = {}

        def _on_response(resp):
            for kind, frag in _MEDIA_ENDPOINTS:
                if frag in resp.url:
                    seen.setdefault(kind, []).append(resp.status)

        page.on("response", _on_response)

        _goto(page, "https://x.com/settings/profile", wait_until="domcontentloaded")
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
        wanted = [k for k, path in (("avatar", avatar_path), ("banner", banner_path)) if path]
        _wait_saved(page, seen, wanted)
        media = _media_result(seen, wanted)

        # تحقق فعلي: النقر على «حفظ» قد يمر بلا أثر (خطأ تحقق في X، حقل لم
        # يُعبّأ، جلسة منتهية) — أعد فتح النموذج واقرأ القيم المحفوظة فعلاً.
        # إبلاغ نجاح زائف أسوأ من فشل صريح: النظام يزامن البرسونا على أساسه.
        if name or bio:
            _goto(page, "https://x.com/settings/profile", wait_until="domcontentloaded")
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
        return media
