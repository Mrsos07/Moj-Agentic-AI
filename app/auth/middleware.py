from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional

from app.db.database import get_db
from app.db.models import User
from app.auth.token_guard import authenticate

security = HTTPBearer()

_DETAIL = {"revoked": "Token has been revoked", "inactive": "Account is disabled"}


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Middleware to get current authenticated user from JWT token (token_guard: signature,
    expiry, revocation, active account)"""
    user, reason = authenticate(credentials.credentials, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_DETAIL.get(reason, "Could not validate credentials"),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Optional authentication - returns None if no valid token"""
    if credentials is None:
        return None
    return authenticate(credentials.credentials, db)[0]


def require_permission(permission: str):
    """Decorator factory for permission checking (extensible)"""
    async def permission_checker(current_user: User = Depends(get_current_user)):
        # Add permission logic here if needed
        # For now, just check if user exists
        if not current_user:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions"
            )
        return current_user
    return permission_checker
