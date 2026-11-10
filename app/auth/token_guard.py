"""
التحقق الموحّد من توكن الدخول — **مرجع واحد** لكل المسارات (REST بنوعيه، WebSocket، /verify).

لماذا (مراجعة 2026-11-08):
- ``middleware.get_current_user`` يفحص الإبطال لكنه يقبل مستخدماً معطّلاً بتوكن صالح.
- ``dependencies.require_current_user`` (``/me``، تيليجرام، الترندات…) يتجاوز الإبطال وحالة النشاط.
- تسجيل الخروج يعيد «نجح» حين يتعطّل Redis، والتوكن يبقى صالحاً 24 ساعة.
- مفتاح Redis كان التوكن الكامل (``blacklist:<jwt>``).

القواعد هنا: توقيع + انتهاء (``decode_token``) ← غير مُبطَل ← المستخدم موجود ونشط ← صادر بعد
آخر «إبطال كل جلساته» (تغيير كلمة المرور/التعطيل). الإبطال يُخزَّن **بتجزئة** التوكن في Redis
وفي ملف محلي دائم (``data/auth_revocations.json``) معاً: تعطّل Redis لا يلغي تسجيل الخروج، وإعادة
التشغيل لا تُحيي توكناً أُبطل.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("auth.token_guard")

_PATH = Path(__file__).resolve().parents[2] / "data" / "auth_revocations.json"
_lock = threading.Lock()
_state: Optional[Dict[str, Dict[str, float]]] = None
_MAX_TTL = 7 * 86400


def digest(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _load() -> Dict[str, Dict[str, float]]:
    global _state
    if _state is None:
        try:
            raw = json.loads(_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        _state = {"tokens": dict(raw.get("tokens") or {}), "users": dict(raw.get("users") or {})}
    return _state


def _save(state: Dict[str, Dict[str, float]]) -> None:
    now = time.time()
    state["tokens"] = {k: v for k, v in state["tokens"].items() if v > now}
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(_PATH)


def _redis():
    from app.db.redis_client import RedisClient

    return RedisClient.get_client()


def revoke_token(token: str, exp: Optional[float] = None) -> bool:
    """أبطل توكناً واحداً (تسجيل الخروج). True فقط إن حُفظ الإبطال فعلاً (الملف المحلي على الأقل)."""
    h = digest(token)
    until = float(exp) if exp else time.time() + _MAX_TTL
    ttl = max(1, int(until - time.time()))
    try:
        client = _redis()
        if client:
            client.setex(f"revoked:{h}", ttl, "1")
    except Exception as exc:  # noqa: BLE001 - المحلي هو الضمان
        logger.warning("redis revoke failed: %s", exc)
    try:
        with _lock:
            state = _load()
            state["tokens"][h] = until
            _save(state)
        return True
    except OSError as exc:
        logger.error("local revoke failed: %s", exc)
        return False


def revoke_user(user_id: int) -> bool:
    """أبطل **كل** توكنات المستخدم الصادرة حتى الآن (تغيير كلمة المرور، التعطيل، الحذف)."""
    try:
        with _lock:
            state = _load()
            state["users"][str(int(user_id))] = time.time()
            _save(state)
        return True
    except OSError as exc:
        logger.error("revoke_user failed: %s", exc)
        return False


def is_revoked(token: str, user_id: Optional[int] = None, iat: Optional[float] = None) -> bool:
    h = digest(token)
    with _lock:
        state = _load()
        until = state["tokens"].get(h)
        if until and until > time.time():
            return True
        cutoff = state["users"].get(str(user_id)) if user_id is not None else None
    if cutoff and (iat is None or iat < cutoff - 1):     # ثانية تسامح لساعة الإصدار
        return True
    try:
        client = _redis()
        return bool(client and client.exists(f"revoked:{h}"))
    except Exception:  # noqa: BLE001
        return False


def authenticate(token: str, db=None) -> Tuple[Optional[object], str]:
    """(المستخدم، "") أو (None، السبب).

    ``db``: جلسة الطلب (تبعيات FastAPI) — يُعاد المستخدم مرتبطاً بها كي تُحفظ تعديلاته.
    بلا ``db`` (WebSocket): جلسة مؤقتة والمستخدم مفصول عنها."""
    from app.auth.security import decode_token
    from app.db.database import SessionLocal
    from app.db.models import User

    token = (token or "").strip()
    if not token:
        return None, "missing"
    data = decode_token(token)
    if data is None:
        return None, "invalid_or_expired"
    if is_revoked(token, data.user_id, data.iat):
        return None, "revoked"
    own = db is None
    db = SessionLocal() if own else db
    try:
        user = db.query(User).filter(User.id == data.user_id).first()
        if user is None:
            return None, "user_not_found"
        if getattr(user, "is_active", True) is False:
            return None, "inactive"
        if own:
            db.expunge(user)
        return user, ""
    finally:
        if own:
            db.close()
