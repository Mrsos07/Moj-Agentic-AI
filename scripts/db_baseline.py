"""
db_baseline.py — لقطة خط الأساس لطبقة البيانات قبل أي تغيير (وبعده للمقارنة).

يكتب إلى backups/baseline-<ts>/ (خارج git والدوكر) ولا يكتب في القاعدة أبداً (تُفتح mode=ro):
  baseline.json : commit، sha256 الملف، المخطط الحي (DDL + أعمدة)، عدد الصفوف، إعدادات المحرّك
  endpoints.txt : كل مسارات FastAPI (الطريقة + المسار)

    .\\venv\\Scripts\\python.exe scripts\\db_baseline.py [--db data/app.db] [--out backups]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONUTF8", "1")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sqlite_schema(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        out = {"objects": [], "tables": {}}
        for typ, name, sql in con.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_autoindex%' "
                "ORDER BY type, name"):
            out["objects"].append({"type": typ, "name": name, "sql": sql})
            if typ == "table":
                cols = [dict(zip(("cid", "name", "type", "notnull", "default", "pk"), r))
                        for r in con.execute(f'PRAGMA table_info("{name}")')]
                rows = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                out["tables"][name] = {"rows": rows, "columns": cols}
        out["foreign_key_violations"] = len(con.execute("PRAGMA foreign_key_check").fetchall())
        try:
            out["alembic_version"] = [r[0] for r in con.execute("SELECT version_num FROM alembic_version")]
        except sqlite3.Error:
            out["alembic_version"] = None
        return out
    finally:
        con.close()


def endpoints() -> list:
    from app.main import app  # noqa: WPS433 - استيراد فقط، بلا startup

    rows = []
    for r in app.routes:
        methods = sorted(getattr(r, "methods", None) or ["WS"])
        rows.append(f"{','.join(methods):<20} {getattr(r, 'path', '')}")
    return sorted(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "app.db"))
    ap.add_argument("--out", default=str(ROOT / "backups"))
    ap.add_argument("--no-endpoints", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db)
    out = Path(args.out) / f"baseline-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()
    branch = subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip()

    from app.db import database as DB

    eng = DB.engine
    info = {
        "taken_at": datetime.now().isoformat(),
        "commit": commit, "branch": branch,
        "db_file": str(db), "db_sha256": sha256(db) if db.exists() else None,
        "db_bytes": db.stat().st_size if db.exists() else None,
        "engine": {"dialect": eng.dialect.name, "url": eng.url.render_as_string(hide_password=True),
                   "pool": type(eng.pool).__name__,
                   "pool_size": getattr(eng.pool, "size", lambda: None)()},
        "schema": sqlite_schema(db) if db.exists() else None,
    }
    (out / "baseline.json").write_text(json.dumps(info, ensure_ascii=False, indent=2, default=str),
                                       encoding="utf-8")
    if not args.no_endpoints:
        eps = endpoints()
        (out / "endpoints.txt").write_text("\n".join(eps) + "\n", encoding="utf-8")
        print(f"endpoints: {len(eps)}")
    tables = (info["schema"] or {}).get("tables", {})
    print(f"baseline → {out}")
    print(f"commit {commit[:8]} ({branch}) · sha256 {str(info['db_sha256'])[:16]}… · "
          f"{len(tables)} tables · {sum(t['rows'] for t in tables.values())} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
