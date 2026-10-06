"""
حجب الأسرار من السجلات — طبقة دفاع ثانية خلف «لا تضع السرّ في الرابط أصلاً».

قيس في سجل الخادم الحي: كل اتصال WebSocket كان يُكتب هكذا
``"WebSocket /ws/chat?token=eyJhbGciOi…" [accepted]`` — توكن دخول صالح 24 ساعة في
ملف نصي يقرؤه كل من يرى الطرفية أو السجل أو لقطة الشاشة.
"""
from __future__ import annotations

import logging
import re

_PATTERNS = [
    # ?token=… / &key=… / sig=… في أي رابط
    (re.compile(r"(?i)\b(token|access_token|key|api_key|sig|password)=([^&\s\"']+)"), r"\1=***"),
    # ترويسة Authorization
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"), r"\1 ***"),
    # أي JWT ظاهر (ثلاثة مقاطع base64url تبدأ بـ eyJ)
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "***JWT***"),
    # مفاتيح المزوّدين (OpenRouter/OpenAI/Tavily)
    (re.compile(r"\b(sk-or-v1-|sk-|tvly-)[A-Za-z0-9_-]{12,}"), r"\1***"),
]


def redact(text: str) -> str:
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 - رسالة لا تُنسَّق: اتركها كما هي
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


_INSTALLED = False
#: المسجّلات التي تكتب الروابط (access) وأحداث الاتصال («WebSocket … [accepted]» من uvicorn.error)
_LOGGERS = ("uvicorn.access", "uvicorn.error", "uvicorn", "fastapi", "httpx", "requests", "")


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    f = RedactSecretsFilter()
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        logger.addFilter(f)
        for h in logger.handlers:
            h.addFilter(f)
    _INSTALLED = True
