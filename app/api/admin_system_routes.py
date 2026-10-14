"""
لوحة تحكم النظام — خوادم MCP العامة ومهارات النظام والمستخدمين والصلاحيات.

العرض: المسؤول والمطوّر. الإضافة/التعديل/الربط/الحذف: **المطوّر فقط** (app.auth.roles).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.roles import require
from app.db.database import get_db
from app.db.models import User

router = APIRouter(prefix="/api/admin/system", tags=["Admin - System"])

_MAX_UPLOAD = 20 * 1024 * 1024


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(_MAX_UPLOAD + 1)
    if len(data) > _MAX_UPLOAD:
        raise HTTPException(413, "الملف أكبر من 20MB")
    if not data:
        raise HTTPException(400, "الملف فارغ")
    return data


def _svc():
    from app.aeos.services import system_catalog

    return system_catalog


@router.get("/overview")
def system_overview(user: User = Depends(require("admin.dashboard")),
                    db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.auth.roles import permissions_of, role_of

    return {"ok": True, **_svc().overview(db),
            "me": {"role": role_of(user), "permissions": permissions_of(user)}}


# ─────────────────────────── MCP ───────────────────────────
class MCPPatchIn(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    scope: Optional[Dict[str, bool]] = None
    allowed_tools: Optional[List[str]] = None
    research_tool: Optional[str] = None
    env: Optional[Dict[str, Optional[str]]] = None
    headers: Optional[Dict[str, Optional[str]]] = None
    query_secrets: Optional[Dict[str, Optional[str]]] = None


@router.get("/mcps")
def list_mcps(user: User = Depends(require("system.mcp.view"))) -> Dict[str, Any]:
    return {"ok": True, **_svc().mcp_overview()}


class MCPAuthIn(BaseModel):
    mode: str = "none"          # none | bearer | header | query | env
    name: str = ""              # اسم الترويسة / معامل الرابط / متغيّر البيئة
    key: str = ""


class MCPFormIn(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    transport: str = "http"     # http | stdio
    url: str = ""
    http_mode: str = ""         # streamable | sse (فارغ = تلقائي)
    command: str = ""
    args: List[str] = []
    auth: MCPAuthIn = MCPAuthIn()
    headers: Dict[str, str] = {}
    env: Dict[str, str] = {}
    allowed_tools: Optional[List[str]] = None
    research_tool: str = ""
    scope: Dict[str, bool] = {"coordinator": True, "employees": True}


def _form_errors(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - فشل الاتصال/البروتوكول/التشغيل
        raise HTTPException(502, f"تعذّر الاتصال بالخادم: {exc}") from exc


class SnippetIn(BaseModel):
    text: str


@router.post("/mcps/parse-snippet")
def parse_mcp_snippet(body: SnippetIn,
                      user: User = Depends(require("system.mcp.manage"))) -> Dict[str, Any]:
    """مقتطف اتصال من المزوّد (curl / Python / CLI / JSON) ← حقول نموذج الإضافة السريعة.
    قراءة نحوية فقط — لا يُنفَّذ أي أمر أو كود من المقتطف."""
    from app.aeos.mcp import snippet_import as SI

    if len(body.text) > 20000:
        raise HTTPException(413, "المقتطف أطول من 20 ألف حرف")
    try:
        parsed = SI.parse(body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "detected": parsed["detected"], "warnings": parsed["warnings"],
            "forms": [SI.to_form(s) for s in parsed["servers"]]}


@router.post("/mcps/test-connection")
def test_mcp_connection(body: MCPFormIn,
                        user: User = Depends(require("system.mcp.manage"))) -> Dict[str, Any]:
    """اختبار الاتصال من نموذج الإضافة السريعة — يكتشف الأدوات ولا يحفظ شيئاً."""
    return {"ok": True, **_form_errors(lambda: _svc().test_form(body.model_dump()))}


@router.post("/mcps")
def add_mcp(body: MCPFormIn, user: User = Depends(require("system.mcp.manage"))) -> Dict[str, Any]:
    """إضافة سريعة: الاسم + الاتصال (رابط HTTP أو أمر stdio) + المفتاح وطريقة إرساله.
    يتصل ويكتشف الأدوات قبل الحفظ، ثم يصير الخادم مدمجاً ويتصل به منسّق كل مالك."""
    return {"ok": True, "server": _form_errors(lambda: _svc().add_from_form(body.model_dump(),
                                                                           added_by=user.email))}


@router.get("/mcps/template")
def mcp_template(user: User = Depends(require("system.mcp.view"))):
    """قالب خادم MCP داخلي (Python قياسي، stdio) لتبنيه بنفسك ثم ترفعه كحزمة."""
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(_svc().SERVER_TEMPLATE, media_type="text/x-python; charset=utf-8",
                             headers={"Content-Disposition": 'attachment; filename="server.py"'})


def _parse_kv(text: str) -> Dict[str, str]:
    out = {}
    for line in (text or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                out[k.strip()] = v.strip()
    return out


@router.post("/mcps/upload")
async def upload_mcp(file: UploadFile = File(...), name: str = Form(""), description: str = Form(""),
                     coordinator: bool = Form(True), employees: bool = Form(True), env: str = Form(""),
                     user: User = Depends(require("system.mcp.manage"))) -> Dict[str, Any]:
    """ملف إعداد JSON (``mcpServers`` أو خادم واحد) أو حزمة خادم ``.py``/``.zip``.
    يتصل النظام بالخادم ويكتشف أدواته قبل الحفظ — خادم لا يعمل لا يُحفظ.
    ``env``: مفاتيح الحزمة الداخلية (سطر KEY=value لكل متغيّر) — تُحفظ خارج git."""
    data = await _read_upload(file)
    try:
        result = _svc().add_from_upload(file.filename or "", data, added_by=user.email, name=name,
                                        description=description, env=_parse_kv(env),
                                        scope={"coordinator": coordinator, "employees": employees})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - فشل تشغيل/اتصال الخادم
        raise HTTPException(502, f"تعذّر تشغيل الخادم أو الاتصال به: {exc}") from exc
    if not result["saved"]:
        raise HTTPException(502, "لم يُحفظ أي خادم: " + "؛ ".join(
            f"{e['name']}: {e['error']}" for e in result["errors"]))
    return {"ok": True, **result}


@router.patch("/mcps/{slug}")
def patch_mcp(slug: str, body: MCPPatchIn,
              user: User = Depends(require("system.mcp.manage"))) -> Dict[str, Any]:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return {"ok": True, "server": _svc().update_mcp(slug, patch)}
    except KeyError:
        raise HTTPException(404, "server not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"تعذّر الاتصال بالخادم بعد التعديل: {exc}") from exc


@router.post("/mcps/{slug}/test")
def test_mcp(slug: str, user: User = Depends(require("system.mcp.manage"))) -> Dict[str, Any]:
    """أعد الاتصال واكتشاف الأدوات (بعد تحديث الخادم أو مفاتيحه)."""
    try:
        return {"ok": True, "server": _svc().update_mcp(slug, {}, rediscover=True)}
    except KeyError:
        raise HTTPException(404, "server not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"فشل الاتصال: {exc}") from exc


@router.delete("/mcps/{slug}")
def delete_mcp(slug: str, user: User = Depends(require("system.mcp.manage"))) -> Dict[str, Any]:
    try:
        deleted = _svc().delete_mcp(slug)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "server not found")
    return {"ok": True, "deleted": slug}


# ─────────────────────────── skills ───────────────────────────
class SkillPatchIn(BaseModel):
    enabled: Optional[bool] = None
    content: Optional[str] = None


@router.get("/skills")
def list_skills(user: User = Depends(require("system.skills.view"))) -> Dict[str, Any]:
    return {"ok": True, "skills": _svc().skills_overview()}


@router.get("/skills/{slug}")
def get_skill(slug: str, user: User = Depends(require("system.skills.view"))) -> Dict[str, Any]:
    from app.aeos.skills import system_skills as SS

    sk = SS.get(slug)
    if sk is None:
        raise HTTPException(404, "skill not found")
    return {"ok": True, "skill": sk, "content": SS.raw(slug)}


@router.post("/skills/upload")
async def upload_skill(file: UploadFile = File(...), overwrite: bool = Form(False),
                       user: User = Depends(require("system.skills.manage"))) -> Dict[str, Any]:
    from app.aeos.skills import system_skills as SS

    data = await _read_upload(file)
    try:
        return {"ok": True, "skill": SS.save_upload(file.filename or "", data, added_by=user.email,
                                                    overwrite=overwrite)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/skills/{slug}")
def patch_skill(slug: str, body: SkillPatchIn,
                user: User = Depends(require("system.skills.manage"))) -> Dict[str, Any]:
    from app.aeos.skills import system_skills as SS

    try:
        return {"ok": True, "skill": SS.update(slug, enabled=body.enabled, content=body.content)}
    except KeyError:
        raise HTTPException(404, "skill not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/skills/{slug}")
def delete_skill(slug: str, user: User = Depends(require("system.skills.manage"))) -> Dict[str, Any]:
    from app.aeos.skills import system_skills as SS

    try:
        deleted = SS.delete(slug)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "skill not found")
    return {"ok": True, "deleted": slug}
