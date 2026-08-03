"""
AEOS REST API.

Manage AI Employees (CRUD), inspect the platform, and trigger the cognitive
engine (async via workers or a synchronous run for testing). This is a thin
adapter over :class:`AEOSPlatform` — no business logic lives here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..bootstrap import get_platform
from ..employee.models import (
    EmployeeConfig, EmployeeStatus, Goal, Persona, SchedulerConfig,
)

logger = logging.getLogger("aeos.api")

router = APIRouter(prefix="/api/aeos", tags=["AEOS - AI Employees"])


# ─────────────────────────── schemas ───────────────────────────
class PersonaIn(BaseModel):
    display_name: str = ""
    role: str = "Social Media Manager"
    bio: str = ""
    tone: str = "professional"
    language: str = "ar"
    dialect: str = ""
    values: List[str] = []
    dos: List[str] = []
    donts: List[str] = []
    interests: List[str] = []
    instructions: str = ""


class PersonaGenerateIn(BaseModel):
    interests: List[str] = []
    brief: str = ""
    language: str = "ar"
    account_username: str = ""


class PersonaCloneIn(BaseModel):
    """Clone the style of a target X account into a persona proposal."""
    account_username: str             # الحساب المرتبط (مصدر الكوكيز)
    target_handle: str                # يوزر الحساب المراد استنساخه
    limit: int = 30                   # حجم العيّنة (تغريدات + ردود)
    language: str = "ar"


class GoalIn(BaseModel):
    description: str
    metric: str = ""
    target: Optional[float] = None
    priority: int = 3


class EmployeeIn(BaseModel):
    platform: str = "x"
    account_username: str
    cookie_label: str = ""
    user_id: Optional[int] = None
    persona: Optional[PersonaIn] = None
    goals: List[GoalIn] = []
    knowledge: List[str] = []
    installed_skills: List[str] = [
        "daily_content_creator", "community_engager",
        "timeline_engager", "viral_content_creator",
    ]
    mcp_servers: List[str] = [
        "x", "playwright_x", "content", "memory", "news", "browser",
    ]
    business_rules: Dict[str, Any] = {}
    scheduler: Optional[Dict[str, Any]] = None


class TriggerIn(BaseModel):
    event_type: str = "manual"        # manual|mention|reply|notification|news_update|webhook
    payload: Dict[str, Any] = {}
    sync: bool = False                # run synchronously (blocking) if true


class AgentToggleIn(BaseModel):
    """Enable/disable an autonomous agent for a connected account (chatbot toggle)."""
    platform: str = "x"
    account_username: str             # X handle / cookie label
    enabled: bool
    cookie_label: str = ""
    user_id: Optional[int] = None
    interval_minutes: Optional[int] = None
    active_hours_start: Optional[int] = None   # 0..23 (employee timezone)
    active_hours_end: Optional[int] = None     # 0..23 (== start => 24h)
    persona: Optional[PersonaIn] = None
    goals: List[GoalIn] = []
    business_rules: Dict[str, Any] = {}
    installed_skills: Optional[List[str]] = None
    mcp_servers: Optional[List[str]] = None


# ─────────────────────────── endpoints ───────────────────────────
@router.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "platform": "AEOS", **get_platform().stats()}


@router.get("/employees")
def list_employees() -> Dict[str, Any]:
    repo = get_platform().repository
    return {"employees": [e.to_dict() for e in repo.list_active()]}


@router.get("/employees/{employee_id}")
def get_employee(employee_id: str) -> Dict[str, Any]:
    cfg = get_platform().repository.get(employee_id)
    if not cfg:
        raise HTTPException(404, "employee not found")
    return cfg.to_dict()


@router.post("/employees")
def create_employee(body: EmployeeIn) -> Dict[str, Any]:
    platform = get_platform()
    persona = Persona.from_dict(body.persona.dict()) if body.persona else Persona(
        display_name=body.account_username
    )
    goals = [
        Goal(id=uuid.uuid4().hex, description=g.description, metric=g.metric,
             target=g.target, priority=g.priority)
        for g in body.goals
    ]
    cfg = EmployeeConfig(
        id=uuid.uuid4().hex,
        user_id=body.user_id,
        platform=body.platform,
        account_username=body.account_username,
        cookie_label=body.cookie_label or body.account_username,
        status=EmployeeStatus.ACTIVE,
        persona=persona,
        goals=goals,
        knowledge=body.knowledge,
        installed_skills=body.installed_skills,
        mcp_servers=body.mcp_servers,
        business_rules=body.business_rules,
        scheduler=SchedulerConfig.from_dict(body.scheduler or {}),
    )
    saved = platform.repository.save(cfg)
    return {"ok": True, "employee": saved.to_dict()}


@router.patch("/employees/{employee_id}/status")
def set_status(employee_id: str, status: str) -> Dict[str, Any]:
    repo = get_platform().repository
    cfg = repo.get(employee_id)
    if not cfg:
        raise HTTPException(404, "employee not found")
    try:
        cfg.status = EmployeeStatus(status)
    except ValueError:
        raise HTTPException(400, f"invalid status '{status}'")
    repo.save(cfg)
    return {"ok": True, "status": cfg.status.value}


@router.delete("/employees/{employee_id}")
def delete_employee(employee_id: str) -> Dict[str, Any]:
    """Permanently delete an AI employee (stops schedule + removes logs)."""
    platform = get_platform()
    if not platform.repository.get(employee_id):
        raise HTTPException(404, "employee not found")
    ok = platform.delete_employee(employee_id)
    if not ok:
        raise HTTPException(500, "failed to delete employee")
    return {"ok": True, "deleted": employee_id}


@router.delete("/agent")
def delete_agent(account_username: str, platform: str = "x") -> Dict[str, Any]:
    """Delete an employee by its connected account (used by the chatbot UI)."""
    plat = get_platform()
    ok = plat.delete_agent(platform, account_username)
    if not ok:
        raise HTTPException(404, "agent not found for this account")
    return {"ok": True, "deleted_account": account_username}


@router.get("/employees/{employee_id}/logs")
def employee_logs(
    employee_id: str,
    limit: int = 25,
    latest_only: bool = True,
    summary: bool = True,
) -> Dict[str, Any]:
    """Per-cycle execution logs.

    - latest_only=True: return only latest cycle in chat-friendly ordered shape.
    - latest_only=False: return full logs list (legacy behavior).
    """
    import json as _json

    from app.db.database import SessionLocal
    from app.db.models import ExecutionLogRecord

    def _loads(s, default):
        try:
            return _json.loads(s) if s else default
        except Exception:  # noqa: BLE001
            return default

    db = SessionLocal()
    try:
        rows = (
            db.query(ExecutionLogRecord)
            .filter(ExecutionLogRecord.employee_id == employee_id)
            .order_by(ExecutionLogRecord.id.desc())
            .limit(max(1, min(int(limit), 100)))
            .all()
        )
        logs = []
        for r in rows:
            outcome = _loads(r.outcome, {})
            # دعم السجلّات القديمة التي كان outcome فيها قائمة نتائج فقط
            if isinstance(outcome, list):
                outcome = {"results": outcome}
            logs.append({
                "id": r.id,
                "cycle_id": r.cycle_id,
                "event_type": r.event_type,
                "success": bool(r.success),
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "decisions": _loads(r.decision, []),
                "thought": outcome.get("thought"),
                "analysis": outcome.get("analysis"),
                "plan": outcome.get("plan"),
                "results": outcome.get("results"),
                "evaluation": outcome.get("evaluation"),
                "lesson": outcome.get("lesson"),
                "phase_errors": outcome.get("phase_errors"),
                "error": outcome.get("error"),
            })

        if not latest_only:
            return {"ok": True, "employee_id": employee_id, "count": len(logs), "logs": logs}

        if not logs:
            return {"ok": True, "employee_id": employee_id, "count": 0, "latest_cycle": None}

        latest = logs[0]
        decisions = latest.get("decisions") or []
        results = latest.get("results") or []

        def _short(s: Any, n: int = 220) -> str:
            t = (str(s or "")).strip()
            return t if len(t) <= n else (t[: n - 3].rstrip() + "...")

        ordered_decisions = []
        for d in decisions:
            if not isinstance(d, dict):
                continue
            ordered_decisions.append({
                "capability": d.get("capability"),
                "rationale": _short(d.get("rationale"), 180),
                "urgency": d.get("urgency"),
                "expected_impact": d.get("expected_impact"),
                "risk": d.get("risk"),
            })

        ordered_results = []
        ok_count = 0
        fail_count = 0
        for r in results if isinstance(results, list) else []:
            if not isinstance(r, dict):
                continue
            success = bool(r.get("success"))
            if success:
                ok_count += 1
            else:
                fail_count += 1
            ordered_results.append({
                "capability": r.get("capability"),
                "success": success,
                "message": _short(r.get("message") or r.get("error") or "", 180),
            })

        final_summary = (
            f"Cycle via {latest.get('event_type')}: "
            f"{ok_count} ok / {fail_count} failed. "
            f"Top decision: {ordered_decisions[0]['capability'] if ordered_decisions else 'none'}."
        )

        payload = {
            "id": latest.get("id"),
            "cycle_id": latest.get("cycle_id"),
            "event_type": latest.get("event_type"),
            "success": latest.get("success"),
            "created_at": latest.get("created_at"),
            "duration_ms": latest.get("duration_ms"),
            "thought": _short(latest.get("thought"), 500) if summary else latest.get("thought"),
            "analysis": _short(latest.get("analysis"), 500) if summary else latest.get("analysis"),
            "plan": _short(latest.get("plan"), 500) if summary else latest.get("plan"),
            "decision_ordered": ordered_decisions,
            "results_ordered": ordered_results,
            "error": latest.get("error"),
            "final_summary": final_summary,
        }

        return {"ok": True, "employee_id": employee_id, "count": 1, "latest_cycle": payload}
    finally:
        db.close()


@router.post("/employees/{employee_id}/trigger")
def trigger(employee_id: str, body: TriggerIn) -> Dict[str, Any]:
    platform = get_platform()
    if not platform.repository.get(employee_id):
        raise HTTPException(404, "employee not found")
    if body.sync:
        report = platform.run_cycle_now(employee_id, body.event_type, body.payload)
        return {"ok": True, "mode": "sync", "report": report}
    event_id = platform.trigger_event(body.event_type, employee_id, body.payload)
    return {"ok": True, "mode": "async", "event_id": event_id}


@router.get("/stats")
def stats() -> Dict[str, Any]:
    return get_platform().stats()


# ─────────────────── agent toggle (chatbot) ───────────────────
@router.post("/agent/toggle")
def toggle_agent(body: AgentToggleIn) -> Dict[str, Any]:
    """Turn an account's autonomous agent on/off from the chatbot UI."""
    platform = get_platform()
    view = platform.toggle_agent(
        platform=body.platform,
        account_username=body.account_username,
        enabled=body.enabled,
        cookie_label=body.cookie_label,
        user_id=body.user_id,
        interval_minutes=body.interval_minutes,
        active_hours_start=body.active_hours_start,
        active_hours_end=body.active_hours_end,
        persona=body.persona.dict() if body.persona else None,
        goals=[g.dict() for g in body.goals] if body.goals else None,
        business_rules=body.business_rules or None,
        installed_skills=body.installed_skills,
        mcp_servers=body.mcp_servers,
    )
    return {"ok": True, "agent": view}


@router.get("/catalog")
def catalog() -> Dict[str, Any]:
    """Available skills and MCP servers an employee can be equipped with."""
    s = get_platform().stats()
    return {
        "ok": True,
        "skills": s.get("skills", []),
        "mcp_servers": s.get("mcp_servers", []),
        "capabilities": s.get("capabilities", []),
    }


# ─────────────────── persona: interests, templates, AI generate ───────────────────
@router.get("/persona/templates")
def persona_templates() -> Dict[str, Any]:
    """Main persona templates per interest (politics, economy, sports, ...)."""
    from ..employee.persona_templates import INTERESTS, list_templates
    return {
        "ok": True,
        "interests": [{"id": k, "label": v} for k, v in INTERESTS.items()],
        "templates": list_templates(),
    }


@router.post("/persona/generate")
def persona_generate(body: PersonaGenerateIn) -> Dict[str, Any]:
    """Generate a persona from interests + a brief using the LLM."""
    import json as _json
    import re as _re

    from ..employee.persona_templates import (
        PERSONA_TEMPLATES, build_generation_prompt,
    )

    platform = get_platform()
    llm = platform.llm

    def _fallback() -> Dict[str, Any]:
        # ادمج نماذج الاهتمامات المختارة كحل احتياطي عند غياب LLM
        first = next((PERSONA_TEMPLATES[i] for i in body.interests
                      if i in PERSONA_TEMPLATES), None)
        base = dict(first or PERSONA_TEMPLATES["politics"])
        base.pop("label", None)
        base.update({
            "display_name": body.account_username or base.get("role", ""),
            "language": body.language or base.get("language", "ar"),
            "interests": body.interests or [],
        })
        return base

    if not (llm and getattr(llm, "available", False)):
        return {"ok": True, "source": "template", "persona": _fallback()}

    prompt = build_generation_prompt(
        interests=body.interests, brief=body.brief,
        language=body.language, account_username=body.account_username,
    )
    try:
        raw = llm.complete(
            system="You generate concise social-media personas. Reply with JSON only.",
            prompt=prompt, temperature=0.7, max_tokens=600,
        ) or ""
        match = _re.search(r"\{.*\}", raw, _re.DOTALL)
        persona = _json.loads(match.group(0)) if match else {}
    except Exception:  # noqa: BLE001
        persona = {}

    if not persona:
        return {"ok": True, "source": "template", "persona": _fallback()}

    persona.setdefault("interests", body.interests or [])
    persona.setdefault("language", body.language or "ar")
    return {"ok": True, "source": "ai", "persona": persona}


@router.post("/persona/clone")
def persona_clone(body: PersonaCloneIn) -> Dict[str, Any]:
    """استنساخ أسلوب حساب X: سحب عيّنة من تغريداته وردوده بكوكيز الحساب
    المرتبط، تحليل الأسلوب والمواضيع وطريقة النقاش، ثم إرجاع برسونا مقترحة
    يراجعها المستخدم في الواجهة قبل الحفظ (لا تُحفظ هنا).
    """
    from ..services.persona_clone import clone_persona

    handle = (body.target_handle or "").strip().lstrip("@")
    if not handle:
        raise HTTPException(400, "target_handle is required")
    if not (body.account_username or "").strip():
        raise HTTPException(400, "account_username is required")

    try:
        result = clone_persona(
            cookie_label=body.account_username.strip(),
            handle=handle,
            llm=get_platform().llm,
            # عيّنة أكبر من 30 لا تُحسّن تحليل الأسلوب وتُطيل العملية على المستخدم
            limit=max(10, min(int(body.limit or 20), 30)),
            language=body.language or "ar",
        )
    except RuntimeError as exc:
        # أسباب متوقّعة ومفهومة للمستخدم (متصفح مشغول/حساب محمي/كوكيز منتهية)
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("persona clone failed for @%s", handle)
        raise HTTPException(500, f"تعذّر الاستنساخ: {exc}")

    return {"ok": True, **result}


@router.get("/agent/status")
def agent_status(account_username: str, platform: str = "x") -> Dict[str, Any]:
    """Current on/off state for one account's agent."""
    view = get_platform().get_agent_by_account(platform, account_username)
    return {"ok": True, "exists": bool(view), "agent": view}


@router.get("/agents")
def agents_overview() -> Dict[str, Any]:
    """Overview of all agents: how many are running right now."""
    return {"ok": True, **get_platform().agents_overview()}


# ─────────────────── employee insights (chat Q&A) ───────────────────
class AskIn(BaseModel):
    question: str


def _resolve_employee(employee_id: str = "", account_username: str = "", platform: str = "x"):
    repo = get_platform().repository
    if employee_id:
        return repo.get(employee_id)
    if account_username:
        return repo.get_by_account(platform, account_username)
    return None


@router.get("/employees/{employee_id}/insights")
def employee_insights(employee_id: str) -> Dict[str, Any]:
    """Aggregated engagement stats + latest contents + suggestions for the chat."""
    from ..services import insights as I

    cfg = get_platform().repository.get(employee_id)
    if not cfg:
        raise HTTPException(404, "employee not found")
    llm = get_platform().llm
    stats = I.aggregate_stats(employee_id)
    contents = I.recent_contents(employee_id, 8)
    return {
        "ok": True,
        "employee_id": employee_id,
        "account_username": cfg.account_username,
        "stats": stats,
        "stats_text": I.format_stats(cfg, stats),
        "contents": contents,
        "contents_text": I.format_contents(cfg, contents),
        "suggestions_text": I.build_suggestions(cfg, stats, llm),
    }


@router.get("/employees/{employee_id}/critique")
def employee_critique(employee_id: str) -> Dict[str, Any]:
    """تقييم الدورات وتوجيهات التحسين السارية على الموظف.

    يُظهر: درجة آخر دورة وحكمها، اتجاه الدرجات، المشكلات المتكرّرة، ونصّ
    التوجيهات المحقونة فعلياً في تعليمات الدورة القادمة.
    """
    from ..cognition.critique import critique_report

    cfg = get_platform().repository.get(employee_id)
    if not cfg:
        raise HTTPException(404, "employee not found")
    return {"ok": True, "employee_id": employee_id,
            "account_username": cfg.account_username, **critique_report(cfg)}


@router.get("/employees/{employee_id}/content-strategy")
def employee_content_strategy(employee_id: str) -> Dict[str, Any]:
    """استراتيجية المحتوى وأداؤها: المحاور، الصيغ، ما نجح فعلاً، وخطة الدورة القادمة.

    هذه النافذة التي تُظهر أن الموظف صانع محتوى يتعلّم: أفضل الصيغ والمحاور
    وساعات النشر مقيسةً بأرقام منشوراته الحقيقية.
    """
    from ..employee import strategy as S

    cfg = get_platform().repository.get(employee_id)
    if not cfg:
        raise HTTPException(404, "employee not found")
    return {
        "ok": True,
        "employee_id": employee_id,
        "account_username": cfg.account_username,
        "pillars": S.content_pillars(cfg),
        "formats": {
            f: {"label": S.format_spec(f)["label"], "parts": S.format_spec(f)["parts"]}
            for f in S.enabled_formats(cfg)
        },
        "next_plan": S.choose_plan(cfg),
        "performance": S.performance_report(cfg),
        "minutes_since_metrics_read": (
            None if S.minutes_since_metrics_read(cfg) == float("inf")
            else round(S.minutes_since_metrics_read(cfg), 1)
        ),
    }


@router.get("/employees/{employee_id}/graph-memory")
def employee_graph_memory(
    employee_id: str, query: str = "", limit: int = 10
) -> Dict[str, Any]:
    """حالة الذاكرة المعرفية الزمنية (Zep/Graphiti) لموظف: هل تُغذّى وتُسترجع؟

    بلا ``query`` تعيد أحدث الحقائق زمنياً؛ ومع ``query`` تجري بحثاً هجيناً.
    مفيدة للتأكد فعلياً من فاعلية الذاكرة بدل الاعتماد على السجلّات.
    """
    cfg = get_platform().repository.get(employee_id)
    if not cfg:
        raise HTTPException(404, "employee not found")
    graph = get_platform().graph_memory
    limit = max(1, min(int(limit), 50))
    facts = (graph.search(employee_id, query, limit=limit) if query.strip()
             else graph.recent_facts(employee_id, limit=limit))
    return {
        "ok": True,
        "employee_id": employee_id,
        "query": query,
        "facts": facts,
        "facts_count": len(facts),
        **graph.stats(employee_id),
    }


@router.post("/employees/{employee_id}/ask")
def employee_ask(employee_id: str, body: AskIn) -> Dict[str, Any]:
    """Free-form discussion with the employee, grounded in its own data."""
    from ..services import insights as I

    cfg = get_platform().repository.get(employee_id)
    if not cfg:
        raise HTTPException(404, "employee not found")
    if not (body.question or "").strip():
        raise HTTPException(400, "question is required")
    answer = I.answer_question(cfg, body.question.strip(), get_platform().llm, employee_id)
    return {"ok": True, "employee_id": employee_id, "answer": answer}
