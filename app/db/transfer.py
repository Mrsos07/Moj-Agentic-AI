"""
transfer — نسخ قاعدة كاملة بين محرّكين (SQLite ⇄ PostgreSQL) على الـmetadata نفسها، قابل للاستئناف.

المبادئ (كلها مُختبرة في verify_database.py):

* المصدر يُقرأ فقط. ``snapshot_sqlite`` يأخذ نسخة متّسقة ويُثبت أن sha256 الأصل لم يتغيّر.
* المصدر والهدف على head (لقطة SQLite قديمة تُتبنّى وتُرقّى **على النسخة** لا على الأصل).
* النسخ بـSQLAlchemy Core على جداول النماذج نفسها: التواريخ والـBoolean والـJSON تتحوّل بأنواعها، لا
  بنص SQL يدوي.
* دفعات keyset بالمفتاح الأساسي. كل دفعة + سجل تقدّمها (``_sqlite_import_progress``) في معاملة واحدة:
  قتل العملية في أي لحظة ثم ``resume=True`` يكمل من آخر دفعة مُرتكبة بلا تكرار ولا فقد.
* صفّ يشير لأب غير موجود في الهدف (SQLite لا يفرض الـFK) لا يُحذف ولا يُكسر به القيد: يُنسخ كاملاً
  إلى ``_migration_quarantine`` مع السبب. في النقل العكسي إلى SQLite يعود لجدوله الأصلي.
* قيمة لا يقبلها الهدف (NUL، تجاوز طول عمود محدود) ⇒ ``TransferError`` بالجدول والمفتاح؛ لا تعديل صامت.
* المتسلسلات: التالي = أكبر من (أكبر معرّف، آخر رقم وزّعه المصدر) — لا يُعاد معرّف ولا ينزل
  تسلسل ``realtime_outbox`` عن آخر ``seq`` رآه المتصفح.
* التحقق: العدد لكل جدول (المصدر = الهدف + العزل) وبصمة sha256 لكل جدول مستقلة عن ترتيب الصفوف
  (ترتيب الفرز يختلف بين SQLite وPG) فوق قيم مطبّعة.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, Integer, String, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.sql.schema import Table

from app.db import migrations as MIG
from app.db.database import Base

PROGRESS = "_sqlite_import_progress"
QUARANTINE = "_migration_quarantine"
#: لا تُنسخ: سجل تقدّم هذه الأداة نفسها
SKIP_TABLES = {PROGRESS}
BATCH = 1000


class TransferError(RuntimeError):
    pass


class InjectedFault(RuntimeError):
    """يرفعها خطّاف الاختبار لمحاكاة قتل العملية."""


def _models_loaded() -> None:
    import app.db.models  # noqa: F401
    import app.trend_detector.models  # noqa: F401


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_sqlite(src: Path, out_dir: Path) -> Dict[str, Any]:
    """نسخة متّسقة من ملف SQLite (online backup، قراءة فقط) + sha256 الأصل قبل/بعد."""
    src = Path(src)
    out_dir.mkdir(parents=True, exist_ok=True)
    before = sha256_file(src)
    dest = out_dir / src.name
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(str(dest))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    pristine = out_dir / f"{src.stem}.pristine{src.suffix}"
    shutil.copyfile(dest, pristine)
    return {"source": str(src), "snapshot": str(dest), "pristine": str(pristine),
            "source_sha256_before": before, "snapshot_sha256": sha256_file(pristine)}


# ─────────────────────────── تطبيع القيم ───────────────────────────
def canon(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def decanon(table: Table, data: Dict[str, Any]) -> Dict[str, Any]:
    """عكس ``canon`` بحسب نوع العمود (لإعادة صفّ معزول إلى جدوله)."""
    out = {}
    for col in table.columns:
        if col.name not in data:
            continue
        v = data[col.name]
        if v is not None:
            t = col.type
            if isinstance(t, DateTime):
                v = datetime.fromisoformat(v)
            elif isinstance(t, Date):
                v = date.fromisoformat(v)
            elif isinstance(t, Float):
                v = float(v)
            elif isinstance(t, JSON) and isinstance(v, str):
                v = json.loads(v)
        out[col.name] = v
    return out


def _row_digest(row: Dict[str, Any]) -> bytes:
    return hashlib.sha256(json.dumps({k: canon(v) for k, v in row.items()}, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")).digest()


# ─────────────────────────── أدوات ───────────────────────────
def app_tables() -> List[Table]:
    _models_loaded()
    return [t for t in Base.metadata.sorted_tables if t.name not in SKIP_TABLES]


def _pk(table: Table):
    cols = list(table.primary_key.columns)
    if len(cols) != 1:
        raise TransferError(f"{table.name}: مفتاح أساسي مركّب غير مدعوم")
    return cols[0]


def _bounded_strings(table: Table, dialect) -> Dict[str, int]:
    """أعمدة نصية محدودة الطول **في لهجة الهدف** (المتغيّرات إلى TEXT على PG مستثناة).

    SQLite لا يفرض الطول أصلاً، فلا حدود عند النقل العكسي إليه."""
    out: Dict[str, int] = {}
    if dialect.name != "postgresql":
        return out
    for col in table.columns:
        impl = col.type.dialect_impl(dialect)
        if isinstance(impl, String) and getattr(impl, "length", None):
            out[col.name] = impl.length
    return out


def _check_values(table: Table, rows: List[Dict[str, Any]], bounded: Dict[str, int], pk_name: str,
                  dst_dialect: str) -> None:
    for r in rows:
        for k, v in r.items():
            if isinstance(v, str):
                if dst_dialect == "postgresql" and "\x00" in v:
                    raise TransferError(f"{table.name} pk={r[pk_name]!r} عمود {k}: يحوي NUL (\\x00) لا يقبله PostgreSQL")
                if k in bounded and len(v) > bounded[k]:
                    raise TransferError(f"{table.name} pk={r[pk_name]!r} عمود {k}: الطول {len(v)} > {bounded[k]}")


def sequence_floor(engine: Engine, table: Table) -> int:
    """آخر رقم وزّعه المحرّك لهذا الجدول (قد يتجاوز أكبر معرّف موجود بعد حذف)."""
    pk = _pk(table)
    with engine.connect() as c:
        mx = c.execute(select(func.max(pk))).scalar() or 0
        if not isinstance(pk.type, Integer):
            return 0
        if engine.dialect.name == "sqlite":
            try:
                seq = c.execute(text("SELECT seq FROM sqlite_sequence WHERE name=:n"), {"n": table.name}).scalar()
            except Exception:  # noqa: BLE001 - لا جدول sqlite_sequence
                seq = None
        else:
            name = c.execute(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": pk.name}).scalar()
            seq = None
            if name:
                last, called = c.execute(text(f"SELECT last_value, is_called FROM {name}")).one()
                seq = last if called else last - 1
    return max(int(mx), int(seq or 0))


def _set_sequence(conn, dialect: str, table: Table, floor: int) -> None:
    pk = _pk(table)
    if not isinstance(pk.type, Integer):
        return
    if dialect == "postgresql":
        name = conn.execute(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": pk.name}).scalar()
        if name:
            if floor > 0:
                conn.execute(text("SELECT setval(:s, :v, true)"), {"s": name, "v": floor})
            else:
                conn.execute(text("SELECT setval(:s, 1, false)"), {"s": name})
    elif dialect == "sqlite" and floor > 0:
        has = conn.execute(text("SELECT name FROM sqlite_master WHERE name='sqlite_sequence'")).scalar()
        if not has:
            return
        n = conn.execute(text("UPDATE sqlite_sequence SET seq=:v WHERE name=:n AND seq<:v"),
                         {"v": floor, "n": table.name}).rowcount
        if not n and conn.execute(text("SELECT 1 FROM sqlite_sequence WHERE name=:n"), {"n": table.name}).scalar() is None:
            if table.dialect_options["sqlite"].get("autoincrement"):
                conn.execute(text("INSERT INTO sqlite_sequence(name, seq) VALUES(:n, :v)"), {"n": table.name, "v": floor})


def _require_head(engine: Engine, role: str) -> None:
    cur, head = MIG.current_revision(engine), MIG.head_revision()
    if cur != head:
        raise TransferError(f"{role} على المراجعة {cur} لا {head} — رقِّه أولاً")


def _counts(engine: Engine, tables: Iterable[Table]) -> Dict[str, int]:
    with engine.connect() as c:
        return {t.name: int(c.execute(select(func.count()).select_from(t)).scalar() or 0) for t in tables}


# ─────────────────────────── النسخ ───────────────────────────
@dataclass
class Report:
    tables: Dict[str, Dict[str, int]] = field(default_factory=dict)
    quarantined: Dict[str, int] = field(default_factory=dict)
    restored_from_quarantine: int = 0
    sequences: Dict[str, int] = field(default_factory=dict)
    resumed: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {"tables": self.tables, "quarantined": self.quarantined, "sequences": self.sequences,
                "restored_from_quarantine": self.restored_from_quarantine, "resumed": self.resumed}


def copy_database(src: Engine, dst: Engine, *, batch: int = BATCH, resume: bool = False,
                  restore_quarantine: Optional[bool] = None,
                  fault: Optional[Callable[[str, int, str], None]] = None,
                  log: Callable[[str], None] = print) -> Report:
    """انسخ كل جداول التطبيق من ``src`` إلى ``dst`` (كلاهما على head).

    ``fault(table, batch_no, phase)`` خطّاف اختبار: يُستدعى بـ phase="inside" قبل ارتكاب الدفعة
    (يُثبت أن الدفعة تتراجع كاملة) و"after" بعد ارتكابها (يُثبت الاستئناف).
    ``restore_quarantine`` (الافتراضي: نعم إن كان الهدف SQLite) يعيد صفوف العزل إلى جداولها.
    """
    _require_head(src, "المصدر")
    _require_head(dst, "الهدف")
    tables = app_tables()
    meta = Base.metadata
    prog, quar = meta.tables[PROGRESS], meta.tables[QUARANTINE]
    dialect = dst.dialect.name
    if restore_quarantine is None:
        restore_quarantine = dialect == "sqlite"
    rep = Report(resumed=resume)

    with dst.connect() as c:
        done = {r.table_name: r for r in c.execute(select(prog))}
    if not resume:
        busy = {n: k for n, k in _counts(dst, tables).items() if k}
        if busy or done:
            raise TransferError(f"الهدف ليس فارغاً: {busy or list(done)} — استخدم resume أو هدفاً جديداً")

    for table in tables:
        state = done.get(table.name)
        if state is not None and state.finished_at is not None:
            rep.tables[table.name] = {"copied": int(state.rows), "skipped_done": 1}
            continue
        pk = _pk(table)
        bounded = _bounded_strings(table, dst.dialect)
        last = state.last_pk if state is not None else None
        copied = int(state.rows) if state is not None else 0
        if state is None:
            with dst.begin() as c:
                c.execute(prog.insert().values(table_name=table.name, last_pk=None, rows=0,
                                               started_at=datetime.utcnow()))
        fks = [(fk.parent.name, fk.column.table, fk.column) for fk in table.foreign_keys]
        n_batch = 0
        while True:
            q = select(table).order_by(pk).limit(batch)
            if last is not None:
                q = q.where(pk > (int(last) if isinstance(pk.type, Integer) else last))
            with src.connect() as sc:
                rows = [dict(r._mapping) for r in sc.execute(q)]
            if not rows:
                break
            _check_values(table, rows, bounded, pk.name, dialect)
            n_batch += 1
            with dst.begin() as c:
                keep, bad = rows, []
                for col, ptable, pcol in fks:
                    vals = {r[col] for r in keep if r[col] is not None}
                    if not vals:
                        continue
                    have = {v for (v,) in c.execute(select(pcol).where(pcol.in_(vals)))}
                    miss = [r for r in keep if r[col] is not None and r[col] not in have]
                    if miss:
                        bad += [(r, f"{col} → {ptable.name}.{pcol.name}={r[col]!r} غير موجود") for r in miss]
                        keep = [r for r in keep if not (r[col] is not None and r[col] not in have)]
                if keep:
                    c.execute(table.insert(), keep)
                if bad:
                    c.execute(quar.insert(), [{
                        "source_table": table.name, "source_pk": str(r[pk.name]), "reason": why,
                        "row_json": {k: canon(v) for k, v in r.items()}, "migrated_at": datetime.utcnow(),
                    } for r, why in bad])
                    rep.quarantined[table.name] = rep.quarantined.get(table.name, 0) + len(bad)
                last = str(rows[-1][pk.name])
                copied += len(rows)
                c.execute(prog.update().where(prog.c.table_name == table.name)
                          .values(last_pk=last, rows=copied))
                if fault:
                    fault(table.name, n_batch, "inside")
            if fault:
                fault(table.name, n_batch, "after")
        with dst.begin() as c:
            c.execute(prog.update().where(prog.c.table_name == table.name).values(finished_at=datetime.utcnow()))
        rep.tables[table.name] = {"copied": copied}
        log(f"  {table.name}: {copied}" + (f" (عزل {rep.quarantined[table.name]})" if rep.quarantined.get(table.name) else ""))

    if restore_quarantine:
        rep.restored_from_quarantine = _restore_quarantine(dst)
    with dst.begin() as c:
        for table in tables:
            floor = max(sequence_floor(src, table), sequence_floor(dst, table))
            _set_sequence(c, dialect, table, floor)
            if isinstance(_pk(table).type, Integer):
                rep.sequences[table.name] = floor
    return rep


def _restore_quarantine(dst: Engine) -> int:
    """صفوف العزل تعود لجداولها (لهدف لا يفرض الـFK: SQLite) — النقل العكسي يطابق الأصل حرفياً."""
    meta = Base.metadata
    quar = meta.tables[QUARANTINE]
    n = 0
    with dst.begin() as c:
        rows = list(c.execute(select(quar).order_by(quar.c.id)))
        for r in rows:
            table = meta.tables.get(r.source_table)
            if table is None:
                continue
            data = r.row_json if isinstance(r.row_json, dict) else json.loads(r.row_json)
            c.execute(table.insert().values(**decanon(table, data)))
            n += 1
        if rows:
            c.execute(quar.delete())
    return n


# ─────────────────────────── التحقق ───────────────────────────
def row_digests(engine: Engine, table: Table, batch: int = 5000) -> List[bytes]:
    """بصمة كل صفّ (قيم مطبّعة)؛ تُفرز عند المقارنة فلا يهم ترتيب الفرز في اللهجة."""
    pk = _pk(table)
    digests: List[bytes] = []
    last = None
    with engine.connect() as c:
        while True:
            q = select(table).order_by(pk).limit(batch)
            if last is not None:
                q = q.where(pk > last)
            rows = [dict(r._mapping) for r in c.execute(q)]
            if not rows:
                break
            digests += [_row_digest(r) for r in rows]
            last = rows[-1][pk.name]
    return digests


def _fingerprint(digests: List[bytes]) -> str:
    return hashlib.sha256(b"".join(sorted(digests))).hexdigest()


def quarantine_digests(engine: Engine) -> Dict[str, List[bytes]]:
    quar = Base.metadata.tables[QUARANTINE]
    out: Dict[str, List[bytes]] = {}
    with engine.connect() as c:
        for r in c.execute(select(quar.c.source_table, quar.c.row_json)):
            data = r.row_json if isinstance(r.row_json, dict) else json.loads(r.row_json)
            out.setdefault(r.source_table, []).append(
                hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).digest())
    return out


def verify(src: Engine, dst: Engine) -> Dict[str, Any]:
    """(صفوف المصدر + عزله) = (صفوف الهدف + عزله)، جدولاً جدولاً، بالعدد والبصمة.

    الأمامي: عزل المصدر فارغ والهدف يعزل الأيتام. العكسي إلى SQLite: الأيتام تعود لجداولها.
    """
    tables = [t for t in app_tables() if t.name != QUARANTINE]
    sq, dq = quarantine_digests(src), quarantine_digests(dst)
    out: Dict[str, Any] = {"tables": {}, "ok": True}
    for t in tables:
        s_rows, d_rows = row_digests(src, t), row_digests(dst, t)
        s_all, d_all = s_rows + sq.get(t.name, []), d_rows + dq.get(t.name, [])
        hs, hd = _fingerprint(s_all), _fingerprint(d_all)
        ok = len(s_all) == len(d_all) and hs == hd
        out["tables"][t.name] = {"source": len(s_rows), "target": len(d_rows),
                                 "quarantined": len(dq.get(t.name, [])), "sha256": hs, "match": ok}
        out["ok"] = out["ok"] and ok
    return out


def collation_warning(engine: Engine) -> Optional[str]:
    """ترتيب النصوص في PG يجب أن يطابق SQLite (ترتيب نقاط الترميز): C أو C.UTF-8."""
    if engine.dialect.name != "postgresql":
        return None
    with engine.connect() as c:
        coll = c.execute(text("SELECT datcollate FROM pg_database WHERE datname = current_database()")).scalar()
        prov = c.execute(text("SELECT datlocprovider FROM pg_database WHERE datname = current_database()")).scalar()
        builtin = c.execute(text("SELECT datlocale FROM pg_database WHERE datname = current_database()")).scalar()
    if prov == "b" and (builtin or "").upper() in ("C", "C.UTF-8"):
        return None
    if prov == "c" and (coll or "").upper() in ("C", "POSIX", "C.UTF-8"):
        return None
    return (f"ترتيب النصوص في قاعدة PostgreSQL هو {coll!r} (provider={prov}) لا ترتيب نقاط الترميز الذي "
            "يستخدمه SQLite — ORDER BY على النصوص سيختلف. أنشئ القاعدة بـ "
            "LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C.UTF-8'")
