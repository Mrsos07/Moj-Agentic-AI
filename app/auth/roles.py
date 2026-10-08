"""
الأدوار والصلاحيات — مصدر واحد يُسأل: «هل يحق لهذا المستخدم أن يفعل كذا؟»

ثلاثة أدوار متداخلة (كل دور يرث ما تحته):

| الدور | يملك |
|---|---|
| ``user`` | موظفيه وحساباته ومحادثاته وذاكرته فقط (طبقة العزل) |
| ``admin`` | + لوحة التحكم: عرض المستخدمين وتفعيلهم/تعطيلهم، وعرض خوادم MCP ومهارات النظام |
| ``developer`` | + **إضافة/تعديل/حذف خوادم MCP ومهارات النظام**، ومنح الأدوار كلها |

لماذا «المطوّر» وحده يضيف MCP؟ خادم MCP بنقل stdio **يشغّل أمراً على هذا الجهاز**،
والحزمة المرفوعة كودٌ يُنفَّذ. قبل هذا التغيير كان أي مستخدم مسجّل (والتسجيل مفتوح)
يستطيع إضافة خادم stdio للنظام كله عبر ``POST /api/aeos/tools/external``.

``is_admin`` القديم يبقى متزامناً (admin/developer ⇒ True) لأن الواجهة وبعض المسارات تقرؤه.
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import Depends, HTTPException, status

from app.auth.middleware import get_current_user
from app.db.models import User

ROLES = ("user", "admin", "developer")
ROLE_LABELS = {"user": "مستخدم", "admin": "مسؤول", "developer": "مطوّر"}

#: الصلاحية ← أدنى دور يملكها
PERMISSIONS: Dict[str, str] = {
    "own.manage": "user",                 # موظفوه وحساباته ومحادثاته
    "admin.dashboard": "admin",
    "admin.users.view": "admin",
    "admin.users.manage": "admin",        # تفعيل/تعطيل/حذف + منح دور «مسؤول»
    "system.mcp.view": "admin",
    "system.skills.view": "admin",
    "system.mcp.manage": "developer",     # رفع/تعديل/ربط/حذف خوادم MCP
    "system.skills.manage": "developer",  # رفع/تعديل/حذف مهارات النظام
    "admin.roles.grant_developer": "developer",
}
PERMISSION_LABELS = {
    "own.manage": "إدارة موظفيه وحساباته",
    "admin.dashboard": "فتح لوحة التحكم",
    "admin.users.view": "عرض المستخدمين",
    "admin.users.manage": "تفعيل/تعطيل/حذف المستخدمين",
    "system.mcp.view": "عرض خوادم MCP",
    "system.skills.view": "عرض مهارات النظام",
    "system.mcp.manage": "إضافة/تعديل/حذف خوادم MCP",
    "system.skills.manage": "إضافة/تعديل/حذف مهارات النظام",
    "admin.roles.grant_developer": "منح دور المطوّر",
}


def role_of(user) -> str:
    role = str(getattr(user, "role", "") or "").strip().lower()
    if role in ROLES:
        return role
    return "admin" if getattr(user, "is_admin", False) else "user"


def has(user, perm: str) -> bool:
    if user is None or getattr(user, "is_active", True) is False:
        return False
    need = PERMISSIONS.get(perm)
    return need is not None and ROLES.index(role_of(user)) >= ROLES.index(need)


def permissions_of(user) -> List[str]:
    return [p for p in PERMISSIONS if has(user, p)]


def matrix() -> Dict[str, object]:
    return {
        "roles": [{"id": r, "label": ROLE_LABELS[r]} for r in ROLES],
        "permissions": [{"id": p, "label": PERMISSION_LABELS.get(p, p), "min_role": r,
                         "roles": [x for x in ROLES if ROLES.index(x) >= ROLES.index(r)]}
                        for p, r in PERMISSIONS.items()],
    }


def require(perm: str):
    """تبعية FastAPI: 401 بلا دخول، 403 إن كان الدور أدنى من الصلاحية."""
    async def _dep(current_user: User = Depends(get_current_user)) -> User:
        if not has(current_user, perm):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail=f"صلاحية «{PERMISSION_LABELS.get(perm, perm)}» مطلوبة")
        return current_user
    return _dep


def set_role(user, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"دور غير معروف: {role}")
    user.role = role
    user.is_admin = role in ("admin", "developer")
