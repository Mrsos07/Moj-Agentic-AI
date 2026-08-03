import time
from typing import Optional
from pathlib import Path
from playwright.sync_api import sync_playwright


# =================
# Tunables
# =================
# مهم: يجب أن تبقى مهلة الميديا أقل من مهلة العميل (x_bridge.API_TIMEOUT=180s)
# وإلا يبقى السيرفر يعمل بعد أن يكون العميل قد استسلم، فتُطلَق محاولة نص-فقط
# متزامنة تُشغّل متصفحاً ثانياً وتضاعف التجمّد. نسقفها عند 90 ثانية لكل انتظار
# (كافية جداً للصور، ومعقولة للفيديو القصير ضمن سقف 180 ثانية للعميل).
MEDIA_TIMEOUT_MS = 90_000
POST_DONE_TIMEOUT_MS = 60_000
DEFAULT_TIMEOUT = 30_000

POST_CLICK_DELAY_MS = 5000        # 5 ثواني بعد الضغط كما طلبت


def _get_scope(page):
    dlg = page.locator("div[role='dialog']")
    return dlg.first if dlg.count() else page


def _is_video_file(media_path: str) -> bool:
    ext = (Path(media_path).suffix or "").lower()
    return ext in {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def _pick_composer_file_input(page):
    scope = _get_scope(page)

    loc = scope.locator('input[type="file"][data-testid="fileInput"]')
    if loc.count():
        return loc.first

    loc = scope.locator('input[type="file"]')
    if loc.count():
        for i in range(min(loc.count(), 10)):
            item = loc.nth(i)
            try:
                if item.is_visible():
                    return item
            except Exception:
                pass
        return loc.first

    loc = page.locator('input[type="file"][data-testid="fileInput"]')
    if loc.count():
        return loc.first

    return page.locator('input[type="file"]').first


def _get_textbox(page):
    scope = _get_scope(page)

    tb = scope.locator('div[data-testid="tweetTextarea_0"][role="textbox"]')
    if tb.count():
        tb.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        return tb.first

    tb2 = scope.locator('div[data-testid="tweetTextarea_0"]')
    if tb2.count():
        tb2.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        return tb2.first

    # حاول على الصفحة كاملة إذا الـ scope لم يجد شيء
    tb3 = page.locator('div[data-testid="tweetTextarea_0"][role="textbox"]')
    if tb3.count():
        tb3.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        return tb3.first

    tb4 = page.locator('div[data-testid="tweetTextarea_0"]')
    if tb4.count():
        tb4.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        return tb4.first

    # فولباك أخير: حدد textbox بالاسم لتجنب strict mode violation
    tb5 = page.get_by_role("textbox", name="نص المنشور")
    if tb5.count():
        tb5.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        return tb5.first

    tb6 = page.get_by_role("textbox", name="Post text")
    if tb6.count():
        tb6.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        return tb6.first

    # آخر محاولة
    tb7 = page.locator('div[role="textbox"][contenteditable="true"]').first
    tb7.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
    return tb7


def _media_preview_visible(page) -> bool:
    scope = _get_scope(page)
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
    ]
    for sel in candidates:
        try:
            loc = scope.locator(sel)
            if loc.count() and loc.first.is_visible():
                return True
        except Exception:
            pass
    return False


def _media_uploading_indicator_visible(page) -> bool:
    scope = _get_scope(page)

    candidates = [
        # مؤشرات واضحة للميديا
        'div[data-testid="mediaUploadProgress"]',
        '#layers div[data-testid="mediaUploadProgress"]',

        # progressbar داخل منطقة attachments (أفضل من العام)
        'div[data-testid="attachments"] div[role="progressbar"]',
        'div[data-testid="mediaContainer"] div[role="progressbar"]',
        'div[data-testid="tweetPhotoContainer"] div[role="progressbar"]',
        'div[data-testid="videoPlayer"] div[role="progressbar"]',

        # نصوص/ARIA شائعة (عربي/انجليزي)
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


def _wait_media_uploaded_or_ready(page, timeout_ms: int = MEDIA_TIMEOUT_MS):
    """
    انتظار عام: ظهور الميديا + اختفاء مؤشرات الرفع (إن وجدت).
    """
    deadline = time.time() + (timeout_ms / 1000.0)
    scope = _get_scope(page)

    remove_btn = scope.locator(
        '#layers [aria-label="Remove"], #layers [aria-label="Remove media"], '
        '#layers [aria-label="إزالة"], #layers [aria-label="حذف"], '
        '#layers [data-testid*="remove"]'
    )

    saw_preview = False
    while time.time() < deadline:
        if _media_preview_visible(page) or (remove_btn.count() and remove_btn.first.is_visible()):
            saw_preview = True

        if not saw_preview:
            page.wait_for_timeout(250)
            continue

        if not _media_uploading_indicator_visible(page):
            page.wait_for_timeout(700)
            return True

        page.wait_for_timeout(400)

    raise TimeoutError("انتهت مهلة انتظار رفع/معالجة الميديا.")


def _wait_media_processed_video(page, timeout_ms: int = MEDIA_TIMEOUT_MS):
    """
    للفيديو تحديدًا: ننتظر “اختفاء” أي مؤشرات رفع/Processing لفترة مستقرة.
    لأن الفيديو أحيانًا يختفي المؤشر ثم يرجع.
    """
    deadline = time.time() + (timeout_ms / 1000.0)

    stable_needed = 3          # عدد مرات متتالية نرى فيها "لا يوجد مؤشر" قبل اعتباره جاهز
    stable_hits = 0

    while time.time() < deadline:
        if _media_uploading_indicator_visible(page):
            stable_hits = 0
            page.wait_for_timeout(600)
            continue

        # لا يوجد مؤشر => نزيد العد
        stable_hits += 1
        if stable_hits >= stable_needed:
            # تهدئة إضافية للفيديو
            page.wait_for_timeout(1200)
            return True

        page.wait_for_timeout(500)

    raise TimeoutError("انتهت مهلة انتظار اكتمال معالجة الفيديو.")


def _wait_publish_button_enabled(page, timeout_ms: int = MEDIA_TIMEOUT_MS):
    """
    أهم شرط عندك:
    انتظر حتى زر النشر يصبح متاحًا (enabled + aria-disabled != true)
    ثم ارجع فورًا.
    """
    deadline = time.time() + (timeout_ms / 1000.0)
    scope = _get_scope(page)

    while time.time() < deadline:
        btn = scope.locator('button[data-testid="tweetButtonInline"]')
        if not (btn.count() and btn.first.is_visible()):
            btn = scope.locator('button[data-testid="tweetButton"]')

        try:
            if btn.count() and btn.first.is_visible():
                aria = btn.first.get_attribute("aria-disabled")
                disabled = (aria == "true")
                # is_enabled غالبًا أدق
                try:
                    if btn.first.is_enabled() and not disabled:
                        return True
                except Exception:
                    if not disabled:
                        return True
        except Exception:
            pass

        page.wait_for_timeout(400)

    raise TimeoutError("زر النشر لم يصبح متاحًا ضمن المهلة.")


def _click_publish(page):
    scope = _get_scope(page)

    btn = scope.locator('button[data-testid="tweetButtonInline"]')
    if not (btn.count() and btn.first.is_visible()):
        btn = scope.locator('button[data-testid="tweetButton"]')

    btn.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
    btn.first.scroll_into_view_if_needed()

    try:
        btn.first.click(timeout=8_000)
        return
    except Exception:
        pass

    try:
        btn.first.click(timeout=8_000, force=True)
        return
    except Exception:
        pass

    box = btn.first.bounding_box()
    if box:
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def _submit_via_keyboard(page, timeout_ms: int = 15_000,
                         allow_status_url_fallback: bool = True) -> bool:
    """فولباك إرسال التغريدة باختصار X الرسمي (Ctrl/Cmd+Enter).

    يُستخدم عندما لا يُفعّل نقرُ زر النشر الإرسالَ في محرّر X الجديد
    (jetfuel/Lexical). نُعيد تركيز منطقة الكتابة ثم نُرسل بالاختصار، ونتحقق
    عبر ``_confirm_submitted`` (الذي يرفع استثناءً لو ظهر خطأ صريح من X).
    """
    try:
        tb = _get_textbox(page)
        tb.click()
    except Exception:
        pass

    # جرّب الاختصارين (Windows/Linux ثم macOS) — أيّهما نجح يُغلق المحرّر
    for combo in ("Control+Enter", "Meta+Enter"):
        try:
            page.keyboard.press(combo)
        except Exception:
            continue
        page.wait_for_timeout(1200)
        if _confirm_submitted(page, timeout_ms=timeout_ms // 2, saw_dialog_before=True,
                              allow_status_url_fallback=allow_status_url_fallback):
            return True
    return False


def _wait_post_done(page, timeout_ms: int = POST_DONE_TIMEOUT_MS):
    deadline = time.time() + (timeout_ms / 1000.0)
    dialog = page.locator("div[role='dialog']")
    toast = page.locator("div[role='status'], div[aria-live='polite'], div[aria-live='assertive']")

    while time.time() < deadline:
        try:
            if dialog.count() == 0:
                return True
            if dialog.first.is_visible() is False:
                return True
        except Exception:
            pass

        try:
            if toast.count() and toast.first.is_visible():
                return True
        except Exception:
            pass

        page.wait_for_timeout(300)

    return False


def _composer_dialog_open(page) -> bool:
    """هل ما زال محرّر النشر (المودال) مفتوحاً؟ إشارة قوية لعدم اكتمال النشر."""
    try:
        dlg = page.locator("div[role='dialog']")
        if not (dlg.count() and dlg.first.is_visible()):
            return False
        # تأكد أنه فعلاً محرّر نشر (يحوي منطقة كتابة التغريدة)
        ta = dlg.locator('div[data-testid="tweetTextarea_0"]')
        return bool(ta.count() and ta.first.is_visible())
    except Exception:
        return False


def _success_toast_visible(page) -> bool:
    """توست تأكيد إرسال المنشور (نصوص X المعتادة عربي/إنجليزي)."""
    try:
        toast = page.get_by_test_id("toast")
        if not (toast.count() and toast.first.is_visible()):
            return False
        txt = (toast.first.inner_text() or "").lower()
        markers = ["your post was sent", "your tweet was sent", "post was sent",
                   "sent", "تم إرسال", "تم النشر", "أُرسل", "ارسل"]
        return any(m in txt for m in markers)
    except Exception:
        return False


# علامات توست/تنبيه خطأ صريح من X (تكرار المحتوى/حد المعدّل/فشل عام) — عربي/إنجليزي
_ERROR_TOAST_MARKERS = [
    "already said that", "whoops", "something went wrong", "please try again",
    "try again", "over the daily limit", "you are over", "rate limit",
    "unable to", "couldn't", "could not", "failed to",
    "قلت هذا", "سبق أن قلت", "حدث خطأ", "حدث خلل", "حاول مرة", "حاول لاحقاً",
    "تعذّر", "تعذر", "تجاوزت", "الحد اليومي", "غير مسموح",
]


def _error_toast_text(page) -> str:
    """نص توست الخطأ الصريح من X إن ظهر (تكرار/حد/فشل) — وإلا سلسلة فارغة.

    نعتمد على توست X الرسمي فقط (get_by_test_id('toast')) لتفادي الإيجابيات
    الكاذبة الناتجة عن مسح نص المحرّر كاملاً (الذي يحوي نص التغريدة نفسه).
    """
    try:
        toast = page.get_by_test_id("toast")
        if not (toast.count() and toast.first.is_visible()):
            return ""
        txt = (toast.first.inner_text() or "").strip()
        low = txt.lower()
        # لا تعتبره خطأ إن كان في الحقيقة توست نجاح
        if any(m in low for m in ("was sent", "تم إرسال", "تم النشر", "أُرسل")):
            return ""
        if any(m in low for m in _ERROR_TOAST_MARKERS):
            return txt[:200]
    except Exception:
        pass
    return ""


def _confirm_submitted(page, timeout_ms: int = 20_000,
                       saw_dialog_before: Optional[bool] = None,
                       allow_status_url_fallback: bool = True) -> bool:
    """تأكيد فعلي أن التغريدة أُرسلت.

    نعتمد على إشارات موثوقة للمسار عبر مودال النشر:
      0) ظهور توست خطأ صريح ("You already said that" / "حدث خطأ") => رفض مؤكد،
         نرفع استثناءً واضحاً لتمييزه عن الفشل الكاذب.
      1) ظهور توست نجاح صريح ("Your post was sent" / "تم إرسال منشورك").
      2) انغلاق مودال المحرّر بعد أن كان مفتوحاً (اختفاء tweetTextarea_0 داخل الديالوج).

    مهم: نمرّر ``saw_dialog_before`` المُلتقطة *قبل* الضغط على زر النشر (حين يكون
    المحرّر مفتوحاً يقيناً)، لأن النشر السريع قد يُغلق المحرّر قبل أن نلتقط حالته
    داخل هذه الدالة، فينتج فشلٌ كاذب. إن لم تُمرَّر نلتقطها الآن (سلوك قديم).
    """
    deadline = time.time() + (timeout_ms / 1000.0)
    saw_dialog = _composer_dialog_open(page) if saw_dialog_before is None else bool(saw_dialog_before)

    while time.time() < deadline:
        # 0) خطأ صريح من X => رفض مؤكد (ليس فشلاً كاذباً)
        err = _error_toast_text(page)
        if err:
            raise RuntimeError(f"رفض X نشر التغريدة: {err}")
        # 1) توست نجاح صريح
        if _success_toast_visible(page):
            return True
        # 2) انغلق المحرّر بعد أن كان مفتوحاً => تم الإرسال
        if saw_dialog and not _composer_dialog_open(page):
            return True
        # حدّث حالة رؤية الديالوج (قد يتأخر ظهوره لحظة)
        if not saw_dialog:
            saw_dialog = _composer_dialog_open(page)
        page.wait_for_timeout(300)

    # ملاذ أخير: لو أصبحنا على صفحة تغريدة فعلية.
    # ممنوع لمسار الاقتباس/الرد: هما يبدآن أصلاً على صفحة /status/ فيصير هذا
    # الملاذ «نجاحاً كاذباً» دائماً (كان الاقتباس يُبلّغ بالنجاح بلا نشر فعلي).
    if allow_status_url_fallback:
        try:
            if "/status/" in page.url:
                return True
        except Exception:
            pass
    return False


def _copy_tweet_link(page, timeout_ms: int = 30_000) -> Optional[str]:
    """
    بعد النشر، اضغط على التغريدة من الـ toast ثم انسخ رابطها.
    يُرجع رابط التغريدة أو None إذا فشل.
    """
    import re
    try:
        print("[DEBUG] محاولة نسخ رابط التغريدة...")
        page.wait_for_timeout(2000)
        
        # الطريقة 1: ابحث عن أي رابط في الـ toast
        try:
            toast_link = page.get_by_test_id("toast").locator("a")
            if toast_link.is_visible(timeout=5000):
                print("[DEBUG] وجدت رابط في الـ toast")
                toast_link.click()
                page.wait_for_timeout(2500)
        except Exception as e:
            print(f"[DEBUG] لم أجد رابط في الـ toast: {e}")
        
        # ملاحظة: لا نضغط على أي تغريدة من التايملاين هنا إطلاقاً، لأن ذلك كان
        # يُعيد رابط تغريدة شخص آخر ويُنتج "نجاحاً كاذباً". نعتمد فقط على رابط
        # التوست (منشورنا) أو رابط الصفحة إن كان فعلاً صفحة تغريدة.

        # الآن نحاول نسخ الرابط
        if "/status/" in page.url:
            print(f"[DEBUG] وصلت لصفحة التغريدة: {page.url}")
            # الطريقة A: زر المشاركة القديم
            try:
                share_btn = page.get_by_role("button", name="Share post")
                if share_btn.is_visible(timeout=5000):
                    print("[DEBUG] وجدت زر Share post")
                    share_btn.click()
                    page.wait_for_timeout(1000)
                    
                    # ابحث عن Copy link
                    copy_link = page.locator("div").filter(has_text=re.compile(r"Copy link", re.IGNORECASE)).first
                    if copy_link.is_visible(timeout=5000):
                        print("[DEBUG] وجدت Copy link")
                        copy_link.click()
                        page.wait_for_timeout(500)
                        return page.url
            except Exception as e:
                print(f"[DEBUG] طريقة Share post فشلت: {e}")
            
            # الطريقة B: زر المشاركة الجديد (ثلاث نقاط)
            try:
                more_btn = page.locator('[data-testid="caret"]').first
                if more_btn.is_visible(timeout=5000):
                    print("[DEBUG] وجدت زر المزيد (ثلاث نقاط)")
                    more_btn.click()
                    page.wait_for_timeout(1000)
                    
                    # ابحث عن Copy link في القائمة
                    copy_link = page.locator("div").filter(has_text=re.compile(r"Copy link", re.IGNORECASE)).first
                    if copy_link.is_visible(timeout=5000):
                        print("[DEBUG] وجدت Copy link في القائمة")
                        copy_link.click()
                        page.wait_for_timeout(500)
                        return page.url
            except Exception as e:
                print(f"[DEBUG] طريقة المزيد فشلت: {e}")
            
            # إذا فشل كل شيء، أرجع الرابط الحالي
            print("[DEBUG] أرجع الرابط الحالي كحل أخير")
            return page.url
        
        # الحل الأخير: تحقق إذا الرابط الحالي هو رابط تغريدة
        current_url = page.url
        if "/status/" in current_url:
            print(f"[DEBUG] الرابط الحالي هو رابط تغريدة: {current_url}")
            return current_url
        
        print("[DEBUG] لم أصل لصفحة التغريدة")
        return None
    except Exception as e:
        print(f"[DEBUG] خطأ عام في نسخ الرابط: {e}")
        return None


def _thread_textbox(page, index: int):
    """منطقة كتابة التغريدة رقم ``index`` داخل محرّر الثريد (tweetTextarea_N)."""
    scope = _get_scope(page)
    for base in (scope, page):
        loc = base.locator(f'div[data-testid="tweetTextarea_{index}"]')
        if loc.count():
            loc.first.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
            return loc.first
    raise RuntimeError(f"لم أجد منطقة كتابة التغريدة رقم {index} في محرّر الثريد.")


def _click_add_another_post(page) -> None:
    """اضغط زر «+» لإضافة تغريدة أخرى إلى الثريد داخل نفس المحرّر.

    X يعرض الزر بـ ``data-testid="addButton"``؛ ولو تغيّر نلجأ إلى الاسم
    المُتاح للوصول (عربي/إنجليزي).
    """
    scope = _get_scope(page)
    candidates = [
        scope.locator('button[data-testid="addButton"]'),
        page.locator('button[data-testid="addButton"]'),
        scope.locator('div[data-testid="addButton"]'),
        page.get_by_role("button", name="إضافة منشور"),
        page.get_by_role("button", name="Add post"),
    ]
    for loc in candidates:
        try:
            if loc.count() and loc.first.is_visible():
                loc.first.scroll_into_view_if_needed()
                loc.first.click(timeout=8_000)
                page.wait_for_timeout(600)
                return
        except Exception:
            continue
    raise RuntimeError("لم أجد زر إضافة تغريدة إلى الثريد (addButton).")


def post_thread_to_x(storage_state_path: str, texts: list, media_path: Optional[str],
                     headless: bool) -> Optional[str]:
    """انشر ثريد (سلسلة تغريدات) في جلسة متصفح واحدة — نشرٌ ذرّي كما يفعل X.

    يكتب التغريدة الأولى، ثم يضغط «+» لكل تغريدة تالية ويكتبها في
    ``tweetTextarea_N``، ثم ينشر السلسلة كاملة بضغطة واحدة («انشر الكل»).
    الميديا — إن وُجدت — تُرفق بالتغريدة الأولى (رأس الثريد).
    يعيد رابط التغريدة الأولى إن أمكن.

    مزية هذا الأسلوب على «نشر ثم ردود متتابعة»: متصفح واحد فقط بدل متصفح لكل
    تغريدة، ولا يمكن أن ينكسر الثريد في منتصفه.
    """
    parts = [str(t or "").strip() for t in (texts or [])]
    parts = [t for t in parts if t]
    if not parts:
        raise ValueError("texts required for a thread")
    if len(parts) == 1:
        return post_to_x(storage_state_path, parts[0], media_path, headless)

    with sync_playwright() as p:
        launch_args = {"channel": "chrome", "headless": headless}
        if headless:
            launch_args["args"] = [
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--disable-web-security", "--disable-features=VizDisplayCompositor",
                "--window-size=1920,1080",
            ]
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            storage_state=storage_state_path,
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)

        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            page.get_by_test_id("SideNav_NewTweet_Button").click(timeout=DEFAULT_TIMEOUT)
            page.wait_for_timeout(900)

            # التغريدة الأولى (رأس الثريد) + الميديا
            first = _get_textbox(page)
            first.click()
            try:
                first.fill(parts[0])
            except Exception:
                first.press("Control+A")
                first.type(parts[0], delay=10)

            if media_path and str(media_path).strip():
                file_input = _pick_composer_file_input(page)
                file_input.wait_for(state="attached", timeout=DEFAULT_TIMEOUT)
                file_input.set_input_files(media_path)
                _wait_media_uploaded_or_ready(page, timeout_ms=MEDIA_TIMEOUT_MS)
                if _is_video_file(media_path):
                    _wait_media_processed_video(page, timeout_ms=MEDIA_TIMEOUT_MS)

            # بقية التغريدات: زر «+» ثم الكتابة في المنطقة التالية
            for idx, part in enumerate(parts[1:], start=1):
                _click_add_another_post(page)
                box = _thread_textbox(page, idx)
                box.click()
                try:
                    box.fill(part)
                except Exception:
                    box.type(part, delay=10)
                page.wait_for_timeout(300)

            _wait_publish_button_enabled(page, timeout_ms=MEDIA_TIMEOUT_MS)
            composer_open_before = _composer_dialog_open(page)

            _click_publish(page)
            page.wait_for_timeout(2000)
            sent = _confirm_submitted(page, timeout_ms=12_000,
                                      saw_dialog_before=composer_open_before)
            if not sent:
                sent = _submit_via_keyboard(page, timeout_ms=15_000)
            if not sent:
                try:
                    page.screenshot(path="debug_thread.png", full_page=True)
                except Exception:
                    pass
                raise RuntimeError(
                    "لم يتم تأكيد نشر الثريد: بقي المحرّر مفتوحاً ولم يظهر تأكيد الإرسال."
                )

            try:
                return _copy_tweet_link(page, timeout_ms=30_000)
            except Exception:
                return None
        except Exception:
            try:
                page.screenshot(path="debug_thread.png", full_page=True)
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()


def post_to_x(storage_state_path: str, text: str, media_path: Optional[str], headless: bool) -> Optional[str]:
    with sync_playwright() as p:
        # إعدادات أفضل للـ headless
        launch_args = {
            "channel": "chrome",
            "headless": headless,
        }
        
        # في وضع headless، أضف إعدادات إضافية لتجنب المشاكل
        if headless:
            launch_args.update({
                "args": [
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-web-security",
                    "--disable-features=VizDisplayCompositor",
                    "--window-size=1920,1080"
                ]
            })
        
        browser = p.chromium.launch(**launch_args)
        
        # إعدادات السياق مع viewport مناسب
        context = browser.new_context(
            storage_state=storage_state_path,
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)

        try:
            page.goto("https://x.com/home", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            page.get_by_test_id("SideNav_NewTweet_Button").click(timeout=DEFAULT_TIMEOUT)
            page.wait_for_timeout(900)

            textbox = _get_textbox(page)
            textbox.click()

            # اكتب النص أولاً
            try:
                textbox.fill(text)
            except Exception:
                textbox.click()
                textbox.press("Control+A")
                textbox.type(text, delay=10)

            # لو فيه ميديا
            if media_path and str(media_path).strip():
                file_input = _pick_composer_file_input(page)
                file_input.wait_for(state="attached", timeout=DEFAULT_TIMEOUT)
                file_input.set_input_files(media_path)

                # 1) انتظر ظهور الميديا بشكل عام
                _wait_media_uploaded_or_ready(page, timeout_ms=MEDIA_TIMEOUT_MS)

                # 2) إذا فيديو: انتظر معالجة “أثبت”
                if _is_video_file(media_path):
                    _wait_media_processed_video(page, timeout_ms=MEDIA_TIMEOUT_MS)

                # 3) الشرط الأهم: انتظر زر النشر يصبح متاح
                _wait_publish_button_enabled(page, timeout_ms=MEDIA_TIMEOUT_MS)

            # حتى في النشر النصّي فقط: تأكّد أن زر النشر أصبح مفعّلاً قبل الضغط،
            # وإلا فالضغط لا يُنشر شيئاً (كان سبباً للنجاح الكاذب).
            _wait_publish_button_enabled(page, timeout_ms=15_000)

            # التقط حالة المحرّر (مفتوح؟) *قبل* الضغط — يقيناً مفتوح الآن — كي لا
            # يُنتج النشرُ السريع فشلاً كاذباً لو أُغلق المحرّر قبل التقاط حالته.
            composer_open_before = _composer_dialog_open(page)

            # انشر ثم تأكّد. إن لم يُرسَل بنقر الزر (سلوك معروف في محرّر X الجديد
            # jetfuel/Lexical حيث لا يُفعّل النقرُ البرمجيُّ الإرسالَ)، نلجأ إلى
            # اختصار X الرسمي Ctrl+Enter لإرسال التغريدة، ثم نعيد التأكيد.
            _click_publish(page)
            page.wait_for_timeout(1500)

            sent = _confirm_submitted(page, timeout_ms=8_000,
                                      saw_dialog_before=composer_open_before)
            if not sent:
                sent = _submit_via_keyboard(page, timeout_ms=15_000)

            if not sent:
                try:
                    page.screenshot(path="debug_post.png", full_page=True)
                except Exception:
                    pass
                raise RuntimeError(
                    "لم يتم تأكيد نشر التغريدة: بقي المحرّر مفتوحاً ولم يظهر تأكيد الإرسال."
                )

            # نسخ رابط التغريدة (اختياري؛ لا يؤثر على تأكيد النشر أعلاه)
            try:
                tweet_url = _copy_tweet_link(page, timeout_ms=30_000)
            except Exception:
                tweet_url = None
            return tweet_url

        except Exception:
            try:
                page.screenshot(path="debug_post.png", full_page=True)
            except Exception:
                pass
            try:
                with open("debug_post.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()
    
    return None
