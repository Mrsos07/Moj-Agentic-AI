import asyncio
import hashlib
import hmac
import json
import os
import time
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from app.aeos.security import gateway
from app.aeos.security.data import clean

from app.core.config import settings
from datetime import datetime
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

class WebhookService:
    def __init__(self):
        self.webhook_url = settings.N8N_WEBHOOK_URL
        self.enabled = settings.N8N_WEBHOOK_ENABLED and self.webhook_url is not None
        self.client = httpx.AsyncClient(timeout=10.0)
    
    async def _post(self, payload):
        parts = urlsplit(str(self.webhook_url))
        key = os.environ.get("N8N_WEBHOOK_SIGNING_KEY", "")
        if len(key) < 32 or parts.scheme != "https" or parts.username or parts.password:
            raise PermissionError("اضبط وجهة HTTPS ومفتاح توقيع webhook")
        message = clean(payload)
        owner = gateway.current.get()
        if not owner or str(payload.get("user_id")) != str(owner.user_id):
            raise PermissionError("webhook owner mismatch")
        scope = {k: v for k, v in message.items() if k != "timestamp"}
        scope["destination"] = hashlib.sha256(str(self.webhook_url).encode()).hexdigest()
        await asyncio.to_thread(gateway.api_decision, "webhook.disclose", owner.agent, scope)
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()
        stamp, nonce = str(int(time.time())), uuid.uuid4().hex
        sig = hmac.new(key.encode(), stamp.encode() + b"." + nonce.encode() + b"." + body, hashlib.sha256)
        async with self.client.stream("POST", str(self.webhook_url), content=body, follow_redirects=False,
                headers={"Content-Type": "application/json", "X-Moj-Timestamp": stamp,
                         "X-Moj-Nonce": nonce, "X-Moj-Signature": sig.hexdigest()}) as response:
            response.raise_for_status()
            raw = bytearray()
            async for chunk in response.aiter_bytes(16384):
                raw.extend(chunk)
                if len(raw) > 262144:
                    raise ValueError("webhook response too large")
            return clean(json.loads(raw)) if raw else None

    async def send_message_to_n8n(
        self,
        user_message: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_email: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        إرسال رسالة المستخدم إلى n8n webhook واستقبال الرد
        
        Args:
            user_message: الرسالة التي أرسلها المستخدم
            session_id: معرف الجلسة (اختياري)
            user_id: معرف المستخدم (اختياري)
            user_email: بريد المستخدم (اختياري)
            metadata: بيانات إضافية (اختياري)
        
        Returns:
            Optional[str]: الرد من n8n إذا نجح، None خلاف ذلك
        """
        if not self.enabled:
            logger.debug("N8N webhook is disabled")
            return None
        
        try:
            payload = {
                "message": user_message,
                "timestamp": datetime.now().isoformat(),
                "source": "moj_ai_chatbot",
                "type": "user_message"
            }
            
            # إضافة البيانات الاختيارية
            if session_id:
                payload["session_id"] = session_id
            
            if user_id:
                payload["user_id"] = user_id
            
            if user_email:
                payload["user_email"] = user_email
            
            if metadata:
                payload["metadata"] = metadata
            
            # إرسال الطلب إلى n8n webhook وانتظار الرد
            response_data = await self._post(payload)
            if isinstance(response_data, list) and len(response_data) > 0:
                n8n_response = response_data[0]
                
                # البحث عن الرد في جميع الحقول الممكنة
                reply = None
                if isinstance(n8n_response, dict):
                    # أولاً: تحقق من وجود output.reply (التنسيق الجديد من n8n)
                    if "output" in n8n_response and isinstance(n8n_response["output"], dict):
                        reply = n8n_response["output"].get("reply")
                    
                    # إذا لم نجد في output.reply، ابحث في الحقول الأخرى
                    if not reply:
                        reply = (
                            n8n_response.get("response") or 
                            n8n_response.get("message") or 
                            n8n_response.get("reply") or
                            n8n_response.get("output") or
                            n8n_response.get("text") or
                            n8n_response.get("content") or
                            n8n_response.get("data")
                        )
                
                # إذا لم نجد رد في الحقول المعروفة، استخدم كامل الـ object
                if not reply:
                    reply = str(n8n_response)
                
                
                # تأكد من إرجاع الرد حتى لو كان string فارغ ظاهرياً
                return reply if reply else str(n8n_response)
                
            elif isinstance(response_data, dict):
                # إذا كان الرد dictionary مباشرة
                reply = (
                    response_data.get("response") or 
                    response_data.get("message") or 
                    response_data.get("reply") or
                    response_data.get("output") or
                    response_data.get("text") or
                    response_data.get("content") or
                    response_data.get("data")
                )
                
                if not reply:
                    reply = str(response_data)
                    
                return reply
            else:
                logger.warning("Unexpected webhook response shape")
                # حتى لو كان التنسيق غير متوقع، أرجع البيانات
                return str(response_data) if response_data else None
            
        except httpx.TimeoutException:
            logger.error("Timeout while sending message to n8n webhook")
            return None
        except httpx.HTTPStatusError as e:
            logger.warning("webhook rejected request: %s", e.response.status_code)
            return None
        except (PermissionError, HTTPException):
            raise
        except Exception as e:
            logger.warning("webhook send failed (%s)", type(e).__name__)
            return None
    
    async def send_ai_response_to_n8n(
        self,
        user_message: str,
        ai_response: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None
    ) -> bool:
        """
        إرسال استجابة AI إلى n8n webhook (اختياري)
        
        Args:
            user_message: الرسالة الأصلية للمستخدم
            ai_response: استجابة AI
            session_id: معرف الجلسة (اختياري)
            user_id: معرف المستخدم (اختياري)
        
        Returns:
            bool: True إذا تم الإرسال بنجاح، False خلاف ذلك
        """
        if not self.enabled:
            return False
        
        try:
            payload = {
                "user_message": user_message,
                "ai_response": ai_response,
                "timestamp": datetime.now().isoformat(),
                "source": "moj_ai_chatbot",
                "type": "ai_response"
            }
            
            if session_id:
                payload["session_id"] = session_id
            
            if user_id:
                payload["user_id"] = user_id
            
            await self._post(payload)
            return True
            
        except (PermissionError, HTTPException):
            raise
        except Exception as e:
            logger.warning("webhook reply failed (%s)", type(e).__name__)
            return False
    
    async def close(self):
        """إغلاق العميل HTTP"""
        await self.client.aclose()
