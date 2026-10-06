"""
دورة حياة الذاكرة: **ماذا يتذكّر كل وكيل، وأين، ومتى يُمحى؟** — مرجع واحد فوق
أنظمة الذاكرة الثلاثة.

الأنظمة (كلها معزولة بمفتاح المالك):

| النظام | الجدول/المخزن | المفتاح | المحتوى |
|---|---|---|---|
| قصيرة المدى | ``agent_memories`` kind=summary | ``conv:<conversation_id>`` | ملخّص المحادثة المتدحرج |
| طويلة المدى | ``agent_memories`` | ``coordinator:<user_id>`` / ``employee:<id>`` | تفضيلات/تعليمات/حقائق/قرارات/دروس/أحداث |
| طبقات الدورة | ``employee_memories`` | ``employee_id`` | working/conversation/long_term/semantic/reflection |
| الرسم المعرفي | Graphiti/Neo4j | ``group_id`` = الموظف | حقائق زمنية عن الكيانات |

قاعدة المحو: حذف المالك يمحو كل ما يخصه في المعاملة نفسها — لا بيانات يتيمة
لمالك لم يعد موجوداً، ولا معرّف مُعاد استخدامه يرث ذاكرة غيره.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, Optional

logger = logging.getLogger("aeos.memory.lifecycle")


def _session():
    from app.db.database import SessionLocal

    return SessionLocal()


def _kinds(db, scope: str) -> Dict[str, int]:
    from app.db.models import AgentMemory

    return dict(Counter(k for (k,) in db.query(AgentMemory.kind).filter(AgentMemory.scope == scope)))


def employee_overview(cfg) -> Dict[str, Any]:
    """صورة واحدة لكل ذاكرة الموظف عبر الأنظمة كلها."""
    from app.db.models import EmployeeMemoryRecord

    from . import agent_memory as AM

    db = _session()
    try:
        layers = dict(Counter(layer for (layer,) in db.query(EmployeeMemoryRecord.layer)
                              .filter(EmployeeMemoryRecord.employee_id == cfg.id)))
        long_term = _kinds(db, AM.employee_scope(cfg.id))
    finally:
        db.close()
    graph: Dict[str, Any] = {}
    try:
        from ..bootstrap import get_platform

        graph = get_platform().graph_memory.stats(cfg.id) or {}
    except Exception:  # noqa: BLE001 - الرسم اختياري
        logger.debug("graph stats unavailable", exc_info=True)
    return {
        "scope": AM.employee_scope(cfg.id),
        "long_term": {"total": sum(long_term.values()), "by_kind": long_term},
        "cycle_layers": {"total": sum(layers.values()), "by_layer": layers},
        "graph": graph,
        "isolation": "يرى هذا الموظف ذاكرته فقط — لا ذاكرة زملائه ولا ذاكرة المنسّق.",
    }


def coordinator_overview(user_id: int) -> Dict[str, Any]:
    from app.db.models import AgentMemory, Conversation

    from . import agent_memory as AM

    db = _session()
    try:
        conv_ids = [c for (c,) in db.query(Conversation.id).filter(Conversation.user_id == user_id)]
        summaries = (db.query(AgentMemory).filter(
            AgentMemory.kind == "summary",
            AgentMemory.key.in_([AM._summary_key(c) for c in conv_ids] or ["-"])).count())
        long_term = _kinds(db, AM.coordinator_scope(user_id))
    finally:
        db.close()
    return {
        "scope": AM.coordinator_scope(user_id),
        "long_term": {"total": sum(long_term.values()), "by_kind": long_term},
        "short_term": {"conversations": len(conv_ids), "summaries": summaries,
                       "recent_turns_window": AM.SHORT_TERM_TURNS},
        "isolation": "ذاكرة المنسّق لك وحدك ومربوطة بحسابك المسجّل دخوله.",
    }


def purge_user(user_id: int, db=None) -> Dict[str, int]:
    """قبل حذف مستخدم: موظفوه (بكل ذاكرتهم ورسمهم)، محادثاته وملخّصاتها، ذاكرة منسّقه،
    وسجلات حساباته. ملفات الكوكيز تبقى على القرص لكنها تصير بلا مالك (لا يراها إلا المشرف)."""
    from app.db.models import AgentMemory, Conversation, Message, SocialAccount, XAccount

    from . import agent_memory as AM

    counts: Dict[str, int] = {}
    try:
        from ..bootstrap import get_platform

        plat = get_platform()
        mine = [c.id for c in plat.repository.list_all() if c.user_id == user_id]
        counts["employees"] = sum(1 for eid in mine if plat.delete_employee(eid))
    except Exception:  # noqa: BLE001
        logger.exception("purge_user: employee deletion failed for user %s", user_id)
        raise
    own = db is None
    db = db or _session()
    try:
        conv_ids = [c for (c,) in db.query(Conversation.id).filter(Conversation.user_id == user_id)]
        if conv_ids:
            counts["summaries"] = db.query(AgentMemory).filter(
                AgentMemory.kind == "summary",
                AgentMemory.key.in_([AM._summary_key(c) for c in conv_ids])).delete(
                synchronize_session=False)
            counts["messages"] = db.query(Message).filter(Message.conversation_id.in_(conv_ids)).delete(
                synchronize_session=False)
        counts["conversations"] = db.query(Conversation).filter(Conversation.user_id == user_id).delete(
            synchronize_session=False)
        counts["coordinator_memories"] = db.query(AgentMemory).filter(
            AgentMemory.scope == AM.coordinator_scope(user_id)).delete(synchronize_session=False)
        counts["social_accounts"] = db.query(SocialAccount).filter(
            SocialAccount.user_id == user_id).delete(synchronize_session=False)
        counts["x_accounts"] = db.query(XAccount).filter(XAccount.user_id == user_id).delete(
            synchronize_session=False)
        if own:
            db.commit()
        return counts
    except Exception:
        if own:
            db.rollback()
        raise
    finally:
        if own:
            db.close()


def orphan_report(db=None) -> Dict[str, int]:
    """بيانات بلا مالك موجود (للتشخيص وفحص التحقق)."""
    from app.db.models import AgentMemory, AIEmployee, Conversation, EmployeeMemoryRecord, User

    own = db is None
    db = db or _session()
    try:
        users = {u for (u,) in db.query(User.id)}
        emps = {e for (e,) in db.query(AIEmployee.id)}
        convs = {c for (c,) in db.query(Conversation.id)}
        out = Counter()
        for scope, kind, key in db.query(AgentMemory.scope, AgentMemory.kind, AgentMemory.key):
            if scope.startswith("employee:") and scope[9:] not in emps:
                out["agent_memories_dead_employee"] += 1
            elif scope.startswith("coordinator:") and scope[12:].isdigit() and int(scope[12:]) not in users:
                out["agent_memories_dead_user"] += 1
            if kind == "summary" and (key or "").startswith("conv:") and key[5:].isdigit() \
                    and int(key[5:]) not in convs:
                out["summaries_dead_conversation"] += 1
        out["layer_memories_dead_employee"] = sum(
            1 for (e,) in db.query(EmployeeMemoryRecord.employee_id) if e not in emps)
        return dict(out)
    finally:
        if own:
            db.close()
