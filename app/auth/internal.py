"""
روابط callback موقّعة للخدمات الداخلية (loginx، ناشر الجدولة في app/x).

لماذا؟ الحماية كانت «يقبل من localhost فقط»، لكن كل طلب يمرّ عبر بروكسي Vite يصل
من 127.0.0.1 أيضاً، فأي متصفح يستطيع تزوير إشعار «تم تسجيل الدخول» أو «نُشرت
تغريدتك» داخل شات أي مستخدم. التوقيع يُضمَّن في رابط الـcallback نفسه، فالمُرسِل لا
يحتاج أي تعديل: يستدعي الرابط كما أُعطي.

v2 (مراجعة 2026-11-08): كان التوقيع ثابتاً لنفس (الغرض، المعرّف) وبسرّ JWT نفسه، فرابط
مسرّب يُعاد استخدامه بلا حد. الآن:
- **مفتاح منفصل**: ``INTERNAL_CALLBACK_SECRET`` إن ضُبط، وإلا مشتق من سرّ الخادم بفصل نطاق
  (HMAC بوسم ثابت) — توقيع callback لا يصلح توقيعاً لـJWT ولا العكس.
- **مهلة**: ``exp`` داخل التوقيع (الدخول 30 دقيقة؛ الجدولة حتى موعد النشر + يوم).
- **مرة واحدة**: ``nonce`` عشوائي داخل التوقيع، يُستهلك عند أول قبول (إعادة الطلب = رفض).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Dict, Optional

_TTL = {"login": 30 * 60, "schedule": 45 * 86400}
_DEFAULT_TTL = 3600
_used: Dict[str, float] = {}
_used_lock = threading.Lock()


def _secret() -> bytes:
    explicit = os.getenv("INTERNAL_CALLBACK_SECRET", "").strip()
    if explicit:
        return explicit.encode("utf-8")
    from app.auth.security import SECRET_KEY

    return hmac.new(SECRET_KEY.encode("utf-8"), b"moj-internal-callback-v2", hashlib.sha256).digest()


def sign(purpose: str, subject: object, exp: int, nonce: str) -> str:
    msg = f"v2:{purpose}:{subject}:{int(exp)}:{nonce}".encode("utf-8")
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def signed_url(url: str, purpose: str, subject: object, ttl: Optional[int] = None) -> str:
    exp = int(time.time()) + int(ttl or _TTL.get(purpose, _DEFAULT_TTL))
    nonce = secrets.token_urlsafe(16)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sig={sign(purpose, subject, exp, nonce)}&exp={exp}&nonce={nonce}"


def verify(purpose: str, subject: object, sig: str, exp: object = None, nonce: str = "") -> bool:
    """صحيح فقط إن طابق التوقيع، ولم تنتهِ المهلة، ولم يُستخدم الـnonce من قبل (ثم يُستهلك)."""
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    now = time.time()
    if not sig or not nonce or exp_i < now:
        return False
    if not hmac.compare_digest(sign(purpose, subject, exp_i, str(nonce)), str(sig)):
        return False
    key = hashlib.sha256(f"{purpose}:{nonce}".encode()).hexdigest()[:32]
    with _used_lock:
        used = _load_used()
        for k in [k for k, v in used.items() if v < now]:
            used.pop(k, None)
        if key in used:
            return False
        used[key] = exp_i
        _save_used(used)          # دائم: إعادة التشغيل لا تُحيي رابطاً استُخدم (الجدولة تعيش أياماً)
    return True


_USED_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "internal_callback_nonces.json")


def _load_used() -> Dict[str, float]:
    if not _used:
        try:
            import json

            with open(_USED_PATH, encoding="utf-8") as f:
                _used.update({str(k): float(v) for k, v in (json.load(f) or {}).items()})
        except (OSError, ValueError):
            pass
    return _used


def _save_used(used: Dict[str, float]) -> None:
    import json

    try:
        os.makedirs(os.path.dirname(_USED_PATH), exist_ok=True)
        tmp = _USED_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(used, f)
        os.replace(tmp, _USED_PATH)
    except OSError:
        pass                      # الذاكرة تبقى حامية داخل العملية
