"""
SQLAlchemy implementation of :class:`IEmployeeRepository`.

Maps the framework-agnostic :class:`EmployeeConfig` domain model to/from the
``AIEmployee`` ORM row. The rest of the platform depends only on the interface,
so this backend can be swapped for Postgres/Mongo without ripple effects.
"""
from __future__ import annotations

import json
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.database import SessionLocal
from app.db.models import AIEmployee

from ..core.interfaces import IEmployeeRepository
from .models import (
    EmployeeConfig,
    EmployeeStatus,
    Goal,
    Persona,
    RuntimeState,
    SchedulerConfig,
)


def _loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:  # noqa: BLE001
        return default


class SqlEmployeeRepository(IEmployeeRepository):
    """Employee persistence backed by SQLAlchemy/SQLite."""

    def __init__(self, session_factory=SessionLocal) -> None:
        self._session_factory = session_factory
        self._save_lock = threading.RLock()

    # ---- mapping helpers ------------------------------------------------
    def _to_domain(self, row: AIEmployee) -> EmployeeConfig:
        config = EmployeeConfig(
            id=row.id,
            user_id=row.user_id,
            platform=row.platform,
            account_username=row.account_username,
            cookie_label=row.cookie_label or "",
            status=EmployeeStatus(row.status),
            persona=Persona.from_dict(_loads(row.persona_json, {})),
            goals=[Goal.from_dict(g) for g in _loads(row.goals_json, [])],
            knowledge=_loads(row.knowledge_json, []),
            installed_skills=_loads(row.skills_json, []),
            mcp_servers=_loads(row.mcp_servers_json, []),
            business_rules=_loads(row.business_rules_json, {}),
            scheduler=SchedulerConfig.from_dict(_loads(row.scheduler_json, {})),
            runtime_state=RuntimeState.from_dict(_loads(row.runtime_state_json, {})),
            created_at=row.created_at or datetime.now(timezone.utc),
            updated_at=row.updated_at or datetime.now(timezone.utc),
        )
        config._custom_skills_baseline = deepcopy(config.business_rules.get("custom_skills", []))
        return config

    def _merge_skill_rules(self, row: AIEmployee, cfg: EmployeeConfig) -> Dict[str, Any]:
        rules = dict(cfg.business_rules)
        baseline = getattr(cfg, "_custom_skills_baseline", None)
        if baseline is None:
            return rules
        before = {s["id"]: s for s in baseline if isinstance(s, dict) and s.get("id")}
        after = {s["id"]: s for s in rules.get("custom_skills", [])
                 if isinstance(s, dict) and s.get("id")}
        stored = _loads(row.business_rules_json, {}).get("custom_skills", [])
        merged = {s["id"]: s for s in stored if isinstance(s, dict) and s.get("id")}
        for skill_id in before.keys() - after.keys():
            merged.pop(skill_id, None)
        for skill_id, spec in after.items():
            if before.get(skill_id) != spec:
                merged[skill_id] = spec
        if len(merged) > 12:
            raise ValueError("بلغ الموظف الحد الأقصى للمهارات المخصصة (12).")
        if merged or "custom_skills" in rules or stored:
            rules["custom_skills"] = list(merged.values())
        return rules

    def _apply(self, row: AIEmployee, cfg: EmployeeConfig) -> None:
        row.user_id = cfg.user_id
        row.platform = cfg.platform
        row.account_username = cfg.account_username
        row.cookie_label = cfg.cookie_label
        row.status = cfg.status.value
        row.persona_json = json.dumps(cfg.persona.to_dict(), ensure_ascii=False)
        row.goals_json = json.dumps([g.to_dict() for g in cfg.goals], ensure_ascii=False)
        row.knowledge_json = json.dumps(cfg.knowledge, ensure_ascii=False)
        row.skills_json = json.dumps(cfg.installed_skills, ensure_ascii=False)
        row.mcp_servers_json = json.dumps(cfg.mcp_servers, ensure_ascii=False)
        row.business_rules_json = json.dumps(self._merge_skill_rules(row, cfg), ensure_ascii=False)
        row.scheduler_json = json.dumps(cfg.scheduler.to_dict(), ensure_ascii=False)
        row.runtime_state_json = json.dumps(cfg.runtime_state.to_dict(), ensure_ascii=False)
        row.next_wake_at = cfg.scheduler.next_wake_at
        row.last_cycle_at = cfg.runtime_state.last_cycle_at

    # ---- IEmployeeRepository -------------------------------------------
    def get(self, employee_id: str) -> Optional[EmployeeConfig]:
        db = self._session_factory()
        try:
            row = db.query(AIEmployee).filter(AIEmployee.id == employee_id).first()
            return self._to_domain(row) if row else None
        finally:
            db.close()

    def get_by_account(self, platform: str, account_username: str) -> Optional[EmployeeConfig]:
        db = self._session_factory()
        try:
            row = (
                db.query(AIEmployee)
                .filter(AIEmployee.platform == platform)
                .filter(AIEmployee.account_username == account_username)
                .first()
            )
            return self._to_domain(row) if row else None
        finally:
            db.close()

    def list_all(self) -> List[EmployeeConfig]:
        db = self._session_factory()
        try:
            rows = db.query(AIEmployee).all()
            return [self._to_domain(r) for r in rows]
        finally:
            db.close()

    def list_active(self) -> List[EmployeeConfig]:
        db = self._session_factory()
        try:
            rows = db.query(AIEmployee).filter(AIEmployee.status == "active").all()
            return [self._to_domain(r) for r in rows]
        finally:
            db.close()

    def save(self, config: EmployeeConfig) -> EmployeeConfig:
        with self._save_lock:
            return self._save(config)

    def _save(self, config: EmployeeConfig) -> EmployeeConfig:
        db = self._session_factory()
        try:
            if not config.id:
                config.id = uuid.uuid4().hex
            row = db.query(AIEmployee).filter(AIEmployee.id == config.id).first()
            if row is None:
                row = AIEmployee(id=config.id, created_at=datetime.utcnow())
                db.add(row)
            self._apply(row, config)
            db.commit()
            db.refresh(row)
            saved = self._to_domain(row)
            if "custom_skills" in saved.business_rules:
                config.business_rules["custom_skills"] = deepcopy(saved.business_rules["custom_skills"])
            config._custom_skills_baseline = deepcopy(saved._custom_skills_baseline)
            return saved
        finally:
            db.close()

    def delete(self, employee_id: str) -> bool:
        """Permanently remove an employee and EVERYTHING it owns, in one transaction.

        كان يحذف السجلات فقط: ذاكرته بطبقاتها (``employee_memories``) وذاكرته طويلة
        المدى (``agent_memories`` بنطاق ``employee:<id>``) وطابور مهامه تبقى يتيمة —
        بيانات لمالك لم يعد يملك الموظف، ولا واجهة تصل إليها لحذفها.
        """
        from app.db.models import (
            AgentMemory, EmployeeMemoryRecord, ExecutionJobRecord, ExecutionLogRecord,
            TaskCheckpoint,
        )

        db = self._session_factory()
        try:
            row = db.query(AIEmployee).filter(AIEmployee.id == employee_id).first()
            if not row:
                return False
            for model in (ExecutionLogRecord, EmployeeMemoryRecord, ExecutionJobRecord, TaskCheckpoint):
                db.query(model).filter(model.employee_id == employee_id).delete(
                    synchronize_session=False)
            db.query(AgentMemory).filter(AgentMemory.scope == f"employee:{employee_id}").delete(
                synchronize_session=False)
            db.delete(row)
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def update_runtime_state(self, employee_id: str, state: Dict[str, Any]) -> None:
        db = self._session_factory()
        try:
            row = db.query(AIEmployee).filter(AIEmployee.id == employee_id).first()
            if not row:
                return
            row.runtime_state_json = json.dumps(state, ensure_ascii=False)
            lca = state.get("last_cycle_at")
            if lca:
                try:
                    row.last_cycle_at = datetime.fromisoformat(lca)
                except Exception:  # noqa: BLE001
                    pass
            db.commit()
        finally:
            db.close()

    def set_next_wake(self, employee_id: str, when: datetime) -> None:
        db = self._session_factory()
        try:
            row = db.query(AIEmployee).filter(AIEmployee.id == employee_id).first()
            if not row:
                return
            row.next_wake_at = when
            sched = _loads(row.scheduler_json, {})
            sched["next_wake_at"] = when.isoformat()
            row.scheduler_json = json.dumps(sched, ensure_ascii=False)
            db.commit()
        finally:
            db.close()

    def list_due(self, now_iso: str) -> List[EmployeeConfig]:
        now = datetime.fromisoformat(now_iso)
        db = self._session_factory()
        try:
            rows = (
                db.query(AIEmployee)
                .filter(AIEmployee.status == "active")
                .filter(AIEmployee.next_wake_at.isnot(None))
                .filter(AIEmployee.next_wake_at <= now)
                .all()
            )
            return [self._to_domain(r) for r in rows]
        finally:
            db.close()
