"""Import one legacy service store into a migrated PostgreSQL database during maintenance."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from psycopg2 import sql
from psycopg2.extras import execute_values

from app.db import stores

TABLES = {
    "xsuite": ("cookies", "operations", "tweets", "scheduled_posts"),
    "timeline": ("x_timeline_tweets",),
    "agent_security": ("audit", "grants", "limits", "account_claims", "login_sessions", "deliveries"),
}


def fingerprint(rows):
    count = total = 0
    for row in rows:
        values = [({"bytes": bytes(v).hex()} if isinstance(v, (memoryview, bytes)) else v) for v in row]
        raw = json.dumps(values, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        total += int.from_bytes(hashlib.sha256(raw).digest(), "big")
        count += 1
    return count, f"{total % (1 << 256):064x}"


def batches(cursor):
    while rows := cursor.fetchmany(1000):
        yield rows


def migrate(source, schema, verify_only=False):
    from contextlib import closing
    source = Path(source).resolve(strict=True)
    report = {}
    sequences = []
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as legacy, stores.transaction(schema) as pg:
        legacy.execute("BEGIN")
        names = {r[0] for r in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        unknown = names - set(TABLES[schema]) - {"sqlite_sequence", "sqlite_stat1", "sqlite_stat4"}
        required = {"audit", "grants"} if schema == "agent_security" else set(TABLES[schema])
        if unknown or not required.issubset(names):
            raise ValueError("Unexpected source schema; verify the selected file and store")
        for table in TABLES[schema]:
            ident = sql.Identifier(table)
            pg.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(ident))
            pg.execute(sql.SQL("SELECT count(*) FROM {}").format(ident))
            existing = pg.fetchone()[0]
            if table not in names:
                if existing:
                    raise ValueError(f"Missing source for non-empty table {table}")
                continue
            columns = [r[1] for r in legacy.execute(f'PRAGMA table_info("{table}")')]
            mapped = ["minute" if table == "limits" and c == "window" else c for c in columns]
            pg.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                       (schema, table))
            if set(mapped) != {r[0] for r in pg}:
                raise ValueError(f"Unexpected columns for {table}")
            identifiers = sql.SQL(",").join(map(sql.Identifier, mapped))
            read = f'SELECT * FROM "{table}"'
            if not existing and not verify_only:
                insert = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(ident, identifiers)
                for rows in batches(legacy.execute(read)):
                    execute_values(pg, insert, rows, page_size=500)
            before = fingerprint(legacy.execute(read))
            pg.execute(sql.SQL("SELECT {} FROM {}").format(identifiers, ident))
            if before != fingerprint(pg):
                raise ValueError(f"Count or fingerprint mismatch for {table}; target retained")
            report[table] = {"count": before[0], "sha256_sum": before[1]}
            key = "seq" if table == "audit" else "id"
            serial = None
            if key in mapped:
                pg.execute("SELECT pg_get_serial_sequence(%s,%s)", (f"{schema}.{table}", key))
                serial = pg.fetchone()[0]
            if serial and not verify_only:
                floor = legacy.execute(f'SELECT coalesce(max("{key}"),0) FROM "{table}"').fetchone()[0]
                if "sqlite_sequence" in names:
                    seq = legacy.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
                    floor = max(floor, seq[0] if seq else 0)
                if floor:
                    sequences.append((serial, floor))
        if schema == "agent_security":
            from app.aeos.security import ledger
            ledger.verify_chain(pg)
        for serial, floor in sequences:
            # Never rewind an existing destination sequence on retry.
            pg.execute("SELECT pg_sequence_last_value(%s::regclass)", (serial,))
            previous = pg.fetchone()[0] or 0
            if previous < floor:
                pg.execute("SELECT setval(%s::regclass,%s,true)", (serial, floor))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("store", choices=TABLES)
    parser.add_argument("source", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.source, args.store, args.verify_only), indent=2))
    except Exception as exc:
        # A database exception can include the row and encrypted credentials.
        sys.exit(f"Import failed ({type(exc).__name__}); no rows committed")
