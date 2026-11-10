from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr
from datetime import timedelta
from typing import Optional

from app.db.database import get_db
from app.db.models import User
from app.db.redis_client import RedisClient
from app.auth.security import (
    verify_password, 
    hash_password, 
    create_access_token, 
    decode_token,
    Token,
    ACCESS_TOKEN_EXPIRE_MINUTES
)
from app.auth.dependencies import require_current_user
from app.auth.token_guard import authenticate, digest, revoke_token, revoke_user
from app.auth.roles import permissions_of, role_of

router = APIRouter(prefix="/api/auth", tags=["Authentication"])
security = HTTPBearer()


class _LoginThrottle:
    """حدّ محاولات الدخول الفاشلة (لم يكن أي حدّ: تخمين كلمات المرور بلا قيد).

    5 محاولات فاشلة خلال 15 دقيقة لنفس البريد **أو** 20 من نفس العنوان ⇒ 429 حتى تنقضي
    النافذة. النجاح يمسح عدّاد البريد. في الذاكرة (يُصفَّر بإعادة التشغيل) — يكفي لخادم
    محلي واحد ولا يحتاج Redis.
    """

    WINDOW = 15 * 60
    PER_EMAIL = 5
    PER_IP = 20

    def __init__(self) -> None:
        import threading

        self._fails: dict = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list:
        hits = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
        self._fails[key] = hits
        return hits

    def retry_after(self, email: str, ip: str) -> int:
        import time

        now = time.time()
        with self._lock:
            for key, cap in ((f"e:{email}", self.PER_EMAIL), (f"i:{ip}", self.PER_IP)):
                hits = self._recent(key, now)
                if len(hits) >= cap:
                    return int(self.WINDOW - (now - hits[0])) + 1
        return 0

    def fail(self, email: str, ip: str) -> None:
        import time

        now = time.time()
        with self._lock:
            for key in (f"e:{email}", f"i:{ip}"):
                self._recent(key, now).append(now)

    def success(self, email: str) -> None:
        with self._lock:
            self._fails.pop(f"e:{email}", None)


_throttle = _LoginThrottle()


# Request/Response Models
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    profile_picture: Optional[str] = None
    is_admin: bool = False
    role: str = "user"
    permissions: list = []
    is_active: bool = True
    created_at: str
    
    class Config:
        from_attributes = True


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None


class ProfilePictureRequest(BaseModel):
    profile_picture: str


class VerifyResponse(BaseModel):
    valid: bool
    user: Optional[UserResponse] = None


class MessageResponse(BaseModel):
    message: str
    success: bool


@router.post("/register", response_model=Token)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    """Register a new user"""
    
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    hashed_password = hash_password(request.password)
    new_user = User(
        email=request.email,
        password_hash=hashed_password
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Create access token
    access_token = create_access_token(
        data={"sub": str(new_user.id), "email": new_user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    return Token(access_token=access_token)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest, http: Request, db: Session = Depends(get_db)):
    """تسجيل دخول - Login user and return JWT token with user data"""
    email = str(request.email).strip().lower()
    ip = http.client.host if http.client else "-"
    wait = _throttle.retry_after(email, ip)
    if wait:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="محاولات دخول كثيرة — حاول لاحقاً",
            headers={"Retry-After": str(wait)},
        )
    
    # Find user by email
    user = db.query(User).filter(User.email == request.email).first()
    
    # الرد نفسه لكل الحالات (بريد غير موجود / كلمة مرور خاطئة / حساب معطّل) — لا نكشف أيها
    if not user or not verify_password(request.password, user.password_hash) \
            or getattr(user, "is_active", True) is False:
        _throttle.fail(email, ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    _throttle.success(email)
    
    # Create access token
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    # Store session in Redis (optional - won't block if Redis is unavailable)
    try:
        # مفتاح بتجزئة التوكن: أول 32 حرفاً من JWT هي الترويسة نفسها لكل التوكنات (تصادم)
        RedisClient.set_session(digest(access_token)[:32], user.id)
    except Exception:
        # Redis is optional, continue without it
        pass
    
    # Return token with user data to avoid second API call
    return LoginResponse(
        access_token=access_token,
        user=UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            profile_picture=user.profile_picture,
            is_admin=user.is_admin, role=role_of(user), permissions=permissions_of(user),
            is_active=user.is_active,
            created_at=user.created_at.isoformat()
        )
    )


@router.get("/verify", response_model=VerifyResponse)
async def verify_token(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """التحقق من Token - Verify if JWT token is valid"""
    
    user, _ = authenticate(credentials.credentials, db)
    if user is None:
        return VerifyResponse(valid=False)
    
    return VerifyResponse(
        valid=True,
        user=UserResponse(
            id=user.id,
            email=user.email,
            created_at=user.created_at.isoformat()
        )
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    current_user: User = Depends(require_current_user)
):
    """تسجيل خروج - Logout user and invalidate token"""
    
    token = credentials.credentials
    data = decode_token(token)
    # الإبطال يُحفظ بتجزئة التوكن محلياً (دائم) + Redis إن وُجد؛ فشل الحفظ = خطأ لا «نجح»
    if not revoke_token(token, exp=data.exp if data else None):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Logout could not be recorded; try again")
    try:
        RedisClient.delete_session(digest(token)[:32])
    except Exception:  # noqa: BLE001 - كاش الجلسة اختياري
        pass
    
    return MessageResponse(
        message="Successfully logged out",
        success=True
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: User = Depends(require_current_user)):
    """Get current authenticated user info"""
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        profile_picture=current_user.profile_picture,
        is_admin=current_user.is_admin, role=role_of(current_user), permissions=permissions_of(current_user),
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat()
    )


@router.post("/change-password", response_model=MessageResponse)
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    """Change user password"""
    if not verify_password(request.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    current_user.password_hash = hash_password(request.new_password)
    db.commit()
    revoke_user(current_user.id)        # كل الجلسات القائمة (ومنها المسروقة) تنتهي فوراً
    
    return MessageResponse(
        message="Password changed successfully",
        success=True
    )


@router.put("/profile", response_model=UserResponse)
async def update_profile(
    request: UpdateProfileRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    """Update user profile"""
    if request.name is not None:
        current_user.name = request.name
    
    if request.email is not None:
        existing_user = db.query(User).filter(
            User.email == request.email,
            User.id != current_user.id
        ).first()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists"
            )
        current_user.email = request.email
    
    db.commit()
    db.refresh(current_user)
    
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        profile_picture=current_user.profile_picture,
        is_admin=current_user.is_admin, role=role_of(current_user), permissions=permissions_of(current_user),
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat()
    )


@router.post("/profile-picture", response_model=UserResponse)
async def upload_profile_picture(
    request: ProfilePictureRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    """Upload profile picture"""
    current_user.profile_picture = request.profile_picture
    db.commit()
    db.refresh(current_user)
    
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        name=current_user.name,
        profile_picture=current_user.profile_picture,
        is_admin=current_user.is_admin, role=role_of(current_user), permissions=permissions_of(current_user),
        is_active=current_user.is_active,
        created_at=current_user.created_at.isoformat()
    )
