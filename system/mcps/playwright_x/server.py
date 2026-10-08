# خادم MCP مدمج في النظام — المصدر الوحيد لهذا الخادم (system/mcps).
# يحمّله app/aeos/mcp/catalog.py عند الإقلاع؛ المسار القديم في app/aeos/mcp/servers اسم بديل.
"""
Playwright X MCP server.

Logs into X **using the account's saved cookies** (Playwright ``storage_state``)
and browses the authenticated home timeline, returning the tweets it sees so the
employee can decide how to engage (like / reply / retweet) according to its
persona instructions.

This is a *native* Playwright MCP server: it reuses the cookie files already
produced by the cookie-upload flow (``app/x/cookies/<label>.json``, Playwright
format including the httpOnly ``auth_token``) and the browser-launch helper from
the existing X automation. Interaction tools (like/reply/retweet) are provided by
the separate ``x`` MCP server; this server focuses on *reading* the timeline.

Tools:
    - read_timeline(limit=10, list_type="home"): authenticated timeline read.
    - read_trends(limit=10): the account's own "Trending" tab (Explore).
    - search_tweets(query, limit=10): live search results for a query/trend.
    - read_top_posts(limit=12): the Creator "Inspiration → Top Posts" page
      (top-performing posts X surfaces for this account), so the employee can
      engage with what is trending among creators in its field.
    - read_user_profile(handle, limit=30): a target user's bio + original
      tweets + replies — the raw sample used by the persona-cloning system to
      imitate that account's style, topics and discussion manner.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.aeos.mcp.base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.playwright_x")


@contextmanager
def _browser_slot(timeout: float):
    """احجز المتصفح بانتظار **محدود** بدل الانتظار الأبدي.

    قفل المتصفح مشترك مع دورات الموظفين، وطور الرصد وحده يحجزه دقيقة أو أكثر.
    العمليات التفاعلية (كاستنساخ حساب) كانت تنتظر القفل بلا حدّ حتى تنتهي مهلة
    الطلب، فتظهر للمستخدم رسالة «انتهت المهلة» الغامضة. الآن نفشل سريعاً برسالة
    تشرح السبب ويمكن التصرّف بناءً عليها.
    """
    from app.services.x_bridge import browser_lock

    if not browser_lock.acquire(timeout=timeout):
        raise RuntimeError(
            "المتصفح مشغول الآن بدورة موظف ذكي (تصفّح/نشر). أعد المحاولة بعد "
            "دقيقة، أو أوقف الموظف مؤقتاً لتسريع العملية."
        )
    try:
        yield
    finally:
        browser_lock.release()

# نفس مجلد الكوكيز المستخدم في رفع الكوكيز وأتمتة X
_COOKIES_DIR = Path(__file__).resolve().parents[3] / "app" / "x" / "cookies"

# JS لاستخراج التغريدات الظاهرة على التايم لاين
_EXTRACT_JS = r"""
() => {
  const arts = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
  return arts.map(a => {
    const textEl = a.querySelector('div[data-testid="tweetText"]');
    const text = textEl ? textEl.innerText : '';
    let url = '';
    const links = a.querySelectorAll('a[href*="/status/"]');
    for (const l of links) {
      const href = l.getAttribute('href') || '';
      if (/\/status\/\d+/.test(href)) { url = l.href; break; }
    }
    let author = '', handle = '';
    const userEl = a.querySelector('div[data-testid="User-Name"]');
    if (userEl) {
      const parts = (userEl.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
      author = parts[0] || '';
      const h = parts.find(s => s.startsWith('@'));
      handle = h || '';
    }
    // استخرج صورة حقيقية من التغريدة (وسائط X الأصلية) إن وُجدت — نتجاهل
    // صور البروفايل والإيموجي، ونأخذ فقط وسائط المنشور pbs.twimg.com/media.
    let media = '';
    const photo = a.querySelector('div[data-testid="tweetPhoto"] img, img[src*="pbs.twimg.com/media"]');
    if (photo && photo.src) { media = photo.src; }
    // العدّادات والتوقيت — نفس محدّدات _MY_POSTS_JS المتحقَّق منها حيّاً. بدونها
    // لا يعرف الموظف أي تغريدة «مهمّة» (منتشرة/حديثة) فيُرتّب بالكلمات فقط.
    const timeEl = a.querySelector('time');
    const created_at = timeEl ? (timeEl.getAttribute('datetime') || '') : '';
    const group = a.querySelector('div[role="group"]');
    const group_label = group ? (group.getAttribute('aria-label') || '') : '';
    const counts_text = group ? (group.innerText || '').replace(/\n/g, ' ') : '';
    const per = {};
    for (const tid of ['reply', 'retweet', 'like']) {
      const b = a.querySelector(`button[data-testid="${tid}"], div[data-testid="${tid}"]`);
      if (b) { per[tid] = b.getAttribute('aria-label') || ''; }
    }
    const viewsLink = a.querySelector('a[href*="/analytics"]');
    if (viewsLink) { per['views'] = viewsLink.getAttribute('aria-label') || viewsLink.innerText || ''; }
    return { author, handle, text, url, media, created_at, group_label, counts_text, per_button: per };
  }).filter(t => t.url && t.text);
}
"""

# JS لاستخراج الترندات من تبويب Explore/Trending
# بطاقة الترند تحوي عادةً: رقم ترتيب + سطر تصنيف («Trending in …»/«رائج») +
# اسم الترند + عدد المنشورات، مع أسطر فاصلة تحمل النقطة «·» فقط. الكود يتخطّى
# الفواصل («·») ورقم الترتيب وعدد المنشورات وسطر التصنيف، ويشترط أن يحوي الاسم
# حرفاً/وسماً فعلياً (لا رموزاً)، ويتجاهل البطاقات المموّلة/الإعلانية.
_TRENDS_JS = r"""
() => {
  // طبّع: أبدل النقاط الفاصلة (·/•) بمسافة واطوِ المسافات
  const norm = s => (s || '').replace(/[·•\u00B7\u2022]/g, ' ').replace(/\s+/g, ' ').trim();
  // سطر فاصل: لا يحوي أي حرف/رقم/وسم (رموز فقط) — يُتخطّى
  const isSep = p => !/[\p{L}\p{N}#@]/u.test(p || '');
  // يحوي حرفاً أو وسماً/منشناً (اسم ترند فعلي)
  const hasWord = p => /[\p{L}#@]/u.test(p || '');
  // رقم ترتيب قصير (1..999) لاتيني/عربي
  const isRank = p => /^[\d\u0660-\u0669]{1,3}$/.test((p || '').replace(/[.,\s]/g, ''));
  // سطر عدد المنشورات: رقم + كلمة منشورات/تغريدات (أو اختصار)
  const isPosts = p => /\d/.test(p) && /(posts?|منشور|تغريد|ألف|آلاف|مليون|\bK\b|\bM\b)/i.test(p);
  // سطر تصنيف: رائج/Trending/يتداول/المتداول/LIVE — بلا اعتماد على «في/in» المجرّدة.
  // «المتداول في المملكة العربية السعودية» = ترويسة القسم (Trending in Saudi Arabia).
  // ملاحظة: نطابق «متداول» مجرّدةً (لا «المتداول» فقط) — بطاقة مثل
  // «الاقتصاد و الأعمال متداول» هي ترويسة قسم، وكانت تُقرأ كاسم ترند فتصبح
  // «موضوعاً» للمنشور بلا أي مضمون حقيقي.
  const isCategory = p => /(trending|رائج|الرائج|يتداول|متداوَل|متداول|الأكثر\s*تداول|\bLIVE\b|مباشر|trending\s*in|الترند\s*في)/i.test(p) && !/\d{3,}/.test(p);
  // بطاقة مموّلة/إعلانية — تُتجاهل بالكامل
  const isPromoted = t => /(Promoted|مُموّل|مموّل|ممول|إعلان|\bAd\b)/i.test(t || '');
  const out = [];
  document.querySelectorAll('[data-testid="trend"]').forEach(e => {
    const full = e.innerText || '';
    if (isPromoted(full)) { return; }
    const parts = full.split('\n').map(norm).filter(Boolean);
    let name = '', category = '', posts = '';
    for (const p of parts) {
      if (isSep(p)) { continue; }
      if (isRank(p)) { continue; }
      if (isPosts(p)) { if (!posts) posts = p; continue; }
      if (isCategory(p)) { if (!category) category = p; continue; }
      if (!name && hasWord(p)) { name = p; }
    }
    if (name && hasWord(name) && !isRank(name)) { out.push({ name, category, posts }); }
  });
  return out;
}
"""


# JS لاستخراج منشورات الحساب نفسه مع مؤشرات أدائها (ردود/إعادات/إعجابات/مشاهدات).
# X يضع العدّادات داخل ``div[role="group"]`` مع aria-label وصفي، كما يظهرها نصّاً
# بجانب كل أيقونة. نُعيد الاثنين خاماً ونحلّلهما في بايثون (أرقام عربية/K/M/ألف).
_MY_POSTS_JS = r"""
() => {
  const arts = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
  return arts.map(a => {
    const textEl = a.querySelector('div[data-testid="tweetText"]');
    const text = textEl ? textEl.innerText : '';
    let url = '';
    for (const l of a.querySelectorAll('a[href*="/status/"]')) {
      const href = l.getAttribute('href') || '';
      if (/\/status\/\d+/.test(href)) { url = l.href; break; }
    }
    let handle = '';
    const userEl = a.querySelector('div[data-testid="User-Name"]');
    if (userEl) {
      const parts = (userEl.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
      handle = parts.find(s => s.startsWith('@')) || '';
    }
    const timeEl = a.querySelector('time');
    const created_at = timeEl ? (timeEl.getAttribute('datetime') || '') : '';
    const group = a.querySelector('div[role="group"]');
    const group_label = group ? (group.getAttribute('aria-label') || '') : '';
    const counts_text = group ? (group.innerText || '').replace(/\n/g, ' ') : '';
    // aria-label لكل زر على حدة (فولباك دقيق لو غاب وصف المجموعة)
    const per = {};
    for (const tid of ['reply', 'retweet', 'like']) {
      const b = a.querySelector(`button[data-testid="${tid}"], div[data-testid="${tid}"]`);
      if (b) { per[tid] = b.getAttribute('aria-label') || ''; }
    }
    const viewsLink = a.querySelector('a[href*="/analytics"]');
    if (viewsLink) { per['views'] = viewsLink.getAttribute('aria-label') || viewsLink.innerText || ''; }
    const photo = a.querySelector('div[data-testid="tweetPhoto"] img, img[src*="pbs.twimg.com/media"]');
    return {
      handle, text, url, created_at, group_label, counts_text,
      per_button: per, has_media: !!photo,
      is_pinned: /(Pinned|مثبّت|مثبت)/i.test(a.innerText || ''),
    };
  }).filter(t => t.url);
}
"""

#: كلمات العدّادات في واجهة X (عربي/إنجليزي) → المفتاح الموحّد
_METRIC_WORDS = {
    "replies": "replies", "reply": "replies", "رد": "replies", "ردود": "replies",
    "ردًا": "replies", "ردا": "replies",
    "reposts": "reposts", "repost": "reposts", "retweets": "reposts",
    "retweet": "reposts", "إعادة": "reposts", "إعادات": "reposts",
    "تدوين": "reposts", "منشور": "reposts",
    "likes": "likes", "like": "likes", "إعجاب": "likes", "إعجابات": "likes",
    "views": "views", "view": "views", "مشاهدة": "views", "مشاهدات": "views",
    "bookmarks": "bookmarks", "bookmark": "bookmarks", "إشارة": "bookmarks",
    "إشارات": "bookmarks",
}


def _to_int(raw: str) -> int:
    """حوّل «1.2K» / «٣٤» / «12 ألف» / «1,203» إلى عدد صحيح."""
    s = (raw or "").strip()
    if not s:
        return 0
    # أرقام عربية-هندية → لاتينية
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = s.replace(",", "").replace("،", "").replace("\u200f", "").strip()
    mult = 1.0
    low = s.lower()
    for suffix, factor in (("k", 1_000), ("m", 1_000_000), ("ألف", 1_000),
                           ("آلاف", 1_000), ("مليون", 1_000_000)):
        if low.endswith(suffix):
            mult = factor
            s = s[: -len(suffix)].strip()
            break
    try:
        return int(float(s) * mult)
    except (TypeError, ValueError):
        return 0


def _parse_metrics(item: Dict[str, Any]) -> Dict[str, int]:
    """استخرج العدّادات من وصف مجموعة الأزرار ثم من كل زر على حدة."""
    import re

    out: Dict[str, int] = {}

    def _scan(text: str) -> None:
        # أنماط «12 likes» و «12 إعجاب» و «إعجاب 12»
        for num, word in re.findall(r"([\d٠-٩][\d٠-٩.,]*\s*(?:K|M|ألف|آلاف|مليون)?)\s*([^\W\d_]+)",
                                    text or "", re.UNICODE):
            key = _METRIC_WORDS.get(word.strip().lower())
            if key and key not in out:
                out[key] = _to_int(num)
        for word, num in re.findall(r"([^\W\d_]+)\s*([\d٠-٩][\d٠-٩.,]*\s*(?:K|M|ألف|آلاف|مليون)?)",
                                    text or "", re.UNICODE):
            key = _METRIC_WORDS.get(word.strip().lower())
            if key and key not in out:
                out[key] = _to_int(num)

    _scan(item.get("group_label") or "")
    for label in (item.get("per_button") or {}).values():
        _scan(label or "")
    _scan(item.get("counts_text") or "")
    for key in ("replies", "reposts", "likes", "views", "bookmarks"):
        out.setdefault(key, 0)
    return out


def _with_metrics(item: Dict[str, Any]) -> Dict[str, Any]:
    """حوّل الحقول الخام (aria-label/نص العدّادات) إلى أرقام وأزلها من العنصر."""
    t = dict(item)
    raw = {k: t.pop(k, None) for k in ("group_label", "counts_text", "per_button")}
    if any(raw.values()):
        t.update(_parse_metrics(raw))
    return t


class PlaywrightXMCP(IMCPServer):
    """Authenticated (cookie-based) X timeline reader via Playwright."""

    name = "playwright_x"

    _TOOLS = ["read_timeline", "read_trends", "search_tweets", "read_top_posts",
              "read_notifications", "read_user_profile", "read_my_posts"]

    #: Mentions tab — the replies/mentions directed at THIS account
    _MENTIONS_URL = "https://x.com/notifications/mentions"

    #: Creator inspiration "Top Posts" page (top-performing posts for this account)
    _TOP_POSTS_URL = "https://x.com/i/jf/creators/inspiration/top_posts"

    @property
    def tools(self) -> List[str]:
        return list(self._TOOLS)

    def health(self) -> bool:
        try:
            import playwright  # noqa: F401
            return _COOKIES_DIR.exists()
        except Exception:  # noqa: BLE001
            return False

    def _cookie_file(self, label: str) -> Path:
        try:
            from app.x.modules.utils import safe_label
            name = safe_label(label)
        except Exception:  # noqa: BLE001
            name = (label or "").strip()
        return _COOKIES_DIR / f"{name}.json"

    def call(self, request: MCPRequest) -> MCPResponse:
        tool = request.tool
        p = request.params
        label = request.context.get("cookie_label") or p.get("cookie_label", "")

        if tool not in self._TOOLS:
            return MCPResponse.fail(f"unknown playwright_x tool '{tool}'")

        cookie_file = self._cookie_file(label)
        if not cookie_file.exists():
            return MCPResponse.fail(
                f"no cookies found for account '{label}' at {cookie_file.name} — upload cookies first"
            )

        limit = int(p.get("limit", 10))
        headless = bool(p.get("headless", True))

        try:
            if tool == "read_trends":
                trends = self._read_trends(str(cookie_file), limit=limit, headless=headless)
                return MCPResponse.ok(trends=trends, count=len(trends), account=label)

            if tool == "read_top_posts":
                tweets = self._read_top_posts(str(cookie_file), limit=limit, headless=headless)
                return MCPResponse.ok(tweets=tweets, count=len(tweets), account=label)

            if tool == "read_notifications":
                # Read the Mentions tab (replies/mentions to this account). Soft
                # wait: an account with zero mentions returns [] instead of
                # failing the whole cycle.
                tweets = self._read(
                    str(cookie_file), self._MENTIONS_URL,
                    limit=limit, headless=headless, soft=True,
                )
                return MCPResponse.ok(tweets=tweets, count=len(tweets), account=label)

            if tool == "read_my_posts":
                handle = (p.get("handle") or label or "").strip().lstrip("@")
                if not handle:
                    return MCPResponse.fail("handle is required for read_my_posts")
                posts = self._read_my_posts(
                    str(cookie_file), handle, limit=limit, headless=headless,
                )
                return MCPResponse.ok(posts=posts, count=len(posts), account=label,
                                      handle=f"@{handle}")

            if tool == "read_user_profile":
                handle = (p.get("handle") or "").strip().lstrip("@")
                if not handle:
                    return MCPResponse.fail("handle is required for read_user_profile")
                profile = self._read_user_profile(
                    str(cookie_file), handle, limit=limit, headless=headless,
                    lock_timeout=float(p.get("lock_timeout", 45.0)),
                    tab_budget_seconds=float(p.get("tab_budget_seconds", 18.0)),
                    replies_limit=int(p.get("replies_limit", 0) or 0),
                )
                return MCPResponse.ok(account=label, **profile)

            if tool == "search_tweets":
                query = (p.get("query") or "").strip()
                if not query:
                    return MCPResponse.fail("query is required for search_tweets")
                from urllib.parse import quote
                mode = p.get("mode", "top")  # top|live
                url = f"https://x.com/search?q={quote(query)}&src=trend_click"
                if mode == "live":
                    url += "&f=live"
                tweets = self._read(str(cookie_file), url, limit=limit, headless=headless)
                return MCPResponse.ok(tweets=tweets, count=len(tweets), account=label, query=query)

            # read_timeline
            list_type = p.get("list_type", "home")
            url = "https://x.com/home" if list_type == "home" else "https://x.com/notifications"
            tweets = self._read(str(cookie_file), url, limit=limit, headless=headless)
            return MCPResponse.ok(tweets=tweets, count=len(tweets), account=label)
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s failed for %s", tool, label)
            return MCPResponse.fail(f"{tool} failed: {exc}")

    # -- Playwright work (sync API, runs in a worker thread) --------------
    def _read(self, storage_state_path: str, url: str, limit: int, headless: bool,
              soft: bool = False) -> List[Dict[str, Any]]:
        from playwright.sync_api import sync_playwright

        from app.services.x_bridge import browser_lock
        from app.x.modules.x_actions import _launch_browser  # reuse chrome-channel launch

        seen: Dict[str, Dict[str, Any]] = {}
        # serialize with all other browser automation: sync playwright is not
        # safe to run concurrently across threads and would deadlock the fleet.
        with browser_lock, sync_playwright() as pw:
            browser = _launch_browser(pw, headless=headless)
            context = browser.new_context(
                storage_state=storage_state_path,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(60_000)
            try:
                page.goto(url, wait_until="domcontentloaded")
                # انتظر ظهور أول تغريدة (يؤكد نجاح الدخول بالكوكيز). في الوضع
                # الليّن (soft) لا نفشل الدورة إن لم توجد تغريدات (مثلاً لا توجد
                # منشنات) بل نرجع قائمة فارغة.
                try:
                    page.locator('article[data-testid="tweet"]').first.wait_for(
                        state="visible", timeout=(20_000 if soft else 45_000)
                    )
                except Exception:  # noqa: BLE001
                    if soft:
                        logger.info("no tweet cards at %s (soft read -> empty)", url)
                        return []
                    raise
                # مرّر لتحميل المزيد حتى نبلغ الحد المطلوب
                for _ in range(8):
                    try:
                        batch = page.evaluate(_EXTRACT_JS) or []
                    except Exception:  # noqa: BLE001
                        batch = []
                    for t in batch:
                        u = t.get("url")
                        if u and u not in seen:
                            seen[u] = _with_metrics(t)
                    if len(seen) >= limit:
                        break
                    page.mouse.wheel(0, 2400)
                    page.wait_for_timeout(1500)
            finally:
                context.close()
                browser.close()

        return list(seen.values())[:limit]

    def _read_top_posts(self, storage_state_path: str, limit: int, headless: bool) -> List[Dict[str, Any]]:
        """Read the Creator "Inspiration → Top Posts" page.

        The page renders standard tweet ``article`` cards, so we reuse the same
        extraction JS. It is tolerant: if the page needs a professional/creator
        account or doesn't surface posts, it returns an empty list instead of
        failing the whole cycle.
        """
        from playwright.sync_api import sync_playwright

        from app.services.x_bridge import browser_lock
        from app.x.modules.x_actions import _launch_browser

        seen: Dict[str, Dict[str, Any]] = {}
        with browser_lock, sync_playwright() as pw:
            browser = _launch_browser(pw, headless=headless)
            context = browser.new_context(
                storage_state=storage_state_path,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(60_000)
            try:
                page.goto(self._TOP_POSTS_URL, wait_until="domcontentloaded")
                # انتظار ليّن: إن لم تظهر بطاقات تغريدات خلال 20 ثانية نرجع فارغاً
                try:
                    page.locator('article[data-testid="tweet"]').first.wait_for(
                        state="visible", timeout=20_000
                    )
                except Exception:  # noqa: BLE001
                    logger.info("top_posts page has no tweet cards (account may lack creator tools)")
                    return []
                for _ in range(6):
                    try:
                        batch = page.evaluate(_EXTRACT_JS) or []
                    except Exception:  # noqa: BLE001
                        batch = []
                    for t in batch:
                        u = t.get("url")
                        if u and u not in seen:
                            seen[u] = _with_metrics(t)
                    if len(seen) >= limit:
                        break
                    page.mouse.wheel(0, 2400)
                    page.wait_for_timeout(1500)
            finally:
                context.close()
                browser.close()

        return list(seen.values())[:limit]

    def _read_my_posts(self, storage_state_path: str, handle: str,
                       limit: int, headless: bool) -> List[Dict[str, Any]]:
        """اقرأ منشورات الحساب نفسه مع مؤشرات أدائها الفعلية.

        هذه هي حلقة التغذية الراجعة التي كانت مفقودة تماماً: بدونها لا يعرف
        الموظف أيّ منشور نجح فعلاً. نقرأ صفحته الشخصية ونستخرج لكل منشور
        (ردود/إعادات نشر/إعجابات/مشاهدات) + وقت النشر، ثم يحسب المستوى الأعلى
        معدّل التفاعل ويتعلّم منه.
        """
        from playwright.sync_api import sync_playwright

        from app.services.x_bridge import browser_lock
        from app.x.modules.x_actions import _launch_browser

        from app.aeos.employee.strategy import engagement_score

        target = f"@{handle.lower()}"
        seen: Dict[str, Dict[str, Any]] = {}
        with browser_lock, sync_playwright() as pw:
            browser = _launch_browser(pw, headless=headless)
            context = browser.new_context(
                storage_state=storage_state_path,
                viewport={"width": 1280, "height": 1000},
            )
            page = context.new_page()
            page.set_default_timeout(60_000)
            try:
                page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded")
                try:
                    page.locator('article[data-testid="tweet"]').first.wait_for(
                        state="visible", timeout=25_000
                    )
                except Exception:  # noqa: BLE001
                    logger.info("no posts on profile of @%s", handle)
                    return []
                for _ in range(8):
                    try:
                        batch = page.evaluate(_MY_POSTS_JS) or []
                    except Exception:  # noqa: BLE001
                        batch = []
                    for item in batch:
                        url = item.get("url") or ""
                        # منشورات الحساب نفسه فقط (لا إعادات نشر لغيره)
                        if not url or f"/{handle.lower()}/status/" not in url.lower():
                            continue
                        if (item.get("handle") or "").lower() not in ("", target):
                            continue
                        if url in seen:
                            continue
                        metrics = _parse_metrics(item)
                        seen[url] = {
                            "url": url,
                            "text": (item.get("text") or "").strip(),
                            "created_at": item.get("created_at") or "",
                            "has_media": bool(item.get("has_media")),
                            "is_pinned": bool(item.get("is_pinned")),
                            **metrics,
                            "engagement": engagement_score(metrics),
                        }
                    if len(seen) >= limit:
                        break
                    page.mouse.wheel(0, 2400)
                    page.wait_for_timeout(1400)
            finally:
                context.close()
                browser.close()

        return list(seen.values())[:limit]

    def _read_user_profile(self, storage_state_path: str, handle: str,
                           limit: int, headless: bool,
                           lock_timeout: float = 45.0,
                           tab_budget_seconds: float = 18.0,
                           replies_limit: int = 0) -> Dict[str, Any]:
        """Scrape a target user's bio + tweets + replies (for persona cloning).

        Reads ``x.com/<handle>`` for original tweets and ``/with_replies`` for
        the reply/discussion style, in ONE browser session. Tweets not authored
        by the target (retweets/quoted context cards) are filtered out by
        matching the handle.
        """
        from playwright.sync_api import sync_playwright

        from app.services.x_bridge import browser_lock
        from app.x.modules.x_actions import _launch_browser

        bio_js = r"""
        () => {
          const pick = sel => { const e = document.querySelector(sel); return e ? e.innerText.trim() : ''; };
          return {
            display_name: pick('div[data-testid="UserName"] span'),
            bio: pick('div[data-testid="UserDescription"]'),
            followers: pick('a[href$="/verified_followers"] span, a[href$="/followers"] span'),
          };
        }
        """

        want = max(5, int(limit))
        target = f"@{handle.lower()}"
        profile: Dict[str, Any] = {"handle": f"@{handle}"}
        # ميزانية زمنية لكل تبويب: التمرير عشر مرات ثابتة كان يستهلك ~14 ثانية
        # لكل تبويب حتى لو توقّف وصول الجديد، فتتضخّم العملية بلا فائدة.
        budget = float(tab_budget_seconds)

        def _collect(page, url: str, cap: int, navigate: bool = True,
                     exclude: Optional[set] = None,
                     with_context: bool = False) -> List[Dict[str, Any]]:
            """اجمع منشورات الحساب الهدف من الصفحة الحالية بميزانية زمنية.

            ``exclude``: روابط معروفة مسبقاً (تغريدات الملف) — تُستثنى **ويُقاس
            التقدّم على الجديد فقط**، وإلا فأول شاشة من تبويب الردود تُكرّر
            تغريدات الملف فيبدو أنه «لا جديد» ويتوقف الجمع فوراً (كانت النتيجة
            ردّاً واحداً بينما أسلوب الردود نصف الشخصية المستنسخة).
            """
            skip = exclude or set()
            seen: Dict[str, Dict[str, Any]] = {}
            if navigate:
                page.goto(url, wait_until="domcontentloaded")
            try:
                page.locator('article[data-testid="tweet"]').first.wait_for(
                    state="visible", timeout=15_000
                )
            except Exception:  # noqa: BLE001
                logger.info("no tweet cards at %s (empty/protected profile?)", url)
                return []
            deadline = time.time() + budget
            idle_rounds = 0
            while time.time() < deadline:
                try:
                    batch = page.evaluate(_EXTRACT_JS) or []
                except Exception:  # noqa: BLE001
                    batch = []
                before = len(seen)
                prev = None
                for t in batch:
                    u = t.get("url")
                    # فقط ما كتبه الحساب الهدف نفسه (نستبعد بطاقات الاقتباس/الرتويت)
                    if (u and u not in seen and u not in skip
                            and (t.get("handle") or "").lower() == target):
                        item = _with_metrics(t)
                        # سياق الرد: في تبويب الردود تظهر التغريدة المردود عليها
                        # بطاقةً مباشرةً فوق الرد. بدونها لا يُعرف «كيف» يرد الحساب
                        # (على اتفاق؟ هجوم؟ سؤال؟) وهذا نصف الشخصية.
                        if with_context and prev and prev.get("text"):
                            item["in_reply_to"] = {
                                "handle": prev.get("handle", ""),
                                "text": (prev.get("text") or "")[:400],
                                "self": (prev.get("handle") or "").lower() == target,
                            }
                        seen[u] = item
                    prev = t
                if len(seen) >= cap:
                    break
                # توقّف مبكر: ثلاث تمريرات بلا أي جديد = نهاية المحتوى المتاح
                idle_rounds = idle_rounds + 1 if len(seen) == before else 0
                if idle_rounds >= 3:
                    break
                page.mouse.wheel(0, 2600)
                page.wait_for_timeout(900)
            return list(seen.values())[:cap]

        started = time.time()
        with _browser_slot(lock_timeout), sync_playwright() as pw:
            browser = _launch_browser(pw, headless=headless)
            context = browser.new_context(
                storage_state=storage_state_path,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(30_000)
            try:
                # 1) الصفحة الرئيسية: البايو + التغريدات الأصلية (تحميل واحد فقط —
                #    كان الكود يعيد الانتقال إلى نفس الرابط مرتين بلا داعٍ)
                page.goto(f"https://x.com/{handle}", wait_until="domcontentloaded")
                try:
                    page.locator('div[data-testid="UserName"]').first.wait_for(
                        state="visible", timeout=20_000
                    )
                    profile.update(page.evaluate(bio_js) or {})
                except Exception:  # noqa: BLE001
                    logger.info("profile header not found for @%s", handle)
                profile["tweets"] = _collect(
                    page, f"https://x.com/{handle}", want, navigate=False)
                # 2) تبويب الردود: أسلوب النقاش والرد — عيّنة أصغر تكفي للأسلوب،
                #    وأي خطأ فيها لا يُسقط العملية (لدينا التغريدات أصلاً)
                try:
                    # الردود = ما ليس في تغريدات الملف؛ نستثنيها من الجمع نفسه
                    tweet_urls = {t["url"] for t in profile.get("tweets", [])}
                    profile["replies"] = _collect(
                        page, f"https://x.com/{handle}/with_replies",
                        int(replies_limit or max(6, want // 2)), exclude=tweet_urls,
                        with_context=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.info("replies tab failed for @%s: %s", handle, exc)
                    profile["replies"] = []
            finally:
                context.close()
                browser.close()
        profile["scrape_seconds"] = round(time.time() - started, 1)

        profile["tweet_count"] = len(profile.get("tweets", []))
        profile["reply_count"] = len(profile.get("replies", []))
        return profile

    def _read_trends(self, storage_state_path: str, limit: int, headless: bool) -> List[Dict[str, Any]]:
        """Read the account's own Trending tab (what X shows THIS account)."""
        from playwright.sync_api import sync_playwright

        from app.services.x_bridge import browser_lock
        from app.x.modules.x_actions import _launch_browser

        with browser_lock, sync_playwright() as pw:
            browser = _launch_browser(pw, headless=headless)
            context = browser.new_context(
                storage_state=storage_state_path,
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            page.set_default_timeout(60_000)
            try:
                page.goto("https://x.com/explore/tabs/trending", wait_until="domcontentloaded")
                page.locator('[data-testid="trend"]').first.wait_for(
                    state="visible", timeout=45_000
                )
                page.wait_for_timeout(1200)
                trends = page.evaluate(_TRENDS_JS) or []
            finally:
                context.close()
                browser.close()

        return trends[:limit]
