"""
Concrete memory layers.

- ``WorkingMemory`` is in-process and ephemeral (lives only for the runtime).
- The persistent layers share a SQL-backed store (``EmployeeMemoryRecord``),
  differing only in their ``layer_type`` and recall strategy.
- ``SemanticMemory`` supports lightweight lexical ranking now and can be upgraded
  to vector search later without changing the interface.
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from app.db.database import SessionLocal
from app.db.models import EmployeeMemoryRecord

from .base import IMemoryLayer, MemoryLayerType, MemoryRecord


class WorkingMemory(IMemoryLayer):
    """Ephemeral scratchpad shared within a single runtime (per-process)."""

    layer_type = MemoryLayerType.WORKING

    def __init__(self) -> None:
        self._store: Dict[str, List[MemoryRecord]] = defaultdict(list)
        self._lock = threading.RLock()

    def store(self, employee_id: str, record: MemoryRecord) -> None:
        with self._lock:
            self._store[employee_id].append(record)

    def recall(self, employee_id: str, query: str = "", limit: int = 10) -> List[MemoryRecord]:
        with self._lock:
            items = list(self._store.get(employee_id, ()))
        return items[-limit:]

    def clear(self, employee_id: str) -> None:
        with self._lock:
            self._store.pop(employee_id, None)


class _SqlMemoryLayer(IMemoryLayer):
    """Base for persistent layers backed by EmployeeMemoryRecord."""

    layer_type: MemoryLayerType

    def __init__(self, session_factory=SessionLocal) -> None:
        self._session_factory = session_factory

    def store(self, employee_id: str, record: MemoryRecord) -> None:
        db = self._session_factory()
        try:
            row = EmployeeMemoryRecord(
                employee_id=employee_id,
                layer=self.layer_type.value,
                key=record.key,
                content=record.content,
                importance=record.importance,
                extra_metadata=json.dumps(record.metadata, ensure_ascii=False),
            )
            db.add(row)
            db.commit()
        finally:
            db.close()

    def _row_to_record(self, row: EmployeeMemoryRecord) -> MemoryRecord:
        meta = {}
        try:
            meta = json.loads(row.extra_metadata) if row.extra_metadata else {}
        except Exception:  # noqa: BLE001
            meta = {}
        return MemoryRecord(
            layer=self.layer_type,
            content=row.content,
            key=row.key,
            importance=row.importance or 1,
            metadata=meta,
            created_at=row.created_at or datetime.utcnow(),
        )

    def recall(self, employee_id: str, query: str = "", limit: int = 10) -> List[MemoryRecord]:
        db = self._session_factory()
        try:
            q = (
                db.query(EmployeeMemoryRecord)
                .filter(EmployeeMemoryRecord.employee_id == employee_id)
                .filter(EmployeeMemoryRecord.layer == self.layer_type.value)
            )
            if query:
                q = q.filter(EmployeeMemoryRecord.content.like(f"%{query}%"))
            rows = q.order_by(EmployeeMemoryRecord.created_at.desc()).limit(limit).all()
            return [self._row_to_record(r) for r in rows]
        finally:
            db.close()

    def clear(self, employee_id: str) -> None:
        db = self._session_factory()
        try:
            db.query(EmployeeMemoryRecord).filter(
                EmployeeMemoryRecord.employee_id == employee_id,
                EmployeeMemoryRecord.layer == self.layer_type.value,
            ).delete()
            db.commit()
        finally:
            db.close()


class ConversationMemory(_SqlMemoryLayer):
    layer_type = MemoryLayerType.CONVERSATION


class LongTermMemory(_SqlMemoryLayer):
    layer_type = MemoryLayerType.LONG_TERM


class ReflectionMemory(_SqlMemoryLayer):
    layer_type = MemoryLayerType.REFLECTION


class SemanticMemory(_SqlMemoryLayer):
    """Knowledge retrieval with a lightweight lexical relevance ranking.

    Upgradeable to vector similarity by populating the ``embedding`` column and
    overriding :meth:`recall` — the interface stays the same.
    """

    layer_type = MemoryLayerType.SEMANTIC

    def recall(self, employee_id: str, query: str = "", limit: int = 10) -> List[MemoryRecord]:
        db = self._session_factory()
        try:
            rows = (
                db.query(EmployeeMemoryRecord)
                .filter(EmployeeMemoryRecord.employee_id == employee_id)
                .filter(EmployeeMemoryRecord.layer == self.layer_type.value)
                .order_by(EmployeeMemoryRecord.created_at.desc())
                .limit(200)
                .all()
            )
        finally:
            db.close()

        records = [self._row_to_record(r) for r in rows]
        if not query:
            return records[:limit]

        terms = {t for t in query.lower().split() if len(t) > 2}
        if not terms:
            return records[:limit]

        def score(rec: MemoryRecord) -> float:
            text = rec.content.lower()
            hits = sum(text.count(t) for t in terms)
            return hits + rec.importance * 0.5

        ranked = sorted(records, key=score, reverse=True)
        return [r for r in ranked if score(r) > 0][:limit]
