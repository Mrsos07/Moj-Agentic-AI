"""
LLM provider implementations behind :class:`ILLMProvider`.

- ``OpenAILLMProvider`` wraps the OpenAI SDK already present in requirements.
- ``NullLLMProvider`` is a deterministic fallback used when no API key is
  configured, so the whole platform still runs (and is testable) offline.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from .interfaces import ILLMProvider

logger = logging.getLogger("aeos.llm")


class NullLLMProvider(ILLMProvider):
    """Deterministic no-network provider. Keeps the platform runnable offline."""

    @property
    def available(self) -> bool:
        return False

    def complete(self, system: str, prompt: str, *, temperature: float = 0.7,
                 max_tokens: int = 1000) -> str:
        return ""

    def complete_json(self, system: str, prompt: str, *,
                      temperature: float = 0.3) -> Dict[str, Any]:
        return {}


class OpenAILLMProvider(ILLMProvider):
    """OpenAI-backed reasoning provider."""

    def __init__(self, api_key: str, model: str = "gpt-4",
                 max_tokens: int = 1500, temperature: float = 0.7) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._client = None
        try:
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to init OpenAI client; falling back to null")

    @property
    def available(self) -> bool:
        return self._client is not None

    def complete(self, system: str, prompt: str, *, temperature: float = 0.7,
                 max_tokens: int = 1000) -> str:
        if not self._client:
            return ""
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception:  # noqa: BLE001
            logger.exception("LLM completion failed")
            return ""

    def complete_json(self, system: str, prompt: str, *,
                      temperature: float = 0.3) -> Dict[str, Any]:
        raw = self.complete(
            system + "\nRespond ONLY with valid minified JSON.",
            prompt,
            temperature=temperature,
            max_tokens=self._max_tokens,
        )
        if not raw:
            return {}
        return _safe_json(raw)


def _safe_json(raw: str) -> Dict[str, Any]:
    """Best-effort JSON extraction from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if "```" in raw[3:] else raw.strip("`")
        raw = raw.replace("json", "", 1).strip() if raw.lower().startswith("json") else raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except Exception:  # noqa: BLE001
                return {}
        return {}


def build_llm_provider() -> ILLMProvider:
    """Factory: pick a provider from environment configuration."""
    try:
        from app.core.config import settings

        if settings.OPENAI_API_KEY:
            return OpenAILLMProvider(
                api_key=settings.OPENAI_API_KEY,
                model=settings.OPENAI_MODEL,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                temperature=settings.OPENAI_TEMPERATURE,
            )
    except Exception:  # noqa: BLE001
        logger.exception("LLM provider config failed")
    return NullLLMProvider()
