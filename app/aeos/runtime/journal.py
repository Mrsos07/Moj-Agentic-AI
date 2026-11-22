"""
سجل الأفعال الخارجية (Action Journal): صفّ **قبل** كل فعل على X وصفّ نتيجته **بعده**.

    begin()  ⇒ "proceed" (سُجّل INTENT تحت السياج)  |  "done" (نجح سابقاً)  |  "unknown" (لا نعرف ⇒ لا تكرار)
    finish() ⇒ SUCCEEDED | FAILED (الأداة أكّدت الفشل) | UNKNOWN (استثناء: قد يكون وقع)

``begin`` يتحقق من العقد في المعاملة نفسها (``assert_fence``): عامل فقد عقده لا يبدأ أثراً خارجياً.
``finish`` لا يُسيَّج عمداً: ما وقع على X حقيقة تُسجَّل حتى لو انتهى العقد أثناء الفعل.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db import database as _DB
from app.db.models import ActionJournalRecord as A

logger = logging.getLogger("aeos.runtime.journal")

INTENT, SUCCEEDED, FAILED, UNKNOWN = "INTENT", "SUCCEEDED", "FAILED", "UNKNOWN"


def _key(job_id: str, capability: str, target: str, ordinal: int) -> str:
    raw = f"{job_id}:{capability}:{target}:{ordinal}"
    return raw if len(raw) <= 255 else f"{job_id}:{capability}:{hashlib.sha256(raw.encode()).hexdigest()}"


class ActionJournal:
    def __init__(self, queue, lease, cycle_id: str, session_factory: Optional[Callable] = None) -> None:
        self._queue = queue
        self._lease = lease
        self._cycle_id = cycle_id
        self._sf = session_factory or (lambda: _DB.SessionLocal())
        self._ordinals: Dict[Tuple[str, str], int] = {}

    def begin(self, capability: str, target: str, params: Optional[Dict[str, Any]] = None,
              resume_key: str = "") -> Tuple[str, Optional[int]]:
        n = self._ordinals.get((capability, target), 0) + 1
        self._ordinals[(capability, target)] = n
        key = _key(self._lease.job_id, capability, target or "", n)
        db = self._sf()
        try:
            self._queue.assert_fence(db, self._lease)
            row = db.execute(select(A).where(A.idempotency_key == key)).scalar()
            if row is not None:
                if row.status == SUCCEEDED:
                    return "done", row.id
                if row.status in (INTENT, UNKNOWN):
                    return "unknown", row.id
                row.status, row.fence_token, row.updated_at = INTENT, self._lease.token, datetime.utcnow()
            else:
                row = A(idempotency_key=key, job_id=self._lease.job_id, cycle_id=self._cycle_id,
                        employee_id=self._lease.employee_id, capability=capability, target=target or None,
                        status=INTENT, fence_token=self._lease.token,
                        request_json=json.dumps({"params": params or {}, "resume_key": resume_key},
                                                ensure_ascii=False, default=str))
                db.add(row)
            self._queue.mark_effects(db, self._lease)
            db.commit()
            return "proceed", row.id
        except IntegrityError:
            db.rollback()
            return "unknown", None
        finally:
            db.close()

    def finish(self, entry_id: Optional[int], status: str, output: Any = None, error: str = "") -> None:
        if entry_id is None:
            return
        db = self._sf()
        try:
            db.execute(update(A).where(A.id == entry_id).values(
                status=status, updated_at=datetime.utcnow(), error=(error or None) and str(error)[:2000],
                result_json=json.dumps(output, ensure_ascii=False, default=str)[:20000] if output is not None else None))
            db.commit()
        except Exception:  # noqa: BLE001 - السجل لا يُسقط التنفيذ؛ يبقى INTENT ⇒ UNKNOWN عند الاستعادة
            db.rollback()
            logger.exception("journal finish failed for entry %s", entry_id)
        finally:
            db.close()


def entries(job_id: Optional[str] = None, cycle_id: Optional[str] = None,
            employee_id: Optional[str] = None) -> List[Dict[str, Any]]:
    db = _DB.SessionLocal()
    try:
        q = select(A).order_by(A.id)
        if job_id:
            q = q.where(A.job_id == job_id)
        if cycle_id:
            q = q.where(A.cycle_id == cycle_id)
        if employee_id:
            q = q.where(A.employee_id == employee_id)
        out = []
        for r in db.execute(q).scalars():
            try:
                req = json.loads(r.request_json or "{}")
            except ValueError:
                req = {}
            out.append({"id": r.id, "key": r.idempotency_key, "job_id": r.job_id, "cycle_id": r.cycle_id,
                        "capability": r.capability, "target": r.target, "status": r.status,
                        "fence_token": r.fence_token, "resume_key": req.get("resume_key") or "",
                        "error": r.error})
        return out
    finally:
        db.close()


def unresolved_for_task(task_id: str) -> List[Dict[str, Any]]:
    """أفعال مهمة مقطوعة بلا نتيجة مؤكَّدة (INTENT/UNKNOWN) — الاستئناف يتخطّاها ويبلّغ المالك."""
    if not task_id or not task_id.startswith("cycle_"):
        return []
    return [e for e in entries(cycle_id=task_id[len("cycle_"):]) if e["status"] in (INTENT, UNKNOWN)]
