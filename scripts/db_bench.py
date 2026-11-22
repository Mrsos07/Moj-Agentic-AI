"""
db_bench.py — قياس زمن عمليات قاعدة البيانات التمثيلية (p50/p95/الإنتاجية) على 1 و8 و32 خيطاً.

يعمل على قاعدة **مؤقتة** فقط (لا يلمس data/app.db):

    .\\venv\\Scripts\\python.exe scripts\\db_bench.py --out backups\\bench-sqlite.json
    .\\venv\\Scripts\\python.exe scripts\\db_bench.py --url postgresql+psycopg2://moj:…@127.0.0.1:55432/bench --out …

``--url`` لـPostgreSQL يجب أن يشير لقاعدة فارغة مخصّصة للقياس (تُبنى بـAlembic). ``--schema create_all``
لقياس خط الأساس على كود ما قبل Alembic.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TOPIC_SEMANTIC", "0")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")
os.environ.pop("DATABASE_URL", None)  # القياس يحدد قاعدته بنفسه

from sqlalchemy import create_engine  # noqa: E402

import app.db.database as DB  # noqa: E402


def _engine(url: str):
    if hasattr(DB, "build_engine"):
        return DB.build_engine(url)
    return create_engine(url, connect_args={"check_same_thread": False})


def setup(url: str, schema: str):
    eng = _engine(url)
    DB.engine = eng
    DB.SessionLocal.configure(bind=eng)
    from app.db import models  # noqa: F401
    import app.trend_detector.models  # noqa: F401

    if schema == "create_all":
        DB.Base.metadata.create_all(bind=eng)
    else:
        from app.db import migrations as MIG

        MIG.upgrade(eng, "head")
    return eng


def seed():
    from app.db.models import Conversation, Message, User
    from app.aeos.employee.models import EmployeeConfig
    from app.aeos.employee.repository import SqlEmployeeRepository

    db = DB.SessionLocal()
    try:
        u = User(email=f"bench-{uuid.uuid4().hex[:8]}@example.test", password_hash="x", role="user")
        db.add(u)
        db.commit()
        uid = u.id
        for i in range(50):
            c = Conversation(user_id=uid, session_id=uuid.uuid4().hex, title=f"محادثة {i}")
            db.add(c)
            db.flush()
            db.add_all(Message(conversation_id=c.id, role="user" if j % 2 else "assistant",
                               content=f"رسالة رقم {j} في المحادثة {i} — نص عربي للقياس") for j in range(20))
        db.commit()
    finally:
        db.close()
    repo = SqlEmployeeRepository()
    cfg = repo.save(EmployeeConfig(id=uuid.uuid4().hex, user_id=uid, platform="x",
                                   account_username=f"bench_{uuid.uuid4().hex[:6]}"))
    from app.aeos.memory.base import MemoryRecord
    from app.aeos.memory.layers import LongTermMemory

    mem = LongTermMemory()
    for i in range(200):
        mem.store(cfg.id, MemoryRecord(layer=mem.layer_type, content=f"ذكرى {i} عن النفط والطاقة Energy {i}",
                                       key=f"k{i}", importance=2))
    return uid, cfg, repo, mem


def ops(uid, cfg, repo, mem):
    from datetime import datetime, timedelta, timezone

    from app.db.models import Conversation, Message
    from app.services import task_checkpoints as TC

    def outbox_record():
        TC.outbox_record(uid, {"type": "assistant_message", "content": "مرحبا " * 20})

    def outbox_since():
        TC.outbox_since(uid, 0, limit=50)

    def checkpoint_cycle():
        tid = uuid.uuid4().hex
        TC.begin(tid, "cycle", uid, title="bench", employee_id=cfg.id)
        TC.phase(tid, "observe", 10)
        TC.record_action(tid, f"like:{tid}", {"url": "https://x.com/a/status/1"})
        TC.finish(tid, TC.SUCCEEDED)

    def claim_resume():
        tid = uuid.uuid4().hex
        TC.begin(tid, "chat", uid, title="bench")
        TC.finish(tid, TC.INTERRUPTED)
        TC.claim_resume(tid, uid, TC.CANCELLED)

    def repo_save():
        c = repo.get(cfg.id)
        c.runtime_state.cycles_completed += 1
        repo.save(c)

    def list_due():
        repo.list_due((datetime.now(timezone.utc) + timedelta(days=1)).isoformat())

    def conversations():
        db = DB.SessionLocal()
        try:
            convs = (db.query(Conversation).filter(Conversation.user_id == uid)
                     .order_by(Conversation.updated_at.desc()).limit(20).all())
            for c in convs[:5]:
                db.query(Message).filter(Message.conversation_id == c.id).order_by(Message.created_at).all()
        finally:
            db.close()

    def memory_search():
        mem.recall(cfg.id, "النفط", limit=10)

    return {"outbox_record": outbox_record, "outbox_since": outbox_since, "checkpoint_cycle": checkpoint_cycle,
            "claim_resume": claim_resume, "repo_save": repo_save, "list_due": list_due,
            "conversations": conversations, "memory_search": memory_search}


def run(fn, threads: int, per_thread: int):
    lat, errors = [], []
    lock = threading.Lock()

    def worker():
        mine = []
        for _ in range(per_thread):
            t0 = time.perf_counter()
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(repr(e)[:200])
            mine.append((time.perf_counter() - t0) * 1000)
        with lock:
            lat.extend(mine)

    t0 = time.perf_counter()
    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    wall = time.perf_counter() - t0
    lat.sort()
    return {"threads": threads, "ops": len(lat), "p50_ms": round(statistics.median(lat), 2),
            "p95_ms": round(lat[int(len(lat) * 0.95) - 1], 2), "ops_per_s": round(len(lat) / wall, 1),
            "errors": len(errors), "error_sample": errors[:3]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=None)
    ap.add_argument("--schema", choices=("alembic", "create_all"), default="alembic")
    ap.add_argument("--threads", default="1,8,32")
    ap.add_argument("--per-thread", type=int, default=25)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    url = args.url or f"sqlite:///{(Path(tempfile.mkdtemp()) / 'bench.db').as_posix()}"
    eng = setup(url, args.schema)
    uid, cfg, repo, mem = seed()
    results = {"dialect": eng.dialect.name, "schema": args.schema, "per_thread": args.per_thread, "ops": {}}
    for name, fn in ops(uid, cfg, repo, mem).items():
        results["ops"][name] = []
        for n in (int(x) for x in args.threads.split(",")):
            r = run(fn, n, args.per_thread)
            results["ops"][name].append(r)
            print(f"{eng.dialect.name:<10} {name:<17} t={n:<3} p50={r['p50_ms']:>8}ms p95={r['p95_ms']:>8}ms "
                  f"{r['ops_per_s']:>8}/s errors={r['errors']}" + (f" {r['error_sample'][0]}" if r['errors'] else ""),
                  flush=True)
    results["pool"] = {"checked_out_after": eng.pool.checkedout() if hasattr(eng.pool, "checkedout") else None}
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    eng.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
