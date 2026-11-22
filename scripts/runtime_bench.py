"""
runtime_bench.py — قياس الطابور الدائم نفسه (لا الدورة): كم مهمة/دقيقة، تأخر الطابور p50/p95، ونسبة
الفشل، بعدد عمال مختلف ودورة مزيّفة مدتها معروفة. قاعدة مؤقتة فقط.

    .\\venv\\Scripts\\python.exe scripts\\runtime_bench.py --jobs 200 --workers 4,8,16 --cycle-ms 200
    (و MOJ_TEST_DATABASE_URL=postgresql+psycopg2://… لـPostgreSQL)

الدورة المزيّفة تنام ``cycle-ms`` (بديل الشبكة والمتصفح). النتيجة تقيس **عبء الطابور والعقود** لا أداء
دورة AEOS الحقيقية (p50 المقيس 86 ث، p95 408 ث) — لا تُقرأ كـ«عدد موظفين مدعوم».
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("DATABASE_URL", None)
os.environ.setdefault("TOPIC_SEMANTIC", "0")

import app.db.database as DB  # noqa: E402
from app.db.testing import use_temp_database  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=200)
    ap.add_argument("--employees", type=int, default=50)
    ap.add_argument("--workers", default="4,8,16")
    ap.add_argument("--cycle-ms", type=int, default=200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    from app.db import models as M
    from app.aeos.core.events import Event, EventType
    from app.aeos.runtime.config import WorkerConfig
    from app.aeos.runtime.durable_queue import DbJobQueue
    from app.aeos.runtime.durable_workers import DurableWorkerPool

    class FakeRuntime:
        def run_cycle(self, employee_id, event, *, lease=None, budget=None, queue=None):
            queue.start(lease)
            time.sleep(args.cycle_ms / 1000)
            return {"cycle_id": uuid.uuid4().hex, "results": []}

    results = []
    for n in (int(x) for x in args.workers.split(",")):
        eng = use_temp_database()
        with DB.SessionLocal() as s:
            users = [M.User(email=f"b{i}-{uuid.uuid4().hex[:6]}@example.com", password_hash="h") for i in range(5)]
            s.add_all(users)
            s.flush()
            emps = [M.AIEmployee(id=uuid.uuid4().hex, user_id=users[i % 5].id, platform="x",
                                 account_username=f"e{i}") for i in range(args.employees)]
            s.add_all(emps)
            s.commit()
            pairs = [(e.id, e.user_id) for e in emps]
        cfg = WorkerConfig.from_env({"AEOS_WORKERS": str(n), "AEOS_TENANT_MAX_ACTIVE": str(n),
                                     "AEOS_POLL_SECONDS": "0.05", "AEOS_QUEUE_MAX_DEPTH": str(args.jobs * 2),
                                     "AEOS_WORKER_ID": f"bench-{n}"})
        q = DbJobQueue(cfg)
        ids = [q.submit(pairs[i % len(pairs)][0], Event(EventType.MANUAL, employee_id=pairs[i % len(pairs)][0]),
                        tenant_id=pairs[i % len(pairs)][1])[0] for i in range(args.jobs)]
        pool = DurableWorkerPool(q, FakeRuntime(), cfg)
        t0 = time.monotonic()
        pool.start()
        while q.stats()["by_state"].get("COMPLETED", 0) < args.jobs and time.monotonic() - t0 < 600:
            time.sleep(0.1)
        wall = time.monotonic() - t0
        pool.stop(drain=True, timeout=5)
        with DB.SessionLocal() as s:
            rows = s.query(M.ExecutionJobRecord).filter(M.ExecutionJobRecord.job_id.in_(ids)).all()
            lags = sorted((r.started_at - r.created_at).total_seconds() for r in rows if r.started_at)
            done = sum(1 for r in rows if r.status == "COMPLETED")
        ideal = args.jobs * args.cycle_ms / 1000 / n
        res = {"dialect": eng.dialect.name, "workers": n, "jobs": args.jobs, "completed": done,
               "wall_s": round(wall, 2), "jobs_per_min": round(done / wall * 60, 1),
               "ideal_wall_s": round(ideal, 2), "overhead_pct": round((wall - ideal) / wall * 100, 1),
               "queue_lag_p50_s": round(statistics.median(lags), 2) if lags else None,
               "queue_lag_p95_s": round(lags[int(len(lags) * 0.95) - 1], 2) if lags else None,
               "failure_rate": round(1 - done / args.jobs, 3)}
        results.append(res)
        print(json.dumps(res, ensure_ascii=False), flush=True)
        eng.dispose()
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
