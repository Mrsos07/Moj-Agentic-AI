"""
Memory contracts and the shared memory record.

The employee's mind has several layers, each implementing the same
:class:`IMemoryLayer` interface so the :class:`MemoryManager` can treat them
uniformly and new layers can be plugged in:

- Working memory      — scratchpad for the current cycle (ephemeral, per-runtime)
- Conversation memory — recent interactions / DMs / replies
- Long-term memory    — durable facts and outcomes
- Semantic memory     — retrievable knowledge (optionally embedding-ranked)
- Reflection memory   — lessons learned after each cycle
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class MemoryLayerType(str, Enum):
    WORKING = "working"
    CONVERSATION = "conversation"
    LONG_TERM = "long_term"
    SEMANTIC = "semantic"
    REFLECTION = "reflection"


@dataclass
class MemoryRecord:
    """A single unit of memory."""

    layer: MemoryLayerType
    content: str
    key: Optional[str] = None
    importance: int = 1                 # 1..5
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layer": self.layer.value,
            "content": self.content,
            "key": self.key,
            "importance": self.importance,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


class IMemoryLayer(abc.ABC):
    """One pluggable memory layer, scoped to a single employee."""

    layer_type: MemoryLayerType

    @abc.abstractmethod
    def store(self, employee_id: str, record: MemoryRecord) -> None: ...

    @abc.abstractmethod
    def recall(
        self, employee_id: str, query: str = "", limit: int = 10
    ) -> List[MemoryRecord]: ...

    @abc.abstractmethod
    def clear(self, employee_id: str) -> None: ...
