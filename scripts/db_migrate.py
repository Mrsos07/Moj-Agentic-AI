"""
db_migrate.py — ترقية مخطط القاعدة وفحصه (الرابط من DATABASE_URL، وإلا data/app.db).

    python scripts/db_migrate.py current            ← المراجعة الحالية والـhead
    python scripts/db_migrate.py check              ← رمز خروج 0 فقط إن كانت القاعدة على head
    python scripts/db_migrate.py upgrade            ← تبنّي SQLite القديمة إن لزم ثم upgrade head
    python scripts/db_migrate.py adopt              ← التبنّي وحده (نسخة احتياطية + مقارنة + stamp)
    python scripts/db_migrate.py downgrade <rev>    ← تحت 0003 مرفوض بلا MOJ_ALLOW_DESTRUCTIVE_DOWNGRADE=1

في الدوكر مع PostgreSQL هذا أمر خدمة ``migrate`` (docker-compose.postgres.yml) قبل إقلاع الخادم الخلفي.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.db import database as DB  # noqa: E402
from app.db import migrations as MIG  # noqa: E402
from app.db import models  # noqa: E402,F401
import app.trend_detector.models  # noqa: E402,F401


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ترقية مخطط قاعدة موج")
    ap.add_argument("action", choices=("current", "check", "upgrade", "adopt", "downgrade"))
    ap.add_argument("revision", nargs="?")
    args = ap.parse_args(argv)
    eng = DB.engine
    print(f"القاعدة: {DB.safe_url(eng)}")
    try:
        if args.action == "current":
            print(f"الحالية: {MIG.current_revision(eng)}  head: {MIG.head_revision()}")
        elif args.action == "check":
            cur, head = MIG.current_revision(eng), MIG.head_revision()
            print(f"الحالية: {cur}  head: {head}")
            return 0 if cur == head else 3
        elif args.action == "upgrade":
            info = MIG.ensure_schema(eng, auto=True)
            print(f"✅ على {info['revision']}" + (" (لا تغيير)" if not info["changed"] else f" (من {info.get('from')})")
                  + (f" — نسخة ما قبل التبنّي: {info['backup']}" if info.get("backup") else ""))
        elif args.action == "adopt":
            print(MIG.adopt(eng))
        elif args.action == "downgrade":
            if not args.revision:
                ap.error("downgrade يحتاج مراجعة")
            MIG.downgrade(eng, args.revision)
            print(f"✅ على {MIG.current_revision(eng)}")
    except (MIG.SchemaMismatch, MIG.SchemaNotCurrent, RuntimeError) as e:
        print(f"❌ {e}")
        return 2
    finally:
        eng.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
