import os
import re
import time
import secrets
from datetime import datetime
from typing import Optional, Any, Dict
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import Session

from app.aeos.security import gateway, ledger
from app.aeos.security.data import clean
from app.auth.security import encrypt_credentials, decrypt_credentials
from app.db.models import TelegramIntegration, User


class TelegramService:
    BOT_TOKEN_PATTERN = re.compile(r"\b\d+:[A-Za-z0-9_-]{10,}\b")

    def _extract_bot_token(self, raw_value: str) -> str:
        value = (raw_value or "").strip()
        if not value:
            raise ValueError("Bot Token مطلوب")

        match = self.BOT_TOKEN_PATTERN.search(value)
        if not match:
            raise ValueError("Bot Token غير صالح. استخدم التوكن الحقيقي من BotFather بصيغة 123456789:ABC...")

        return match.group(0)

    def _validate_bot_token(self, bot_token: str) -> str:
        clean_token = self._extract_bot_token(bot_token)
        if ":" not in clean_token:
            raise ValueError("Bot Token غير صالح. استخدم التوكن الحقيقي من BotFather بصيغة 123456789:ABC...")

        token_id, token_secret = clean_token.split(":", 1)
        if not token_id.isdigit() or len(token_secret) < 10:
            raise ValueError("Bot Token غير صالح. استخدم التوكن الحقيقي من BotFather بصيغة 123456789:ABC...")

        return clean_token

    def _build_api_url(self, bot_token: str, method: str) -> str:
        return f"https://api.telegram.org/bot{bot_token}/{method}"

    def _normalize_base_url(self, base_url: str) -> str:
        domain = os.environ.get("DOMAIN", "").strip()
        configured = (os.environ.get("TELEGRAM_WEBHOOK_BASE_URL") or
                      (f"https://{domain}" if domain else "")).strip().rstrip("/")
        parsed = urlparse(configured)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.params or parsed.query or parsed.fragment
                or (base_url and base_url.rstrip("/") != configured)):
            raise ValueError("يلزم عنوان HTTPS مطابق لعنوان Telegram المعتمد في إعداد الخادم")
        return configured

    def _build_webhook_url(self, base_url: str, webhook_token: str) -> str:
        normalized = self._normalize_base_url(base_url)
        return f"{normalized}/api/telegram/webhook/{webhook_token}"

    def _generate_webhook_token(self) -> str:
        return secrets.token_urlsafe(32)

    def _parse_telegram_response(self, response: requests.Response, fallback_message: str) -> Dict[str, Any]:
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError(fallback_message) from exc

    def _get_me(self, bot_token: str) -> Dict[str, Any]:
        try:
            response = requests.get(self._build_api_url(bot_token, "getMe"), allow_redirects=False, timeout=20)
        except requests.RequestException as exc:
            raise ValueError("تعذر الاتصال بـ Telegram للتحقق من Bot Token") from None

        data = self._parse_telegram_response(
            response,
            "تعذر قراءة استجابة Telegram أثناء التحقق من Bot Token",
        )
        if not response.ok or not data.get("ok"):
            raise ValueError("فشل التحقق من Telegram Bot Token")
        return data["result"]

    def _set_webhook(self, bot_token: str, webhook_url: str) -> None:
        origin, path, token = webhook_url.partition("/api/telegram/webhook/")
        if not path or not re.fullmatch(r"[A-Za-z0-9_-]{32,80}", token):
            raise ValueError("عنوان Telegram غير صالح")
        self._normalize_base_url(origin)
        try:
            response = requests.post(
                self._build_api_url(bot_token, "setWebhook"),
                json={"url": webhook_url, "secret_token": self.callback_secret(webhook_url.rsplit("/", 1)[1]),
                      "allowed_updates": ["message"]},
                allow_redirects=False, timeout=20,
            )
        except requests.RequestException as exc:
            raise ValueError("تعذر الاتصال بـ Telegram لإعداد Webhook") from None

        data = self._parse_telegram_response(
            response,
            "تعذر قراءة استجابة Telegram أثناء إعداد Webhook",
        )
        if not response.ok or not data.get("ok"):
            raise ValueError("فشل إعداد Telegram webhook")

    def _delete_webhook(self, bot_token: str) -> None:
        try:
            response = requests.post(self._build_api_url(bot_token, "deleteWebhook"), allow_redirects=False, timeout=20)
        except requests.RequestException as exc:
            raise ValueError("تعذر الاتصال بـ Telegram لحذف Webhook") from None

        data = self._parse_telegram_response(
            response,
            "تعذر قراءة استجابة Telegram أثناء حذف Webhook",
        )
        if not response.ok or not data.get("ok"):
            raise ValueError("فشل حذف Telegram webhook")

    def _send_message(self, bot_token: str, chat_id: str, text: str) -> None:
        try:
            response = requests.post(
                self._build_api_url(bot_token, "sendMessage"),
                json={"chat_id": chat_id, "text": text},
                allow_redirects=False, timeout=20,
            )
        except requests.RequestException as exc:
            raise ValueError("تعذر الاتصال بـ Telegram لإرسال الرسالة") from None

        data = self._parse_telegram_response(
            response,
            "تعذر قراءة استجابة Telegram أثناء إرسال الرسالة",
        )
        if not response.ok or not data.get("ok"):
            raise ValueError("فشل إرسال الرسالة إلى Telegram")

    @staticmethod
    def callback_secret(token):
        return ledger.fingerprint(["telegram-callback-v1", token])

    def get_user_integration(self, db: Session, user_id: int) -> Optional[TelegramIntegration]:
        return db.query(TelegramIntegration).filter(TelegramIntegration.user_id == user_id).first()

    def get_integration_by_webhook_token(self, db: Session, webhook_token: str) -> Optional[TelegramIntegration]:
        return db.query(TelegramIntegration).filter(TelegramIntegration.webhook_token == webhook_token).first()

    def upsert_integration(self, db: Session, user: User, bot_token: str, chat_id: str, base_url: str) -> TelegramIntegration:
        clean_bot_token = self._validate_bot_token(bot_token)
        clean_chat_id = str(chat_id or "").strip()
        if not re.fullmatch(r"[1-9][0-9]{0,18}", clean_chat_id):
            raise ValueError("يلزم معرّف محادثة Telegram الخاصة بالمالك")
        clean_base_url = self._normalize_base_url(base_url)

        bot_info = self._get_me(clean_bot_token)
        integration = self.get_user_integration(db, user.id)
        webhook_token = self._generate_webhook_token()
        webhook_url = self._build_webhook_url(clean_base_url, webhook_token)

        self._set_webhook(clean_bot_token, webhook_url)

        if integration is None:
            integration = TelegramIntegration(
                user_id=user.id,
                encrypted_bot_token=encrypt_credentials(clean_bot_token),
                chat_id=clean_chat_id,
                webhook_token=webhook_token,
                webhook_url=webhook_url,
                is_enabled=True,
                bot_username=bot_info.get("username"),
                bot_display_name=bot_info.get("first_name"),
                last_connected_at=datetime.utcnow(),
                last_error=None,
            )
            db.add(integration)
        else:
            integration.encrypted_bot_token = encrypt_credentials(clean_bot_token)
            integration.chat_id = clean_chat_id
            integration.webhook_token = webhook_token
            integration.webhook_url = webhook_url
            integration.is_enabled = True
            integration.bot_username = bot_info.get("username")
            integration.bot_display_name = bot_info.get("first_name")
            integration.last_connected_at = datetime.utcnow()
            integration.last_error = None

        db.commit()
        db.refresh(integration)
        return integration

    def set_enabled(self, db: Session, integration: TelegramIntegration, is_enabled: bool) -> TelegramIntegration:
        bot_token = decrypt_credentials(integration.encrypted_bot_token)
        if is_enabled:
            if integration.webhook_url:
                self._set_webhook(bot_token, integration.webhook_url)
        else:
            self._delete_webhook(bot_token)
        integration.is_enabled = is_enabled
        integration.last_error = None
        integration.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(integration)
        return integration

    def delete_integration(self, db: Session, integration: TelegramIntegration) -> None:
        try:
            bot_token = decrypt_credentials(integration.encrypted_bot_token)
            self._delete_webhook(bot_token)
        except Exception:
            pass
        db.delete(integration)
        db.commit()

    def process_webhook_update(self, db, integration, update):
        from app.aeos.services import orchestrator

        message = update.get("message")
        if not integration.is_enabled or not isinstance(message, dict):
            return {"ok": True, "ignored": "inactive"}
        chat, sender = message.get("chat"), message.get("from")
        if (not isinstance(chat, dict) or not isinstance(sender, dict) or chat.get("type") != "private"
                or sender.get("is_bot") or str(chat.get("id")) != integration.chat_id
                or sender.get("id") != chat.get("id")):
            return {"ok": True, "ignored": "chat"}
        uid, update_id = integration.user_id, update.get("update_id")
        sent = message.get("date")
        text = message.get("text")
        if (type(update_id) is not int or not 0 <= update_id < 2**63 or type(sent) is not int
                or not time.time() - 86400 <= sent <= time.time() + 60
                or not isinstance(text, str) or not 0 < len(text.strip()) <= 4096):
            return {"ok": True, "ignored": "invalid"}
        session = f"telegram:{integration.id}:{integration.chat_id}"
        with gateway.execution(uid, conversation=session):
            gateway.owner()
            if not ledger.admit(uid, "telegram.receive", 30):
                return {"ok": True, "ignored": "limit"}
            gateway.decision("telegram.receive", "", {"integration": integration.id})
            if not ledger.claim_delivery(self.callback_secret(integration.webhook_token), update_id):
                return {"ok": True, "ignored": "duplicate"}
            if text.strip().lower() in {"/start", "/chatid", "/get_chat_id"}:
                reply = f"معرّف محادثتك الخاصة هو {integration.chat_id}"
            else:
                result = orchestrator.handle(clean(text), uid, db=db, session_id=session)
                reply = (result or {}).get("message") or "تعذرت معالجة الرسالة."
            # The owner explicitly linked this private recipient. It cannot be
            # redirected by the update, tool output or generated reply.
            reply = clean(str(reply))[:4096]
            gateway.decision("telegram.reply", "", {"recipient": integration.chat_id})
            self._send_message(decrypt_credentials(integration.encrypted_bot_token), integration.chat_id, reply)
            integration.last_error = None
            integration.last_connected_at = datetime.utcnow()
            db.commit()
        return {"ok": True, "sent": True}


telegram_service = TelegramService()
