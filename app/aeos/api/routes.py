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

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.auth.ownership import is_admin, label_or_404, owns_employee
from app.auth.dependencies import get_current_user as _optional_user
from app.auth.ownership import require_user as get_current_user
from app.auth.roles import require as require_perm
from app.db.models import User

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
    limit: int = 100                  # التغريدات الأصلية؛ الردود مجمّع عميق ثابت (≥100 تُحلَّل)
    language: str = "ar"
    test: bool = True                 # اختبار مطابقة بعيّنات مولَّدة (نداءان إضافيان)
    employee_id: str = ""             # مع apply=true: احفظ الهوية على هذا الموظف مباشرة
    apply: bool = False
    background: bool = False          # مهمة خلفية (دقائق) تُتابَع بـ GET /persona/clone/jobs/{id}


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


class IdentityFieldIn(BaseModel):
    """إعادة توليد عنصر واحد من الهوية (avatar/banner/name/bio) مع برومبت
    اختياري يوجّه التعديل."""
    field: str
    prompt: Optional[str] = ""


class IdentityApplyIn(BaseModel):
    """تطبيق هوية على حساب X: مرّر identity من معاينة سابقة لتطبيقها كما هي،
    أو اتركها فارغة ليولّد هوية جديدة من البرسونا ثم يطبّقها."""
    user_id: Optional[int] = None
    identity: Optional[Dict[str, Any]] = None   # {name, bio, avatar_url, banner_url}


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
async def health(viewer=Depends(_optional_user)) -> Dict[str, Any]:
    from app.db.database import engine

    # مسار عام (فحص الصحة): كان يكشف لأي زائر عدد الموظفين وخوادم MCP وحالة قاعدة البيانات.
    # التفاصيل للمشرف/المطوّر فقط
    if not is_admin(viewer):
        return {"ok": True, "platform": "AEOS"}

    # حالة مجمّع الاتصالات: checked_out ثابت فوق الصفر والخادم خامل = جلسة لم تُغلق
    # (هكذا شُخّص تسرّب جلسات شات الـWebSocket الذي أسقط المجدول بـ QueuePool limit reached)
    pool = engine.pool
    db_pool = {"dialect": engine.dialect.name, "size": pool.size(), "checked_out": pool.checkedout(),
               "overflow": pool.overflow(), "checked_in": pool.checkedin()}
    return {"ok": True, "platform": "AEOS", "db_pool": db_pool, **get_platform().stats()}


@router.get("/employees")
def list_employees(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    repo = get_platform().repository
    return {"employees": [e.to_dict() for e in repo.list_active() if owns_employee(e, current_user)]}


@router.get("/employees/{employee_id}")
def get_employee(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return _employee_or_404(employee_id, current_user).to_dict()


@router.post("/employees")
def create_employee(body: EmployeeIn, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    platform = get_platform()
    # المالك من التوكن لا من الجسم، والحساب يجب أن يكون للمستخدم (لا موظف على كوكيز غيره)
    body.user_id = current_user.id
    label_or_404(body.cookie_label or body.account_username, current_user)
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

    # هوية الحساب من البرسونا عند الإنشاء (اسم + نبذة، بلا صورة) — في الخلفية
    try:
        from ..services.identity import apply_persona_identity_async

        apply_persona_identity_async(saved, llm=platform.llm, repo=platform.repository)
    except Exception:  # noqa: BLE001
        logger.exception("identity apply scheduling failed for %s", saved.id)

    return {"ok": True, "employee": saved.to_dict()}


@router.patch("/employees/{employee_id}/status")
def set_status(employee_id: str, status: str,
               current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    repo = get_platform().repository
    cfg = _employee_or_404(employee_id, current_user)
    try:
        cfg.status = EmployeeStatus(status)
    except ValueError:
        raise HTTPException(400, f"invalid status '{status}'")
    repo.save(cfg)
    return {"ok": True, "status": cfg.status.value}


@router.delete("/employees/{employee_id}")
def delete_employee(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Permanently delete an AI employee (stops schedule + removes logs + purges memory)."""
    platform = get_platform()
    _employee_or_404(employee_id, current_user)
    ok = platform.delete_employee(employee_id)
    if not ok:
        raise HTTPException(500, "failed to delete employee")
    return {"ok": True, "deleted": employee_id}


@router.delete("/agent")
def delete_agent(account_username: str, platform: str = "x",
                 current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Delete an employee by its connected account (used by the chatbot UI)."""
    plat = get_platform()
    cfg = plat.repository.get_by_account(platform, account_username)
    if not owns_employee(cfg, current_user):
        raise HTTPException(404, "agent not found for this account")
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
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Per-cycle execution logs.

    - latest_only=True: return only latest cycle in chat-friendly ordered shape.
    - latest_only=False: return full logs list (legacy behavior).
    """
    import json as _json

    _employee_or_404(employee_id, current_user)

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
def trigger(employee_id: str, body: TriggerIn,
            current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    platform = get_platform()
    _employee_or_404(employee_id, current_user)
    if body.sync:
        report = platform.run_cycle_now(employee_id, body.event_type, body.payload)
        return {"ok": True, "mode": "sync", "report": report}
    event_id = platform.trigger_event(body.event_type, employee_id, body.payload)
    return {"ok": True, "mode": "async", "event_id": event_id}


# ─────────── هوية X من البرسونا (اسم/بايو/صورة/هيدر) ───────────
@router.post("/employees/{employee_id}/identity/preview")
def identity_preview(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """يولّد هوية كاملة من برسونا الموظف (اسم + بايو + صورة + هيدر)
    للمعاينة في الواجهة — بلا أي تغيير على حساب X. يستغرق ~دقيقة
    (صورتا Gemini). الصور روابط مؤقتة تُحذف بعد ساعة — طبّق أو أعد التوليد."""
    from ..services import identity_service

    cfg = _employee_or_404(employee_id, current_user)
    try:
        identity = identity_service.generate_preview(cfg)
    except Exception as exc:  # noqa: BLE001
        logger.exception("identity preview failed for %s", employee_id)
        raise HTTPException(500, str(exc))
    return {"ok": True, "account_username": cfg.account_username,
            "identity": identity}


@router.post("/employees/{employee_id}/identity/field")
def identity_field(employee_id: str, body: IdentityFieldIn,
                   current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """يعيد توليد عنصر واحد من الهوية (صورة/هيدر/اسم/بايو) من البرسونا
    مع برومبت اختياري — للمعاينة فقط، بلا تغيير على X. الصور تأخذ 30-90ث."""
    from ..services import identity_service

    cfg = _employee_or_404(employee_id, current_user)
    if body.field not in identity_service.IDENTITY_FIELDS:
        raise HTTPException(400, f"field must be one of {identity_service.IDENTITY_FIELDS}")
    try:
        patch = identity_service.generate_field(cfg, body.field, body.prompt or "")
    except Exception as exc:  # noqa: BLE001
        logger.exception("identity field regen failed for %s/%s", employee_id, body.field)
        raise HTTPException(500, str(exc))
    return {"ok": True, "field": body.field, "patch": patch}


@router.post("/employees/{employee_id}/identity/apply")
def identity_apply(employee_id: str, body: IdentityApplyIn,
                   current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """يطبّق الهوية على حساب X في الخلفية (متصفح + كوكيز الحساب) ويعيد
    إقراراً فورياً؛ النتيجة النهائية تُدفع لشات المستخدم عند الاكتمال.
    صور المعاينة تُثبَّت لحظة الاعتماد — إن لم تعد متاحة يُرفض الطلب برسالة
    واضحة بدل تطبيق صورة غير التي يراها المالك."""
    from ..services import identity_service, identity_store

    cfg = _employee_or_404(employee_id, current_user)
    # النتيجة تُدفع لشات المستخدم الموثَّق — لا لرقم يرسله المتصفح
    try:
        ack = identity_service.request_apply_identity(cfg, current_user.id, identity=body.identity)
    except identity_store.IdentityError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "message": ack}


@router.get("/employees/{employee_id}/x-profile")
def x_profile(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """هوية الحساب كما قُرئت من X آخر مرة (اسم/نبذة/صورة/هيدر) — لبطاقة الموظف، بلا متصفح."""
    from ..services import identity_store

    cfg = _employee_or_404(employee_id, current_user)
    return {"ok": True, "x_profile": identity_store.load_x_profile(cfg.id)}


@router.post("/employees/{employee_id}/x-profile/refresh")
def x_profile_refresh(employee_id: str,
                      current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """يقرأ هوية الحساب من X الآن ويحدّث البطاقة (~15-30ث). طلب تفاعلي: يفشل سريعاً
    برسالة واضحة إن كان المتصفح محجوزاً بدورة موظف."""
    from ..services import identity_service

    cfg = _employee_or_404(employee_id, current_user)
    try:
        snap = identity_service.refresh_x_profile(cfg, lock_timeout=25.0)
    except Exception as exc:  # noqa: BLE001
        logger.info("x profile refresh failed for %s: %s", employee_id, exc)
        raise HTTPException(502, str(exc))
    return {"ok": True, "x_profile": snap}


@router.get("/stats")
def stats(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    return get_platform().stats()


# ─────────────────── agent toggle (chatbot) ───────────────────
@router.post("/agent/toggle")
def toggle_agent(body: AgentToggleIn, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Turn an account's autonomous agent on/off from the chatbot UI."""
    platform = get_platform()
    label_or_404(body.cookie_label or body.account_username, current_user)
    existing = platform.repository.get_by_account(body.platform, body.account_username)
    if existing is not None and not owns_employee(existing, current_user):
        raise HTTPException(404, "agent not found for this account")
    view = platform.toggle_agent(
        platform=body.platform,
        account_username=body.account_username,
        enabled=body.enabled,
        cookie_label=body.cookie_label,
        user_id=current_user.id,
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
def catalog(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
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
def persona_templates(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Main persona templates per interest (politics, economy, sports, ...)."""
    from ..employee.persona_templates import INTERESTS, list_templates
    return {
        "ok": True,
        "interests": [{"id": k, "label": v} for k, v in INTERESTS.items()],
        "templates": list_templates(),
    }


@router.post("/persona/generate")
def persona_generate(body: PersonaGenerateIn,
                     current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
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
def persona_clone(body: PersonaCloneIn,
                  current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """استنساخ أسلوب حساب X: سحب عيّنة من تغريداته وردوده بكوكيز الحساب
    المرتبط، تحليل الأسلوب والمواضيع وطريقة النقاش، ثم إرجاع برسونا مقترحة
    يراجعها المستخدم في الواجهة قبل الحفظ (لا تُحفظ هنا).
    """
    from ..services import persona_clone as PC

    handle = (body.target_handle or "").strip().lstrip("@")
    if not handle:
        raise HTTPException(400, "target_handle is required")
    if not (body.account_username or "").strip():
        raise HTTPException(400, "account_username is required")
    # السحب يجري بكوكيز هذا الحساب: يجب أن يكون للمستخدم
    label_or_404(body.account_username.strip(), current_user)
    cfg = _employee_or_404(body.employee_id, current_user) if body.apply else None

    # عيّنة الاستنساخ: persona_clone.SAMPLE (200 منشور + 150 رداً، طلب المالك 2026-11-16) — بلا بحث تاريخي
    limit = max(20, min(int(body.limit or PC.SAMPLE["limit"]), PC.SAMPLE["limit"]))

    def run(progress) -> Dict[str, Any]:
        result = PC.clone_persona(
            cookie_label=body.account_username.strip(), handle=handle, llm=get_platform().llm,
            limit=limit, language=body.language or "ar", test=body.test, progress=progress)
        if cfg is not None:
            PC.apply_to_employee(cfg, result["persona"], result["voice_profile"],
                                 get_platform().repository)
            result["applied"] = True
        return {"ok": True, **result}

    if body.background:
        return {"ok": True, "job_id": PC.start_job(current_user.id, run)}
    try:
        return run(lambda _msg: None)
    except RuntimeError as exc:
        # أسباب متوقّعة ومفهومة للمستخدم (متصفح مشغول/حساب محمي/كوكيز منتهية)
        raise HTTPException(422, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("persona clone failed for @%s", handle)
        raise HTTPException(500, f"تعذّر الاستنساخ: {exc}")


@router.get("/persona/clone/jobs/{job_id}")
def persona_clone_job(job_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """حالة استنساخ يجري في الخلفية: running (مع وصف المرحلة) أو done (النتيجة) أو error."""
    from ..services import persona_clone as PC

    job = PC.get_job(job_id, current_user.id)
    if job is None:
        raise HTTPException(404, "مهمة غير موجودة أو انتهت صلاحيتها")
    return {"ok": True, **job}


@router.get("/agent/status")
def agent_status(account_username: str, platform: str = "x",
                 current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Current on/off state for one account's agent."""
    cfg = get_platform().repository.get_by_account(platform, account_username)
    if cfg is not None and not owns_employee(cfg, current_user):
        return {"ok": True, "exists": False, "agent": None}
    view = get_platform().get_agent_by_account(platform, account_username)
    return {"ok": True, "exists": bool(view), "agent": view}


@router.get("/agents")
def agents_overview(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Overview of the CURRENT USER's agents: how many are running right now."""
    overview = get_platform().agents_overview()
    if is_admin(current_user):
        return {"ok": True, **overview}
    mine = {c.account_username for c in get_platform().repository.list_all()
            if owns_employee(c, current_user)}
    agents = [a for a in overview.get("agents") or [] if a["account_username"] in mine]
    return {"ok": True, "agents": agents, "total": len(agents),
            "running": sum(1 for a in agents if a["enabled"])}


@router.get("/team")
def team_roster(current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """فريق المستخدم الموثَّق كما يراه المنسّق (كان يقبل user_id من المتصفح فيكشف
    فريق أي مستخدم بتخمين رقمه)."""
    from ..services import orchestrator

    return {"ok": True, "employees": orchestrator.roster(current_user.id)}


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
def employee_insights(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Aggregated engagement stats + latest contents + suggestions for the chat."""
    from ..services import insights as I

    cfg = _employee_or_404(employee_id, current_user)
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
def employee_critique(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """تقييم الدورات وتوجيهات التحسين السارية على الموظف.

    يُظهر: درجة آخر دورة وحكمها، اتجاه الدرجات، المشكلات المتكرّرة، ونصّ
    التوجيهات المحقونة فعلياً في تعليمات الدورة القادمة.
    """
    from ..cognition.critique import critique_report

    cfg = _employee_or_404(employee_id, current_user)
    return {"ok": True, "employee_id": employee_id,
            "account_username": cfg.account_username, **critique_report(cfg)}


@router.get("/employees/{employee_id}/learning")
def employee_learning(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """ماذا تعلّم الموظف من دوراته: منحنى الأداء، قيمة كل ذراع (فعل × مهارة) من القبول
    والرفض والتفاعل الحقيقي، ما أُوقف مؤقتاً ولماذا، وفاعلية كل درس."""
    from ..cognition import learning

    cfg = _employee_or_404(employee_id, current_user)
    return {"ok": True, "employee_id": employee_id, "account_username": cfg.account_username,
            "summary": learning.summary_line(cfg), **learning.report(cfg)}


@router.get("/employees/{employee_id}/content-strategy")
def employee_content_strategy(employee_id: str,
                              current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """استراتيجية المحتوى وأداؤها: المحاور، الصيغ، ما نجح فعلاً، وخطة الدورة القادمة.

    هذه النافذة التي تُظهر أن الموظف صانع محتوى يتعلّم: أفضل الصيغ والمحاور
    وساعات النشر مقيسةً بأرقام منشوراته الحقيقية.
    """
    from ..employee import strategy as S

    cfg = _employee_or_404(employee_id, current_user)
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


class SkillGenerateIn(BaseModel):
    """برومبت المالك: «ماذا تريد أن يتقن موظفك؟»"""
    prompt: str = Field(min_length=1, max_length=8000)
    activate: bool = False            # true = ولّد واحفظ وفعّل مباشرة


class SkillImportIn(BaseModel):
    content: str = Field(default="", max_length=32768)
    url: str = Field(default="", max_length=2048)
    filename: str = Field(default="", max_length=160)


class SkillSaveIn(BaseModel):
    """حفظ مواصفة مهارة (من معاينة سابقة أو بعد تعديلها يدوياً)."""
    spec: Dict[str, Any]


class SkillPatchIn(BaseModel):
    enabled: Optional[bool] = None


def _employee_or_404(employee_id: str, current_user: User):
    # سياسة الملكية واحدة (owns_employee)، والوصول للبيانات عبر منصة هذه الطبقة
    cfg = get_platform().repository.get(employee_id)
    if not owns_employee(cfg, current_user):
        raise HTTPException(404, "employee not found")
    return cfg


def _skill_preview(cfg, spec, *, warnings=None, saved=False) -> Dict[str, Any]:
    from ..skills import custom as CS

    return {"ok": True, "employee_id": cfg.id, "saved": saved,
            "skill": spec, "summary": CS.describe_spec(spec),
            "warnings": warnings or [], "readiness": CS.skill_readiness(cfg, spec)}


def _save_skill(cfg, spec):
    from ..skills import custom as CS

    clean = CS.normalize_spec(spec)
    readiness = CS.skill_readiness(cfg, clean)
    if clean.get("enabled", True) and not readiness["ready"]:
        raise ValueError("المهارة غير جاهزة: " + "؛ ".join(readiness["warnings"]))
    saved = CS.save_spec(cfg, clean)
    get_platform().repository.save(cfg)
    return saved


class EmployeeLLMIn(BaseModel):
    provider: str = ""
    model: str = ""


@router.get("/employees/{employee_id}/llm")
def get_employee_llm(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """نموذج هذا الموظف: اختياره الخاص، ونموذج الموظفين العام لمالكه، وما يعمل به فعلاً."""
    from app.services import user_llm as UL

    cfg = _employee_or_404(employee_id, current_user)
    return {"ok": True, "employee_id": cfg.id, **UL.employee_view(cfg.user_id or current_user.id, cfg)}


@router.put("/employees/{employee_id}/llm")
def put_employee_llm(employee_id: str, body: EmployeeLLMIn,
                     current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """اختيار نموذج خاص لهذا الموظف (provider فارغ ⇒ يتبع نموذج الموظفين العام). يسري من دورته التالية.

    يُتحقق بمفاتيح **مالك الموظف** وصلاحيته (رصيد النظام للمدير/المطوّر فقط)."""
    from app.services import user_llm as UL

    cfg = _employee_or_404(employee_id, current_user)
    owner = cfg.user_id or current_user.id
    try:
        UL.set_employee_selection(owner, cfg, body.provider, body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    get_platform().repository.save(cfg)
    return {"ok": True, "employee_id": cfg.id, **UL.employee_view(owner, cfg)}


@router.get("/employees/{employee_id}/skills")
def list_employee_skills(employee_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """مهارات هذا الموظف: المدمجة المثبّتة + المخصّصة التي أنشأها المالك."""
    from ..skills import custom as CS

    cfg = _employee_or_404(employee_id, current_user)
    specs = CS.list_specs(cfg)
    return {
        "ok": True,
        "employee_id": employee_id,
        "installed_skills": list(cfg.installed_skills or []),
        "custom_skills": [
            {**s, "summary": CS.describe_spec(s), "runs_today": CS.runs_today(cfg, s["id"]),
             "readiness": CS.skill_readiness(cfg, s)}
            for s in specs
        ],
    }


@router.post("/employees/{employee_id}/skills/generate")
def generate_employee_skill(employee_id: str, body: SkillGenerateIn,
                            current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """حوّل برومبت المالك إلى مواصفة مهارة — **معاينة بلا حفظ** افتراضياً.

    المالك يرى بالعربية متى تعمل المهارة وماذا تفعل وبأي حدود، ثم يفعّلها.
    ``activate=true`` يحفظها ويفعّلها فوراً لمن يريد المسار السريع.
    """
    from ..skills import custom as CS

    cfg = _employee_or_404(employee_id, current_user)
    warnings = []
    try:
        spec = CS.generate_spec(body.prompt, cfg, get_platform().llm,
                                _strict=True, _warnings=warnings)
        spec["id"] = f"custom_{uuid.uuid4().hex}"
        if body.activate:
            spec = _save_skill(cfg, spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _skill_preview(cfg, spec, warnings=warnings, saved=body.activate)


@router.post("/employees/{employee_id}/skills/import")
def import_employee_skill(employee_id: str, body: SkillImportIn,
                          current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    from ..skills import custom as CS

    cfg = _employee_or_404(employee_id, current_user)
    if bool(body.content.strip()) == bool(body.url.strip()):
        raise HTTPException(400, "أرسل محتوى المهارة أو رابطها، وليس كليهما.")
    try:
        content = CS.fetch_skill_document(body.url.strip()) if body.url.strip() else body.content
        preview = CS.import_spec(content, cfg, get_platform().llm,
                                 source_name=body.url.strip() or body.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    preview["skill"]["id"] = f"custom_{uuid.uuid4().hex}"
    return _skill_preview(cfg, preview["skill"], warnings=preview.get("warnings"))


@router.post("/employees/{employee_id}/skills")
def save_employee_skill(employee_id: str, body: SkillSaveIn,
                        current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """احفظ/حدّث مهارة مخصّصة (تمرّ إلزامياً بالتطبيع والقوائم البيضاء)."""
    from ..skills import custom as CS

    cfg = _employee_or_404(employee_id, current_user)
    if not (body.spec or {}).get("actions"):
        raise HTTPException(400, "spec.actions is required")
    try:
        spec = _save_skill(cfg, body.spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _skill_preview(cfg, spec, saved=True)


@router.patch("/employees/{employee_id}/skills/{skill_id}")
def patch_employee_skill(employee_id: str, skill_id: str,
                         body: SkillPatchIn,
                         current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """تفعيل/إيقاف مهارة مخصّصة دون حذفها."""
    from ..skills import custom as CS

    cfg = _employee_or_404(employee_id, current_user)
    if body.enabled is None:
        raise HTTPException(400, "nothing to update")
    spec = next((s for s in CS.list_specs(cfg) if s.get("id") == skill_id), None)
    if spec is None:
        raise HTTPException(404, "skill not found")
    try:
        _save_skill(cfg, {**spec, "enabled": body.enabled})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "employee_id": employee_id, "skill_id": skill_id, "enabled": body.enabled}


@router.delete("/employees/{employee_id}/skills/{skill_id}")
def delete_employee_skill(employee_id: str, skill_id: str,
                          current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    from ..skills import custom as CS

    cfg = _employee_or_404(employee_id, current_user)
    if not CS.delete_spec(cfg, skill_id):
        raise HTTPException(404, "skill not found")
    get_platform().repository.save(cfg)
    return {"ok": True, "deleted": skill_id}


# ─────────────────── ذاكرة الوكلاء (المنسّق + الموظفون) ───────────────────
class MemoryIn(BaseModel):
    content: str
    kind: str = "instruction"
    importance: int = 4
    employee_id: str = ""


def _memory_scope(current_user: User, employee_id: str = "") -> str:
    """نطاق الذاكرة من **المستخدم الموثَّق** لا من user_id يرسله المتصفح: ذاكرة
    المنسّق تحوي تفضيلات المالك وقراراته، فلا تُقرأ ولا تُحذف بمعرّف مخمَّن."""
    from ..memory import agent_memory as AM

    if employee_id:
        _employee_or_404(employee_id, current_user)
        return AM.employee_scope(employee_id)
    return AM.coordinator_scope(current_user.id)


@router.get("/memory/overview")
def memory_overview(employee_id: str = "",
                    current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """صورة موحّدة لكل أنظمة الذاكرة (قصيرة/طويلة/طبقات الدورة/الرسم) لموظف أو للمنسّق."""
    from ..memory import lifecycle

    if employee_id:
        return {"ok": True, **lifecycle.employee_overview(_employee_or_404(employee_id, current_user))}
    return {"ok": True, **lifecycle.coordinator_overview(current_user.id)}


@router.get("/memory")
def list_memory(employee_id: str = "",
                current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """الذاكرة طويلة المدى لموظف (employee_id) أو للمنسّق (user_id) + أحدث الأحداث."""
    from ..memory import agent_memory as AM

    scope = _memory_scope(current_user, employee_id)
    return {"ok": True, "scope": scope, "memories": AM.list_memories([scope], 150)}


@router.post("/memory")
def add_memory(body: MemoryIn, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    from ..memory import agent_memory as AM

    scope = _memory_scope(current_user, body.employee_id)
    try:
        saved = AM.add(scope, body.content, body.kind, body.importance,
                       meta={"source": "owner_ui"})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "memory": saved}


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: int, employee_id: str = "",
                  current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    from ..memory import agent_memory as AM

    if not AM.delete(memory_id, [_memory_scope(current_user, employee_id)]):
        raise HTTPException(404, "memory not found in this scope")
    return {"ok": True, "deleted": memory_id}


# ─────────────────── أدوات الموظف (بحث + MCP خارجي) ───────────────────
class ExternalMCPIn(BaseModel):
    name: str
    label: str = ""
    transport: str = "stdio"            # stdio | http
    command: str = ""
    args: List[str] = []
    env: Dict[str, str] = {}
    url: str = ""
    headers: Dict[str, str] = {}
    allowed_tools: Optional[List[str]] = None   # None = كل ما يعرضه الخادم
    research_tool: str = ""
    research_arg: str = ""


class ToolAttachIn(BaseModel):
    server: str
    attach: bool = True


class ToolCallIn(BaseModel):
    server: str
    tool: str
    arguments: Dict[str, Any] = {}


@router.get("/tools")
def tools_overview(employee_id: str = "",
                   current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """مزوّد البحث + الخوادم الخارجية، وحالة ربط كلٍّ منها بالموظف إن حُدّد."""
    from ..services import tools_service as TS

    cfg = _employee_or_404(employee_id, current_user) if employee_id else None
    return {"ok": True, **TS.overview(cfg)}


@router.post("/tools/external")
def add_external_tool(body: ExternalMCPIn,
                      current_user: User = Depends(require_perm("system.mcp.manage"))) -> Dict[str, Any]:
    """يتصل بالخادم فعلاً ويكتشف أدواته قبل الحفظ — لا يُحفظ خادم لا يعمل.

    للمطوّر فقط: كان أي مستخدم مسجّل يضيف خادم stdio (أمر يُشغَّل على هذا الجهاز)
    للنظام كله. الواجهة المعتمدة الآن لوحة التحكم (/api/admin/system/mcps)."""
    from ..services import tools_service as TS

    try:
        return {"ok": True, "server": TS.add_external(body.model_dump())}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - فشل الاتصال/البروتوكول
        raise HTTPException(502, f"تعذّر الاتصال بالخادم: {exc}") from exc


@router.delete("/tools/external/{name}")
def delete_external_tool(name: str,
                         current_user: User = Depends(require_perm("system.mcp.manage"))) -> Dict[str, Any]:
    from ..services import tools_service as TS

    if not TS.remove_external(name):
        raise HTTPException(404, "server not found")
    return {"ok": True, "deleted": name}


@router.post("/employees/{employee_id}/tools")
def attach_employee_tool(employee_id: str, body: ToolAttachIn,
                         current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    from ..services import tools_service as TS

    cfg = _employee_or_404(employee_id, current_user)
    try:
        return {"ok": True, **TS.set_attached(cfg, body.server, body.attach)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class PrivateMCPIn(BaseModel):
    name: str
    url: str
    headers: Dict[str, str] = {}
    description: str = ""
    allowed_tools: Optional[List[str]] = None
    research_tool: str = ""
    research_arg: str = ""


def _private_errors(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - فشل الاتصال/البروتوكول
        raise HTTPException(502, f"تعذّر الاتصال بالخادم: {exc}") from exc


@router.post("/employees/{employee_id}/tools/private")
def add_private_tool(employee_id: str, body: PrivateMCPIn,
                     current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """أداة MCP فرعية **لهذا الموظف فقط** يضيفها مالكه: خادم HTTP عام، يُتصل به وتُكتشف
    أدواته قبل الحفظ، ثم يُربط بالموظف. stdio والحزم لأدوات النظام (المطوّر)."""
    from ..services import tools_service as TS

    cfg = _employee_or_404(employee_id, current_user)
    raw = {**body.model_dump(), "transport": "http"}
    return {"ok": True, "server": _private_errors(lambda: TS.add_private(cfg, raw, current_user.email))}


@router.post("/employees/{employee_id}/tools/private/upload")
async def upload_private_tool(employee_id: str, file: UploadFile = File(...),
                              current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """ملف إعداد JSON (صيغة mcpServers أو خادم واحد) — خوادم HTTP فقط."""
    from ..mcp import employee_mcp as EM
    from ..services import tools_service as TS

    cfg = _employee_or_404(employee_id, current_user)
    data = await file.read(512 * 1024 + 1)
    if len(data) > 512 * 1024:
        raise HTTPException(413, "ملف الإعداد أكبر من 512KB")
    raws = _private_errors(lambda: EM.parse_upload(data))
    saved, errors = [], []
    for raw in raws:
        try:
            saved.append(TS.add_private(cfg, raw, current_user.email))
        except Exception as exc:  # noqa: BLE001
            errors.append({"name": raw.get("name") or "?", "error": str(exc)[:300]})
    if not saved:
        raise HTTPException(400, "لم يُضف أي خادم: " + "؛ ".join(f"{e['name']}: {e['error']}" for e in errors))
    return {"ok": True, "saved": saved, "errors": errors}


@router.delete("/employees/{employee_id}/tools/private/{name}")
def delete_private_tool(employee_id: str, name: str,
                        current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    from ..services import tools_service as TS

    cfg = _employee_or_404(employee_id, current_user)
    if not TS.remove_private(cfg, name):
        raise HTTPException(404, "server not found")
    return {"ok": True, "deleted": name}


@router.post("/employees/{employee_id}/tools/call")
def call_employee_tool(employee_id: str, body: ToolCallIn,
                       current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """استدعاء يطلبه المالك لأداة مربوطة بالموظف (للتجربة أو لمهمة مباشرة)."""
    from ..services import tools_service as TS

    cfg = _employee_or_404(employee_id, current_user)
    try:
        return {"ok": True, "result": TS.call_tool(cfg, body.server, body.tool, body.arguments)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/employees/{employee_id}/interactions")
def employee_interactions(employee_id: str, limit: int = 10,
                          current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """ما تعلّمه الموظف عن **تفاعله** فعلياً: ماذا أثمر ومع مَن.

    نظير ``/content-strategy`` لكن لجانب التفاعل: نسبة الاستجابة لكل نوع
    (رد/إعجاب/اقتباس/إعادة نشر/متابعة)، والحسابات الأكثر تجاوباً، ودرجة
    الإلحاح على كل حساب. هذه الأرقام هي ما يرجّح به محرّك القرار أهدافه.
    """
    from ..employee import interaction_ledger as IL

    cfg = _employee_or_404(employee_id, current_user)
    return {
        "ok": True,
        "employee_id": employee_id,
        "account_username": cfg.account_username,
        "summary": IL.summary_line(cfg),
        **IL.report(cfg, limit=max(1, min(int(limit), 50))),
    }


@router.get("/employees/{employee_id}/graph-memory")
def employee_graph_memory(
    employee_id: str, query: str = "", limit: int = 10,
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """حالة الذاكرة المعرفية الزمنية (Zep/Graphiti) لموظف: هل تُغذّى وتُسترجع؟

    بلا ``query`` تعيد أحدث الحقائق زمنياً؛ ومع ``query`` تجري بحثاً هجيناً.
    مفيدة للتأكد فعلياً من فاعلية الذاكرة بدل الاعتماد على السجلّات.
    """
    _employee_or_404(employee_id, current_user)
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
def employee_ask(employee_id: str, body: AskIn,
                 current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    """Free-form discussion with the employee, grounded in its own data."""
    from ..services import insights as I

    cfg = _employee_or_404(employee_id, current_user)
    if not (body.question or "").strip():
        raise HTTPException(400, "question is required")
    answer = I.answer_question(cfg, body.question.strip(), get_platform().llm, employee_id)
    return {"ok": True, "employee_id": employee_id, "answer": answer}
