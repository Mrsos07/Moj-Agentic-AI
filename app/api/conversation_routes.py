#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Conversation API Routes
API endpoints لإدارة المحادثات والذاكرة
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session
import json

from app.services.memory_service import memory_service
from app.db.database import get_db
from app.db.models import Conversation, Message
from app.auth.ownership import conversation_or_404, require_user as get_current_user
from app.db.models import User

router = APIRouter(prefix="/api/conversations", tags=["Conversations"])


class ConversationResponse(BaseModel):
    """استجابة محادثة"""
    id: int
    user_id: Optional[int]
    session_id: Optional[str]
    title: str
    created_at: str
    updated_at: str
    message_count: int


class MessageResponse(BaseModel):
    """استجابة رسالة"""
    id: int
    role: str
    content: str
    intent: Optional[str]
    confidence: Optional[str]
    agent: Optional[str]
    attachment: Optional[Dict[str, Any]] = None
    created_at: str


class ConversationDetailResponse(BaseModel):
    """استجابة تفاصيل محادثة"""
    conversation: ConversationResponse
    messages: List[MessageResponse]


class UserPreferencesResponse(BaseModel):
    """استجابة تفضيلات المستخدم"""
    total_conversations: int
    common_intents: List[str]
    preferred_platforms: List[str]
    last_interaction: Optional[str]


class EnsureConversationRequest(BaseModel):
    """طلب إنشاء/جلب محادثة بجلسة محددة (مثل محادثة الموظف الذكي)."""
    session_id: str = Field(..., min_length=1)
    title: Optional[str] = None


@router.post("/ensure", response_model=ConversationDetailResponse)
async def ensure_conversation(
    body: EnsureConversationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """إنشاء أو جلب محادثة مرتبطة بجلسة محددة (session_id) مع عنوان اختياري،
    وإرجاعها مع كامل رسائلها — تُستخدم لفتح «محادثة الموظف الذكي» وتحميل سياقها."""
    if not current_user:
        raise HTTPException(status_code=401, detail="غير مصرح")
    try:
        conversation = memory_service.get_or_create_conversation(
            db=db, user_id=current_user.id,
            session_id=body.session_id, title=body.title,
        )
        # حصر الملكية: لا نُرجع محادثة مستخدم آخر
        if conversation.user_id and conversation.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="conversation not found")
        messages = memory_service.get_conversation_history(db, conversation.id)
        return ConversationDetailResponse(
            conversation=ConversationResponse(
                id=conversation.id,
                user_id=conversation.user_id,
                session_id=conversation.session_id,
                title=conversation.title or "محادثة",
                created_at=conversation.created_at.isoformat(),
                updated_at=conversation.updated_at.isoformat(),
                message_count=len(messages),
            ),
            messages=[
                MessageResponse(
                    id=msg.id, role=msg.role, content=msg.content,
                    intent=msg.intent, confidence=msg.confidence, agent=msg.agent,
                    attachment=(json.loads(msg.extra_data).get("attachment") if msg.extra_data else None),
                    created_at=msg.created_at.isoformat(),
                )
                for msg in messages
            ],
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"خطأ في تجهيز المحادثة: {str(e)}")


@router.get("/", response_model=List[ConversationResponse])
async def get_user_conversations(
    user_id: Optional[int] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    الحصول على محادثات المستخدم
    
    Args:
        user_id: معرف المستخدم (اختياري)
        limit: عدد المحادثات
        db: جلسة قاعدة البيانات
        current_user: المستخدم الحالي
    
    Returns:
        قائمة المحادثات
    """
    try:
        if not current_user:
            raise HTTPException(status_code=401, detail="غير مصرح")

        # استخدام user_id من current_user إذا كان متاحاً
        user_id = current_user.id
        
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id مطلوب")
        
        conversations = memory_service.get_user_conversations(db, user_id, limit)
        
        return [
            ConversationResponse(
                id=conv.id,
                user_id=conv.user_id,
                session_id=conv.session_id,
                title=conv.title or "محادثة",
                created_at=conv.created_at.isoformat(),
                updated_at=conv.updated_at.isoformat(),
                message_count=len(conv.messages)
            )
            for conv in conversations
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"خطأ في الحصول على المحادثات: {str(e)}"
        )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation_detail(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    الحصول على تفاصيل محادثة محددة
    
    Args:
        conversation_id: معرف المحادثة
        db: جلسة قاعدة البيانات
        current_user: المستخدم الحالي
    
    Returns:
        تفاصيل المحادثة مع الرسائل
    """
    try:
        # 404 لا 403: لا نؤكد وجود محادثة لا يملكها
        conversation = conversation_or_404(conversation_id, current_user, db)
        
        messages = memory_service.get_conversation_history(db, conversation_id)
        
        return ConversationDetailResponse(
            conversation=ConversationResponse(
                id=conversation.id,
                user_id=conversation.user_id,
                session_id=conversation.session_id,
                title=conversation.title or "محادثة",
                created_at=conversation.created_at.isoformat(),
                updated_at=conversation.updated_at.isoformat(),
                message_count=len(messages)
            ),
            messages=[
                MessageResponse(
                    id=msg.id,
                    role=msg.role,
                    content=msg.content,
                    intent=msg.intent,
                    confidence=msg.confidence,
                    agent=msg.agent,
                    attachment=(json.loads(msg.extra_data).get("attachment") if msg.extra_data else None),
                    created_at=msg.created_at.isoformat()
                )
                for msg in messages
            ]
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"خطأ في الحصول على المحادثة: {str(e)}"
        )


@router.get("/{conversation_id}/context")
async def get_conversation_context(
    conversation_id: int,
    max_messages: int = 10,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    الحصول على سياق المحادثة
    
    Args:
        conversation_id: معرف المحادثة
        max_messages: عدد الرسائل الأخيرة
        db: جلسة قاعدة البيانات
    
    Returns:
        سياق المحادثة
    """
    conversation_or_404(conversation_id, current_user, db)
    try:
        context = memory_service.get_conversation_context(
            db, conversation_id, max_messages
        )
        
        return {
            "conversation_id": conversation_id,
            "context": context,
            "timestamp": datetime.now().isoformat()
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"خطأ في الحصول على السياق: {str(e)}"
        )


@router.get("/user/{user_id}/preferences", response_model=UserPreferencesResponse)
async def get_user_preferences(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    الحصول على تفضيلات المستخدم من المحادثات السابقة
    
    Args:
        user_id: معرف المستخدم
        db: جلسة قاعدة البيانات
        current_user: المستخدم الحالي
    
    Returns:
        تفضيلات المستخدم
    """
    try:
        # التحقق من الصلاحية
        if user_id != current_user.id and not current_user.is_admin:
            raise HTTPException(status_code=404, detail="غير موجود")
        
        preferences = memory_service.get_user_preferences(db, user_id)
        
        return UserPreferencesResponse(**preferences)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"خطأ في الحصول على التفضيلات: {str(e)}"
        )


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    حذف محادثة
    
    Args:
        conversation_id: معرف المحادثة
        db: جلسة قاعدة البيانات
        current_user: المستخدم الحالي
    
    Returns:
        نتيجة الحذف
    """
    from app.aeos.security.gateway import api_decision
    conversation_or_404(conversation_id, current_user, db)
    api_decision("conversation.delete", "", {"id": conversation_id})
    try:
        user_id = current_user.id if current_user else None
        
        success = memory_service.delete_conversation(db, conversation_id, user_id)
        
        if success:
            return {
                "status": "success",
                "message": "تم حذف المحادثة بنجاح",
                "conversation_id": conversation_id,
                "timestamp": datetime.now().isoformat()
            }
        else:
            raise HTTPException(status_code=404, detail="المحادثة غير موجودة أو لا يمكن حذفها")
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"خطأ في حذف المحادثة: {str(e)}"
        )


@router.get("/stats/summary")
async def get_conversations_summary(
    user_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    الحصول على ملخص إحصائيات المحادثات
    
    Args:
        user_id: معرف المستخدم
        db: جلسة قاعدة البيانات
        current_user: المستخدم الحالي
    
    Returns:
        ملخص الإحصائيات
    """
    try:
        # دائماً المستخدم الموثَّق: كان مجهول يمرّر user_id أي مستخدم فيقرأ إحصاءاته
        user_id = current_user.id
        conversations = memory_service.get_user_conversations(db, user_id, limit=100)
        
        total_messages = sum(len(conv.messages) for conv in conversations)
        
        # حساب النوايا الأكثر استخداماً
        intent_counts = {}
        for conv in conversations:
            for msg in conv.messages:
                if msg.intent:
                    intent_counts[msg.intent] = intent_counts.get(msg.intent, 0) + 1
        
        top_intents = sorted(intent_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        return {
            "total_conversations": len(conversations),
            "total_messages": total_messages,
            "top_intents": [{"intent": intent, "count": count} for intent, count in top_intents],
            "last_conversation": conversations[0].updated_at.isoformat() if conversations else None,
            "timestamp": datetime.now().isoformat()
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"خطأ في الحصول على الإحصائيات: {str(e)}"
        )
