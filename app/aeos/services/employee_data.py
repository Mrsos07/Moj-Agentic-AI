"""
On-demand data retrieval for the AI employee, from the chat.

Lets the user ask the employee (in a chat linked to it) to fetch and show live
data from its own sources, then discuss it:

- ``news``       Google News by the employee's specialty (Google News RSS).
- ``trends``     X trends the account sees (Playwright, cookie login).
- ``timeline``   the account's home timeline (Playwright).
- ``top_posts``  the creator "Top Posts" inspiration page (Playwright).

Browser-based sources take time, so fetching runs in a **background thread** and
the formatted result is pushed to the user's chat when ready (the websocket is
never blocked). The latest fetched summary is cached per-employee so the
employee can *discuss* it afterwards (grounding the LLM answer).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeos.employee_data")

DATA_KINDS = ("news", "trends", "timeline", "top_posts")

_KIND_LABEL = {
    "news": "الأخبار حسب تخصصك",
    "trends": "الترندات",
    "timeline": "التايم لاين",
    "top_posts": "أهم البوستات (منشئ المحتوى)",
}

# آخر ما جلبه كل موظف (نص مقروء) — يُستخدم لتأصيل النقاش اللاحق مع المستخدم.
_LAST_FETCHED: Dict[str, str] = {}


def recent_data_text(employee_id: str) -> str:
    """آخر بيانات جلبها الموظف (أخبار/ترند/…)، أو '' إن لا يوجد."""
    return _LAST_FETCHED.get(employee_id, "")


# ─────────────────────────── public API ───────────────────────────
def request_data(kind: str, cfg, user_id: Optional[int]) -> Optional[str]:
    """Spawn a background fetch for ``kind`` and return an immediate chat ack.

    Returns ``None`` if the kind is unknown (caller falls through)."""
    if kind not in DATA_KINDS:
        return None
    threading.Thread(
        target=_run, args=(kind, cfg, user_id), daemon=True,
    ).start()
    return (f"🔎 جارِ جلب {_KIND_LABEL[kind]} للموظف **@{cfg.account_username}**… "
            "سأوافيك بالنتيجة هنا خلال لحظات. ⏳")


# ─────────────────────────── worker ───────────────────────────
def _run(kind: str, cfg, user_id: Optional[int]) -> None:
    try:
        from app.aeos.bootstrap import get_platform
        from app.aeos.mcp.registry import MCPRegistry

        platform = get_platform()
        mcp = platform.container.resolve(MCPRegistry)
        if kind == "news":
            text = _fetch_news(mcp, cfg)
        elif kind == "trends":
            text = _fetch_trends(mcp, cfg)
        elif kind == "timeline":
            text = _fetch_timeline(mcp, cfg)
        else:
            text = _fetch_top_posts(mcp, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("employee data fetch '%s' failed", kind)
        text = f"⚠️ تعذّر جلب {_KIND_LABEL.get(kind, 'البيانات')}: {exc}"

    try:
        _LAST_FETCHED[cfg.id] = text
    except Exception:  # noqa: BLE001
        pass
    _push(user_id, text, cfg)


# ─────────────────────────── fetchers ───────────────────────────
def _mcp_req(employee_id, tool, params, server, cookie_label=None):
    from app.aeos.mcp.base import MCPRequest

    ctx = {"server": server}
    if cookie_label:
        ctx["cookie_label"] = cookie_label
    return MCPRequest(employee_id=employee_id, tool=tool, params=params, context=ctx)


def _news_items(mcp, cfg) -> List[dict]:
    from app.aeos.employee.news_sources import feeds_for_interests
    from app.aeos.employee.persona_templates import relevance_score

    interests = getattr(cfg.persona, "interests", []) or []
    lang = getattr(cfg.persona, "language", "ar") or "ar"
    feeds = feeds_for_interests(interests, hl=lang, gl="SA")
    seen: set = set()
    items: List[dict] = []
    for feed_url in feeds[:6]:
        res = mcp.call(_mcp_req(cfg.id, "fetch_feed",
                                {"feed_url": feed_url, "limit": 8}, "news"))
        if not res.success:
            continue
        for it in res.data.get("items", []):
            title = (it.get("title") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            items.append({
                "title": title,
                "summary": (it.get("summary") or "").strip(),
                "url": (it.get("link") or "").strip(),
                "source": (it.get("source") or "").strip(),
            })
    if interests:
        def _rel(n):
            return relevance_score(f"{n['title']} {n['summary']}", interests)
        ranked = [n for n in items if _rel(n) > 0]
        ranked.sort(key=_rel, reverse=True)
        items = ranked or items
    return items[:8]


def _trends_items(mcp, cfg) -> List[dict]:
    res = mcp.call(_mcp_req(cfg.id, "read_trends",
                            {"cookie_label": cfg.cookie_label, "limit": 12, "headless": True},
                            "playwright_x", cookie_label=cfg.cookie_label))
    return (res.data.get("trends", []) or [])[:12] if res.success else []


def _timeline_items(mcp, cfg) -> List[dict]:
    res = mcp.call(_mcp_req(cfg.id, "read_timeline",
                            {"cookie_label": cfg.cookie_label, "list_type": "home",
                             "limit": 12, "headless": True},
                            "playwright_x", cookie_label=cfg.cookie_label))
    return (res.data.get("tweets", []) or [])[:12] if res.success else []


def _top_items(mcp, cfg) -> List[dict]:
    res = mcp.call(_mcp_req(cfg.id, "read_top_posts",
                            {"cookie_label": cfg.cookie_label, "limit": 10, "headless": True},
                            "playwright_x", cookie_label=cfg.cookie_label))
    return (res.data.get("tweets", []) or [])[:10] if res.success else []


def _fetch_news(mcp, cfg) -> str:
    items = _news_items(mcp, cfg)
    if not items:
        return (f"📰 لم أجد أخباراً مطابقة لتخصص الموظف @{cfg.account_username} الآن. "
                "جرّب لاحقاً أو راجع اهتماماته.")
    lines = [f"📰 **آخر الأخبار المتعلقة بتخصص @{cfg.account_username}:**", ""]
    for i, n in enumerate(items, 1):
        src = f" — {n['source']}" if n["source"] else ""
        lines.append(f"{i}. {n['title']}{src}")
    lines.append("\n💬 تقدر تناقشني في أي خبر (مثلاً: «ناقشني في الخبر 2») أو «انشر عن الخبر 2».")
    return "\n".join(lines)


def _fetch_trends(mcp, cfg) -> str:
    trends = _trends_items(mcp, cfg)
    if not trends:
        return f"📈 لا توجد ترندات ظاهرة لحساب @{cfg.account_username} الآن."
    lines = [f"📈 **الترندات التي يراها حساب @{cfg.account_username}:**", ""]
    for i, t in enumerate(trends[:12], 1):
        name = (t.get("name") or "").strip() if isinstance(t, dict) else str(t)
        posts = (t.get("posts") or "").strip() if isinstance(t, dict) else ""
        extra = f" · {posts}" if posts else ""
        lines.append(f"{i}. {name}{extra}")
    lines.append("\n💬 تقدر تقول «انشر عن الترند 3» أو «ناقشني في الترند 1».")
    return "\n".join(lines)


def _fetch_timeline(mcp, cfg) -> str:
    tweets = _timeline_items(mcp, cfg)
    if not tweets:
        return f"🧵 لا توجد تغريدات في التايم لاين لحساب @{cfg.account_username} الآن."
    lines = [f"🧵 **التايم لاين لحساب @{cfg.account_username}:**", ""]
    for i, t in enumerate(tweets[:10], 1):
        author = (t.get("author") or t.get("handle") or "").strip()
        text = (t.get("text") or "").replace("\n", " ").strip()[:140]
        lines.append(f"{i}. **{author}**: {text}")
    lines.append("\n💬 تقدر تقول «ناقشني في التغريدة 2» أو «رد على التغريدة 2».")
    return "\n".join(lines)


def _fetch_top_posts(mcp, cfg) -> str:
    tweets = _top_items(mcp, cfg)
    if not tweets:
        return f"🏆 لا توجد بوستات مقترحة من منشئ المحتوى لحساب @{cfg.account_username} الآن."
    lines = [f"🏆 **أهم البوستات (منشئ المحتوى) لحساب @{cfg.account_username}:**", ""]
    for i, t in enumerate(tweets[:10], 1):
        author = (t.get("author") or t.get("handle") or "").strip()
        text = (t.get("text") or "").replace("\n", " ").strip()[:140]
        who = f"**{author}**: " if author else ""
        lines.append(f"{i}. {who}{text}")
    lines.append("\n💬 تقدر تقول «اكتب مثل البوست 2» أو «ناقشني في البوست 1».")
    return "\n".join(lines)


# ─────────────────────── cross-source analysis ───────────────────────
def request_analysis(cfg, user_id: Optional[int]) -> str:
    """يبحث ويحلّل عبر كل المصادر (أخبار/ترند/تايم لاين/أهم بوستات) ثم يستنتج
    المواضيع المرتبطة باهتمامات الموظف — في الخلفية، ويرسل النتيجة للنقاش."""
    threading.Thread(target=_run_analysis, args=(cfg, user_id), daemon=True).start()
    return (f"🧠 جارِ البحث والتحليل عبر مصادر @{cfg.account_username} "
            "(الأخبار والترند والتايم لاين وأهم البوستات)… قد يستغرق دقيقة أو "
            "دقيقتين، ثم أعرض لك المواضيع المرتبطة باهتماماتك للنقاش. ⏳")


def _run_analysis(cfg, user_id: Optional[int]) -> None:
    try:
        from app.aeos.bootstrap import get_platform
        from app.aeos.mcp.registry import MCPRegistry

        platform = get_platform()
        mcp = platform.container.resolve(MCPRegistry)
        news = _news_items(mcp, cfg)
        trends = _trends_items(mcp, cfg)
        timeline = _timeline_items(mcp, cfg)
        top = _top_items(mcp, cfg)
        text, stored = _analyze(platform, cfg, news, trends, timeline, top)
    except Exception as exc:  # noqa: BLE001
        logger.exception("employee analysis failed")
        text = f"⚠️ تعذّر إجراء التحليل: {exc}"
        stored = ""
    if stored:
        _LAST_FETCHED[cfg.id] = stored
    _push(user_id, text, cfg)


def _sources_context(news, trends, timeline, top) -> str:
    parts: List[str] = []
    if news:
        parts.append("【الأخبار】\n" + "\n".join(f"- {n.get('title', '')}" for n in news[:8]))
    if trends:
        names = [str(t.get("name", "")).strip() for t in trends[:12] if isinstance(t, dict)]
        parts.append("【الترند】\n" + "\n".join(f"- {n}" for n in names if n))
    if timeline:
        parts.append("【التايم لاين】\n" + "\n".join(
            f"- {(t.get('text') or '').replace(chr(10), ' ')[:120]}" for t in timeline[:10]))
    if top:
        parts.append("【أهم البوستات】\n" + "\n".join(
            f"- {(t.get('text') or '').replace(chr(10), ' ')[:120]}" for t in top[:8]))
    return "\n\n".join(p for p in parts if p.strip())


def _analyze(platform, cfg, news, trends, timeline, top):
    """يعيد (رسالة العرض، النص المخزَّن للنقاش)."""
    account = getattr(cfg, "account_username", "") or "الموظف"
    interests = getattr(cfg.persona, "interests", []) or []
    ctx = _sources_context(news, trends, timeline, top)
    if not ctx.strip():
        msg = (f"لم أتمكّن من رصد بيانات كافية الآن من مصادر @{account}. "
               "قد يكون المتصفح مشغولاً أو الجلسة تحتاج تحديثاً — جرّب بعد قليل.")
        return msg, ""

    llm = getattr(platform, "llm", None)
    if llm and getattr(llm, "available", False):
        try:
            from app.aeos.services.insights import _persona_brief
            persona = _persona_brief(cfg)
        except Exception:  # noqa: BLE001
            persona = ""
        interests_line = "، ".join(interests) if interests else "غير محددة"
        prompt = (
            f"اهتماماتي/تخصصي: {interests_line}.\n"
            f"شخصيتي: {persona}.\n\n"
            f"هذا ما رصدته الآن من مصادري:\n{ctx[:3500]}\n\n"
            "بصفتي الموظف الذكي، استنتج المواضيع المرتبطة مباشرةً باهتماماتي فقط:\n"
            "- اذكر 3 إلى 6 مواضيع، ولكل موضوع: عنوان مختصر، سبب أهميته الآن، وزاوية تغريدة مقترحة.\n"
            "- رتّبها حسب قوة الصلة والزخم عبر المصادر (تقاطع الخبر مع الترند/التفاعل أقوى).\n"
            "- تجاهل الإعلانات والسبام وأي شيء خارج تخصصي.\n"
            "- ابدأ بجملة تمهيدية قصيرة بصيغة المتكلم، ثم قائمة مرقّمة موجزة."
        )
        out = llm.complete(
            system=("أنت موظف ذكي محلل محتوى تدير حساب X. تحلّل مصادرك وتستنتج "
                    "مواضيع ضمن تخصصك فقط بذكاء ووضوح. اكتب بالعربية بصيغة المتكلم وبإيجاز."),
            prompt=prompt, temperature=0.6, max_tokens=750,
        )
        if out and out.strip():
            msg = (f"🧠 **تحليل @{account} — المواضيع المرتبطة باهتماماتك الآن:**\n\n"
                   f"{out.strip()}\n\n"
                   "💬 ناقشني: «فصّل الموضوع 2» · «ليش هذا مهم؟» · «اكتب تغريدة عن 1» · "
                   "«أي موضوع أنشر عنه أولاً؟»")
            stored = f"المصادر المرصودة:\n{ctx[:2500]}\n\nتحليلي:\n{out.strip()}"
            return msg, stored

    # بلا LLM: نعرض المصادر مجمّعة ليتناقش عليها المستخدم
    msg = (f"🧠 **ما رصده @{account} من مصادره** (التحليل الذكي غير متاح — LLM غير مُهيّأ):\n\n"
           f"{ctx[:2500]}\n\n💬 تقدر تناقشني في أي عنصر منها.")
    return msg, f"المصادر المرصودة:\n{ctx[:2500]}"


# ─────────────────────────── chat push ───────────────────────────
def _push(user_id: Optional[int], content: str, cfg=None) -> None:
    """Persist as an assistant message and push it live to the user's chat.

    When ``cfg`` is given, the message is stored in the employee's own
    conversation (session ``emp_<account>``) with a recognizable title, so the
    whole employee thread (data/analysis/discussion) lives in one place and
    shows up in the sidebar."""
    if user_id is None:
        return
    account = getattr(cfg, "account_username", "") if cfg is not None else ""
    session_id = f"emp_{account}" if account else None
    title = f"🤖 الموظف @{account}" if account else None
    try:
        from app.db.database import SessionLocal
        from app.services.memory_service import memory_service

        db = SessionLocal()
        try:
            conv = memory_service.get_or_create_conversation(
                db=db, user_id=int(user_id), session_id=session_id, title=title)
            memory_service.add_message(
                db=db, conversation_id=conv.id, role="assistant",
                content=content, intent="aeos_employee_data", agent="AEOS",
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.debug("persist employee data message skipped", exc_info=True)
    try:
        from app.services import realtime_bus

        # session_id/account يوجّهان الواجهة للمحادثة الصحيحة — بدونهما تختلط
        # رسائل الموظفين المتعددين في المحادثة المفتوحة أياً كانت
        realtime_bus.publish_chat(user_id, {
            "type": "assistant_message",
            "message": content,
            "account": account or None,
            "session_id": session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:  # noqa: BLE001
        logger.debug("push employee data message skipped", exc_info=True)
