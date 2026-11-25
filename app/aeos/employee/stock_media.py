"""
ميديا المنشور من Pixabay (مهارة ``stock_media``): عند قرار النشر عن موضوع يبحث الموظف عن صورة أو
فيديو **يطابق الموضوع فعلاً** ويرفقه بالمنشور.

الخطوات (نداءا نموذج صغيران كحد أقصى):
1. **التخطيط**: من نص المنشور يصوغ النموذج كلمتَي بحث إنجليزيتين حرفيتين (Pixabay يفهم الإنجليزية
   أدق)، ويقرّر صورة أم فيديو، وما يجب تجنّبه (أعلام دول أخرى، رموز دينية، شخصيات بعينها…).
2. **البحث**: ``pixabay.search_images`` / ``search_videos`` (أفقي، آمن، مخزّن 24 ساعة).
   الفيديو: ≤ 60 ثانية وحجم ≤ 40MB (حدود X لغير المشتركين 140 ثانية/512MB، والرفع عبر البروكسي بطيء).
3. **الاختيار**: النموذج يرى وسوم أفضل المرشحين ويختار الأنسب أو «لا شيء» — صورة مخزون مضلِّلة
   أسوأ من لا صورة (يُترك القرار عندها لتوليد صورة أصلية).
4. لا يُكرَّر نفس الملف للموظف نفسه خلال 30 يوماً.

السياسة لكل موظف ``business_rules.stock_media``: ``auto`` (افتراضي — فقط إن لم توجد ميديا حقيقية
من المصدر)، ``prefer`` (تتقدّم على ميديا المصدر)، ``off``. والنوع ``stock_media_kind``:
``auto`` | ``image`` | ``video``. المطوّر يطفئ المهارة كلها من اللوحة (system skill ``stock_media``).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:  # pragma: no cover
    from ..cognition.context import ExecutionContext

logger = logging.getLogger("aeos.stock_media")

SKILL_SLUG = "stock_media"
_USED_PATH = Path(__file__).resolve().parents[3] / "data" / "stock_media_used.json"
_REUSE_DAYS = 30
_MAX_VIDEO_SECONDS = 60
_MAX_VIDEO_BYTES = 40 * 1024 * 1024
_lock = threading.Lock()


def enabled(config) -> bool:
    try:
        from app.aeos.skills import system_skills

        if not system_skills.is_enabled(SKILL_SLUG):
            return False
    except Exception:  # noqa: BLE001
        pass
    return policy(config) != "off"


def policy(config) -> str:
    raw = str((getattr(config, "business_rules", None) or {}).get("stock_media", "auto")).strip().lower()
    return "off" if raw in ("off", "0", "false", "no") else ("prefer" if raw == "prefer" else "auto")


def available(context: "ExecutionContext") -> bool:
    if not (context.mcp.has("pixabay") and enabled(context.config)):
        return False
    from app.aeos.mcp.catalog import mcp_module

    try:
        return bool(mcp_module("pixabay", "server.py").api_key())
    except Exception:  # noqa: BLE001
        return False


def _used() -> Dict[str, Dict[str, float]]:
    try:
        return json.loads(_USED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _mark_used(employee_id: str, media_id: str) -> None:
    with _lock:
        data = _used()
        cutoff = time.time() - _REUSE_DAYS * 86400
        mine = {k: v for k, v in (data.get(employee_id) or {}).items() if v > cutoff}
        mine[media_id] = time.time()
        data[employee_id] = mine
        _USED_PATH.parent.mkdir(parents=True, exist_ok=True)
        _USED_PATH.write_text(json.dumps(data), encoding="utf-8")


def _plan(context: "ExecutionContext", text: str, topic: str) -> Optional[Dict[str, Any]]:
    llm = getattr(context, "llm", None)
    if llm is None or not getattr(llm, "available", False):
        return None
    want = str((context.config.business_rules or {}).get("stock_media_kind", "auto")).lower()
    out = llm.complete_json(
        system=('أنت محرر صور لحساب أخبار على X. أعد JSON فقط: {"queries":["english words","..."],'
                '"kind":"image|video","avoid":["..."],"skip":false}'),
        prompt=(f"المنشور:\n{text[:600]}\n" + (f"الموضوع: {topic[:200]}\n" if topic else "") +
                "\nصِغ كلمتَي بحث إنجليزيتين قصيرتين (2-4 كلمات) تصفان **مشهداً حرفياً** يناسب المنشور في "
                "مكتبة صور مخزون (مثل: air defense missile night, oil refinery desert). صِف **الشيء المادي** "
                "نفسه لا المفهوم: لا charts ولا graphs ولا concept (خبر أسعار النفط ← oil pump jack, oil "
                "tanker — قيس: «oil prices chart» أعاد رسوم فوركس رفضها فحص الرؤية). لا أسماء أشخاص ولا "
                "رموز سياسية. صِغ الكلمات بحيث تصلح لصورة **ولفيديو** معاً (نبحث في الاثنين). "
                "kind=video إن كان للموضوع مشهد حركي يُصوَّر (طائرات، سفن، صواريخ، حرائق، حشود، طقس، "
                "مركبات، منشآت تعمل، رياضة)، وإلا image"
                + (f" (المالك يفضّل: {want})" if want in ("image", "video") else "") +
                ". avoid: ما يجعل صورة مخزون مضلِّلة هنا (أعلام دول أخرى، رموز دينية، أشخاص بعينهم). "
                "skip=true ما لم يكن المنشور عن **شيء ملموس يمكن تصويره** (حدث، مكان، سلعة، آلة، منشأة): "
                "الرأي والنصيحة والتحليل المجرد ← skip (قيس: رأي عن «الحوار الهادئ» أُرفقت له سبورة طباشير)."),
        temperature=0.2) or {}
    if not isinstance(out, dict) or out.get("skip"):
        return None
    queries = [re.sub(r"[^A-Za-z0-9 \-]", " ", str(q)).strip()[:60] for q in out.get("queries") or []]
    kind = want if want in ("image", "video") else ("video" if out.get("kind") == "video" else "image")
    return {"queries": [q for q in queries if q][:2], "kind": kind,
            "want": want if want in ("image", "video") else "auto",
            "avoid": [str(a)[:40] for a in out.get("avoid") or []][:6]}


def _search(context: "ExecutionContext", query: str, kind: str) -> List[Dict[str, Any]]:
    from ..mcp.base import MCPRequest

    tool = "search_videos" if kind == "video" else "search_images"
    params = {"q": query, "per_page": 12, "safesearch": "true", "order": "popular"}
    params.update({"video_type": "film"} if kind == "video" else {"image_type": "photo", "orientation": "horizontal"})
    r = context.mcp.call(MCPRequest(employee_id=context.config.id, tool=tool, params=params,
                                    context={"server": "pixabay"}))
    if not r.success:
        logger.info("pixabay search failed: %s", r.error)
        return []
    items = r.data.get("results") or []
    if kind == "video":
        items = [i for i in items if (i.get("duration") or 999) <= _MAX_VIDEO_SECONDS
                 and (i.get("size") or 0) <= _MAX_VIDEO_BYTES and i.get("url")]
    else:
        items = [i for i in items if i.get("url") and (i.get("width") or 0) >= 1000]
    return items


# صور مخزون خيالية/مفاهيمية تُضلّل في منشور خبري. قيس 2026-11-09 على Pixabay الحقيقي: خبر اعتراض
# مسيّرة حوثية اختير له «ufo, intercept, science fiction» لأن الوسم «intercept» طابق.
_FAKE_TAGS = re.compile(r"(?i)\b(ufo|alien|fantasy|science fiction|sci-fi|scifi|cartoon|anime|comic|"
                        r"illustration|3d|render|rendering|cgi|vector|clipart|drawing|toy|lego|miniature|"
                        r"halloween|christmas|zombie|video game|gaming|chalk|blackboard|meme|emoji)\b")
_MIN_SCORE = 7
#: أفضلية الفيديو في الترتيب: يفوز عند التعادل أو الفارق الصغير (المالك يريد فيديو لا صوراً فقط)،
#: وصورة أوضح مطابقةً بنقطة كاملة تبقى أولى — فيديو بعيد عن الموضوع أسوأ من صورة دقيقة
_VIDEO_BONUS = 1.0


def _choose(context: "ExecutionContext", text: str, cands: List[Dict[str, Any]],
            avoid: List[str], video_bonus: float = 0.0) -> List[Dict[str, Any]]:
    """المرشّحون المقبولون مرتّبين (درجة ≥ 7 من 10)، بعد إسقاط الوسوم الخيالية/المفاهيمية حتمياً.
    ``video_bonus`` يرجّح الفيديو في الترتيب فقط — حدّ القبول على الدرجة الخام."""
    cands = [c for c in cands if not _FAKE_TAGS.search(c.get("tags") or "")][:12]
    if not cands:
        return []
    llm = getattr(context, "llm", None)
    listing = "\n".join(f"{i + 1}. [{c['kind']}] وسوم: {c.get('tags', '')}" for i, c in enumerate(cands))
    try:
        out = llm.complete_json(
            system='أعد JSON فقط: {"scores":[{"n":1,"score":0-10}]}',
            prompt=(f"المنشور:\n{text[:500]}\n\nمرشّحو المخزون (وسومهم فقط):\n{listing}\n\n"
                    + (f"تجنّب: {'، '.join(avoid)}\n" if avoid else "") +
                    "قيّم كل مرشّح 0-10: هل يصوّر **حرفياً** موضوع المنشور نفسه (الشيء/الحدث/المكان)؟ "
                    "10 = المشهد نفسه، 7 = نفس نوع الشيء بوضوح، ≤4 = استعارة أو مفهوم عام أو موضوع آخر."),
            temperature=0) or {}
        scores = {int(s.get("n")): float(s.get("score") or 0) for s in out.get("scores") or []
                  if str(s.get("n", "")).isdigit()}
    except Exception:  # noqa: BLE001 - بلا تقييم لا نختار (صورة مضلِّلة أسوأ من لا صورة)
        return []
    def rank(n: int) -> float:
        return scores[n] + (video_bonus if cands[n - 1].get("kind") == "video" else 0.0)

    ranked = sorted((n for n, s in scores.items() if s >= _MIN_SCORE and 0 < n <= len(cands)),
                    key=lambda n: -rank(n))
    return [dict(cands[n - 1], _score=scores[n]) for n in ranked]


def _vision_ok(text: str, cand: Dict[str, Any]) -> Optional[bool]:
    """نموذج رؤية (إعداد النظام) يرى معاينة الملف فعلاً: هل تصوّر موضوع المنشور بلا تضليل؟
    None = تعذّر الفحص (لا مفتاح/لا دعم صور)."""
    preview = cand.get("preview") or ""
    if not preview:
        return None
    try:
        from openai import OpenAI

        from app.core.llm_provider import resolve_llm

        ep = resolve_llm()
        if not ep.available:
            return None
        # معاينة الفيديو لقطة واحدة منه: قيس حيّاً أن لقطة «دخان فوق جبال» من مقطع حريق غابات رُفضت
        # لأنها «لا تُظهر طائرات الإطفاء» المذكورة في المنشور — فيُحكم على نوع المشهد لا على كل تفصيل
        frame = ("This is ONE frame (thumbnail) of a short stock video clip: judge whether the clip is the "
                 "same kind of scene/event as the post, not whether this single frame shows every detail. "
                 if cand.get("kind") == "video" else "")
        r = OpenAI(**ep.client_kwargs(), timeout=45).chat.completions.create(
            model=ep.model, temperature=0, max_tokens=120,
            messages=[{"role": "system", "content": (
                "You check stock media for a news post on X. Reply JSON only: "
                '{"match": true|false, "reason": "short"}. ' + frame + "match=true ONLY if the media literally "
                "shows the post's subject (same kind of object/event/place) and would not mislead a reader: "
                "no fantasy/sci-fi, no unrelated country's flag or symbols, no staged concept art.")},
                {"role": "user", "content": [
                    {"type": "text", "text": f"Post: {text[:500]}"},
                    {"type": "image_url", "image_url": {"url": preview, "detail": "low"}}]}])
        m = re.search(r"\{.*\}", r.choices[0].message.content or "", re.S)
        return bool(json.loads(m.group(0)).get("match")) if m else None
    except Exception as exc:  # noqa: BLE001
        logger.info("stock media vision check skipped: %s", exc)
        return None


def _verify(text: str, ranked: List[Dict[str, Any]], trail: List[str]) -> Optional[Dict[str, Any]]:
    """فحص رؤية لأفضل اثنين، ثم لأفضل مرشّح من النوع الآخر: رفضُ فيديوهين متصدّرَين كان يترك المنشور
    بلا ميديا رغم صورة مطابقة خلفهما (حكم الرؤية متذبذب: True/False/True لنفس اللقطة)."""
    checks = ranked[:2]
    other = next((c for c in ranked[2:] if c["kind"] not in {x["kind"] for x in checks}), None)
    for cand in checks + ([other] if other else []):
        seen = _vision_ok(text, cand)
        # فيديو مطابق الوسوم بقوة (≥9) ورُفض: لقطة واحدة من مقطع حكمٌ جزئي ومتذبذب، فرأي ثانٍ
        if seen is False and cand.get("kind") == "video" and cand["_score"] >= 9:
            seen = _vision_ok(text, cand)
        trail.append(f"{cand['kind']}:{cand['id']}:{cand['_score']:g}:{seen}")
        if seen is True or (seen is None and cand["_score"] >= 8):
            return cand
    return None


# ─────────────── مكتبة ميديا الموضوع (التوجيه الإعلامي → كل موظفي الحملة) ───────────────
#: تُبنى مرة لكل (مالك، موضوع) من الرسالة الإعلامية، ويختار منها كل موظف بعد أن يكتب تغريدته ما
#: يناسب نصّه هو — ولا يأخذ ملفاً أخذه زميله في الحملة نفسها (منشورات الفريق لا تتشابه بصرياً).
_POOL_TTL = 30 * 60
_POOL_SIZE = 10
_POOLS: Dict[str, Dict[str, Any]] = {}
_POOL_LOCKS: Dict[str, threading.Lock] = {}


def pool_key(user_id: Optional[int], topic: str) -> str:
    norm = re.sub(r"\s+", " ", (topic or "").strip().lower())[:200]
    return f"{user_id or 0}|{norm}"


def _pool_ctx(llm: Any, mcp: Any) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(config=SimpleNamespace(id="media_pool", business_rules={}, mcp_servers=["pixabay"]),
                           mcp=mcp, llm=llm, scratch={}, emit_progress=lambda *a, **k: None)


def news_media_enabled(config) -> bool:
    """مصدر اختياري (إعداد الموظف ``news_media``، مطفأ افتراضياً): صور مقالات الأخبار نفسها — أقرب
    للخبر من المخزون، لكن حقوقها لناشريها؛ المالك يقرّر."""
    return bool((getattr(config, "business_rules", None) or {}).get("news_media", False))


_OG_RE = re.compile(r"""<meta[^>]+(?:property|name)\s*=\s*["'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["']"""
                    r"""[^>]*?content\s*=\s*["']([^"']+)["']|<meta[^>]+content\s*=\s*["']([^"']+)["'][^>]*?"""
                    r"""(?:property|name)\s*=\s*["'](?:og:image|twitter:image)["']""", re.I)
_WEAK_NEWS_IMG = re.compile(r"(?i)logo|favicon|placeholder|default[-_]|sprite|avatar|/icons?/|share[-_]?image|\.svg(?:$|\?)")


def _public_url(url: str) -> bool:
    """رابط http(s) لعنوان عام فقط: صفحات الأخبار تأتي من نتائج بحث، وصفحة خبيثة قد توجّه الجالب إلى
    عنوان داخلي في الخادم (SSRF) عبر og:image أو التحويل."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    try:
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        for info in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80)):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except (OSError, ValueError):
        return False


def _fetch_page(url: str, limit: int = 600_000) -> str:
    import requests

    for _ in range(3):                                   # تحويلان كحد أقصى، كلٌّ يُفحص من جديد
        if not _public_url(url):
            return ""
        from ..security.egress import public_request
        r = public_request("GET", url, timeout=8, stream=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; MojAgenticAI/1.0)"})
        if r.is_redirect and r.headers.get("Location"):
            from urllib.parse import urljoin

            url = urljoin(url, r.headers["Location"])
            r.close()
            continue
        if r.status_code != 200 or "html" not in (r.headers.get("Content-Type") or ""):
            return ""
        chunks, size = [], 0
        for chunk in r.iter_content(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size >= limit:                            # og:image في الرأس — لا نقرأ الصفحة كلها
                break
        r.close()
        return b"".join(chunks).decode(r.encoding or "utf-8", errors="ignore")
    return ""


def news_images(sources: List[Dict[str, Any]], limit: int = 6) -> List[Dict[str, Any]]:
    """صورة المقال الرئيسية (og:image) لكل مصدر في الرسالة الإعلامية — بالتوازي، بحد زمني."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    from html import unescape
    from urllib.parse import urljoin, urlsplit

    srcs = [s for s in sources or [] if str(s.get("url") or "").startswith("http")][:limit]

    def one(s: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            m = _OG_RE.search(_fetch_page(s["url"]))
            img = unescape(urljoin(s["url"], (m.group(1) or m.group(2)) if m else "")).strip()
            if not m or _WEAK_NEWS_IMG.search(img) or not _public_url(img):
                return None
            site = s.get("site") or s.get("source") or (urlsplit(s["url"]).hostname or "").removeprefix("www.")
            return {"id": "n" + hashlib.sha1(img.encode()).hexdigest()[:12], "kind": "image", "url": img,
                    "preview": img, "tags": f"{site}: {(s.get('title') or '')[:120]}", "page_url": s["url"],
                    "source": "news", "site": site}
        except Exception:  # noqa: BLE001 - مصدر لا يُجلب لا يوقف المكتبة
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        out = [x for x in ex.map(one, srcs) if x]
    return list({x["url"]: x for x in out}.values())


def build_pool(topic: str, message: str, user_id: Optional[int], llm: Any = None,
               mcp: Any = None, news_sources: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """صور وفيديوهات للموضوع والرسالة الإعلامية: كلمات بحث من الرسالة، فيديو وصور معاً، تقييم أولي
    للمطابقة مع الرسالة (≥7) — والمطابقة النهائية مع كل تغريدة عند الاختيار. يعيد {"items": [...]}."""
    key = pool_key(user_id, topic)
    with _lock:
        lk = _POOL_LOCKS.setdefault(key, threading.Lock())
    with lk:                                   # موظفو الحملة يصلون معاً: بناء واحد لا عشرة
        hit = _POOLS.get(key)
        if hit and time.time() - hit["at"] < _POOL_TTL:
            if news_sources and not hit.get("news_done"):   # أول موظف فعّل صور الأخبار يضيفها للحملة
                hit["news_done"] = True
                hit["items"] = hit["items"] + news_images(news_sources)
            return hit
        if llm is None or mcp is None:
            from app.aeos.bootstrap import get_platform
            from app.aeos.mcp.registry import MCPRegistry

            plat = get_platform()
            llm = llm or plat.llm
            mcp = mcp or plat.container.resolve(MCPRegistry)
        ctx = _pool_ctx(llm, mcp)
        items: List[Dict[str, Any]] = []
        if available(ctx):
            text = f"{topic}\n{message}".strip()
            plan = _plan(ctx, text[:600], topic)
            if plan and plan["queries"]:
                cands = _collect(ctx, plan["queries"], ["video", "image"], set())
                keep = ("id", "kind", "url", "preview", "tags", "page_url", "duration", "_score")
                items = [{k: c.get(k) for k in keep}
                         for c in _choose(ctx, text, cands, plan["avoid"], _VIDEO_BONUS)[:_POOL_SIZE]]
        if news_sources:
            items += news_images(news_sources)
        pool = {"at": time.time(), "topic": topic, "items": items, "taken": {}, "announced": False,
                "news_done": bool(news_sources)}
        _POOLS[key] = pool
        return pool


def get_pool(key: str) -> Optional[Dict[str, Any]]:
    hit = _POOLS.get(key or "")
    return hit if hit and time.time() - hit["at"] < _POOL_TTL else None


def pool_line(pool: Dict[str, Any]) -> str:
    items = pool.get("items") or []
    news = sum(1 for i in items if i.get("source") == "news")
    vids = sum(1 for i in items if i.get("kind") == "video")
    return (f"🎞️ **مكتبة ميديا الموضوع جاهزة:** {len(items) - vids - news} صورة و{vids} فيديو من Pixabay"
            + (f" و{news} صورة من مقالات الأخبار (حقوقها لناشريها)" if news else "") +
            " — كل موظف يختار منها ما يناسب تغريدته بعد كتابتها، بلا تكرار بين الزملاء.")


def pick_from_pool(context: "ExecutionContext", text: str, key: str) -> str:
    """ميديا تغريدة هذا الموظف من مكتبة الموضوع: تقييم المطابقة مع **نصّه** + فحص الرؤية، بلا ملف
    أخذه زميل أو استخدمه هو خلال 30 يوماً. "" = لا مناسب (فيُكمل بالبحث العادي ثم التوليد)."""
    pool = get_pool(key)
    if not pool or not pool.get("items") or not available(context) or not (text or "").strip():
        return ""
    want = str((context.config.business_rules or {}).get("stock_media_kind", "auto")).lower()
    mine = set((_used().get(context.config.id) or {}).keys())
    with _lock:
        taken = set(pool["taken"])
    news_ok = news_media_enabled(context.config)
    cands = [dict(c) for c in pool["items"]
             if f"{c['kind']}:{c['id']}" not in mine | taken and (want not in ("image", "video") or c["kind"] == want)
             and (c.get("source") != "news" or news_ok)]
    if not cands:
        return ""
    trail: List[str] = []
    pick = _verify(text, _choose(context, text, cands, [], 0.0 if want == "image" else _VIDEO_BONUS), trail)
    ref = f"{pick['kind']}:{pick['id']}" if pick else ""
    with _lock:
        if pick and ref in pool["taken"]:      # زميل سبقه إليه أثناء التقييم
            pick = None
        elif pick:
            pool["taken"][ref] = context.config.id
    context.scratch["stock_media"] = {"from_pool": True, "candidates": len(cands), "checked": trail,
                                      **({"kind": pick["kind"], "id": pick["id"], "tags": pick.get("tags", ""),
                                          "page_url": pick.get("page_url", "")} if pick else {"picked": None})}
    if not pick:
        return ""
    _mark_used(context.config.id, ref)
    try:
        where = f"صورة مقال {pick.get('site', '')}" if pick.get("source") == "news" else (
            "فيديو" if pick["kind"] == "video" else "صورة")
        context.emit_progress(
            f"🖼️ اختار {where} من مكتبة ميديا الموضوع لتغريدته "
            f"(«{(pick.get('tags') or '')[:60]}»): {pick.get('page_url', '')}", intent="aeos_cycle_media")
    except Exception:  # noqa: BLE001
        pass
    return pick["url"]


def _collect(context: "ExecutionContext", queries: List[str], kinds: List[str],
             used: set) -> List[Dict[str, Any]]:
    """حتى 6 مرشّحين لكل نوع، متناوبين (فيديو، صورة، فيديو…) فيصل الاثنان للتقييم."""
    per_kind: Dict[str, List[Dict[str, Any]]] = {}
    for kind in kinds:
        got: List[Dict[str, Any]] = []
        for q in queries:
            got += [c for c in _search(context, q, kind) if f"{c['kind']}:{c['id']}" not in used
                    and c["id"] not in {x["id"] for x in got}]
            if len(got) >= 6:
                break
        per_kind[kind] = got[:6]
    out: List[Dict[str, Any]] = []
    for i in range(6):
        out += [per_kind[k][i] for k in kinds if i < len(per_kind[k])]
    return out


def find(context: "ExecutionContext", text: str, topic: str = "") -> str:
    """رابط صورة/فيديو Pixabay مناسب للمنشور، أو "" (فيُترك لتوليد صورة أصلية أو نص فقط)."""
    if not available(context) or not (text or "").strip():
        return ""
    try:
        plan = _plan(context, text, topic)
        if not plan or not plan["queries"]:
            return ""
        used = set((_used().get(context.config.id) or {}).keys())
        want = plan.get("want", "auto")
        # auto: فيديو وصور في ترتيب واحد (كان البحث بنوع واحد يختاره المخطِّط، فلا يُرى فيديو جيد
        # بجانب الصور أبداً — قيس: كل ميديا المخزون في السجلات صور). video: فيديو ثم صورة احتياطاً.
        rounds = {"image": [["image"]], "video": [["video"], ["image"]]}.get(want, [["video", "image"]])
        cands: List[Dict[str, Any]] = []
        pick = None
        trail: List[str] = []          # لسجل الدورة: لماذا اختير هذا النوع (أو لا شيء)
        for kinds in rounds:
            cands = _collect(context, plan["queries"], kinds, used)
            bonus = _VIDEO_BONUS if want != "image" else 0.0
            pick = _verify(text, _choose(context, text, cands, plan["avoid"], bonus), trail)
            if pick:
                break
        if not pick:
            context.scratch["stock_media"] = {"queries": plan["queries"], "picked": None,
                                              "candidates": len(cands), "checked": trail}
            return ""
        _mark_used(context.config.id, f"{pick['kind']}:{pick['id']}")
        context.scratch["stock_media"] = {"queries": plan["queries"], "kind": pick["kind"],
                                          "id": pick["id"], "tags": pick.get("tags", ""),
                                          "page_url": pick.get("page_url", ""), "checked": trail}
        try:
            context.emit_progress(
                f"🖼️ اختار {'فيديو' if pick['kind'] == 'video' else 'صورة'} من Pixabay للمنشور "
                f"(«{pick.get('tags', '')[:60]}»): {pick.get('page_url', '')}", intent="aeos_cycle_media")
        except Exception:  # noqa: BLE001
            pass
        return pick["url"]
    except Exception as exc:  # noqa: BLE001 - الميديا تحسين: لا تُسقط النشر
        logger.warning("stock media failed: %s", exc)
        return ""
