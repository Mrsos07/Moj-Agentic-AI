from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.db.database import get_db
from app.db.models import User, XAccount, Conversation, Message, SocialAccount
from app.auth.admin_dependencies import require_admin

router = APIRouter(prefix="/api/admin", tags=["Admin"])


class SocialAccountInfo(BaseModel):
    id: int
    platform: str
    username: str
    status: str
    last_login: Optional[str] = None
    last_used: Optional[str] = None


class ConversationInfo(BaseModel):
    id: int
    title: Optional[str] = None
    messages_count: int
    created_at: str
    updated_at: str


class UserListResponse(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    profile_picture: Optional[str] = None
    is_admin: bool
    role: str = "user"
    permissions: List[str] = []
    is_active: bool
    created_at: str
    x_accounts_count: int
    conversations_count: int
    social_accounts_count: int
    employees_count: int = 0
    
    class Config:
        from_attributes = True


class UserDetailResponse(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    profile_picture: Optional[str] = None
    is_admin: bool
    is_active: bool
    created_at: str
    social_accounts: List[SocialAccountInfo]
    conversations: List[ConversationInfo]
    total_messages: int
    
    class Config:
        from_attributes = True


class DashboardStats(BaseModel):
    total_users: int
    active_users: int
    inactive_users: int
    admin_users: int
    total_x_accounts: int
    users_created_today: int


class UpdateUserRequest(BaseModel):
    is_admin: Optional[bool] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    name: Optional[str] = None


def _user_row(db: Session, user: User) -> "UserListResponse":
    """صف المستخدم كاملاً (كان update_user يعيد صفاً ناقص الحقول الإلزامية ⇒ خطأ 500)."""
    from app.auth.roles import permissions_of, role_of
    from app.db.models import AIEmployee

    return UserListResponse(
        id=user.id, email=user.email, name=user.name, profile_picture=user.profile_picture,
        is_admin=user.is_admin, role=role_of(user), permissions=permissions_of(user),
        is_active=user.is_active, created_at=user.created_at.isoformat(),
        x_accounts_count=len(user.x_accounts),
        conversations_count=db.query(Conversation).filter(Conversation.user_id == user.id).count(),
        social_accounts_count=db.query(SocialAccount).filter(SocialAccount.user_id == user.id).count(),
        employees_count=db.query(AIEmployee).filter(AIEmployee.user_id == user.id).count(),
    )


class MessageResponse(BaseModel):
    message: str
    success: bool


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    admin_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    total_users = db.query(User).count()
    active_users = db.query(User).filter(User.is_active == True).count()
    inactive_users = db.query(User).filter(User.is_active == False).count()
    admin_users = db.query(User).filter(User.role.in_(("admin", "developer"))).count()
    total_x_accounts = db.query(XAccount).count()
    
    today = datetime.utcnow().date()
    users_created_today = db.query(User).filter(
        func.date(User.created_at) == today
    ).count()
    
    return DashboardStats(
        total_users=total_users,
        active_users=active_users,
        inactive_users=inactive_users,
        admin_users=admin_users,
        total_x_accounts=total_x_accounts,
        users_created_today=users_created_today
    )


@router.get("/users", response_model=List[UserListResponse])
async def get_all_users(
    admin_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    skip: int = 0,
    limit: int = 100
):
    users = db.query(User).offset(skip).limit(limit).all()
    return [_user_row(db, user) for user in users]


@router.put("/users/{user_id}", response_model=UserListResponse)
async def update_user(
    user_id: int,
    request: UpdateUserRequest,
    admin_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user.id == admin_user.id and request.is_active == False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account"
        )
    
    from app.auth.roles import ROLES, has, role_of, set_role

    # is_admin القديم يُترجم إلى دور (للواجهة القديمة)
    new_role = request.role
    if new_role is None and request.is_admin is not None:
        new_role = "admin" if request.is_admin else "user"
    if new_role is not None and new_role != role_of(user):
        if new_role not in ROLES:
            raise HTTPException(status_code=400, detail=f"دور غير معروف: {new_role}")
        if user.id == admin_user.id:
            raise HTTPException(status_code=400, detail="لا يمكنك تغيير دورك بنفسك")
        # منح «المطوّر» أو سحبه للمطوّر وحده: المسؤول لا يرقّي نفسه أو غيره لصلاحية تشغيل كود
        touches_dev = "developer" in (new_role, role_of(user))
        if touches_dev and not has(admin_user, "admin.roles.grant_developer"):
            raise HTTPException(status_code=403, detail="منح دور المطوّر أو سحبه للمطوّر فقط")
    from app.aeos.security.gateway import api_decision
    api_decision("admin.user.update", "", {"account": user.id, "changes": request.model_dump(exclude_unset=True)})
    if new_role is not None and new_role != role_of(user):
        set_role(user, new_role)
    if request.is_active is not None:
        user.is_active = request.is_active
        if request.is_active is False:
            from app.auth.token_guard import revoke_user

            revoke_user(user.id)     # التعطيل يُنهي جلساته القائمة (وتُرفض حتى لو أُعيد تفعيله)
    if request.name is not None:
        user.name = request.name
    
    db.commit()
    db.refresh(user)
    return _user_row(db, user)


@router.get("/permissions")
async def get_permissions(admin_user: User = Depends(require_admin)):
    """مصفوفة الأدوار والصلاحيات + صلاحيات المستخدم الحالي."""
    from app.auth.roles import matrix, permissions_of, role_of

    return {**matrix(), "me": {"role": role_of(admin_user), "permissions": permissions_of(admin_user)}}


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user_details(
    user_id: int,
    admin_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    """الحصول على تفاصيل المستخدم الكاملة مع المحادثات والحسابات"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # الحسابات الاجتماعية
    social_accounts = db.query(SocialAccount).filter(
        SocialAccount.user_id == user_id
    ).all()
    
    social_accounts_info = [
        SocialAccountInfo(
            id=acc.id,
            platform=acc.platform,
            username=acc.username,
            status=acc.status,
            last_login=acc.last_login.isoformat() if acc.last_login else None,
            last_used=acc.last_used.isoformat() if acc.last_used else None
        )
        for acc in social_accounts
    ]
    
    # المحادثات
    conversations = db.query(Conversation).filter(
        Conversation.user_id == user_id
    ).all()
    
    conversations_info = []
    total_messages = 0
    
    for conv in conversations:
        messages_count = db.query(Message).filter(
            Message.conversation_id == conv.id
        ).count()
        total_messages += messages_count
        
        conversations_info.append(ConversationInfo(
            id=conv.id,
            title=conv.title,
            messages_count=messages_count,
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat()
        ))
    
    return UserDetailResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        profile_picture=user.profile_picture,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at.isoformat(),
        social_accounts=social_accounts_info,
        conversations=conversations_info,
        total_messages=total_messages
    )


@router.delete("/users/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: int,
    admin_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user.id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account"
        )
    
    # كل ما يخص المستخدم يُمحى معه: كان الحذف يترك موظفيه يعملون بلا مالك، ومحادثاته
    # وذاكرة منسّقه يتيمة في قاعدة البيانات (app.aeos.memory.lifecycle)
    from app.aeos.security.gateway import api_decision
    api_decision("admin.user.delete", "", {"account": user.id})
    from app.aeos.memory.lifecycle import purge_user

    from app.auth.token_guard import revoke_user

    purge_user(user.id, db)
    revoke_user(user.id)     # SQLite قد يعيد استخدام المعرّف: توكن قديم لا يصير لمستخدم جديد
    db.delete(user)
    db.commit()
    
    return MessageResponse(
        message=f"User {user.email} deleted successfully",
        success=True
    )
