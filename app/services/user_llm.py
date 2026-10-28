"""
إعدادات مزوّد النماذج لكل مستخدم: مفاتيحه، ونموذج الشات، ونموذج الموظفين.

- **المفاتيح مشفّرة** بخزنة النظام (Fernet + ``ENCRYPTION_KEY``) قبل الحفظ، ولا تُعاد للواجهة
  أبداً (``has_key`` وآخر 4 أحرف فقط).
- **الاختياران مستقلان** (قرار المالك): ``chat`` للمنسّق ومحادثات الموظفين في الشات،
  و``employee`` لدورات الموظفين الآلية (الكتابة والقرار والتقييم).
- **الرجوع للافتراضي**: بلا اختيار، أو اختيار مزوّد بلا مفتاح ⇒ ``None`` فيستخدم المتصل
  إعداد النظام (OpenRouter). التضمين والصور يبقيان دائماً على إعداد النظام: تغيير نموذج
  التضمين يكسر متجهات الذاكرة المخزّنة.
- **العزل**: كل الدوال تأخذ ``user_id`` صراحةً؛ لا دالة تعيد مفاتيح مستخدم لآخر.
- **OpenRouter على مفتاح النظام** (قرار المالك 2026-10-28): المدير والمطوّر (صلاحية
  ``llm.system_models``) يختاران أي نموذج من قائمة OpenRouter نفسها دون مفتاح خاص — التكلفة
  على رصيد النظام. المستخدم العادي يحتاج مفتاحه، فلا يستنزف أحد الرصيد بنموذج غالٍ.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from app.core import llm_catalog as CAT

logger = logging.getLogger("user_llm")

PURPOSES = ("chat", "employee")
_CACHE_TTL = 60
_cache: Dict[int, tuple] = {}
_lock = threading.Lock()
_vault = None


def _v():
    global _vault
    if _vault is None:
        from app.aeos.security.vault import build_vault

        _vault = build_vault()
    return _vault


def _session():
    from app.db.database import SessionLocal

    return SessionLocal()


def _row(db, user_id: int, create: bool = False):
    from app.db.models import UserLLMSettings

    row = db.query(UserLLMSettings).filter(UserLLMSettings.user_id == int(user_id)).first()
    if row is None and create:
        row = UserLLMSettings(user_id=int(user_id), keys_json="{}", status_json="{}")
        db.add(row)
    return row


def invalidate(user_id: int) -> None:
    with _lock:
        _cache.pop(int(user_id), None)


def _system_openrouter():
    """مفتاح OpenRouter الخاص بالنظام إن كان هو المزوّد الفعّال، وإلا ``None``."""
    from app.core.llm_provider import resolve_llm

    base = resolve_llm()
    return base if base.provider == "openrouter" and base.api_key else None


def _may_use_system(db, user_id: int) -> bool:
    from app.auth.roles import has
    from app.db.models import User

    user = db.query(User).filter(User.id == int(user_id)).first()
    return bool(user) and has(user, "llm.system_models") and _system_openrouter() is not None


def _load(user_id: int) -> Dict[str, Any]:
    """إعدادات المستخدم مفكوكة التشفير — داخلية فقط (لا تُعاد للواجهة). مخزّنة مؤقتاً 60 ث."""
    uid = int(user_id)
    with _lock:
        hit = _cache.get(uid)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return hit[1]
    db = _session()
    try:
        row = _row(db, uid)
        data: Dict[str, Any] = {"keys": {}, "chat": {"provider": "", "model": ""},
                                "employee": {"provider": "", "model": ""}, "status": {},
                                "system_ok": _may_use_system(db, uid)}
        if row is not None:
            for prov, token in (json.loads(row.keys_json or "{}") or {}).items():
                try:
                    data["keys"][prov] = _v().decrypt(token)
                except Exception:  # noqa: BLE001 - مفتاح تشفير تغيّر ⇒ نعامله كغير موجود
                    logger.warning("cannot decrypt %s key for user %s (ENCRYPTION_KEY changed?)", prov, uid)
            data["chat"] = {"provider": row.chat_provider or "", "model": row.chat_model or ""}
            data["employee"] = {"provider": row.employee_provider or "", "model": row.employee_model or ""}
            data["status"] = json.loads(row.status_json or "{}") or {}
    finally:
        db.close()
    with _lock:
        _cache[uid] = (time.time(), data)
    return data


def _via_system(provider: str, d: Dict[str, Any]) -> bool:
    """OpenRouter بلا مفتاح خاص لمن يملك صلاحية رصيد النظام."""
    return provider == "openrouter" and not d["keys"].get("openrouter") and bool(d.get("system_ok"))


def _connected(provider: str, keys: Dict[str, str], system_ok: bool = False) -> bool:
    if provider == "openrouter" and system_ok:
        return True
    return provider in CAT.PROVIDERS and (not CAT.PROVIDERS[provider]["needs_key"] or bool(keys.get(provider)))


# ─────────────────────────── للواجهة ───────────────────────────
def public_view(user_id: int) -> Dict[str, Any]:
    d = _load(user_id)
    providers = []
    for p in CAT.public_catalog():
        key = d["keys"].get(p["id"], "")
        providers.append({**p, "has_key": bool(key), "key_last4": key[-4:] if len(key) >= 8 else "",
                          "connected": _connected(p["id"], d["keys"], d.get("system_ok", False)),
                          "via_system": _via_system(p["id"], d),
                          "last_test": d["status"].get(p["id"])})
    return {"providers": providers, "chat": d["chat"], "employee": d["employee"],
            "system_models": bool(d.get("system_ok")),
            "default": {"provider": "openrouter", "label": "إعداد النظام (OpenRouter)"}}


def openrouter_models(user_id: int) -> Dict[str, Any]:
    """قائمة OpenRouter الغنية (اسم، سعر، سياق) لمنتقي الشات — لمن ربط مفتاحه أو يملك رصيد النظام."""
    d = _load(user_id)
    if not _connected("openrouter", d["keys"], d.get("system_ok", False)):
        raise ValueError("اربط مفتاح OpenRouter من الإعدادات أولاً (رصيد النظام للمدير والمطوّر فقط)")
    return {"models": CAT.openrouter_catalog(), "via_system": _via_system("openrouter", d),
            "has_own_key": bool(d["keys"].get("openrouter"))}


def set_key(user_id: int, provider: str, api_key: Optional[str]) -> None:
    """حفظ (أو حذف إن كان فارغاً) مفتاح مزوّد — مشفّراً. حذف المفتاح يُسقط أي اختيار يعتمد عليه."""
    if provider not in CAT.PROVIDERS or not CAT.PROVIDERS[provider]["needs_key"]:
        raise ValueError("هذا المزوّد لا يحتاج مفتاحاً" if provider in CAT.PROVIDERS else "مزوّد غير معروف")
    api_key = (api_key or "").strip()
    if api_key and (len(api_key) < 8 or len(api_key) > 400 or any(c.isspace() for c in api_key)):
        raise ValueError("صيغة المفتاح غير صحيحة")
    db = _session()
    try:
        row = _row(db, user_id, create=True)
        keys = json.loads(row.keys_json or "{}") or {}
        status = json.loads(row.status_json or "{}") or {}
        if api_key:
            keys[provider] = _v().encrypt(api_key)
        else:
            keys.pop(provider, None)
            for purpose in PURPOSES:
                if getattr(row, f"{purpose}_provider") == provider:
                    setattr(row, f"{purpose}_provider", None)
                    setattr(row, f"{purpose}_model", None)
        status.pop(provider, None)
        row.keys_json, row.status_json = json.dumps(keys), json.dumps(status)
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    invalidate(user_id)


def models(user_id: int, provider: str):
    d = _load(user_id)
    if provider == "openrouter" and _connected("openrouter", d["keys"], d.get("system_ok", False)):
        return [m["id"] for m in CAT.openrouter_catalog()]    # الداعمة للأدوات فقط، بمفتاح أو بدونه
    return CAT.list_models(provider, d["keys"].get(provider, ""))


def test(user_id: int, provider: str, model: str = "") -> Dict[str, Any]:
    d = _load(user_id)
    key = d["keys"].get(provider, "")
    if _via_system(provider, d):
        key = _system_openrouter().api_key
    if CAT.PROVIDERS.get(provider, {}).get("needs_key") and not key:
        raise ValueError("أدخل مفتاح المزوّد أولاً")
    try:
        res = CAT.test(provider, key, model)
        rec = {"ok": True, "model": res["model"], "at": datetime.utcnow().isoformat(timespec="seconds")}
    except ValueError as exc:
        rec = {"ok": False, "error": str(exc), "at": datetime.utcnow().isoformat(timespec="seconds")}
        res = None
    db = _session()
    try:
        row = _row(db, user_id, create=True)
        status = json.loads(row.status_json or "{}") or {}
        status[provider] = rec
        row.status_json = json.dumps(status, ensure_ascii=False)
        db.commit()
    finally:
        db.close()
    invalidate(user_id)
    if res is None:
        raise ValueError(rec["error"])
    return res


def set_selection(user_id: int, purpose: str, provider: str, model: str) -> Dict[str, str]:
    """اختيار نموذج الشات أو الموظفين. provider فارغ ⇒ الرجوع لإعداد النظام."""
    if purpose not in PURPOSES:
        raise ValueError("الاختيار: chat أو employee")
    provider, model = (provider or "").strip(), (model or "").strip()[:200]
    if provider:
        if provider not in CAT.PROVIDERS:
            raise ValueError("مزوّد غير معروف")
        d = _load(user_id)
        if not _connected(provider, d["keys"], d.get("system_ok", False)):
            raise ValueError(f"اربط مزوّد {CAT.PROVIDERS[provider]['label']} بمفتاحه أولاً")
        model = model or CAT.PROVIDERS[provider]["default_model"]
        if not model:
            raise ValueError("اختر نموذجاً")
        if provider == "openrouter":
            try:
                ids = {m["id"] for m in CAT.openrouter_catalog()}
            except ValueError:
                ids = set()                         # القائمة غير متاحة الآن: لا نمنع الحفظ
            if ids and model not in ids:
                raise ValueError(f"«{model}» ليس في قائمة OpenRouter للنماذج الداعمة للأدوات")
    db = _session()
    try:
        row = _row(db, user_id, create=True)
        setattr(row, f"{purpose}_provider", provider or None)
        setattr(row, f"{purpose}_model", (model if provider else None))
        row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    invalidate(user_id)
    return {"provider": provider, "model": model if provider else ""}


# ─────────────────────────── للتشغيل ───────────────────────────
def endpoint_for(user_id: Optional[int], purpose: str):
    """``LLMEndpoint`` لاختيار المستخدم، أو ``None`` ⇒ استخدم إعداد النظام."""
    if not user_id or purpose not in PURPOSES:
        return None
    try:
        d = _load(int(user_id))
    except Exception:  # noqa: BLE001 - قاعدة غير متاحة لا توقف الاستدلال
        logger.debug("user llm settings unavailable", exc_info=True)
        return None
    sel = d.get(purpose) or {}
    provider, model = sel.get("provider") or "", sel.get("model") or ""
    if not provider or not model or not _connected(provider, d["keys"], d.get("system_ok", False)):
        return None
    from app.core.llm_provider import LLMEndpoint, resolve_llm

    base = resolve_llm()   # التضمين والصور وحدود الطول من إعداد النظام
    if _via_system(provider, d):
        # نموذج اختاره المدير/المطوّر من OpenRouter على مفتاح النظام (رصيده)
        return LLMEndpoint(provider="openrouter", api_key=base.api_key, base_url=base.base_url, model=model,
                           embedding_model=base.embedding_model, image_model=base.image_model,
                           max_tokens=base.max_tokens, temperature=base.temperature)
    kw = CAT.client_kwargs(provider, d["keys"].get(provider, ""))
    return LLMEndpoint(provider=provider, api_key=kw["api_key"], base_url=kw["base_url"], model=model,
                       embedding_model=base.embedding_model, image_model=base.image_model,
                       max_tokens=base.max_tokens, temperature=base.temperature)
