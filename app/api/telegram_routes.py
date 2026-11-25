from typing import Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

import hmac
import json
from app.aeos.security import gateway, ledger
from app.auth.dependencies import require_current_user
from app.db.database import get_db
from app.db.models import User
from app.services.telegram_service import telegram_service


router = APIRouter(prefix="/api/telegram", tags=["Telegram"])


class TelegramConnectRequest(BaseModel):
    bot_token: str
    chat_id: str
    webhook_base_url: Optional[str] = None

    @field_validator("bot_token", "chat_id", "webhook_base_url", mode="before")
    @classmethod
    def strip_string_fields(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class TelegramToggleRequest(BaseModel):
    is_enabled: bool


class TelegramMessageResponse(BaseModel):
    success: bool
    message: str
    integration: Optional[Dict[str, Any]] = None


@router.get("/status", response_model=TelegramMessageResponse)
async def get_telegram_status(
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    integration = telegram_service.get_user_integration(db, current_user.id)
    return TelegramMessageResponse(
        success=True,
        message="تم جلب حالة تكامل Telegram",
        integration=integration.to_dict() if integration else None,
    )


@router.post("/connect", response_model=TelegramMessageResponse)
async def connect_telegram(
    payload: TelegramConnectRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    try:
        bot_token = (payload.bot_token or "").strip()
        chat_id = (payload.chat_id or "").strip()
        base_url = telegram_service._normalize_base_url(payload.webhook_base_url)

        if not bot_token:
            raise HTTPException(status_code=400, detail="Bot Token مطلوب")

        if not chat_id:
            raise HTTPException(status_code=400, detail="Chat ID مطلوب")

        gateway.api_decision("telegram.connect", "", {"chat": chat_id, "base": base_url,
                        "credential_digest": ledger.fingerprint(bot_token)})
        integration = telegram_service.upsert_integration(
            db=db,
            user=current_user,
            bot_token=bot_token,
            chat_id=chat_id,
            base_url=base_url,
        )
        return TelegramMessageResponse(
            success=True,
            message="تم ربط Telegram بنجاح",
            integration=integration.to_dict(),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="فشل ربط Telegram") from None


@router.post("/toggle", response_model=TelegramMessageResponse)
async def toggle_telegram(
    payload: TelegramToggleRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    integration = telegram_service.get_user_integration(db, current_user.id)
    if not integration:
        raise HTTPException(status_code=404, detail="لا يوجد تكامل Telegram لهذا المستخدم")

    try:
        gateway.api_decision("telegram.toggle", "", {"integration": integration.id,
                             "configuration": ledger.fingerprint(integration.webhook_token), "enabled": payload.is_enabled})
        integration = telegram_service.set_enabled(db, integration, payload.is_enabled)
        return TelegramMessageResponse(
            success=True,
            message="تم تحديث حالة Telegram",
            integration=integration.to_dict(),
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="فشل تحديث حالة Telegram") from None


@router.delete("", response_model=TelegramMessageResponse)
async def disconnect_telegram(
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    integration = telegram_service.get_user_integration(db, current_user.id)
    if not integration:
        raise HTTPException(status_code=404, detail="لا يوجد تكامل Telegram لهذا المستخدم")

    gateway.api_decision("telegram.disconnect", "", {"integration": integration.id,
                         "configuration": ledger.fingerprint(integration.webhook_token)})
    telegram_service.delete_integration(db, integration)
    return TelegramMessageResponse(success=True, message="تم فصل Telegram بنجاح", integration=None)


@router.post("/webhook/{webhook_token}")
async def telegram_webhook(
    webhook_token: str,
    request: Request,
    db: Session = Depends(get_db)
):
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if (not supplied.isascii() or len(webhook_token) > 80 or
            not hmac.compare_digest(telegram_service.callback_secret(webhook_token), supplied)):
        raise HTTPException(403, "invalid webhook")
    integration = telegram_service.get_integration_by_webhook_token(db, webhook_token)
    if not integration or not integration.is_enabled:
        raise HTTPException(404, "unknown webhook")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 65536:
            raise HTTPException(413, "update too large")
        body.extend(chunk)
    try:
        update = json.loads(body)
    except (ValueError, RecursionError):
        raise HTTPException(400, "invalid update") from None
    if not isinstance(update, dict):
        raise HTTPException(400, "invalid update")
    try:
        return telegram_service.process_webhook_update(db, integration, update)
    except PermissionError:
        raise HTTPException(403, "webhook action denied") from None
    except Exception:
        db.rollback()
        raise HTTPException(502, "webhook processing failed") from None
