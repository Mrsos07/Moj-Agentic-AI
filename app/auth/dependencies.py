#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Authentication Dependencies
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional

from app.db.database import get_db
from app.db.models import User
from app.auth.token_guard import authenticate

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """
    Get current authenticated user (optional)
    Returns None if no valid token is provided
    """
    if not credentials:
        return None
    return authenticate(credentials.credentials, db)[0]


async def require_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Get current authenticated user (required)
    Raises HTTPException if no valid token is provided
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required"
        )
    
    # نفس قواعد middleware حرفياً (token_guard): توقيع، انتهاء، إبطال، حساب نشط
    user, reason = authenticate(credentials.credentials, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"revoked": "Token has been revoked", "inactive": "Account is disabled",
                    "user_not_found": "User not found"}.get(reason, "Invalid or expired token")
        )
    return user


async def require_admin(
    current_user: User = Depends(require_current_user)
) -> User:
    """
    Require admin privileges
    Raises HTTPException if user is not admin
    """
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    
    return current_user
