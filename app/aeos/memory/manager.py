"""
MemoryManager — the single façade the cognition layer uses to read/write memory.

It composes all layers, so a phase never needs to know which concrete layer a
piece of memory lives in. This keeps the cognitive code decoupled from storage.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .base import IMemoryLayer, MemoryLayerType, MemoryRecord
from .layers import (
    ConversationMemory,
    LongTermMemory,
    ReflectionMemory,
    SemanticMemory,
    WorkingMemory,
)


class MemoryManager:
    """Aggregates memory layers behind one cohesive API."""

    def __init__(self, layers: Optional[Dict[MemoryLayerType, IMemoryLayer]] = None) -> None:
        self._layers: Dict[MemoryLayerType, IMemoryLayer] = layers or {
            MemoryLayerType.WORKING: WorkingMemory(),
            MemoryLayerType.CONVERSATION: ConversationMemory(),
            MemoryLayerType.LONG_TERM: LongTermMemory(),
            MemoryLayerType.SEMANTIC: SemanticMemory(),
            MemoryLayerType.REFLECTION: ReflectionMemory(),
        }

    def layer(self, layer_type: MemoryLayerType) -> IMemoryLayer:
        return self._layers[layer_type]

    # ---- write helpers --------------------------------------------------
    def remember(
        self,
        employee_id: str,
        layer: MemoryLayerType,
        content: str,
        *,
        key: Optional[str] = None,
        importance: int = 1,
        metadata: Optional[Dict] = None,
    ) -> None:
        self._layers[layer].store(
            employee_id,
            MemoryRecord(
                layer=layer,
                content=content,
                key=key,
                importance=importance,
                metadata=metadata or {},
            ),
        )

    def learn(self, employee_id: str, lesson: str, importance: int = 3) -> None:
        """Persist a reflection lesson (used by the Learn phase)."""
        self.remember(
            employee_id, MemoryLayerType.REFLECTION, lesson, importance=importance
        )

    # ---- read helpers ---------------------------------------------------
    def recall(
        self, employee_id: str, layer: MemoryLayerType, query: str = "", limit: int = 10
    ) -> List[MemoryRecord]:
        return self._layers[layer].recall(employee_id, query, limit)

    def recall_all(self, employee_id: str, query: str = "", limit: int = 5) -> Dict[str, List[MemoryRecord]]:
        """Snapshot across every layer — used when building execution context."""
        return {
            layer.value: self._layers[layer].recall(employee_id, query, limit)
            for layer in self._layers
        }

    def clear_working(self, employee_id: str) -> None:
        """Wipe ephemeral working memory (on runtime destroy)."""
        self._layers[MemoryLayerType.WORKING].clear(employee_id)
