"""
التوجيه الإعلامي عبر خادم «نواه» (``nawah_brief``) — محرّك «مهمة للفريق».

قبله: ``media_brief.build`` يبحث في Tavily ويكتب رسالة إعلامية واحدة، ثم يوزّع «زوايا» على
الموظفين بالدور (round-robin) بلا معرفة بمن هم. الآن نداء ``brief`` واحد لكل (مستخدم، موضوع):
1. بحث الويب (Tavily عبر منفذ نواه) → موجز بحثي مرقّم المصادر.
2. **التوجيه**: مدير تحرير يقرأ كل صوت في الفريق (استنساخه إن استُنسخ، وإلا شخصيته ومنشوراته
   الحقيقية — ``persona`` مؤقتة) ويقرّر الرسالة العامة، والحقائق التي تستحق الحمل، ولكل موظف:
   ما يؤمن به، موقفه، زاويته التي لا يكرّر بها زميله، وما يتجنّبه.
3. **مسودة** بصوت كل موظف من التوجيه نفسه، مع درجة تطابقها (للمستنسَخين والمقيسين فقط).

ما يصل الموظف (``for_employee``) = الموجز البحثي + التوجيه + دوره هو + مسودته، ثم يكتب **كاتبه
المعتاد** المنشور النهائي عبر بوابة الجودة كما كان (قرار المالك: التوجيه والمسودة من نواه،
والنشر النهائي من خط الكتابة الحالي). النداء يستغرق دقيقة إلى ثلاث لفريق كامل، لذلك يُطلق في
الخلفية من ``order_publish`` وينتظره ``dispatch_directed`` قبل تشغيل الدورة — بلا تعليق للشات.
الاحتياط المعلَن عند تعذّر نواه: ``media_brief`` القديم، والسبب يُقال للمالك.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from . import nawah_clone as NC

logger = logging.getLogger("aeos.nawah_brief")

SLUG = NC.BRIEF_SLUG
_TTL = 30 * 60
CALL_TIMEOUT = 420.0
MAX_VOICES = 10
_cache: Dict[tuple, Dict[str, Any]] = {}
_inflight: Dict[tuple, threading.Event] = {}
_announced: set = set()
_lock = threading.Lock()
_TONE_AR = {"professional": "مهنية", "casual": "عفوية", "witty": "ذكية بخفّة", "formal": "رسمية", "friendly": "ودودة"}


def enabled() -> bool:
    try:
        return NC.enabled(SLUG)
    except Exception:  # noqa: BLE001
        return False


def status() -> Dict[str, Any]:
    return NC.status(SLUG)


def _norm(topic: str) -> str:
    return re.sub(r"\s+", " ", topic or "").strip().lower()[:200]


def _key(user_id: Optional[int], topic: str) -> tuple:
    return (user_id or 0, _norm(topic))


# ─────────────────────────── voices ───────────────────────────
def own_posts(cfg, limit: int = 40) -> List[str]:
    """منشورات الموظف الحقيقية المنشورة (سجل الدورات + دفتر الأداء) — عيّنة أسلوبه إن لم يُستنسخ."""
    texts: List[str] = []
    try:
        from . import insights as I

        texts += [i.get("text", "") for i in I.recent_contents(cfg.id, limit) if i.get("kind") == "post"]
    except Exception:  # noqa: BLE001
        logger.debug("recent_contents failed", exc_info=True)
    try:
        ledger = ((getattr(cfg, "runtime_state", None) and cfg.runtime_state.flags) or {}).get("post_performance") or {}
        texts += [p.get("text", "") for p in (ledger.get("posts") or {}).values()]
    except Exception:  # noqa: BLE001
        pass
    out, seen = [], set()
    for t in texts:
        t = re.sub(r"\s+", " ", t or "").strip()
        if len(t) >= 15 and t not in seen:
            seen.add(t)
            out.append(t[:600])
    return out[:limit]


def voice_for(cfg) -> Dict[str, Any]:
    """صوت الموظف لنداء brief: استنساخه المحفوظ إن وُجد، وإلا شخصيته (+ منشوراته) كصوت مؤقت."""
    vp = (getattr(cfg, "business_rules", None) or {}).get("voice_profile") or {}
    nid = (vp.get("nawah") or {}).get("id")
    if nid and (NC.DATA_DIR / "clones" / f"{nid}.json").exists():
        return {"clone": nid, "count": 1}
    p = cfg.persona
    a = vp.get("analysis") or {}
    st = vp.get("style") or {}
    parts = [f"الدور: {p.role}." if p.role else "",
             f"النبذة: {p.bio}" if p.bio else "",
             f"الاهتمامات: {'، '.join(p.interests)}." if p.interests else "",
             f"القيم: {'، '.join(p.values)}." if p.values else "",
             f"النبرة: {_TONE_AR.get(p.tone, p.tone)}." if p.tone else "",
             a.get("voice_summary") or "",
             f"تعليماته: {p.instructions[:600]}" if p.instructions else ""]
    persona = {
        "name": p.display_name or cfg.account_username,
        "handle": cfg.account_username,
        "description": " ".join(x for x in parts if x)[:1500],
        "dialect": p.dialect or st.get("dialect_guess") or "",
        "register": a.get("register") or "",
        "style_guide": (a.get("style_guide") or list(p.dos or []))[:8],
        "never_uses": (a.get("never_uses") or list(p.donts or []))[:6],
        "signature_phrases": ((a.get("lexicon") or {}).get("signature") or st.get("signature_phrases") or [])[:6],
        "samples": own_posts(cfg),
        "category": (p.interests or [""])[0],
    }
    return {"persona": {k: v for k, v in persona.items() if v not in ("", None, [])}, "count": 1}


def team_voices(user_id: Optional[int], employees=None) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """أصوات الفريق + خريطة (معرّف الصوت عند نواه ← يوزر الموظف)."""
    if employees is None:
        from .orchestrator import employees_for

        employees = employees_for(user_id)
    active = [c for c in employees if getattr(getattr(c, "status", None), "value", "active") == "active"]
    voices, by_id = [], {}
    for cfg in (active or list(employees))[:MAX_VOICES]:
        v = voice_for(cfg)
        ref = v.get("clone") or f"persona:{cfg.account_username}"
        by_id[ref] = cfg.account_username
        voices.append(v)
    return voices, by_id


# ─────────────────────────── the call ───────────────────────────
def _call(topic: str, voices: List[Dict[str, Any]], timeout: float = CALL_TIMEOUT) -> Dict[str, Any]:
    st = NC.status(SLUG)
    if not st["ok"]:
        raise RuntimeError(st["reason"])
    srv = NC._server(SLUG)
    from ..mcp.servers.external_mcp import result_text

    res = srv.client.call_tool("brief", {"topic": topic, "voices": voices, "research": "web",
                                         "drafts": True, "length": "medium", "report": True},
                               timeout=timeout)
    if res.get("isError"):
        raise RuntimeError(result_text(res)[:300] or "نواه أعادت خطأ بلا تفاصيل")
    sc = res.get("structuredContent") or {}
    if not isinstance(sc.get("directive"), dict):
        raise RuntimeError("نواه لم تُعد توجيهاً")
    return sc


def _site(url: str) -> str:
    return (urlsplit(url or "").hostname or "").removeprefix("www.")


def normalize(sc: Dict[str, Any], by_id: Dict[str, str], topic: str) -> Dict[str, Any]:
    """ناتج brief ← بنية ثابتة مفهرسة بيوزر الموظف."""
    d = sc.get("directive") or {}
    research = sc.get("research") or {}
    sources = [{"n": i + 1, "title": (s.get("title") or "")[:140], "url": s.get("uri") or "",
                "site": _site(s.get("uri") or ""), "date": s.get("date") or ""}
               for i, s in enumerate(research.get("sources") or []) if s.get("uri")][:10]
    meta = {v.get("clone"): v for v in sc.get("voices") or [] if isinstance(v, dict)}
    drafts: Dict[str, List[Dict[str, Any]]] = {}
    for p in sc.get("pieces") or []:
        if isinstance(p, dict) and p.get("text"):
            drafts.setdefault(str(p.get("clone")), []).append({"text": str(p["text"]).strip(), "score": p.get("score")})
    voices: Dict[str, Dict[str, Any]] = {}
    for v in d.get("voices") or []:
        cid = str(v.get("clone") or "")
        handle = by_id.get(cid) or str(v.get("name") or cid).lstrip("@")
        best = sorted(drafts.get(cid) or [], key=lambda x: -(x.get("score") or 0))[:1]
        voices[handle] = {
            "beliefs": str(v.get("beliefs") or "")[:400], "stance": str(v.get("stance") or "")[:300],
            "angle": str(v.get("angle") or "")[:400], "avoid": str(v.get("avoid") or "")[:300],
            "draft": best[0]["text"][:700] if best else "", "score": best[0].get("score") if best else None,
            "adhoc": bool((meta.get(cid) or {}).get("adhoc")),
        }
    return {"ok": True, "engine": "nawah", "topic": topic, "message": str(d.get("message") or "")[:600],
            "facts": [str(f)[:300] for f in (d.get("facts") or [])][:8],
            "voices": voices, "research_brief": str(research.get("brief") or "")[:5000],
            "sources": sources, "failed": list(sc.get("failed") or []),
            "markdown": str(sc.get("markdown") or ""), "created_at": time.time()}


# ─────────────────────────── cache / in-flight ───────────────────────────
def peek(topic: str, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    with _lock:
        hit = _cache.get(_key(user_id, topic))
        return hit if hit and time.time() - hit.get("created_at", 0) < _TTL else None


def build(topic: str, user_id: Optional[int], employees=None, timeout: float = CALL_TIMEOUT) -> Dict[str, Any]:
    """توجيه (مستخدم، موضوع) — من الذاكرة المؤقتة، أو بانتظار نداء جارٍ، أو ببنائه الآن."""
    key = _key(user_id, topic)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit.get("created_at", 0) < _TTL:
            return hit
        ev = _inflight.get(key)
        if ev is None:
            ev = _inflight[key] = threading.Event()
            builder = True
        else:
            builder = False
    if not builder:
        ev.wait(timeout)
        return peek(topic, user_id) or {"ok": False, "engine": "nawah", "error": "انتهت مهلة انتظار توجيه نواه"}
    try:
        voices, by_id = team_voices(user_id, employees)
        if not voices:
            raise RuntimeError("لا موظفين نشطين لبناء توجيه لهم")
        t0 = time.time()
        out = normalize(_call(topic, voices, timeout), by_id, topic)
        out["seconds"] = round(time.time() - t0, 1)
        logger.info("nawah brief ready for %r: %d voices in %ss", topic[:60], len(out["voices"]), out["seconds"])
    except Exception as exc:  # noqa: BLE001 - يُعلَن للمالك ويُستخدم الاحتياط
        logger.warning("nawah brief failed for %r: %s", topic[:60], exc)
        out = {"ok": False, "engine": "nawah", "error": (str(exc) or exc.__class__.__name__)[:300],
               "created_at": time.time()}
    with _lock:
        _cache[key] = out
        _inflight.pop(key, None)
    ev.set()
    return out


def prepare(topic: str, user_id: Optional[int], employees=None) -> str:
    """ابدأ البناء في الخلفية إن لم يكن جاهزاً/جارياً. يعيد ready | building."""
    if peek(topic, user_id):
        return "ready"
    key = _key(user_id, topic)
    with _lock:
        running = key in _inflight
    if not running:
        threading.Thread(target=build, args=(topic, user_id, employees), daemon=True,
                         name=f"nawah-brief-{abs(hash(key)) % 10000}").start()
    return "building"


def announce_once(brief: Dict[str, Any], user_id: Optional[int]) -> bool:
    """أول موظف يصل إلى التوجيه الجاهز يعلنه للمالك؛ البقية لا يكرّرون الرسالة."""
    key = (_key(user_id, brief.get("topic", "")), round(brief.get("created_at", 0)))
    with _lock:
        if key in _announced:
            return False
        _announced.add(key)
        if len(_announced) > 200:
            _announced.clear()
        return True


# ─────────────────────────── texts ───────────────────────────
def for_employee(brief: Dict[str, Any], handle: str) -> str:
    """ما يُحقن في بريف النشر: البحث + التوجيه + دور هذا الموظف + مسودته، ثم قواعد الكتابة منها."""
    v = (brief.get("voices") or {}).get(handle) or {}
    blocks = [f"🧭 التوجيه الإعلامي (نواه) — {brief.get('topic', '')}"]
    if brief.get("message"):
        blocks.append(f"الرسالة العامة التي يخرج بها الجمهور من منشورات الفريق كلها: {brief['message']}")
    if brief.get("facts"):
        blocks.append("الحقائق الجديدة من البحث (استخدم ما يخدم زاويتك فقط):\n"
                      + "\n".join(f"- {f}" for f in brief["facts"][:8]))
    if brief.get("research_brief"):
        blocks.append("الموجز البحثي (الأرقام تشير إلى المصادر):\n" + brief["research_brief"][:2500])
    mine = [f"ما تؤمن به كما يظهر من أسلوبك: {v['beliefs']}" if v.get("beliefs") else "",
            f"موقفك من هذا الموضوع: {v['stance']}" if v.get("stance") else "",
            f"زاويتك أنت (زملاؤك يأخذون زوايا أخرى، فلا تكرّرهم): {v['angle']}" if v.get("angle") else "",
            f"تجنّب: {v['avoid']}" if v.get("avoid") else ""]
    if any(mine):
        blocks.append("دورك في هذا التوجيه:\n" + "\n".join(f"- {m}" for m in mine if m))
    if v.get("draft"):
        blocks.append("مسودة كتبها نواه بصوتك من التوجيه نفسه"
                      + (f" (تطابق {v['score']}/100)" if v.get("score") is not None else "")
                      + " — انطلق منها: حسّنها أو اكتب أقوى منها، ولا تنشرها حرفياً إن رأيت فيها ضعفاً:\n"
                      + v["draft"])
    srcs = "؛ ".join(f"[{s['n']}] {s['site']}" for s in brief.get("sources") or [])
    if srcs:
        blocks.append(f"المصادر: {srcs}")
    rules = (
        "\n\nطريقة الكتابة من التوجيه:\n"
        "- اكتب منشورك أنت بأسلوب شخصيتك ولهجتك؛ الحقائق والأرقام كما وردت في البحث فقط، ولا تضف حقيقة من خارجه.\n"
        "- التزم بالرسالة العامة وبزاويتك، واختر أقوى معلومة أو اثنتين — المنشور ليس تلخيصاً للتوجيه.\n"
        "- يمكنك الإشارة للمصدر باسمه (مثلاً: بحسب العربية) عند رقم أو تصريح حساس."
    )
    return "\n\n".join(b for b in blocks if b) + rules


def research_items(brief: Dict[str, Any]) -> List[Dict[str, Any]]:
    items = [{"source": s.get("site", ""), "title": s.get("title", ""), "url": s.get("url", ""), "snippet": ""}
             for s in brief.get("sources") or []][:4]
    if items and brief.get("message"):
        items[0]["snippet"] = brief["message"][:300]
    return items


def owner_summary(brief: Dict[str, Any]) -> Dict[str, Any]:
    voices = brief.get("voices") or {}
    return {"engine": "nawah", "message": brief.get("message", ""), "facts": len(brief.get("facts") or []),
            "who_says_what": [{"employee": h, "angle": v.get("angle", ""), "draft": bool(v.get("draft")),
                               "score": v.get("score")} for h, v in voices.items()],
            "sources": [s["site"] for s in brief.get("sources") or []][:8],
            "failed": brief.get("failed") or [], "seconds": brief.get("seconds")}


def owner_line(brief: Dict[str, Any], handle: str = "") -> str:
    """سطر الشات حين يجهز التوجيه."""
    voices = brief.get("voices") or {}
    who = "؛ ".join(f"@{h}: {v['angle']}" for h, v in voices.items() if v.get("angle"))[:700]
    sites = "، ".join(s["site"] for s in (brief.get("sources") or [])[:4])
    return (f"🧭 **التوجيه الإعلامي من نواه جاهز** — {brief.get('message', '')}\n"
            f"{len(brief.get('facts') or [])} حقيقة من {len(brief.get('sources') or [])} مصدراً ({sites})، "
            f"ومسودة بصوت كل موظف.\nمن يقول ماذا: {who or '—'}"
            + (f"\nتعذّر صوت: {'، '.join(brief['failed'])}" if brief.get("failed") else ""))


def attach(payload: Dict[str, Any], cfg, timeout: float = CALL_TIMEOUT) -> Tuple[Optional[Dict[str, Any]], str]:
    """ألحق توجيه نواه بحمولة نشر موجّه. يعيد (التوجيه, "") أو (None, سبب) للاحتياط."""
    topic = str(payload.get("directed_topic") or "").strip()
    if not topic or payload.get("media_brief") or payload.get("deep_research") is False:
        return None, ""
    brief = build(topic, getattr(cfg, "user_id", None), timeout=timeout)
    if not brief.get("ok"):
        return None, brief.get("error") or "سبب غير معروف"
    payload["media_brief"] = for_employee(brief, cfg.account_username)
    payload["media_brief_items"] = research_items(brief)
    payload["media_brief_engine"] = "nawah"
    return brief, ""
