"""
migrations — المسار الوحيد لبناء/ترقية مخطط القاعدة (Alembic)، لـSQLite وPostgreSQL معاً.

كان ``init_db()`` يستدعي ``create_all`` ثم ``PRAGMA``/``ALTER TABLE`` عند كل إقلاع للـAPI، وAlembic
موجود بمراجعتين قديمتين لا تُشغَّلان. الآن:

* ``ensure_schema(engine)`` (يستدعيها ``init_db`` عند الإقلاع):
  - المراجعة = head ⇒ لا شيء.
  - ``DB_AUTO_MIGRATE`` (الافتراضي 1 مع SQLite و0 مع PostgreSQL) ⇒ تبنّي القاعدة القديمة إن لزم ثم
    ``upgrade head``. وإلا ⇒ ``SchemaNotCurrent``: الـAPI لا يقلع على مخطط قديم؛ الترقية مع PG
    خطوة منفصلة (``scripts/db_migrate.py upgrade`` / خدمة ``migrate`` في الدوكر).
* التبنّي (قاعدة SQLite بُنيت بـ``create_all`` بلا ``alembic_version``): نسخة احتياطية أولاً، ثم أعمدة
  الترقيات الخفيفة القديمة، ثم مقارنة المخطط الحي بما تبنيه ``0003_full_baseline`` حرفياً؛ تطابق ⇒
  ``stamp`` ثم ``upgrade``؛ أي فرق ⇒ ``SchemaMismatch`` بتقرير وبلا أي تعديل تلقائي.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

logger = logging.getLogger("app.db.migrations")

ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_REV = "0003_full_baseline"
#: مراجعات ما قبل الأساس: قاعدة بها جداول create_all وتحمل إحداها (أو لا شيء) تُتبنّى
PRE_BASELINE = (None, "6e5dcbbbdac8", "e153d985cf12")
#: جدول لم يكن في المراجعتين القديمتين — وجوده يعني أن القاعدة بُنيت بـ create_all
_CREATE_ALL_MARKER = "ai_employees"


class SchemaNotCurrent(RuntimeError):
    """المخطط ليس على head والترقية التلقائية معطّلة."""


class SchemaMismatch(RuntimeError):
    """قاعدة قديمة لا يطابق مخططها الأساس — لا تُختم ولا تُعدَّل تلقائياً."""


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def alembic_config(connection=None) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head_revision() -> str:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> Optional[str]:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def table_names(engine: Engine) -> set:
    return set(inspect(engine).get_table_names())


def needs_adoption(engine: Engine) -> bool:
    return current_revision(engine) in PRE_BASELINE and _CREATE_ALL_MARKER in table_names(engine)


def upgrade(engine: Engine, revision: str = "head") -> None:
    with engine.begin() as conn:
        command.upgrade(alembic_config(conn), revision)


def downgrade(engine: Engine, revision: str) -> None:
    with engine.begin() as conn:
        command.downgrade(alembic_config(conn), revision)


def stamp(engine: Engine, revision: str) -> None:
    with engine.begin() as conn:
        command.stamp(alembic_config(conn), revision)


# ─────────────────────────── مقارنة المخطط ───────────────────────────
def schema_signature(engine: Engine, ignore: tuple = ("alembic_version", "sqlite_sequence")) -> Dict[str, Any]:
    """صورة مطبّعة للمخطط: الأعمدة (النوع، القابلية لـNULL، المفتاح)، الفهارس، القيود الفريدة، الـFK.

    القيم الافتراضية على الخادم مُستثناة عمداً: الترقيات الخفيفة القديمة أضافت ``role`` بـ
    ``DEFAULT 'user'`` بينما قاعدة بُنيت بعدها بـcreate_all بلا قيمة افتراضية — والمخطط واحد فعلياً.
    """
    insp = inspect(engine)
    sig: Dict[str, Any] = {}
    for t in sorted(insp.get_table_names()):
        if t in ignore:
            continue
        pk = insp.get_pk_constraint(t).get("constrained_columns") or []
        sig[t] = {
            "columns": {c["name"]: {"type": str(c["type"]).upper(), "nullable": bool(c["nullable"]) and c["name"] not in pk}
                        for c in insp.get_columns(t)},
            "pk": sorted(pk),
            "indexes": sorted((i["name"], tuple(i["column_names"]), bool(i["unique"]))
                              for i in insp.get_indexes(t) if not (i["name"] or "").startswith("sqlite_autoindex")),
            "unique": sorted(tuple(u["column_names"]) for u in insp.get_unique_constraints(t)),
            "fks": sorted((tuple(f["constrained_columns"]), f["referred_table"], tuple(f["referred_columns"]))
                          for f in insp.get_foreign_keys(t)),
        }
    return sig


def diff_signatures(live: Dict[str, Any], expected: Dict[str, Any]) -> list:
    out = []
    for t in sorted(set(live) | set(expected)):
        if t not in live:
            out.append(f"جدول ناقص: {t}")
            continue
        if t not in expected:
            out.append(f"جدول زائد: {t}")
            continue
        a, b = live[t], expected[t]
        for col in sorted(set(a["columns"]) | set(b["columns"])):
            if a["columns"].get(col) != b["columns"].get(col):
                out.append(f"{t}.{col}: الحي={a['columns'].get(col)} المتوقع={b['columns'].get(col)}")
        for key in ("pk", "indexes", "unique", "fks"):
            # unique=True + index=True يظهر في SQLite فهرساً فريداً لا قيداً — يكفي تطابق الفهارس
            if key == "unique":
                continue
            if a[key] != b[key]:
                out.append(f"{t} {key}: الحي={a[key]} المتوقع={b[key]}")
    return out


def baseline_signature() -> Dict[str, Any]:
    """المخطط الذي تبنيه السلسلة حتى ``0003_full_baseline`` على SQLite نظيف."""
    tmp = Path(tempfile.mkdtemp()) / "baseline.db"
    eng = create_engine(f"sqlite:///{tmp.as_posix()}")
    try:
        upgrade(eng, BASELINE_REV)
        return schema_signature(eng)
    finally:
        eng.dispose()


# ─────────────────────────── التبنّي ───────────────────────────
def _legacy_columns(engine: Engine) -> None:
    """الترقيات الخفيفة التي كانت تُطبَّق عند كل إقلاع — هنا فقط، مرة واحدة قبل الختم."""
    migrations = [
        ("social_accounts", "category", "VARCHAR(50)"),
        ("users", "role", "VARCHAR(20) NOT NULL DEFAULT 'user'"),
    ]
    with engine.connect() as con:
        for table, column, definition in migrations:
            cols = [c[1] for c in con.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()]
            if not cols or column in cols:
                continue
            con.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            if column == "category":
                con.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table}({column})")
            if (table, column) == ("users", "role"):
                con.exec_driver_sql("UPDATE users SET role='admin' WHERE is_admin=1")
            con.commit()
            logger.info("legacy column added: %s.%s", table, column)


def backup_sqlite(engine: Engine, label: str = "pre-adopt") -> Optional[Path]:
    """نسخة متّسقة (sqlite3 online backup) بجانب البيانات: data/backups/<label>-<ts>.db."""
    path = engine.url.database
    if engine.dialect.name != "sqlite" or not path or path == ":memory:" or not Path(path).exists():
        return None
    dest_dir = Path(path).resolve().parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{label}-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    src = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(dest))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return dest


def adopt(engine: Engine, backup: bool = True) -> Dict[str, Any]:
    """اختم قاعدة SQLite قديمة على الأساس بعد التأكد أن مخططها هو الأساس نفسه."""
    if engine.dialect.name != "sqlite":
        raise SchemaMismatch("التبنّي لقواعد SQLite القديمة فقط؛ PostgreSQL تُبنى بـ upgrade من الصفر")
    if not needs_adoption(engine):
        return {"adopted": False}
    saved = backup_sqlite(engine) if backup else None
    _legacy_columns(engine)
    diff = diff_signatures(schema_signature(engine), baseline_signature())
    if diff:
        raise SchemaMismatch("مخطط القاعدة لا يطابق الأساس — لم يُختم ولم يُعدَّل شيء:\n  "
                             + "\n  ".join(diff) + (f"\nالنسخة الاحتياطية: {saved}" if saved else ""))
    stamp(engine, BASELINE_REV)
    logger.info("legacy database adopted at %s (backup: %s)", BASELINE_REV, saved)
    return {"adopted": True, "backup": str(saved) if saved else None}


def ensure_schema(engine: Engine, auto: Optional[bool] = None) -> Dict[str, Any]:
    head = head_revision()
    cur = current_revision(engine)
    if cur == head:
        return {"revision": cur, "changed": False}
    if auto is None:
        auto = _flag("DB_AUTO_MIGRATE", engine.dialect.name == "sqlite")
    if not auto:
        raise SchemaNotCurrent(
            f"مخطط القاعدة على {cur or 'لا شيء'} والمطلوب {head}. شغّل الترقية أولاً: "
            "python scripts/db_migrate.py upgrade  (في الدوكر: خدمة migrate)")
    info: Dict[str, Any] = {}
    if needs_adoption(engine):
        info = adopt(engine)
    upgrade(engine, "head")
    return {"revision": head_revision(), "changed": True, "from": cur, **info}
