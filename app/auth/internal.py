"""
روابط callback موقّعة للخدمات الداخلية (loginx، ناشر الجدولة في app/x).

لماذا؟ الحماية كانت «يقبل من localhost فقط»، لكن كل طلب يمرّ عبر بروكسي Vite يصل
من 127.0.0.1 أيضاً، فأي متصفح يستطيع تزوير إشعار «تم تسجيل الدخول» أو «نُشرت
تغريدتك» داخل شات أي مستخدم. التوقيع يُضمَّن في رابط الـcallback نفسه (HMAC بسرّ
الخادم على الغرض + المعرّف)، فالمُرسِل لا يحتاج أي تعديل: يستدعي الرابط كما أُعطي.
"""
from __future__ import annotations

import hashlib
import hmac


def _secret() -> bytes:
    from app.auth.security import SECRET_KEY

    return SECRET_KEY.encode("utf-8")


def sign(purpose: str, subject: object) -> str:
    return hmac.new(_secret(), f"{purpose}:{subject}".encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def signed_url(url: str, purpose: str, subject: object) -> str:
    return f"{url}{'&' if '?' in url else '?'}sig={sign(purpose, subject)}"


def verify(purpose: str, subject: object, sig: str) -> bool:
    return bool(sig) and hmac.compare_digest(sign(purpose, subject), str(sig))
