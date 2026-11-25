"""
verify_database.py — طبقة البيانات: الإعداد، ترحيلات Alembic، النقل SQLite ⇄ PostgreSQL، التراجع،
فروق اللهجة، التزامن، حقن الأعطال، واستمرارية الجلسات والتشفير.

لا يلمس data/app.db ولا X ولا أي نموذج. أقسام PostgreSQL تعمل إن ضُبط MOJ_TEST_DATABASE_URL (رابط
خادم PG يُسمح فيه بإنشاء قواعد؛ كل قسم ينشئ قاعدة مؤقتة ويحذفها):

    .\\venv\\Scripts\\python.exe verify_database.py
    $env:MOJ_TEST_DATABASE_URL="postgresql+psycopg2://moj:…@127.0.0.1:55432/moj"; .\\venv\\Scripts\\python.exe verify_database.py

``MOJ_MASKED_DB=backups/masked/app.db`` (من scripts/mask_sqlite.py) يضيف نقل نسخة مموّهة من البيانات الحقيقية.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("TOPIC_SEMANTIC", "0")
os.environ.pop("DATABASE_URL", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent

from sqlalchemy import create_engine, func, select, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

import app.db.database as DB  # noqa: E402
from app.db import migrations as MIG  # noqa: E402
from app.db import testing as TST  # noqa: E402
from app.db import transfer as T  # noqa: E402
from app.db import models as M  # noqa: E402
import app.trend_detector.models as TDM  # noqa: E402

_auth_dir = tempfile.mkdtemp()
import app.auth.token_guard as _TG  # noqa: E402

_TG._PATH = Path(_auth_dir) / "auth_revocations.json"
_TG._state = None

FAILED = []
PG = TST.test_database_url()


def check(name, ok, detail=""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        FAILED.append(name)


def raises(fn, exc=Exception):
    try:
        fn()
    except exc as e:  # noqa: BLE001
        return e
    return None


def sqlite_engine(path: Path):
    return DB.build_engine(f"sqlite:///{Path(path).as_posix()}")


def pg_engine(env=None):
    return DB.build_engine(TST.create_pg_database(PG), env=env)


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="moj_vdb_"))


# ───────────────────────── بيانات اختبار تغطي كل الأنواع ─────────────────────────
def seed(engine) -> dict:
    """مستخدمان، محادثة برسائل عربية/إيموجي، موظف، ذاكرة (+3 أيتام)، صندوق صادر بأرقام محذوفة، أخبار JSON."""
    from app.aeos.security.vault import build_vault

    vault = build_vault()
    TST.bind_app(engine)
    S = DB.SessionLocal()
    try:
        u1 = M.User(email="a@example.test", password_hash="h1", name="علي", role="admin", is_admin=True)
        u2 = M.User(email="b@example.test", password_hash="h2", is_active=False)
        S.add_all([u1, u2])
        S.flush()
        c = M.Conversation(user_id=u1.id, session_id="s-1", title="محادثة 🚀 «اختبار»")
        S.add(c)
        S.flush()
        t0 = datetime(2026, 11, 22, 10, 0, 0, 123456)
        for i in range(25):
            S.add(M.Message(conversation_id=c.id, role="user" if i % 2 else "assistant",
                            content=f"رسالة {i} — Energy النفط 😀 '\"\\ ", confidence="0.8333333333333334",
                            created_at=t0 + timedelta(seconds=i)))
        S.add(M.AIEmployee(id="emp-1", user_id=u1.id, platform="x", account_username="acct_a",
                           persona_json=json.dumps({"display_name": "مازن"}, ensure_ascii=False),
                           next_wake_at=datetime(2026, 11, 22, 12, 0, 0)))
        S.flush()
        for i in range(8):
            S.add(M.EmployeeMemoryRecord(employee_id="emp-1", layer="long_term", content=f"ذكرى {i} Energy"))
        for i in range(3):
            S.add(M.EmployeeMemoryRecord(employee_id="ghost-employee", layer="long_term", content=f"يتيم {i}"))
        S.add(M.UserLLMSettings(user_id=u1.id, keys_json=json.dumps({"openrouter": vault.encrypt("sk-test-123")})))
        S.add(M.TaskCheckpoint(id="task-1", kind="chat", user_id=u1.id, status="interrupted",
                               state=json.dumps({"actions": [{"key": "like:1"}]})))
        S.add(TDM.Signal(platform="tavily", title="خبر", raw_data={"k": [1, "٢", None], "nested": {"b": True}},
                         has_media=True, is_processed=False, published_at=datetime(2026, 11, 21, 19, 10, 23)))
        S.add(TDM.Watchlist(candidate_id=1, next_check_at=datetime(2026, 11, 23), is_active=True))
        S.commit()
        for i in range(5):
            S.add(M.RealtimeOutbox(user_id=u1.id, payload=json.dumps({"type": "assistant_message", "i": i})))
        S.commit()
        top = [r.id for r in S.query(M.RealtimeOutbox).order_by(M.RealtimeOutbox.id.desc()).limit(2)]
        S.query(M.RealtimeOutbox).filter(M.RealtimeOutbox.id.in_(top)).delete(synchronize_session=False)
        S.commit()
        return {"u1": u1.id, "u2": u2.id, "conv": c.id, "outbox_max_issued": max(top)}
    finally:
        S.close()


# ═════════════════════════════ 1) الإعداد ═════════════════════════════
print("1) الإعداد: الرابط، الخيارات، لا رجوع صامت إلى SQLite")
check("PostgreSQL is the default and SQLite runtime URLs are rejected", DB.resolve_database_url({}).startswith("postgresql+") and raises(lambda: DB.resolve_database_url({"DATABASE_URL": "sqlite:///legacy.db"}), ValueError))
check("postgres:// ⇒ postgresql+psycopg2", DB.resolve_database_url({"DATABASE_URL": "postgres://u:p@h:5432/d"})
      .startswith("postgresql+psycopg2://"))
check("لهجة غير مدعومة تُرفض", raises(lambda: DB.resolve_database_url({"DATABASE_URL": "mysql://u:p@h/d"}), ValueError))
opts = DB.engine_options("postgresql+psycopg2://u:p@h/d", {"DB_POOL_SIZE": "7", "DB_STATEMENT_TIMEOUT_MS": "1234"})
check("PG: pool_pre_ping + حجم المجمّع + timezone=UTC + statement_timeout",
      opts["pool_pre_ping"] and opts["pool_size"] == 7 and "-c timezone=UTC" in opts["connect_args"]["options"]
      and "statement_timeout=1234" in opts["connect_args"]["options"], opts)
_e = DB.build_engine("postgresql+psycopg2://moj:s3cr3t-pw@127.0.0.1:1/x")
check("كلمة المرور لا تظهر في safe_url", "s3cr3t-pw" not in DB.safe_url(_e) and "***" in DB.safe_url(_e))
_e.dispose()
_probe = ("import app.db.database as D; print('DIALECT', D.engine.dialect.name, flush=True); D.init_db()")
_p = subprocess.run([sys.executable, "-c", _probe], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=120,
                    env={**os.environ, "PYTHONUTF8": "1", "DB_CONNECT_TIMEOUT": "2",
                         "DATABASE_URL": "postgresql+psycopg2://moj:x@127.0.0.1:1/nope"})
check("PG غير متاح ⇒ الإقلاع يفشل (لا رجوع صامت إلى SQLite)",
      _p.returncode != 0 and "DIALECT postgresql" in _p.stdout and "OperationalError" in _p.stderr,
      (_p.returncode, _p.stdout[-200:], _p.stderr[-300:]))

# ═════════════════════════════ 2) الترحيلات ═════════════════════════════
print("2) Alembic: من الصفر، الفرق مع النماذج، downgrade/upgrade، الحماية، التبنّي")
from alembic.autogenerate import compare_metadata  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402


def model_diff(engine):
    with engine.connect() as c:
        return compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), DB.Base.metadata)


engines = [("SQLite", sqlite_engine(tmpdir() / "m.db"))] + ([("PostgreSQL", pg_engine())] if PG else [])
for label, eng in engines:
    MIG.upgrade(eng, "head")
    check(f"[{label}] upgrade head من الصفر ⇒ {MIG.head_revision()}", MIG.current_revision(eng) == MIG.head_revision())
    d = model_diff(eng)
    check(f"[{label}] المخطط = النماذج (فرق autogenerate فارغ)", d == [], d)
    MIG.downgrade(eng, MIG.BASELINE_REV)
    MIG.upgrade(eng, "head")
    check(f"[{label}] downgrade إلى الأساس ثم upgrade ⇒ head بلا فرق",
          MIG.current_revision(eng) == MIG.head_revision() and model_diff(eng) == [])
    e = raises(lambda: MIG.downgrade(eng, "e153d985cf12"))
    check(f"[{label}] downgrade تحت الأساس (حذف جداول) مرفوض بلا العلم، والقاعدة باقية",
          e is not None and "MOJ_ALLOW_DESTRUCTIVE_DOWNGRADE" in str(e)
          and MIG.current_revision(eng) == MIG.head_revision() and "users" in MIG.table_names(eng))
    with eng.begin() as c:
        c.execute(M.MigrationQuarantine.__table__.insert().values(
            source_table="x", source_pk="1", reason="test", row_json={"a": 1}, migrated_at=datetime.utcnow()))
    e = raises(lambda: MIG.downgrade(eng, MIG.BASELINE_REV))
    check(f"[{label}] downgrade 0004 يرفض حذف جدول العزل وفيه صفوف",
          e is not None and MIG.current_revision(eng) == MIG.head_revision())
    if label == "PostgreSQL":
        with eng.connect() as c:
            kinds = dict(c.execute(text("SELECT column_name, data_type FROM information_schema.columns "
                                        "WHERE table_name='messages' AND column_name IN ('confidence','role')")).all())
        check("[PostgreSQL] النص الحرّ TEXT والحالات محدودة (confidence=text، role=varchar)",
              kinds == {"confidence": "text", "role": "character varying"}, kinds)
        check("[PostgreSQL] مخطط ليس على head + DB_AUTO_MIGRATE الافتراضي ⇒ رفض الإقلاع",
              isinstance(raises(lambda: MIG.ensure_schema(pg_engine())), MIG.SchemaNotCurrent))
    eng.dispose()

# التبنّي: قاعدة create_all قديمة = ما يبنيه 0003 بلا alembic_version
legacy_dir = tmpdir()
legacy = sqlite_engine(legacy_dir / "app.db")
MIG.upgrade(legacy, MIG.BASELINE_REV)
with legacy.begin() as c:
    c.execute(text("DROP TABLE alembic_version"))
    c.execute(text("INSERT INTO users(email,password_hash,is_admin,role,is_active) VALUES('old@example.test','h',1,'admin',1)"))
check("قاعدة قديمة غير مختومة تُكتشف للتبنّي", MIG.needs_adoption(legacy))
info = MIG.ensure_schema(legacy, auto=True)
backups = list((legacy_dir / "backups").glob("pre-adopt-*.db"))
check("التبنّي: نسخة احتياطية ثم stamp ثم upgrade head، والبيانات باقية",
      info.get("adopted") and backups and MIG.current_revision(legacy) == MIG.head_revision()
      and legacy.connect().execute(text("SELECT COUNT(*) FROM users")).scalar() == 1, info)
check("التبنّي: المخطط بعده = قاعدة جديدة على head", model_diff(legacy) == [], model_diff(legacy))
legacy.dispose()

odd_dir = tmpdir()
odd = sqlite_engine(odd_dir / "app.db")
MIG.upgrade(odd, MIG.BASELINE_REV)
with odd.begin() as c:
    c.execute(text("DROP TABLE alembic_version"))
    c.execute(text("ALTER TABLE users ADD COLUMN surprise TEXT"))
e = raises(lambda: MIG.ensure_schema(odd, auto=True), MIG.SchemaMismatch)
cols = [r[1] for r in odd.connect().execute(text("PRAGMA table_info(users)"))]
check("مخطط قديم غير متوقع ⇒ رفض بتقرير، بلا ختم ولا تعديل",
      e is not None and "surprise" in str(e) and MIG.current_revision(odd) is None and "surprise" in cols, e)
odd.dispose()

# ═════════════════════════════ 3+4) النقل والتراجع ═════════════════════════════
if PG:
    print("3) النقل SQLite → PostgreSQL: العدد، البصمة، العزل، المتسلسلات، الأصل لم يُلمس، الاستئناف")
    work = tmpdir()
    src_file = work / "app.db"
    src = sqlite_engine(src_file)
    MIG.upgrade(src, "head")
    ids = seed(src)
    src.dispose()
    sha_before = T.sha256_file(src_file)
    snap = T.snapshot_sqlite(src_file, work / "snap")
    s_eng = sqlite_engine(Path(snap["snapshot"]))
    dst = pg_engine()
    MIG.upgrade(dst, "head")
    check("قاعدة الاختبار بترتيب نقاط الترميز (C.UTF-8)", T.collation_warning(dst) is None, T.collation_warning(dst))
    rep = T.copy_database(s_eng, dst, batch=4, log=lambda *_: None)
    ver = T.verify(s_eng, dst)
    check("كل الجداول: المصدر = الهدف + العزل (عدد + sha256)", ver["ok"],
          {k: v for k, v in ver["tables"].items() if not v["match"]})
    check("الأيتام الثلاثة في _migration_quarantine لا محذوفة ولا في جدولها",
          rep.quarantined == {"employee_memories": 3}
          and ver["tables"]["employee_memories"]["target"] == 8, rep.quarantined)
    check("sha256 الأصل لم يتغيّر", T.sha256_file(src_file) == sha_before)
    TST.bind_app(dst)
    from app.services import task_checkpoints as TC  # noqa: E402

    rec = TC.outbox_record(ids["u1"], {"type": "assistant_message", "content": "بعد النقل"})
    check("تسلسل الصندوق الصادر لا ينزل عن آخر رقم وزّعه SQLite (المحذوف من القمة)",
          rec.get("seq", 0) > ids["outbox_max_issued"], (rec.get("seq"), ids["outbox_max_issued"]))
    with dst.connect() as c:
        mid = c.execute(text("SELECT nextval(pg_get_serial_sequence('messages','id'))")).scalar()
    check("تسلسل الجداول الأخرى بعد أكبر معرّف", mid > 25, mid)
    check("نقل ثانٍ إلى هدف غير فارغ بلا --resume مرفوض",
          isinstance(raises(lambda: T.copy_database(s_eng, dst, log=lambda *_: None)), T.TransferError))
    again = T.copy_database(s_eng, dst, resume=True, log=lambda *_: None)
    check("resume بعد اكتمال لا يفعل شيئاً (لا تكرار)",
          all(v.get("skipped_done") for v in again.tables.values()) and T.verify(s_eng, dst)["tables"]["messages"]["match"])

    print("3b) حقن عطل: قتل داخل الدفعة ⇒ تراجعها كاملة؛ بعدها ⇒ استئناف بلا تكرار")
    dst2 = pg_engine()
    MIG.upgrade(dst2, "head")

    def boom(where):
        def fault(table, n, phase):
            if (table, n, phase) == where:
                raise T.InjectedFault(where)
        return fault

    e = raises(lambda: T.copy_database(s_eng, dst2, batch=10, fault=boom(("messages", 2, "inside")), log=lambda *_: None),
               T.InjectedFault)
    with dst2.connect() as c:
        n_msg = c.execute(text("SELECT COUNT(*) FROM messages")).scalar()
        last = c.execute(text("SELECT last_pk, rows FROM _sqlite_import_progress WHERE table_name='messages'")).one()
    check("عطل داخل الدفعة 2 ⇒ الدفعة 1 فقط مرتكبة (10 صفوف) وسجل التقدّم يطابقها",
          e is not None and n_msg == 10 and last.rows == 10, (n_msg, tuple(last)))
    e = raises(lambda: T.copy_database(s_eng, dst2, batch=10, resume=True, fault=boom(("messages", 1, "after")),
                                       log=lambda *_: None), T.InjectedFault)
    with dst2.connect() as c:
        n_msg = c.execute(text("SELECT COUNT(*) FROM messages")).scalar()
    check("عطل بعد ارتكاب دفعة مُستأنفة ⇒ 20 صفاً بلا تكرار", e is not None and n_msg == 20, n_msg)
    T.copy_database(s_eng, dst2, batch=10, resume=True, log=lambda *_: None)
    ver2 = T.verify(s_eng, dst2)
    check("الاستئناف الأخير يكمل: تطابق تام بلا تكرار ولا فقد", ver2["ok"],
          {k: v for k, v in ver2["tables"].items() if not v["match"]})
    dst2.dispose()

    print("3c) قيم لا يقبلها PostgreSQL ⇒ فشل بالجدول والمفتاح، لا تعديل صامت")
    bad = sqlite_engine(tmpdir() / "bad.db")
    MIG.upgrade(bad, "head")
    with bad.begin() as c:
        c.execute(text("INSERT INTO users(id,email,password_hash,is_admin,role,is_active) VALUES(1,'n@example.test','h',0,'user',1)"))
        c.execute(text("INSERT INTO conversations(id,user_id,title) VALUES(7,1,'x')"))
        c.execute(text("INSERT INTO messages(id,conversation_id,role,content) VALUES(42,7,'user','قبل' || char(0) || 'بعد')"))
    dst3 = pg_engine()
    MIG.upgrade(dst3, "head")
    e = raises(lambda: T.copy_database(bad, dst3, log=lambda *_: None), T.TransferError)
    check("NUL في messages.content ⇒ TransferError يذكر الجدول والمفتاح 42",
          e is not None and "messages" in str(e) and "42" in str(e), e)
    bad.dispose()
    dst3.dispose()

    print("4) التراجع: PostgreSQL → ملف SQLite جديد = الأصل حرفياً (الأيتام تعود لجدولها)")
    rev_file = tmpdir() / "from_pg.db"
    rev = sqlite_engine(rev_file)
    MIG.upgrade(rev, "head")
    with dst.begin() as c:  # لا نقارن الرسالة التي أضيفت بعد النقل
        c.execute(text("DELETE FROM realtime_outbox WHERE payload LIKE '%بعد النقل%'"))
    rrep = T.copy_database(dst, rev, log=lambda *_: None)
    check("العكسي: PG = SQLite الجديد", T.verify(dst, rev)["ok"])
    vo = T.verify(s_eng, rev)
    check("العكسي: SQLite الجديد = لقطة الأصل (الأيتام الثلاثة أُعيدت لـemployee_memories)",
          vo["ok"] and rrep.restored_from_quarantine == 3 and vo["tables"]["employee_memories"]["target"] == 11,
          {k: v for k, v in vo["tables"].items() if not v["match"]})
    TST.bind_app(rev)
    from app.aeos.employee.repository import SqlEmployeeRepository  # noqa: E402

    emp = SqlEmployeeRepository().get("emp-1")
    check("التطبيق يعمل على SQLite الناتج (الموظف + نقاط الاستعادة)",
          emp is not None and emp.persona.display_name == "مازن" and TC.get("task-1") is not None)
    rev.dispose()

    print("8) الاستمرارية: التوكن، التشفير، ترتيب الرسائل")
    TST.bind_app(dst)
    from app.auth.security import create_access_token  # noqa: E402
    from app.aeos.security.vault import build_vault  # noqa: E402

    tok = create_access_token({"sub": str(ids["u1"]), "email": "a@example.test"})
    user, why = _TG.authenticate(tok)
    check("توكن صادر قبل النقل يُقبل بعده (المعرّفات محفوظة)", user is not None and user.id == ids["u1"], why)
    tok2 = create_access_token({"sub": str(ids["u2"]), "email": "b@example.test"})
    check("مستخدم معطّل يبقى معطّلاً بعد النقل", _TG.authenticate(tok2) == (None, "inactive"))
    with dst.connect() as c:
        kj = c.execute(text("SELECT keys_json FROM user_llm_settings")).scalar()
    check("الحقول المشفّرة تُفك بعد النقل", build_vault().decrypt(json.loads(kj)["openrouter"]) == "sk-test-123")
    order = lambda e: [r[0] for r in e.connect().execute(  # noqa: E731
        text("SELECT id FROM messages WHERE conversation_id=:c ORDER BY created_at, id"), {"c": ids["conv"]})]
    check("ترتيب رسائل المحادثة مطابق", order(s_eng) == order(dst) and len(order(dst)) == 25)
    s_eng.dispose()

    masked = os.environ.get("MOJ_MASKED_DB")
    if masked and Path(masked).exists():
        print("3d) نسخة مموّهة من البيانات الحقيقية")
        mwork = tmpdir()
        msnap = T.snapshot_sqlite(Path(masked), mwork)
        ms = sqlite_engine(Path(msnap["snapshot"]))
        MIG.ensure_schema(ms, auto=True)
        with sqlite3.connect(msnap["pristine"]) as raw:
            orphans = len(raw.execute("PRAGMA foreign_key_check").fetchall())
        md = pg_engine()
        MIG.upgrade(md, "head")
        mrep = T.copy_database(ms, md, log=lambda *_: None)
        mv = T.verify(ms, md)
        check(f"المموّهة: تطابق تام، والعزل = مخالفات FK في الأصل ({orphans})",
              mv["ok"] and sum(mrep.quarantined.values()) == orphans, mrep.quarantined)
        check("المموّهة: الأصل المموّه لم يتغيّر", T.sha256_file(Path(masked)) == msnap["source_sha256_before"])
        ms.dispose()
        md.dispose()
else:
    print("3/4/8) ⚠️ تخطّي أقسام PostgreSQL — اضبط MOJ_TEST_DATABASE_URL")

# ═════════════════════════════ 5) اللهجة ═════════════════════════════
print("5) فروق اللهجة: ilike، التواريخ، الترتيب، Boolean، JSON")
from app.aeos.memory.base import MemoryRecord  # noqa: E402
from app.aeos.memory.layers import LongTermMemory  # noqa: E402
from app.aeos.employee.repository import SqlEmployeeRepository  # noqa: E402

results = {}
for label, eng in [("SQLite", sqlite_engine(tmpdir() / "d.db"))] + ([("PostgreSQL", pg_engine())] if PG else []):
    MIG.upgrade(eng, "head")
    TST.bind_app(eng)
    with DB.SessionLocal() as S:
        S.add(M.AIEmployee(id="e1", platform="x", account_username="a"))
        S.commit()
    mem = LongTermMemory()
    mem.store("e1", MemoryRecord(layer=mem.layer_type, content="Energy النفط", key="k"))
    hit = [r.content for r in mem.recall("e1", "energy")]
    repo = SqlEmployeeRepository()
    aware = datetime(2026, 11, 22, 9, 30, 0, 654321, tzinfo=timezone.utc)
    repo.set_next_wake("e1", aware)
    naive_back = repo.get("e1").scheduler.next_wake_at
    with DB.SessionLocal() as S:
        stored = S.get(M.AIEmployee, "e1").next_wake_at
        due = [c.id for c in repo.list_due("2026-11-22T09:30:01+00:00")]
        for name in ("b", "a", "B", "ا", "Z", "é", "a b", "a-b", "_x"):
            S.add(M.AgentMemory(scope=name, kind="fact", content="x"))
        S.add(TDM.Signal(platform="t", raw_data={"x": [1, "٢"]}, is_processed=True))
        S.commit()
        names = [r.scope for r in S.query(M.AgentMemory).order_by(M.AgentMemory.scope)]
        sig = S.query(TDM.Signal).filter(TDM.Signal.is_processed == True).one()  # noqa: E712
    results[label] = {"hit": hit, "stored": stored, "due": due, "names": names, "raw": sig.raw_data}
    check(f"[{label}] بحث الذاكرة غير حساس لحالة الأحرف (energy ⇒ Energy)", hit == ["Energy النفط"], hit)
    check(f"[{label}] datetime واعٍ بـUTC يُخزَّن naive UTC ويُقارن صحيحاً",
          stored == aware.replace(tzinfo=None) and due == ["e1"], (stored, due))
    check(f"[{label}] Boolean وJSON ذهاباً وإياباً", sig.raw_data == {"x": [1, "٢"]})
    eng.dispose()
if PG:
    check("ترتيب النصوص متطابق في اللهجتين (نقاط الترميز)",
          results["SQLite"]["names"] == results["PostgreSQL"]["names"],
          (results["SQLite"]["names"], results["PostgreSQL"]["names"]))

# ═════════════════════════════ 6) التزامن ═════════════════════════════
if PG:
    print("6) التزامن على PostgreSQL (32 خيطاً)")
    eng = pg_engine({"DB_POOL_SIZE": "20", "DB_MAX_OVERFLOW": "20"})
    MIG.upgrade(eng, "head")
    TST.bind_app(eng)
    from app.services import task_checkpoints as TC  # noqa: E402,F811

    with DB.SessionLocal() as S:
        u = M.User(email="c@example.test", password_hash="h")
        S.add(u)
        S.commit()
        uid = u.id

    def parallel(n, fn):
        out, errs = [None] * n, []
        bar = threading.Barrier(n)

        def run(i):
            bar.wait()
            try:
                out[i] = fn(i)
            except Exception as ex:  # noqa: BLE001
                errs.append(repr(ex)[:200])
        ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        return out, errs

    TC.begin("claim-me", "chat", uid, title="t")
    TC.finish("claim-me", TC.INTERRUPTED)
    out, errs = parallel(32, lambda i: TC.claim_resume("claim-me", uid, TC.RUNNING))
    check("claim_resume متزامن ×32 ⇒ فائز واحد بالضبط", sum(1 for o in out if o) == 1 and not errs, (out.count(None), errs[:2]))
    out, errs = parallel(32, lambda i: TC.begin("same-id-123", "chat", uid, title="t"))
    with DB.SessionLocal() as S:
        n_rows = S.query(M.TaskCheckpoint).filter(M.TaskCheckpoint.id == "same-id-123").count()
    check("begin بالمعرّف نفسه ×32 ⇒ مهمة واحدة، وواحد فقط «جديد»",
          not errs and n_rows == 1 and sum(1 for o in out if o and o[1]) == 1, (errs[:2], n_rows))
    TC.begin("actions-1", "cycle", uid, title="t")
    out, errs = parallel(16, lambda i: [TC.record_action("actions-1", f"like:{i}:{k}") for k in range(5)])
    got = len(TC.get("actions-1", with_internals=True)["state"]["actions"])
    check("record_action ×16×5 متزامن ⇒ لا فعل ضائع (قفل الصف)", got == 80 and not errs, (got, errs[:2]))

    final, snaps, stop = set(), [], threading.Event()

    def reader():
        while not stop.is_set():
            snaps.append({r["seq"] for r in TC.outbox_since(uid, 0, limit=10_000)})

    rt = threading.Thread(target=reader)
    rt.start()
    out, errs = parallel(16, lambda i: [TC.outbox_record(uid, {"type": "assistant_message", "i": i, "k": k})["seq"]
                                        for k in range(10)])
    stop.set()
    rt.join()
    final = {r["seq"] for r in TC.outbox_since(uid, 0, limit=10_000)}
    gaps = [s for s in snaps if s and not {i for i in final if i <= max(s)} <= s]
    check("الصندوق الصادر متزامن ×16×10: كل رقم مرئي ⇒ كل ما قبله مرئي (لا رسالة تضيع عند الإعادة)",
          len(final) == 160 and not gaps and not errs, (len(final), len(gaps), errs[:2]))

    os.environ["DB_OPTIMISTIC_LOCKING"] = "1"
    try:
        repo = SqlEmployeeRepository()
        from app.aeos.employee.models import EmployeeConfig  # noqa: E402

        repo.save(EmployeeConfig(id="opt-1", user_id=uid, platform="x", account_username="opt"))
        a, b = repo.get("opt-1"), repo.get("opt-1")
        a.persona.display_name = "من الواجهة"
        repo.save(a)
        b.runtime_state.cycles_completed = 7
        repo.save(b)
        z = repo.get("opt-1")
        check("قفل متفائل (DB_OPTIMISTIC_LOCKING=1): حفظ نسخة قديمة يدمج ولا يمحو تعديل غيره",
              z.persona.display_name == "من الواجهة" and z.runtime_state.cycles_completed == 7,
              (z.persona.display_name, z.runtime_state.cycles_completed))
    finally:
        os.environ.pop("DB_OPTIMISTIC_LOCKING", None)
    c1, c2 = repo.get("opt-1"), repo.get("opt-1")
    c1.persona.display_name = "أ"
    repo.save(c1)
    c2.runtime_state.cycles_completed = 9
    repo.save(c2)
    check("بلا العلم: السلوك القديم كما هو (آخر حفظ يكتب الكل)", repo.get("opt-1").persona.display_name != "أ")

    # ═════════════════════════ 7) حقن الأعطال ═════════════════════════
    print("7) حقن الأعطال: اتصال مقتول، statement/lock timeout، انقطاع داخل معاملة")
    admin = create_engine(PG, isolation_level="AUTOCOMMIT")
    url = eng.url.render_as_string(hide_password=False)

    def kill(pid):
        with admin.connect() as c:
            c.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})

    with eng.connect() as c:
        pid = c.execute(text("SELECT pg_backend_pid()")).scalar()
    kill(pid)
    time.sleep(0.3)
    with eng.connect() as c:
        alive = c.execute(text("SELECT 1")).scalar()
    check("اتصال قتله الخادم ⇒ pool_pre_ping يستبدله ولا جلسة عالقة",
          alive == 1 and eng.pool.checkedout() == 0, eng.pool.checkedout())
    slow = DB.build_engine(url, env={"DB_STATEMENT_TIMEOUT_MS": "300"})
    t0 = time.time()
    def _sleep():
        with slow.connect() as c:
            c.execute(text("SELECT pg_sleep(3)"))
    e = raises(_sleep, OperationalError)
    check("statement_timeout يقطع استعلاماً معلّقاً", e is not None and "statement timeout" in str(e)
          and time.time() - t0 < 2.5, str(e)[:120])
    slow.dispose()
    holder = eng.connect()
    tx = holder.begin()
    holder.execute(text("SELECT id FROM users WHERE id=:u FOR UPDATE"), {"u": uid})
    locked = DB.build_engine(url, env={"DB_LOCK_TIMEOUT_MS": "300"})
    t0 = time.time()

    def _blocked():
        with locked.begin() as c:
            c.execute(text("UPDATE users SET name='x' WHERE id=:u"), {"u": uid})
    e = raises(_blocked, OperationalError)
    check("lock_timeout: صفّ محجوز ⇒ خطأ سريع لا انتظار بلا نهاية", e is not None and "lock timeout" in str(e)
          and time.time() - t0 < 2.5, str(e)[:120])
    tx.rollback()
    holder.close()
    locked.dispose()
    S = DB.SessionLocal()
    before = S.query(M.Conversation).count()
    S.add(M.Conversation(user_id=uid, title="نصف معاملة"))
    S.flush()
    kill(S.connection().execute(text("SELECT pg_backend_pid()")).scalar())
    e = raises(S.commit)
    S.rollback()
    S.close()
    with DB.SessionLocal() as S2:
        after = S2.query(M.Conversation).count()
    check("انقطاع داخل معاملة ⇒ لا نصف كتابة", e is not None and after == before, (before, after))
    admin.dispose()
    eng.dispose()
else:
    print("6/7) ⚠️ تخطّي التزامن وحقن الأعطال — اضبط MOJ_TEST_DATABASE_URL")

shutil.rmtree(_auth_dir, ignore_errors=True)
print()
if FAILED:
    print(f"❌ {len(FAILED)} فحص فشل: {FAILED}")
    sys.exit(1)
print("✅ كل فحوص طبقة البيانات نجحت" + ("" if PG else " (SQLite فقط)"))
