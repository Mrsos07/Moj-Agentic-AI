"""
مزوّد النماذج — إعدادات كل مستخدم لنفسه فقط (الهوية من التوكن، لا من الطلب).

GET    /api/llm/providers                  الحالة: المزوّدون، ربط كلٍّ منهم، اختيار الشات والموظفين
PUT    /api/llm/providers/{provider}/key   حفظ/حذف المفتاح (مشفّراً) — لا يُعاد أبداً
GET    /api/llm/providers/{provider}/models  نماذج الدردشة المتاحة بمفتاح المستخدم
POST   /api/llm/providers/{provider}/test  نداء صغير فعلي يكشف المفتاح الخاطئ ونفاد الرصيد
PUT    /api/llm/selection/{purpose}        chat | employee — provider فارغ ⇒ إعداد النظام
GET    /api/llm/openrouter/models          قائمة OpenRouter نفسها (اسم/سعر/سياق، الداعمة للأدوات)
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.ownership import require_user
from app.db.models import User
from app.services import user_llm as UL

router = APIRouter(prefix="/api/llm", tags=["LLM providers"])


class KeyIn(BaseModel):
    api_key: str = ""


class TestIn(BaseModel):
    model: str = ""


class SelectionIn(BaseModel):
    provider: str = ""
    model: str = ""


def _err(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/providers")
def get_providers(user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"ok": True, **UL.public_view(user.id)}


@router.put("/providers/{provider}/key")
def put_key(provider: str, body: KeyIn, user: User = Depends(require_user)) -> Dict[str, Any]:
    _err(lambda: UL.set_key(user.id, provider, body.api_key))
    return {"ok": True, **UL.public_view(user.id)}


@router.get("/providers/{provider}/models")
def get_models(provider: str, user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"ok": True, "provider": provider, "models": _err(lambda: UL.models(user.id, provider))}


@router.post("/providers/{provider}/test")
def post_test(provider: str, body: TestIn, user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"ok": True, **_err(lambda: UL.test(user.id, provider, body.model))}


@router.get("/openrouter/models")
def get_openrouter_models(user: User = Depends(require_user)) -> Dict[str, Any]:
    return {"ok": True, **_err(lambda: UL.openrouter_models(user.id))}


@router.put("/selection/{purpose}")
def put_selection(purpose: str, body: SelectionIn, user: User = Depends(require_user)) -> Dict[str, Any]:
    sel = _err(lambda: UL.set_selection(user.id, purpose, body.provider, body.model))
    return {"ok": True, "purpose": purpose, **sel}
