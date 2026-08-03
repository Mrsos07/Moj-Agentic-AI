"""
realtime_bus — a tiny, dependency-free bridge that lets background threads
(e.g. the AEOS worker pool) push chat messages to connected WebSocket clients.

The web layer (``app/main.py``) registers a *sink* callback at startup. Producers
anywhere in the codebase just call :func:`publish_chat` — they never import the
web layer, avoiding circular imports. If no sink is registered (tests, CLI), the
calls are safe no-ops.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("app.realtime_bus")

# sink signature: (user_id: Optional[int], payload: dict) -> None
_Sink = Callable[[Optional[int], Dict[str, Any]], None]

_sink: Optional[_Sink] = None
_lock = threading.RLock()


def register_sink(sink: Optional[_Sink]) -> None:
    """Register (or clear with ``None``) the callback used to deliver messages."""
    global _sink
    with _lock:
        _sink = sink
    logger.info("realtime_bus sink %s", "registered" if sink else "cleared")


def publish_chat(user_id: Optional[int], payload: Dict[str, Any]) -> None:
    """Deliver a chat payload to the given user's live WebSocket connections.

    ``payload`` should match what the frontend expects, e.g.::

        {"type": "assistant_message", "message": "...", "timestamp": "..."}
    """
    with _lock:
        sink = _sink
    if sink is None:
        return
    try:
        sink(user_id, payload)
    except Exception:  # noqa: BLE001 - realtime delivery must never break producers
        logger.exception("realtime_bus sink delivery failed")
