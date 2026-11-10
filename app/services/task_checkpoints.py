"""
task_checkpoints — نقاط استعادة للمهام الجارية + صندوق صادر للرسائل الحيّة.

لماذا؟ مهمة الشات كانت تُنفَّذ داخل حلقة السوكت نفسها، ودورة الموظف لا تحفظ شيئاً
حتى نهايتها. فانقطاع الاتصال كان يُضيع الرد (يُحفظ في المحادثة لكن لا يصل للشاشة)،
وإعادة تشغيل الخادم كانت تُضيع المهمة كلها بلا خبر ولا معرفة بما نُفّذ على X.

طبقتان مستقلتان، كلتاهما في قاعدة البيانات (تبقى بعد إعادة التشغيل):

1. ``TaskCheckpoint``: حالة كل مهمة (running/succeeded/failed/interrupted/resumed/cancelled)
   ومرحلتها وما يلزم لإعادة تشغيلها، وللدورات: الأفعال التي نجحت على X.
   عند الإقلاع تصير كل ``running`` ← ``interrupted``، ولا يُعاد شيء تلقائياً
   (قرار المالك): المستخدم يرى المهمة المقطوعة ويضغط «استئناف» أو «تجاهل».
2. ``RealtimeOutbox``: كل رسالة مهمة دُفعت للمستخدم برقم تسلسلي متزايد؛ المتصفح
   يطلب عند عودة الاتصال ما بعد آخر رقم رآه.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.db import database as _DB
from app.db.models import RealtimeOutbox, TaskCheckpoint

logger = logging.getLogger("app.task_checkpoints")

RUNNING, SUCCEEDED, FAILED = "running", "succeeded", "failed"
INTERRUPTED, RESUMED, CANCELLED = "interrupted", "resumed", "cancelled"
TERMINAL = {SUCCEEDED, FAILED, RESUMED, CANCELLED}

#: أنواع الرسائل التي تُحفظ للإعادة. typing/pong/task_update لا: حالة المهام تصل كاملة
#: في لقطة الاستئناف، وتخزين مؤشر الكتابة يعيد «يكتب…» قديماً.
REPLAYABLE = frozenset({"assistant_message", "error"})

_OUTBOX_KEEP = timedelta(hours=48)
_OUTBOX_MAX_REPLAY = 300
_TASK_KEEP = timedelta(days=7)
_MAX_ACTIONS = 100
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

PHASE_LABELS = {
    "observe": "المراقبة", "topic_intel": "تحليل المواضيع", "performance": "قياس الأداء",
    "emotion": "الحالة والذاكرة", "analyze": "التحليل", "think": "التفكير",
    "strategy": "الاستراتيجية", "prioritize": "ترتيب الأولويات", "plan": "التخطيط",
    "decide": "القرار", "guard": "مراجعة الشخصية", "execute": "التنفيذ على X",
    "evaluate": "التقييم", "learn": "التعلّم", "critique": "النقد الذاتي",
    "graph_memory": "الذاكرة المعرفية", "support": "طلب الدعم", "save": "الحفظ",
}

_inserts = 0
_prune_lock = threading.Lock()


def _session():
    return _DB.SessionLocal()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(text: Optional[str], default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def _uid(user_id: Any) -> Optional[int]:
    try:
        return int(user_id) if user_id is not None else None
    except (TypeError, ValueError):
        return None


def clean_id(raw: Any) -> str:
    """معرّف المهمة من المتصفح (مفتاح منع التكرار) أو معرّف جديد إن كان غير صالح."""
    raw = str(raw or "")
    return raw if _ID_RE.match(raw) else uuid.uuid4().hex


def cycle_task_id(cycle_id: str) -> str:
    return f"cycle_{cycle_id}"


# ─────────────────────────── views ───────────────────────────
def _view(row: TaskCheckpoint) -> Dict[str, Any]:
    state = _loads(row.state, {})
    phases = state.get("phases") or []
    total = state.get("total_phases") or 0
    view = {
        "id": row.id, "kind": row.kind, "status": row.status, "title": row.title or "",
        "phase": row.phase, "phase_label": PHASE_LABELS.get(row.phase or "", row.phase or ""),
        "employee_id": row.employee_id, "session_id": row.session_id,
        "attempts": row.attempts or 1, "error": row.error,
        "done_actions": len(state.get("actions") or []),
        "started_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "finished_at": row.finished_at.isoformat() + "Z" if row.finished_at else None,
    }
    if row.kind == "cycle" and total and row.status == RUNNING:
        view["progress"] = min(99, int(len(phases) * 100 / total))
    return view


# ─────────────────────────── tasks ───────────────────────────
def begin(task_id: str, kind: str, user_id: Any, *, title: str = "",
          employee_id: Optional[str] = None, session_id: Optional[str] = None,
          request: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], bool]:
    """سجّل مهمة جديدة ``running``. إن وُجد المعرّف نفسه لهذا المستخدم ← (المهمة، False):
    إعادة إرسال الرسالة بعد انقطاع لا تنفّذها مرة ثانية. معرّف يخص مستخدماً آخر
    لا يكشف شيئاً: تُسجَّل المهمة بمعرّف جديد."""
    uid = _uid(user_id)
    db = _session()
    try:
        row = db.get(TaskCheckpoint, task_id)
        if row is not None:
            if row.user_id == uid:
                return _view(row), False
            task_id = uuid.uuid4().hex
        row = TaskCheckpoint(id=task_id, kind=kind, user_id=uid, employee_id=employee_id,
                             session_id=session_id, title=(title or "")[:500], status=RUNNING,
                             request=_dumps(request or {}), state=_dumps({}))
        db.add(row)
        db.commit()
        return _view(row), True
    finally:
        db.close()


def _update(task_id: str, fn) -> Optional[Dict[str, Any]]:
    db = _session()
    try:
        row = db.get(TaskCheckpoint, task_id)
        if row is None:
            return None
        fn(row)
        row.updated_at = datetime.utcnow()
        db.commit()
        return _view(row)
    except Exception:  # noqa: BLE001 - نقطة الاستعادة لا تُسقط المهمة نفسها أبداً
        db.rollback()
        logger.exception("checkpoint update failed for %s", task_id)
        return None
    finally:
        db.close()


def phase(task_id: str, name: str, total: int = 0) -> Optional[Dict[str, Any]]:
    """اكتملت مرحلة: تُضاف لقائمة المراحل المنجزة."""
    def fn(row):
        state = _loads(row.state, {})
        done = state.setdefault("phases", [])
        if name not in done:
            done.append(name)
        if total:
            state["total_phases"] = total
        row.phase = name
        row.state = _dumps(state)
    return _update(task_id, fn)


def record_action(task_id: str, key: str, info: Optional[Dict[str, Any]] = None) -> None:
    """فعل نجح على X — يُحفظ فوراً (لا في نهاية الدورة) فلا يتكرر عند الاستئناف."""
    def fn(row):
        state = _loads(row.state, {})
        actions = state.setdefault("actions", [])
        actions.append({"key": key, "day": datetime.utcnow().strftime("%Y-%m-%d"), **(info or {})})
        state["actions"] = actions[-_MAX_ACTIONS:]
        row.state = _dumps(state)
    _update(task_id, fn)


def finish(task_id: str, status: str, error: Optional[str] = None) -> Optional[Dict[str, Any]]:
    def fn(row):
        row.status = status
        row.error = (error or None) and str(error)[:1000]
        row.finished_at = datetime.utcnow()
    return _update(task_id, fn)


def claim_resume(task_id: str, user_id: Any, new_status: str) -> Optional[Dict[str, Any]]:
    """استئناف مهمة مقطوعة يملكها المستخدم — ذرّي: ضغطتان متتاليتان (أو تبويبان) لا
    تشغّلان المهمة مرتين. يعيد المهمة بطلبها وحالتها، أو None."""
    uid = _uid(user_id)
    db = _session()
    try:
        values = {"status": new_status, "updated_at": datetime.utcnow(), "error": None}
        if new_status == RUNNING:
            values.update(attempts=TaskCheckpoint.attempts + 1, finished_at=None)
        else:
            values["finished_at"] = datetime.utcnow()
        n = (db.query(TaskCheckpoint)
             .filter(TaskCheckpoint.id == task_id, TaskCheckpoint.user_id == uid,
                     TaskCheckpoint.status == INTERRUPTED)
             .update(values, synchronize_session=False))
        db.commit()
        return get(task_id, with_internals=True) if n else None
    finally:
        db.close()


def reopen(task_id: str, reason: str) -> Optional[Dict[str, Any]]:
    """تعذّر الاستئناف (الموظف موقوف/مشغول) ← تعود ``interrupted`` ليحاول المالك لاحقاً."""
    def fn(row):
        row.status = INTERRUPTED
        row.error = reason[:1000]
        row.finished_at = None
    return _update(task_id, fn)


def get(task_id: str, with_internals: bool = False) -> Optional[Dict[str, Any]]:
    db = _session()
    try:
        row = db.get(TaskCheckpoint, task_id)
        if row is None:
            return None
        view = _view(row)
        view["user_id"] = row.user_id
        if with_internals:
            view["request"] = _loads(row.request, {})
            view["state"] = _loads(row.state, {})
        return view
    finally:
        db.close()


def for_user(user_id: Any, limit: int = 12) -> List[Dict[str, Any]]:
    """لقطة الاستئناف: كل الجاري والمقطوع، ثم أحدث المنتهي (الأحدث أولاً)."""
    uid = _uid(user_id)
    if uid is None:
        return []
    db = _session()
    try:
        q = db.query(TaskCheckpoint).filter(TaskCheckpoint.user_id == uid)
        live = (q.filter(TaskCheckpoint.status.in_([RUNNING, INTERRUPTED]))
                .order_by(TaskCheckpoint.created_at.desc()).limit(50).all())
        recent = (q.filter(TaskCheckpoint.status.in_(list(TERMINAL)))
                  .order_by(TaskCheckpoint.updated_at.desc()).limit(limit).all())
        return [_view(r) for r in live] + [_view(r) for r in recent[:max(0, limit - len(live))]]
    finally:
        db.close()


def mark_interrupted_on_boot() -> List[Dict[str, Any]]:
    """عند الإقلاع: لا عملية تنفّذ أي مهمة الآن، فكل ``running`` انقطعت فعلاً."""
    db = _session()
    try:
        rows = db.query(TaskCheckpoint).filter(TaskCheckpoint.status == RUNNING).all()
        out = []
        for row in rows:
            row.status = INTERRUPTED
            row.error = "انقطع التنفيذ بإعادة تشغيل الخادم"
            row.updated_at = datetime.utcnow()
            view = _view(row)
            view["user_id"] = row.user_id
            out.append(view)
        cutoff = datetime.utcnow() - _TASK_KEEP
        (db.query(TaskCheckpoint)
         .filter(TaskCheckpoint.status.in_(list(TERMINAL)), TaskCheckpoint.updated_at < cutoff)
         .delete(synchronize_session=False))
        db.commit()
        return out
    finally:
        db.close()


def done_action_keys(task: Optional[Dict[str, Any]]) -> List[str]:
    actions = ((task or {}).get("state") or {}).get("actions") or []
    return [a["key"] for a in actions if a.get("key")]


# ─────────────────────────── outbox ───────────────────────────
def outbox_record(user_id: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    """احفظ رسالة قبل دفعها وأعدها مع ``seq``. أي فشل ⇒ الرسالة كما هي (التسليم الحي أهم)."""
    global _inserts
    uid = _uid(user_id)
    if uid is None or payload.get("type") not in REPLAYABLE or "seq" in payload:
        return payload
    db = _session()
    try:
        row = RealtimeOutbox(user_id=uid, payload=_dumps(payload))
        db.add(row)
        db.commit()
        out = {**payload, "seq": row.id}
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("outbox record failed")
        return payload
    finally:
        db.close()
    _inserts += 1
    if _inserts % 200 == 0:
        prune_outbox()
    return out


def outbox_since(user_id: Any, seq: int, limit: int = _OUTBOX_MAX_REPLAY) -> List[Dict[str, Any]]:
    uid = _uid(user_id)
    if uid is None:
        return []
    db = _session()
    try:
        rows = (db.query(RealtimeOutbox)
                .filter(RealtimeOutbox.user_id == uid, RealtimeOutbox.id > int(seq))
                .order_by(RealtimeOutbox.id.asc()).limit(limit).all())
        return [{**_loads(r.payload, {}), "seq": r.id, "replayed": True} for r in rows]
    finally:
        db.close()


def outbox_latest(user_id: Any) -> int:
    uid = _uid(user_id)
    db = _session()
    try:
        row = (db.query(RealtimeOutbox.id).filter(RealtimeOutbox.user_id == uid)
               .order_by(RealtimeOutbox.id.desc()).first())
        return int(row[0]) if row else 0
    finally:
        db.close()


def prune_outbox(now: Optional[datetime] = None) -> int:
    if not _prune_lock.acquire(blocking=False):
        return 0
    db = _session()
    try:
        cutoff = (now or datetime.utcnow()) - _OUTBOX_KEEP
        n = db.query(RealtimeOutbox).filter(RealtimeOutbox.created_at < cutoff).delete(
            synchronize_session=False)
        db.commit()
        return n
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("outbox prune failed")
        return 0
    finally:
        db.close()
        _prune_lock.release()


def iter_payloads(views: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """ما يُرسل للمتصفح من مهمة: بلا الطلب الخام ولا الحالة الداخلية."""
    return [{k: v for k, v in t.items() if k not in ("request", "state", "user_id")} for t in views]
