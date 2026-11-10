"""جسر كابتشا Arkose لتدفق الدخول (x_auth).

``x_auth`` يعمل كحزمة مستقلة (cwd = مجلد x_auth، أو ``python -m x_auth.login_sync``)
فلا يستطيع استيراد ``app``؛ نحمّل الوحدة المشتركة ``app/core/captcha_solver.py``
بالبحث صعوداً في المسار — نفس الملف من أي نسخة من نسخ x_auth.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict


def _core_file() -> Path:
    for parent in Path(__file__).resolve().parents:
        for cand in (parent / "core" / "captcha_solver.py",
                     parent / "app" / "core" / "captcha_solver.py"):
            if cand.exists():
                return cand
    raise ImportError("لم أجد app/core/captcha_solver.py — تشغيل الدخول خارج المستودع؟")


def _load():
    name = "moj_captcha_solver"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(name, _core_file())
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_solver = _load()

X_LOGIN_URL = _solver.X_LOGIN_URL


def enabled() -> bool:
    return _solver.is_enabled()


def status(check_balance: bool = False) -> Dict[str, Any]:
    return _solver.status(check_balance=check_balance)


def solve_arkose(blob: str = "", public_key: str = "", page_url: str = "",
                 user_agent: str = "") -> str:
    """توكن Arkose لصفحة دخول X. يرفع ``CaptchaError`` برسالة عربية عند الفشل."""
    return _solver.solve_x_login_captcha(
        blob=blob, public_key=public_key,
        page_url=page_url or X_LOGIN_URL, user_agent=user_agent)
