"""
متابعة حملة الفريق حتى نهايتها: نتيجة واحدة موحّدة تصل للمالك في المحادثة التي أمر منها.

قيس 2026-11-12: المنسّق يقول «ستصلك الروابط هنا في الشات فور النشر»، لكن ما وصل كان سرد دورة
طويل لكل موظف في محادثته هو (بلا رابط أصلاً)، فالمالك لا يعرف بنظرة واحدة: مَن نشر، وأين الرابط،
ومَن فشل ولماذا. الآن ``plan_campaign`` يفتح حملة بموظفيها، وكل ``dispatch_directed`` يسجّل نتيجته
(الرابط من X أو سبب الفشل)، وعند اكتمال الجميع — أو بعد مهلة — تُرسل خلاصة واحدة.

الحالة في الذاكرة: إعادة تشغيل الخادم تُسقطها، وتغطي ذلك نقاط الاستعادة (المهمة «مقطوعة» + استئناف).
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("aeos.services.campaigns")

#: مهلة الخلاصة الجزئية: موظف لم يُبلغ بعدها (دورة طويلة/انقطاع) يُذكر «لم يصل خبره»
TIMEOUT_SEC = 40 * 60
_KIND = {"publish_thread": "ثريد", "publish_content": "منشور", "quote_post": "اقتباس",
         "reply_to_user": "رد", "retweet_post": "إعادة نشر", "like_post": "إعجاب"}

_lock = threading.Lock()
_CAMPAIGNS: Dict[str, Dict[str, Any]] = {}


def start(user_id: Optional[int], session_id: Optional[str], topic: str, handles: List[str],
          timeout: float = TIMEOUT_SEC) -> str:
    cid = "camp_" + uuid.uuid4().hex[:10]
    with _lock:
        _CAMPAIGNS[cid] = {"user_id": user_id, "session_id": session_id, "topic": topic,
                           "expected": list(dict.fromkeys(handles)), "results": {}, "at": time.time(),
                           "done": False}
    timer = threading.Timer(timeout, _finish, args=(cid,))
    timer.daemon = True
    timer.start()
    return cid


def record(cid: Optional[str], handle: str, ok: bool, url: str = "", detail: str = "",
           kind: str = "") -> None:
    """نتيجة موظف واحد. أول نتيجة لكل موظف هي المعتمدة (الإعادة لا تكتبها مرتين)."""
    if not cid:
        return
    with _lock:
        camp = _CAMPAIGNS.get(cid)
        if not camp or camp["done"] or handle in camp["results"]:
            return
        camp["results"][handle] = {"ok": bool(ok), "url": url or "", "detail": (detail or "")[:200],
                                   "kind": kind or ""}
        complete = set(camp["expected"]) <= set(camp["results"])
    if complete:
        _finish(cid)


def summary(camp: Dict[str, Any]) -> str:
    expected, results = camp["expected"], camp["results"]
    ok = sum(1 for h in expected if (results.get(h) or {}).get("ok"))
    lines = [f"📣 **نتيجة حملة «{camp['topic'][:80]}»** — نُفّذ {ok} من {len(expected)}:"]
    for h in expected:
        r = results.get(h)
        if r is None:
            lines.append(f"- ⏳ @{h}: لم يصل خبره بعد — تابع محادثته (قد تكون دورته ما زالت جارية).")
        elif r["ok"]:
            kind = _KIND.get(r["kind"], "")
            lines.append(f"- ✅ @{h}: {kind + ' ' if kind else ''}{r['url'] or '(الرابط في محادثته)'}")
        else:
            lines.append(f"- ❌ @{h}: {r['detail'] or 'لم ينجح دون سبب موصوف'}")
    if ok < len(expected):
        lines.append("\nللإعادة لمن لم ينجح: اكتب «أعد النشر لـ @…» أو اطلب تعديل الزاوية.")
    return "\n".join(lines)


def _finish(cid: str) -> None:
    with _lock:
        camp = _CAMPAIGNS.get(cid)
        if not camp or camp["done"]:
            return
        camp["done"] = True
        _CAMPAIGNS.pop(cid, None)
    try:
        _deliver(camp, summary(camp))
    except Exception:  # noqa: BLE001 - الخلاصة تحسين؛ نتائج كل موظف في محادثته على أي حال
        logger.exception("campaign summary delivery failed for %s", cid)


def _deliver(camp: Dict[str, Any], text: str) -> None:
    """خزّن الخلاصة في المحادثة التي أمر منها المالك وادفعها حيّة. بلا محادثة معروفة: محادثة
    أول موظف في الحملة (لا محادثة جديدة فارغة لا يراها)."""
    from app.db.database import SessionLocal
    from app.services import realtime_bus
    from app.services.memory_service import memory_service

    user_id = camp.get("user_id")
    if user_id is None:
        return
    session_id = camp.get("session_id") or (f"emp_{camp['expected'][0]}" if camp["expected"] else None)
    db = SessionLocal()
    try:
        from app.aeos.security import gateway
        with gateway.execution(int(user_id), agent=getattr(gateway.current.get(), 'agent', ''), conversation=session_id or ''):
            conv = memory_service.get_or_create_conversation(db=db, user_id=int(user_id), session_id=session_id)
            memory_service.add_message(db=db, conversation_id=conv.id, role="assistant", content=text,
                                       intent="aeos_campaign_result", agent="Coordinator")
            db.commit()
    finally:
        db.close()
    realtime_bus.publish_chat(int(user_id), {
        "type": "assistant_message", "message": text, "session_id": session_id, "account": None,
        "intent": "aeos_campaign_result", "timestamp": datetime.now(timezone.utc).isoformat()})


def active(user_id: Optional[int]) -> List[Dict[str, Any]]:
    """حملات المالك الجارية (للمنسّق: «وش صار في الحملة؟»)."""
    with _lock:
        return [{"topic": c["topic"], "expected": list(c["expected"]),
                 "reported": {h: r["ok"] for h, r in c["results"].items()},
                 "minutes": int((time.time() - c["at"]) // 60)}
                for c in _CAMPAIGNS.values() if c["user_id"] == user_id and not c["done"]]
