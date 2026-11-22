"""
إعداد وقت التشغيل من البيئة — قيمة واحدة لكل حد، تُقرأ مرة عند بناء المنصة.

``AEOS_RUNTIME=durable`` يفعّل الطابور الدائم (execution_jobs) والعقود والسجل؛ الافتراضي ``legacy``
= السلوك السابق حرفياً (طابور في الذاكرة، دورات مباشرة).
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, fields
from typing import Mapping, Optional


def runtime_mode(env: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if env is None else env
    mode = (env.get("AEOS_RUNTIME") or "legacy").strip().lower()
    if mode not in ("legacy", "durable"):
        raise ValueError(f"AEOS_RUNTIME غير معروف: {mode} (legacy | durable)")
    return mode


@dataclass(frozen=True)
class WorkerConfig:
    # ── التزامن ──
    workers: int = 4                     # AEOS_WORKERS: تزامن عام (كل العمال)
    tenant_max_active: int = 2           # AEOS_TENANT_MAX_ACTIVE: دورات متزامنة لكل مالك
    llm_max_concurrency: int = 4         # AEOS_LLM_MAX_CONCURRENCY: حاجز نداءات النموذج
    # ── العقود ──
    lease_seconds: int = 120             # AEOS_LEASE_SECONDS
    heartbeat_seconds: int = 20          # AEOS_HEARTBEAT_SECONDS
    poll_seconds: float = 1.0            # AEOS_POLL_SECONDS
    reclaim_every_seconds: int = 15      # AEOS_RECLAIM_EVERY_SECONDS
    # ── إعادة المحاولة ──
    max_attempts: int = 3                # AEOS_MAX_ATTEMPTS ⇒ بعدها DLQ
    retry_base_seconds: int = 30         # AEOS_RETRY_BASE_SECONDS (أسّي مع تذبذب)
    retry_max_seconds: int = 900         # AEOS_RETRY_MAX_SECONDS
    # ── المهل ──
    cycle_deadline_seconds: int = 1800   # AEOS_CYCLE_DEADLINE_SECONDS (p95 المقيس 408 ث، الأقصى 3089)
    queue_wait_seconds: int = 3600       # AEOS_QUEUE_WAIT_SECONDS: انتظار طلب متزامن (run_cycle_now)
    drain_seconds: int = 60              # AEOS_DRAIN_SECONDS: مهلة الإيقاف النظيف
    # ── حدود الدورة ──
    max_llm_calls: int = 60              # AEOS_MAX_LLM_CALLS
    max_llm_tokens: int = 200_000        # AEOS_MAX_LLM_TOKENS (تقدير: 4 أحرف ≈ رمز)
    max_tool_calls: int = 40             # AEOS_MAX_TOOL_CALLS (تنفيذ القدرات)
    max_cost_usd: float = 0.0            # AEOS_MAX_COST_USD (0 = بلا حد؛ يحتاج سعراً لكل رمز)
    cost_per_1k_tokens_usd: float = 0.0  # AEOS_COST_PER_1K_TOKENS_USD
    # ── المهام الفرعية (Agent-to-Agent) ──
    max_task_depth: int = 3              # AEOS_MAX_TASK_DEPTH
    max_fanout: int = 10                 # AEOS_MAX_FANOUT: أبناء مباشرون لمهمة
    max_tree: int = 50                   # AEOS_MAX_TREE: كل نسل مهمة جذر
    # ── الضغط العكسي وقاطع الدائرة ──
    queue_max_depth: int = 500           # AEOS_QUEUE_MAX_DEPTH: فوقه تُسقط المجدولة وتُرفض الباقية
    breaker_failures: int = 5            # AEOS_BREAKER_FAILURES: إخفاقات متتالية تفتح القاطع
    breaker_cooldown_seconds: int = 120  # AEOS_BREAKER_COOLDOWN_SECONDS
    # ── السياسة ──
    guard_fail_closed: bool = True       # AEOS_GUARD_FAIL_CLOSED: تعذّر تدقيق الحارس ⇒ رفض
    auto_retry_interrupted: bool = False  # AEOS_AUTO_RETRY_INTERRUPTED: دورة بأثر خارجي انقطعت ⇒ لا إعادة تلقائية
    worker_id: str = ""                  # AEOS_WORKER_ID (افتراضياً host:pid)

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "WorkerConfig":
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            raw = env.get(f"AEOS_{f.name.upper()}")
            if raw is None or not str(raw).strip():
                continue
            raw = str(raw).strip()
            if f.type in ("bool", bool):
                values[f.name] = raw.lower() in ("1", "true", "yes", "on")
            elif f.type in ("int", int):
                values[f.name] = int(raw)
            elif f.type in ("float", float):
                values[f.name] = float(raw)
            else:
                values[f.name] = raw
        cfg = cls(**values)
        if not cfg.worker_id:
            object.__setattr__(cfg, "worker_id", f"{socket.gethostname()}:{os.getpid()}")
        if cfg.heartbeat_seconds * 2 > cfg.lease_seconds:
            raise ValueError("AEOS_HEARTBEAT_SECONDS يجب ألا يتجاوز نصف AEOS_LEASE_SECONDS")
        return cfg
