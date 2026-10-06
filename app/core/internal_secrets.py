"""
أسرار الخدمات الداخلية: قوية، فريدة لكل تثبيت، وخارج git.

لماذا؟ (قيس قبل الكتابة) الخوادم الداخلية كانت محمية بقيم افتراضية **منشورة في
المستودع على GitHub**:
- خادم X Suite (5789) — الذي يملك كوكيز كل الحسابات وينشر باسمها — توكن الـAPI فيه
  ``your-secure-token-here``، وكلمة مرور لوحته ``Mm112233@@``، ومفتاح جلساته
  ``change-me`` (من يعرفه يزوّر جلسة دخول للوحة).
- خادم loginx (5000): مفتاح جلساته ``x_automation_2026`` ومفتاح الـAPI ثابت في الكود.

هنا: عند أول تشغيل يُولَّد لكل مفتاح سرّ عشوائي قوي، ويُحفظ في
``data/internal_secrets.env`` (مجلد ``data/`` خارج git)، ويُوضع في بيئة العملية فترثه
الخوادم الابنة تلقائياً (x_bridge وidentity_bridge يمرّران ``os.environ``). أي قيمة ضبطها
المالك صراحةً في ``.env`` تُحترم — إلا إن كانت إحدى القيم الافتراضية المنشورة.

الملف عمداً بلا أي استيراد من التطبيق: تستطيع الخوادم الابنة استيراده وحدها.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Dict, List

#: قيم افتراضية منشورة في المستودع — لا تُقبل أبداً كأسرار
KNOWN_WEAK = frozenset({
    "", "your-secure-token-here", "change-me", "Mm112233@@", "sk-loginx-2026-secret",
    "x_automation_2026",
})
KEYS = ("XSUITE_API_TOKENS", "XSUITE_SECRET_KEY", "XSUITE_ADMIN_PASS",
        "LOGINX_API_KEY", "LOGINX_SECRET_KEY")
FILE = Path(__file__).resolve().parent.parent.parent / "data" / "internal_secrets.env"


def _read() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for line in FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return out


def ensure() -> List[str]:
    """ضع سرّاً قوياً لكل مفتاح في البيئة. يعيد أسماء المفاتيح المولَّدة لأول مرة."""
    stored = _read()
    generated: List[str] = []
    for key in KEYS:
        if os.environ.get(key, "").strip() not in KNOWN_WEAK:
            continue                              # ضبطه المالك صراحةً بقيمة غير منشورة
        val = stored.get(key, "")
        if val in KNOWN_WEAK:
            val = secrets.token_urlsafe(32)
            stored[key] = val
            generated.append(key)
        os.environ[key] = val
    if generated:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(
            "# أسرار الخدمات الداخلية — مولَّدة تلقائياً. لا تشاركها ولا ترفعها إلى git.\n"
            "# XSUITE_ADMIN_PASS = كلمة مرور لوحة X Suite (http://127.0.0.1:5789، المستخدم admin)\n"
            + "".join(f"{k}={v}\n" for k, v in stored.items()),
            encoding="utf-8")
        try:
            os.chmod(FILE, 0o600)
        except OSError:
            pass
    return generated


def get(key: str) -> str:
    """قيمة سرّ داخلي (يضمن توليده). للخوادم الابنة حين تعمل وحدها."""
    if os.environ.get(key, "").strip() in KNOWN_WEAK:
        ensure()
    return os.environ.get(key, "")
