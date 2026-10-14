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
                                "employee": {"provider": "", "model": ""}, "status": {}}
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


def _connected(provider: str, keys: Dict[str, str]) -> bool:
    return provider in CAT.PROVIDERS and (not CAT.PROVIDERS[provider]["needs_key"] or bool(keys.get(provider)))


# ─────────────────────────── للواجهة ───────────────────────────
def public_view(user_id: int) -> Dict[str, Any]:
    d = _load(user_id)
    providers = []
    for p in CAT.public_catalog():
        key = d["keys"].get(p["id"], "")
        providers.append({**p, "has_key": bool(key), "key_last4": key[-4:] if len(key) >= 8 else "",
                          "connected": _connected(p["id"], d["keys"]),
                          "last_test": d["status"].get(p["id"])})
    return {"providers": providers, "chat": d["chat"], "employee": d["employee"],
            "default": {"provider": "openrouter", "label": "إعداد النظام (OpenRouter)"}}


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
    return CAT.list_models(provider, _load(user_id)["keys"].get(provider, ""))


def test(user_id: int, provider: str, model: str = "") -> Dict[str, Any]:
    key = _load(user_id)["keys"].get(provider, "")
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
        if not _connected(provider, _load(user_id)["keys"]):
            raise ValueError(f"اربط مزوّد {CAT.PROVIDERS[provider]['label']} بمفتاحه أولاً")
        model = model or CAT.PROVIDERS[provider]["default_model"]
        if not model:
            raise ValueError("اختر نموذجاً")
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
    if not provider or not model or not _connected(provider, d["keys"]):
        return None
    from app.core.llm_provider import LLMEndpoint, resolve_llm

    base = resolve_llm()   # التضمين والصور وحدود الطول من إعداد النظام
    kw = CAT.client_kwargs(provider, d["keys"].get(provider, ""))
    return LLMEndpoint(provider=provider, api_key=kw["api_key"], base_url=kw["base_url"], model=model,
                       embedding_model=base.embedding_model, image_model=base.image_model,
                       max_tokens=base.max_tokens, temperature=base.temperature)
