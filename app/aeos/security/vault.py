"""
Secret vault.

Encrypts everything sensitive an employee owns: cookies, access/refresh tokens,
API keys, environment variables and browser sessions. Secrets are never exposed
unless an authorized capability explicitly requests them.

Uses Fernet (symmetric AES) from the ``cryptography`` package already in
requirements. The master key comes from ``settings.ENCRYPTION_KEY``; if absent a
process-ephemeral key is generated so the platform still runs in dev (with a
loud warning — such secrets won't survive a restart).
"""
from __future__ import annotations

import abc as _abc
import base64
import hashlib
import json
import logging
from typing import Any, Optional

from ..core.exceptions import SecurityError

logger = logging.getLogger("aeos.security")


class ISecretVault(_abc.ABC):
    """Encryption contract for per-employee secrets."""

    @_abc.abstractmethod
    def encrypt(self, plaintext: str) -> str: ...

    @_abc.abstractmethod
    def decrypt(self, token: str) -> str: ...

    def encrypt_obj(self, obj: Any) -> str:
        return self.encrypt(json.dumps(obj, ensure_ascii=False))

    def decrypt_obj(self, token: str) -> Any:
        return json.loads(self.decrypt(token))


class FernetVault(ISecretVault):
    """Fernet-backed vault."""

    def __init__(self, master_key: Optional[str] = None) -> None:
        try:
            from cryptography.fernet import Fernet
        except Exception as exc:  # noqa: BLE001
            raise SecurityError("cryptography package is required for the vault") from exc

        self._Fernet = Fernet
        if master_key:
            key = base64.urlsafe_b64encode(
                hashlib.sha256(master_key.encode("utf-8")).digest()
            )
        else:
            logger.warning(
                "ENCRYPTION_KEY not set — using an EPHEMERAL vault key. "
                "Encrypted secrets will NOT survive a restart."
            )
            key = Fernet.generate_key()
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        try:
            return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise SecurityError("encryption failed") from exc

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise SecurityError("decryption failed") from exc


def build_vault() -> ISecretVault:
    """Factory reading the master key from app settings."""
    try:
        from app.core.config import settings

        return FernetVault(settings.ENCRYPTION_KEY)
    except SecurityError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("vault init failed; using ephemeral key")
        return FernetVault(None)
