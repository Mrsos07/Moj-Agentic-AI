"""
sqlite_to_pg.py — نقل data/app.db إلى PostgreSQL (والعكس للتراجع)، قابل للاستئناف، والأصل لا يُلمس.

الأمامي (أوقف الخادم الخلفي أولاً حتى لا تُكتب بيانات بعد اللقطة):

    python scripts/sqlite_to_pg.py --source data/app.db --target "$DATABASE_URL" --dry-run
    python scripts/sqlite_to_pg.py --source data/app.db --target "$DATABASE_URL"
    python scripts/sqlite_to_pg.py --source data/app.db --target "$DATABASE_URL" --resume   ← بعد انقطاع
    python scripts/sqlite_to_pg.py --verify-only --snapshot backups/premigration-…/app.db --target "$DATABASE_URL"

العكسي (تراجع بعد كتابات على PostgreSQL) — إلى ملف SQLite **جديد**:

    python scripts/sqlite_to_pg.py --reverse --target "$DATABASE_URL" --out data/app.db.from_pg

ما يحدث في الأمامي:
1. sha256 للأصل، ونسخة متّسقة (online backup) إلى backups/premigration-<ts>/ (+ نسخة pristine).
2. النسخة (لا الأصل) تُتبنّى وتُرقّى إلى head.
3. الهدف يجب أن يكون على head وفارغاً (أو --resume): ``python scripts/db_migrate.py upgrade``.
4. النسخ بدفعات، الأيتام إلى _migration_quarantine، المتسلسلات، ثم التحقق (عدد + بصمة لكل جدول).
5. sha256 الأصل يُعاد حسابه ويجب أن يطابق — وإلا فشل (شيء كتب في الأصل أثناء النقل).
التقرير JSON في مجلد اللقطة. رمز الخروج 0 فقط إن نجح التحقق.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.db import database as DB  # noqa: E402
from app.db import migrations as MIG  # noqa: E402
from app.db import transfer as T  # noqa: E402


def _sqlite_engine(path: Path):
    return DB.build_engine(f"sqlite:///{Path(path).resolve().as_posix()}")


def _dry_run(src, dst) -> dict:
    from sqlalchemy import select

    tables = T.app_tables()
    counts = T._counts(src, tables)
    orphans = {}
    with src.connect() as c:
        for t in tables:
            for fk in t.foreign_keys:
                p = fk.column
                q = (select(t.c[fk.parent.name]).where(t.c[fk.parent.name].isnot(None))
                     .where(~t.c[fk.parent.name].in_(select(p))))
                n = len(c.execute(q).all())
                if n:
                    orphans[f"{t.name}.{fk.parent.name}→{p.table.name}"] = n
    return {"source_rows": counts, "total": sum(counts.values()), "orphans_to_quarantine": orphans,
            "target_revision": MIG.current_revision(dst), "head": MIG.head_revision(),
            "target_rows": T._counts(dst, tables) if MIG.current_revision(dst) == MIG.head_revision() else None,
            "collation_warning": T.collation_warning(dst)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="استيراد قاعدة التطبيق الرئيسية القديمة أو تصدير مخططها الرئيسي للأرشيف")
    ap.add_argument("--source", default=str(ROOT / "data" / "app.db"), help="ملف SQLite الأصلي (قراءة فقط)")
    ap.add_argument("--target", default=None, help="رابط PostgreSQL (postgresql+psycopg2://…)")
    ap.add_argument("--out-dir", default=str(ROOT / "backups"))
    ap.add_argument("--snapshot", default=None, help="لقطة موجودة (للاستئناف/التحقق) بدل لقطة جديدة")
    ap.add_argument("--batch", type=int, default=T.BATCH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--reverse", action="store_true", help="PostgreSQL ← ملف SQLite جديد (--out)")
    ap.add_argument("--out", default=None, help="مع --reverse: مسار ملف SQLite جديد (يجب ألا يكون موجوداً)")
    args = ap.parse_args(argv)

    dst = DB.build_engine(DB.resolve_database_url({"DATABASE_URL": args.target} if args.target else None))
    if dst.dialect.name != "postgresql":
        ap.error("--target يجب أن يكون PostgreSQL")
    print(f"PostgreSQL: {DB.safe_url(dst)}")

    if args.reverse:
        out = Path(args.out or ROOT / "data" / "app.db.from_pg")
        if out.exists():
            ap.error(f"{out} موجود — اختر مساراً جديداً (لا نكتب فوق ملف)")
        out.parent.mkdir(parents=True, exist_ok=True)
        new = _sqlite_engine(out)
        MIG.upgrade(new, "head")
        rep = T.copy_database(dst, new, batch=args.batch)
        ver = T.verify(dst, new)
        report = {"direction": "pg_to_sqlite", "out": str(out), "copy": rep.as_dict(), "verify": ver}
        Path(str(out) + ".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                   encoding="utf-8")
        print(("✅" if ver["ok"] else "❌") + f" التحقق — {out} (التقرير بجانبه)")
        return 0 if ver["ok"] else 1

    src_path = Path(args.source)
    if args.snapshot:
        snap = {"snapshot": args.snapshot, "source": str(src_path), "pristine": None,
                "source_sha256_before": T.sha256_file(src_path) if src_path.exists() else None}
        work = Path(args.snapshot).parent
    else:
        work = Path(args.out_dir) / f"premigration-{datetime.now():%Y%m%d-%H%M%S}"
        snap = T.snapshot_sqlite(src_path, work)
        print(f"لقطة: {snap['snapshot']}  (sha256 الأصل {snap['source_sha256_before'][:16]}…)")
    src = _sqlite_engine(Path(snap["snapshot"]))
    adopt = MIG.ensure_schema(src, auto=True)  # على اللقطة فقط
    print(f"اللقطة على {adopt['revision']}" + (" (تبنّي)" if adopt.get("adopted") else ""))

    if args.dry_run:
        print(json.dumps(_dry_run(src, dst), ensure_ascii=False, indent=2, default=str))
        return 0
    warn = T.collation_warning(dst)
    if warn:
        print(f"⚠️ {warn}")

    report = {"direction": "sqlite_to_pg", "snapshot": snap, "adopt": adopt, "target": DB.safe_url(dst)}
    if not args.verify_only:
        rep = T.copy_database(src, dst, batch=args.batch, resume=args.resume)
        report["copy"] = rep.as_dict()
    ver = T.verify(src, dst)
    report["verify"] = ver
    after = T.sha256_file(src_path) if src_path.exists() else None
    report["source_sha256_after"] = after
    untouched = snap.get("source_sha256_before") == after
    report["source_untouched"] = untouched
    (work / "transfer-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                               encoding="utf-8")
    for name, t in ver["tables"].items():
        if t["source"] or t["target"] or not t["match"]:
            print(f"  {'✅' if t['match'] else '❌'} {name}: {t['source']} → {t['target']}"
                  + (f" + عزل {t['quarantined']}" if t["quarantined"] else ""))
    print(("✅" if ver["ok"] else "❌") + " التحقق (عدد + بصمة لكل جدول)")
    print(("✅" if untouched else "❌") + " الأصل لم يتغيّر (sha256)"
          + ("" if untouched else " — كُتب فيه أثناء النقل: أوقف الخادم الخلفي وأعد من لقطة جديدة"))
    print(f"التقرير: {work / 'transfer-report.json'}")
    return 0 if ver["ok"] and untouched else 1


if __name__ == "__main__":
    raise SystemExit(main())
