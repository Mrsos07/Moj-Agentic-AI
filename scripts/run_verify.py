"""
run_verify.py — يشغّل كل verify_*.py (أو المحدّدة) واحداً تلو الآخر ويحفظ النتيجة في JSON.

    .\\venv\\Scripts\\python.exe scripts\\run_verify.py --out backups\\verify-postgres.json
    .\\venv\\Scripts\\python.exe scripts\\run_verify.py verify_checkpoints.py verify_database.py

لكل ملف: رمز الخروج، المدة، عدد ✅/❌، وآخر 40 سطراً من المخرجات. المتغيّرات الحالية تُمرَّر كما هي
(مثلاً MOJ_TEST_DATABASE_URL لتشغيل الاختبارات على PostgreSQL).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--out", default=None)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args(argv)
    files = args.files or sorted(p.name for p in ROOT.glob("verify_*.py"))
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    results = []
    for name in files:
        t0 = time.time()
        try:
            p = subprocess.run([sys.executable, name], cwd=ROOT, env=env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=args.timeout)
            code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
        except subprocess.TimeoutExpired as e:
            code, out = "timeout", str(e.stdout or "")
        dt = round(time.time() - t0, 1)
        res = {"file": name, "code": code, "seconds": dt, "ok": out.count("✅"), "fail": out.count("❌"),
               "tail": out.splitlines()[-40:]}
        results.append(res)
        print(f"{'PASS' if code == 0 else 'FAIL'} {name:<32} {dt:>7}s  ✅{res['ok']} ❌{res['fail']}",
              flush=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [r["file"] for r in results if r["code"] != 0]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed" + (f" — failed: {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
