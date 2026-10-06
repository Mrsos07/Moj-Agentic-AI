"""
طبقة العزل الموحّدة: **من يملك ماذا؟** — مرجع واحد تستخدمه كل نقاط الدخول
(REST، WebSocket، المنسّق، الخدمات).

لماذا؟ (قيس قبل الكتابة)
- 25 نقطة في ``/api/aeos`` بلا أي تحقق دخول: قائمة موظفي **كل** المستخدمين، حذف
  موظف، تشغيل دورة، تطبيق هوية على X، قراءة السجلات وذاكرة الرسم.
- ``app.auth.dependencies.get_current_user`` **يعيد None** بلا توكن بدل الرفض، ونقاط X
  تستخدمه: مجهول يستطيع النشر من أي حساب، وحذف كوكيزه، وتعديل ملفه.
- الـWebSocket يثق بـ``user_id`` الذي يرسله المتصفح: أي أحد ينتحل أي مستخدم.

القواعد:
- **الدخول إلزامي** (``require_user``) ويحترم القائمة السوداء للتوكنات (تسجيل الخروج).
- **الملكية تُشتق من الخادم لا من الطلب**: ``user_id`` في جسم الطلب يُتجاهل.
- **غير المالك يرى 404 لا 403** — لا نؤكد وجود مورد لا يملكه.
- المشرف (``is_admin``) يتجاوز الملكية صراحةً.
- ملكية حساب X (ملف الكوكيز) = سجل ``social_accounts`` للمستخدم، أو موظف ذكي مملوك له
  على هذا الحساب. ملف بلا مالك مسجّل لا يراه إلا المشرف.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Set

from fastapi import HTTPException

from app.auth.middleware import get_current_user as require_user  # noqa: F401 - مُصدَّر
from app.db.database import SessionLocal
from app.db.models import Conversation, SocialAccount, User

__all__ = [
    "require_user", "user_from_token", "is_admin", "owns_employee", "employee_or_404",
    "label_owner_ids", "owns_label", "label_or_404", "visible_labels",
    "conversation_or_404", "employee_ids_for",
]


def is_admin(user: Optional[User]) -> bool:
    return bool(user is not None and getattr(user, "is_admin", False))


def user_from_token(token: str) -> Optional[User]:
    """توثيق خارج تبعيات FastAPI (الـWebSocket): نفس قواعد ``require_user`` حرفياً —
    توكن صالح، غير مُبطَل بتسجيل خروج، ومستخدم موجود ونشط."""
    from app.auth.security import decode_token
    from app.db.redis_client import RedisClient

    token = (token or "").strip()
    if not token or RedisClient.is_blacklisted(token):
        return None
    data = decode_token(token)
    if data is None:
        return None
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == data.user_id).first()
        if user is not None and getattr(user, "is_active", True) is False:
            return None
        if user is not None:
            db.expunge(user)
        return user
    finally:
        db.close()


# ─────────────────────────── employees ───────────────────────────
def owns_employee(cfg, user: Optional[User]) -> bool:
    return cfg is not None and user is not None and (is_admin(user) or cfg.user_id == user.id)


def employee_or_404(employee_id: str, user: User):
    from app.aeos.bootstrap import get_platform

    cfg = get_platform().repository.get(employee_id)
    if not owns_employee(cfg, user):
        raise HTTPException(404, "employee not found")
    return cfg


def employee_ids_for(user: User) -> List[str]:
    from app.aeos.bootstrap import get_platform

    return [c.id for c in get_platform().repository.list_all() if owns_employee(c, user)]


# ─────────────────────────── X accounts (cookie labels) ───────────────────────────
def _norm(label: str) -> str:
    return (label or "").strip().lstrip("@").lower()


def label_owner_ids(label: str) -> Set[int]:
    """مالكو حساب X: سجلات ``social_accounts`` + موظفون أذكياء على هذا الحساب."""
    from app.db.models import AIEmployee

    key = _norm(label)
    if not key:
        return set()
    db = SessionLocal()
    try:
        owners = {r.user_id for r in db.query(SocialAccount).filter(SocialAccount.platform == "x").all()
                  if r.user_id and key in {_norm(r.username), _norm(r.account_label or ""),
                                           _norm((r.cookie_filename or "").removesuffix(".json"))}}
        owners |= {r.user_id for r in db.query(AIEmployee).all()
                   if r.user_id and key in {_norm(r.account_username), _norm(r.cookie_label or "")}}
        return owners
    finally:
        db.close()


def owns_label(label: str, user: Optional[User]) -> bool:
    return user is not None and (is_admin(user) or user.id in label_owner_ids(label))


def label_or_404(label: str, user: User) -> str:
    if not owns_label(label, user):
        raise HTTPException(404, f"الحساب '{label}' غير موجود")
    return label


def visible_labels(labels: Iterable[str], user: User) -> List[str]:
    labels = list(labels)
    if is_admin(user):
        return labels
    return [lb for lb in labels if user.id in label_owner_ids(lb)]


# ─────────────────────────── conversations ───────────────────────────
def conversation_or_404(conversation_id: int, user: User, db) -> Conversation:
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if conv is None or not (is_admin(user) or conv.user_id == user.id):
        raise HTTPException(404, "conversation not found")
    return conv
