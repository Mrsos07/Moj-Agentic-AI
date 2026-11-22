"""
الطابور الدائم (AEOS_RUNTIME=durable): جدول ``execution_jobs`` هو الطابور والحالة معاً.

الضمانات (كلها مُختبرة في verify_runtime.py على SQLite وPostgreSQL):

* **الاستلام ذرّي**: ``claim`` يغيّر QUEUED→LEASED بجملة UPDATE مشروطة بالحالة (CAS)؛ عاملان لا يستلمان
  المهمة نفسها. ``ux_execution_jobs_one_active`` يمنع دورتين نشطتين للموظف نفسه حتى عبر العمليات.
* **السياج (fencing)**: كل استلام يزيد ``lease_token``. كل كتابة لاحقة من العامل (نبض، بدء، إنهاء، سجل
  الأفعال) مشروطة بالرمز؛ عامل فقد عقده (انتهى، أُعيد لغيره) يحصل على ``StaleLease`` ولا يغيّر شيئاً.
* **الاستعادة**: ``reclaim_expired`` يأخذ العقود المنتهية. لم تبدأ، أو بدأت بلا أي أثر خارجي (ومسموح
  بالإعادة) ⇒ تعود للطابور. بدأت ولها أثر ⇒ لا إعادة تلقائية (قرار المالك: الاستئناف يدوي) — FAILED
  ونقطة استعادتها «مقطوعة»، وما بقي INTENT في سجل الأفعال يصير UNKNOWN.
* **إعادة المحاولة**: تأخير أسّي بتذبذب، وبعد ``max_attempts`` ⇒ ``dead_letter`` (DLQ).
* **العدالة والحصص**: الأولوية أولاً، ثم المالك الأقل دورات نشطة؛ لا يتجاوز مالك ``tenant_max_active``.
* **الضغط العكسي**: فوق ``queue_max_depth`` تُسقط المهام القابلة للإسقاط (المجدولة: الدورة التالية تعيدها)
  وتُرفض غيرها بـ ``QueueFull``.
* **حدود المهام الفرعية**: العمق، عدد الأبناء، حجم الشجرة، ومنع الحلقة (الموظف نفسه بالحدث نفسه في
  سلسلة الآباء). الأب يُستنتج تلقائياً من ``CURRENT_JOB`` (المهمة التي ينفّذها هذا الخيط).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db import database as _DB
from app.db.models import ActionJournalRecord, DistributedLock, ExecutionJobRecord as J

from ..core.events import Event, EventType
from ..core.interfaces import IJobQueue
from .config import WorkerConfig
from .states import ACTIVE, JobState as S, check, sources_of

logger = logging.getLogger("aeos.runtime.queue")


class StaleLease(RuntimeError):
    """العقد لم يعد لهذا العامل (انتهى، استُعيد، أُلغي) — لا كتابة."""


class QueueFull(RuntimeError):
    """الطابور فوق حدّه — الضغط العكسي."""


class TaskLimitExceeded(RuntimeError):
    """تجاوز حدود المهام الفرعية (العمق/الأبناء/الشجرة) أو حلقة Agent-to-Agent."""


@dataclass
class Lease:
    job_id: str
    token: int
    employee_id: str
    event: Event
    tenant_id: Optional[int] = None
    attempts: int = 0
    max_attempts: int = 3
    depth: int = 0
    root_job_id: Optional[str] = None
    parent_job_id: Optional[str] = None
    deadline_at: Optional[datetime] = None
    meta: Dict[str, Any] = field(default_factory=dict)


#: المهمة التي ينفّذها هذا الخيط الآن — أي إدراج خلالها يصير ابناً لها (حدود A2A تلقائياً)
CURRENT_JOB: ContextVar[Optional[Lease]] = ContextVar("aeos_current_job", default=None)


def _now() -> datetime:
    return datetime.utcnow()


def _event_signature(employee_id: str, event: Event) -> str:
    body = json.dumps({"e": employee_id, "t": event.type.value, "p": event.payload or {}},
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def _event_from_row(row) -> Event:
    try:
        etype = EventType(row.event_type)
    except ValueError:
        etype = EventType.MANUAL
    try:
        payload = json.loads(row.event_payload or "{}") or {}
    except ValueError:
        payload = {}
    return Event(etype, employee_id=row.employee_id, payload=payload, source=row.event_source or "queue")


def job_view(row) -> Dict[str, Any]:
    if row is None:
        return {}
    try:
        result = json.loads(row.result_json) if row.result_json else None
    except ValueError:
        result = None
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    return {"job_id": row.job_id, "employee_id": row.employee_id, "tenant_id": row.tenant_id,
            "event_type": row.event_type, "status": row.status, "priority": row.priority,
            "attempts": row.attempts, "max_attempts": row.max_attempts, "lease_owner": row.lease_owner,
            "lease_token": row.lease_token, "cancel_requested": bool(row.cancel_requested),
            "dead_letter": bool(row.dead_letter), "had_effects": bool(row.had_effects),
            "depth": row.depth, "parent_job_id": row.parent_job_id, "root_job_id": row.root_job_id,
            "error": row.error_message, "result": result, "created_at": iso(row.created_at),
            "started_at": iso(row.started_at), "finished_at": iso(row.finished_at),
            "available_at": iso(row.available_at), "lease_expires_at": iso(row.lease_expires_at)}


class DbJobQueue(IJobQueue):
    def __init__(self, cfg: Optional[WorkerConfig] = None,
                 session_factory: Optional[Callable] = None) -> None:
        self.cfg = cfg or WorkerConfig.from_env()
        self._sf = session_factory or (lambda: _DB.SessionLocal())

    # ───────────────────────── إنتاج ─────────────────────────
    def submit(self, employee_id: str, event: Event, *, priority: int = 3,
               idempotency_key: Optional[str] = None, tenant_id: Optional[int] = None,
               deadline_seconds: Optional[float] = None, delay_seconds: float = 0,
               max_attempts: Optional[int] = None, droppable: bool = False,
               parent: Optional[Lease] = None) -> Tuple[Optional[str], bool]:
        """أدرج مهمة. يعيد (job_id، أُنشئت؟). مفتاح منع التكرار الموجود ⇒ المهمة القائمة.
        ``droppable`` (المجدولة): تُدمج مع مهمة منتظرة للموظف نفسه وتُسقط تحت الضغط بدل الرفض."""
        parent = parent if parent is not None else CURRENT_JOB.get()
        db = self._sf()
        try:
            if idempotency_key:
                existing = db.execute(select(J.job_id).where(J.idempotency_key == idempotency_key)).scalar()
                if existing:
                    return existing, False
            if droppable:
                waiting = db.execute(select(J.job_id).where(J.employee_id == employee_id,
                                                            J.status == S.QUEUED.value)).scalar()
                if waiting:
                    return waiting, False
            depth_q = db.execute(select(func.count()).select_from(J).where(J.status == S.QUEUED.value)).scalar()
            if depth_q >= self.cfg.queue_max_depth:
                if droppable:
                    logger.warning("queue full (%s): dropped droppable job for %s", depth_q, employee_id)
                    return None, False
                raise QueueFull(f"الطابور ممتلئ ({depth_q}/{self.cfg.queue_max_depth}) — أعد المحاولة بعد قليل")
            depth, root, parent_id = 0, None, None
            if parent is not None:
                depth, root, parent_id = parent.depth + 1, parent.root_job_id or parent.job_id, parent.job_id
                self._check_task_limits(db, parent, employee_id, event, depth, root)
            now = _now()
            job_id = uuid.uuid4().hex
            row = J(job_id=job_id, employee_id=employee_id, tenant_id=tenant_id,
                    event_type=event.type.value, event_source=(event.source or "")[:500],
                    event_payload=json.dumps(event.payload or {}, ensure_ascii=False, default=str),
                    status=S.QUEUED.value, priority=int(priority), attempts=0,
                    max_attempts=int(max_attempts or self.cfg.max_attempts),
                    idempotency_key=idempotency_key, created_at=now, updated_at=now,
                    available_at=now + timedelta(seconds=delay_seconds),
                    deadline_at=now + timedelta(seconds=deadline_seconds) if deadline_seconds else None,
                    parent_job_id=parent_id, root_job_id=root, depth=depth)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                if idempotency_key:
                    existing = db.execute(select(J.job_id).where(J.idempotency_key == idempotency_key)).scalar()
                    if existing:
                        return existing, False
                raise
            return job_id, True
        finally:
            db.close()

    def _check_task_limits(self, db, parent: Lease, employee_id: str, event: Event,
                           depth: int, root: str) -> None:
        cfg = self.cfg
        if depth > cfg.max_task_depth:
            raise TaskLimitExceeded(f"عمق المهام {depth} > {cfg.max_task_depth}")
        kids = db.execute(select(func.count()).select_from(J).where(J.parent_job_id == parent.job_id)).scalar()
        if kids >= cfg.max_fanout:
            raise TaskLimitExceeded(f"أبناء المهمة {kids} ≥ {cfg.max_fanout}")
        tree = db.execute(select(func.count()).select_from(J).where(J.root_job_id == root)).scalar()
        if tree >= cfg.max_tree:
            raise TaskLimitExceeded(f"شجرة المهام {tree} ≥ {cfg.max_tree}")
        sig = _event_signature(employee_id, event)
        cur: Optional[str] = parent.job_id
        for _ in range(cfg.max_task_depth + 1):
            if not cur:
                break
            row = db.execute(select(J.employee_id, J.event_type, J.event_payload, J.event_source,
                                    J.parent_job_id).where(J.job_id == cur)).first()
            if row is None:
                break
            if _event_signature(row.employee_id, _event_from_row(row)) == sig:
                raise TaskLimitExceeded("حلقة: الموظف نفسه بالمهمة نفسها في سلسلة الآباء")
            cur = row.parent_job_id

    # IJobQueue (توافق مع WakeScheduler والمنتجين القدامى)
    def enqueue(self, job) -> None:
        droppable = job.event.type == EventType.SCHEDULER
        self.submit(job.employee_id, job.event, priority=job.priority, droppable=droppable)

    def dequeue(self, timeout: float = 1.0):  # العمال يستخدمون claim
        return None

    def size(self) -> int:
        db = self._sf()
        try:
            return int(db.execute(select(func.count()).select_from(J).where(J.status == S.QUEUED.value)).scalar())
        finally:
            db.close()

    # ───────────────────────── استهلاك ─────────────────────────
    def claim(self, worker_id: str, now: Optional[datetime] = None) -> Optional[Lease]:
        now = now or _now()
        db = self._sf()
        try:
            cands = db.execute(
                select(J.job_id, J.employee_id, J.tenant_id, J.priority, J.available_at, J.created_at)
                .where(J.status == S.QUEUED.value)
                .where((J.available_at.is_(None)) | (J.available_at <= now))
                .order_by(J.priority, J.available_at, J.created_at).limit(64)).all()
            if not cands:
                return None
            active = db.execute(select(J.employee_id, J.tenant_id)
                                .where(J.status.in_([s.value for s in ACTIVE]))).all()
            busy = {r.employee_id for r in active}
            per_tenant: Dict[Any, int] = {}
            for r in active:
                per_tenant[r.tenant_id] = per_tenant.get(r.tenant_id, 0) + 1
            quota = self.cfg.tenant_max_active
            eligible = [c for c in cands if c.employee_id not in busy
                        and not (quota and c.tenant_id is not None and per_tenant.get(c.tenant_id, 0) >= quota)]
            eligible.sort(key=lambda c: (c.priority, per_tenant.get(c.tenant_id, 0), c.available_at or c.created_at))
            for c in eligible:
                try:
                    n = db.execute(
                        update(J).where(J.job_id == c.job_id, J.status == S.QUEUED.value)
                        .values(status=S.LEASED.value, lease_owner=worker_id, lease_token=J.lease_token + 1,
                                lease_expires_at=now + timedelta(seconds=self.cfg.lease_seconds),
                                heartbeat_at=now, attempts=J.attempts + 1, updated_at=now)
                    ).rowcount
                    db.commit()
                except IntegrityError:          # سبقنا عامل آخر بدورة لهذا الموظف
                    db.rollback()
                    continue
                if n == 1:
                    row = db.get(J, db.execute(select(J.id).where(J.job_id == c.job_id)).scalar())
                    return Lease(job_id=row.job_id, token=row.lease_token, employee_id=row.employee_id,
                                 event=_event_from_row(row), tenant_id=row.tenant_id, attempts=row.attempts,
                                 max_attempts=row.max_attempts, depth=row.depth,
                                 root_job_id=row.root_job_id, parent_job_id=row.parent_job_id,
                                 deadline_at=row.deadline_at)
            return None
        finally:
            db.close()

    def _fenced(self, lease: Lease, target: S, **values) -> None:
        """انتقال مشروط بالحالة المسموحة ورمز السياج — 0 صف ⇒ StaleLease."""
        now = _now()
        db = self._sf()
        try:
            n = db.execute(
                update(J).where(J.job_id == lease.job_id, J.lease_token == lease.token,
                                J.status.in_([s.value for s in sources_of(target)]))
                .values(status=target.value, updated_at=now, **values)).rowcount
            db.commit()
        finally:
            db.close()
        if n != 1:
            raise StaleLease(f"{lease.job_id}: العقد {lease.token} لم يعد صالحاً للانتقال إلى {target.value}")

    def start(self, lease: Lease, task_id: Optional[str] = None) -> None:
        self._fenced(lease, S.RUNNING, started_at=_now(),
                     result_json=json.dumps({"task_id": task_id}) if task_id else None)

    def wait(self, lease: Lease) -> None:
        self._fenced(lease, S.WAITING)

    def resume_running(self, lease: Lease) -> None:
        self._fenced(lease, S.RUNNING)

    def heartbeat(self, lease: Lease) -> bool:
        """مدّد العقد. يعيد True إن طُلب الإلغاء. عقد فُقد ⇒ StaleLease."""
        now = _now()
        db = self._sf()
        try:
            n = db.execute(
                update(J).where(J.job_id == lease.job_id, J.lease_token == lease.token,
                                J.status.in_([s.value for s in ACTIVE]))
                .values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=self.cfg.lease_seconds))
            ).rowcount
            cancel = db.execute(select(J.cancel_requested).where(J.job_id == lease.job_id)).scalar()
            db.commit()
        finally:
            db.close()
        if n != 1:
            raise StaleLease(f"{lease.job_id}: فُقد العقد {lease.token}")
        return bool(cancel)

    def assert_fence(self, db, lease: Lease) -> None:
        """داخل معاملة المستدعي (سجل الأفعال): العقد ما زال لهذا العامل ونشطاً."""
        row = db.execute(select(J.lease_token, J.status, J.cancel_requested)
                         .where(J.job_id == lease.job_id)).first()
        if row is None or row.lease_token != lease.token or row.status not in {s.value for s in ACTIVE}:
            raise StaleLease(f"{lease.job_id}: العقد {lease.token} لم يعد صالحاً — لا أثر خارجي")
        if row.cancel_requested:
            from .budget import Cancelled

            raise Cancelled("طُلب الإلغاء")

    def mark_effects(self, db, lease: Lease) -> None:
        db.execute(update(J).where(J.job_id == lease.job_id, J.lease_token == lease.token)
                   .values(had_effects=True))

    def complete(self, lease: Lease, report: Optional[Dict[str, Any]] = None) -> None:
        self._fenced(lease, S.COMPLETED, finished_at=_now(), lease_owner=None, lease_expires_at=None,
                     result_json=_dump_report(report))

    def cancel_finish(self, lease: Lease, reason: str, report=None) -> None:
        self._fenced(lease, S.CANCELLED, finished_at=_now(), error_message=reason[:1000],
                     lease_owner=None, lease_expires_at=None, result_json=_dump_report(report))

    def timeout_finish(self, lease: Lease, reason: str, report=None) -> None:
        self._fenced(lease, S.TIMED_OUT, finished_at=_now(), error_message=reason[:1000],
                     lease_owner=None, lease_expires_at=None, result_json=_dump_report(report))

    def backoff(self, attempts: int) -> float:
        base = self.cfg.retry_base_seconds * (2 ** max(0, attempts - 1))
        return min(self.cfg.retry_max_seconds, base) * random.uniform(0.8, 1.2)

    def fail(self, lease: Lease, error: str, *, retryable: bool = True, report=None) -> str:
        """فشل: إعادة (بلا أثر خارجي وتحت الحد) أو FAILED، وDLQ عند استنفاد المحاولات. يعيد الحالة."""
        db = self._sf()
        try:
            row = db.execute(select(J.attempts, J.max_attempts, J.had_effects).where(J.job_id == lease.job_id)).first()
        finally:
            db.close()
        attempts, max_attempts, effects = (row.attempts, row.max_attempts, row.had_effects) if row else (99, 0, True)
        if retryable and not effects and attempts < max_attempts:
            self._fenced(lease, S.QUEUED, error_message=error[:1000], lease_owner=None, lease_expires_at=None,
                         available_at=_now() + timedelta(seconds=self.backoff(attempts)))
            return S.QUEUED.value
        self._fenced(lease, S.FAILED, error_message=error[:1000], finished_at=_now(), lease_owner=None,
                     lease_expires_at=None, dead_letter=bool(retryable and not effects),
                     result_json=_dump_report(report))
        return S.FAILED.value

    def release(self, lease: Lease) -> None:
        """إيقاف نظيف قبل البدء: تعود للطابور ولا تُحتسب محاولة."""
        self._fenced(lease, S.QUEUED, lease_owner=None, lease_expires_at=None, attempts=J.attempts - 1)

    # ───────────────────────── الإلغاء ─────────────────────────
    def request_cancel(self, *, job_id: Optional[str] = None, employee_id: Optional[str] = None,
                       reason: str = "cancelled") -> Dict[str, int]:
        """المنتظرة ⇒ CANCELLED فوراً؛ النشطة ⇒ cancel_requested (يصل العامل في النبض/نقطة الفحص التالية)."""
        if not (job_id or employee_id):
            raise ValueError("job_id أو employee_id")
        now = _now()
        db = self._sf()
        try:
            cond = (J.job_id == job_id) if job_id else (J.employee_id == employee_id)
            queued = db.execute(update(J).where(cond, J.status.in_([S.QUEUED.value, S.CREATED.value]))
                                .values(status=S.CANCELLED.value, finished_at=now, updated_at=now,
                                        error_message=reason[:1000])).rowcount
            active = db.execute(update(J).where(cond, J.status.in_([s.value for s in ACTIVE]))
                                .values(cancel_requested=True, updated_at=now)).rowcount
            db.commit()
            return {"cancelled_queued": queued, "signalled_running": active}
        finally:
            db.close()

    # ───────────────────────── الاستعادة ─────────────────────────
    def reclaim_expired(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """عقود انتهت (عامل مات/علق). السياج يُرفع أولاً فلا يكتب العامل القديم بعدها شيئاً."""
        now = now or _now()
        out: List[Dict[str, Any]] = []
        db = self._sf()
        try:
            rows = db.execute(select(J).where(J.status.in_([s.value for s in ACTIVE]),
                                              J.lease_expires_at < now)).scalars().all()
            for row in rows:
                token = row.lease_token
                unknown = db.execute(
                    update(ActionJournalRecord).where(ActionJournalRecord.job_id == row.job_id,
                                                      ActionJournalRecord.status == "INTENT")
                    .values(status="UNKNOWN", updated_at=now,
                            error="انقطع العامل بعد بدء الفعل وقبل تأكيده — لا نعرف هل وقع على X")).rowcount
                effects = bool(row.had_effects or unknown)
                started = row.status != S.LEASED.value
                may_retry = (not started) or (not effects and (row.event_type == EventType.SCHEDULER.value
                                                               or self.cfg.auto_retry_interrupted))
                base = update(J).where(J.job_id == row.job_id, J.lease_token == token)
                common = dict(lease_token=token + 1, lease_owner=None, lease_expires_at=None, updated_at=now)
                if may_retry and row.attempts < row.max_attempts:
                    n = db.execute(base.values(status=S.QUEUED.value, available_at=now + timedelta(
                        seconds=self.backoff(row.attempts)), error_message="عقد منتهٍ — أُعيدت للطابور",
                        **common)).rowcount
                    action = "requeued"
                else:
                    dead = may_retry and row.attempts >= row.max_attempts
                    n = db.execute(base.values(status=S.FAILED.value, finished_at=now, dead_letter=dead,
                                               had_effects=effects,
                                               error_message=("استُنفدت المحاولات" if dead else
                                                              "انقطع التنفيذ بعد أثر خارجي — الاستئناف يدوي"),
                                               **common)).rowcount
                    action = "dead_letter" if dead else "interrupted"
                if n:
                    out.append({"job_id": row.job_id, "employee_id": row.employee_id, "action": action,
                                "unknown_actions": unknown, "task_id": _task_of(row)})
            db.commit()
        finally:
            db.close()
        for item in out:
            if item["action"] == "interrupted" and item.get("task_id"):
                try:
                    from app.services import task_checkpoints as TC

                    TC.reopen(item["task_id"], "انقطع العامل أثناء الدورة — راجع ما نُفّذ ثم استأنف")
                except Exception:  # noqa: BLE001
                    logger.exception("reopen checkpoint failed for %s", item["job_id"])
        if out:
            logger.warning("reclaimed %d expired lease(s): %s", len(out), out)
        return out

    # ───────────────────────── قراءة ─────────────────────────
    def get(self, job_id: str) -> Dict[str, Any]:
        db = self._sf()
        try:
            return job_view(db.execute(select(J).where(J.job_id == job_id)).scalar())
        finally:
            db.close()

    def wait_for(self, job_id: str, timeout: float, poll: float = 0.5) -> Dict[str, Any]:
        end = time.monotonic() + timeout
        while True:
            view = self.get(job_id)
            if not view or S(view["status"]) in (S.COMPLETED, S.FAILED, S.CANCELLED, S.TIMED_OUT):
                return view
            if time.monotonic() >= end:
                return view
            time.sleep(poll)

    def stats(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        now = now or _now()
        db = self._sf()
        try:
            by_state = dict(db.execute(select(J.status, func.count()).group_by(J.status)).all())
            oldest = db.execute(select(func.min(J.available_at)).where(J.status == S.QUEUED.value,
                                                                       J.available_at <= now)).scalar()
            dlq = db.execute(select(func.count()).select_from(J).where(J.dead_letter.is_(True))).scalar()
            unknown = db.execute(select(func.count()).select_from(ActionJournalRecord)
                                 .where(ActionJournalRecord.status == "UNKNOWN")).scalar()
        finally:
            db.close()
        return {"by_state": by_state, "queue_lag_s": round((now - oldest).total_seconds(), 1) if oldest else 0.0,
                "dead_letter": int(dlq or 0), "unknown_actions": int(unknown or 0)}


def _task_of(row) -> Optional[str]:
    try:
        return (json.loads(row.result_json) or {}).get("task_id") if row.result_json else None
    except (ValueError, AttributeError):
        return None


def _dump_report(report) -> Optional[str]:
    if report is None:
        return None
    text = json.dumps(report, ensure_ascii=False, default=str)
    if len(text) > 500_000:   # تقرير ضخم (ملاحظات خام): يُحفظ ملخّصه فقط
        keep = {k: report.get(k) for k in ("cycle_id", "employee_id", "results", "error", "skipped", "reason",
                                           "directed_note", "guard_report", "phase_errors", "runtime")}
        text = json.dumps(keep, ensure_ascii=False, default=str)
    return text


# ───────────────────────── قفل موزّع ─────────────────────────
class DbLock:
    """قفل بعقد ورمز سياج متزايد في ``aeos_locks`` (قيادة المجدول عبر العمليات)."""

    def __init__(self, name: str, session_factory: Optional[Callable] = None) -> None:
        self.name = name
        self._sf = session_factory or (lambda: _DB.SessionLocal())

    def acquire(self, owner: str, ttl_seconds: float, now: Optional[datetime] = None) -> Optional[int]:
        """يعيد رمز السياج إن صار (أو بقي) القفل لـ``owner``، وإلا None."""
        now = now or _now()
        exp = now + timedelta(seconds=ttl_seconds)
        db = self._sf()
        try:
            n = db.execute(update(DistributedLock).where(
                DistributedLock.name == self.name,
                (DistributedLock.owner == owner) | (DistributedLock.expires_at.is_(None))
                | (DistributedLock.expires_at < now))
                .values(owner=owner, token=DistributedLock.token + 1, expires_at=exp)).rowcount
            if n == 0:
                exists = db.execute(select(DistributedLock.name).where(DistributedLock.name == self.name)).scalar()
                if exists:
                    db.rollback()
                    return None
                db.add(DistributedLock(name=self.name, owner=owner, token=1, expires_at=exp))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                return None
            return int(db.execute(select(DistributedLock.token).where(DistributedLock.name == self.name)).scalar())
        finally:
            db.close()

    def release(self, owner: str) -> None:
        db = self._sf()
        try:
            db.execute(update(DistributedLock).where(DistributedLock.name == self.name,
                                                     DistributedLock.owner == owner)
                       .values(owner=None, expires_at=None))
            db.commit()
        finally:
            db.close()

    def holder(self) -> Optional[Dict[str, Any]]:
        db = self._sf()
        try:
            r = db.execute(select(DistributedLock).where(DistributedLock.name == self.name)).scalar()
            return {"owner": r.owner, "token": r.token, "expires_at": r.expires_at} if r else None
        finally:
            db.close()
