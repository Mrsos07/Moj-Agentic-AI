"""Disposable PostgreSQL databases for verification. Legacy imports use explicit SQLite snapshots."""
from __future__ import annotations

import atexit
import os
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from app.db import database as DB
from app.db import migrations as MIG

_created: list = []


def _pg_admin(url: str) -> Engine:
    return create_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")


def create_pg_database(base_url: str, prefix: str = "moj_test") -> str:
    """قاعدة PG فارغة جديدة؛ يعيد رابطها."""
    name = f"{prefix}_{uuid.uuid4().hex[:12]}"
    admin = _pg_admin(base_url)
    try:
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\' '
                           "LOCALE_PROVIDER builtin BUILTIN_LOCALE 'C.UTF-8'"))
    finally:
        admin.dispose()
    _created.append((base_url, name))
    return make_url(base_url).set(database=name).render_as_string(hide_password=False)


def drop_pg_database(base_url: str, name: str) -> None:
    admin = _pg_admin(base_url)
    try:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin.dispose()


@atexit.register
def _cleanup() -> None:
    for base, name in _created:
        try:
            drop_pg_database(base, name)
        except Exception:  # noqa: BLE001 - تنظيف أفضل جهد
            pass


def test_database_url() -> Optional[str]:
    url = (os.environ.get("MOJ_TEST_DATABASE_URL") or "").strip()
    if not url:
        return None
    prod = (os.environ.get("DATABASE_URL") or "").strip()
    if prod and make_url(prod).set(drivername="x") == make_url(url).set(drivername="x"):
        raise RuntimeError("MOJ_TEST_DATABASE_URL يساوي DATABASE_URL — الاختبارات لا تعمل على قاعدة حقيقية")
    return url


def temp_database(migrate: bool = True, sqlite_dir: Optional[Path] = None) -> Engine:
    base = test_database_url()
    if not base:
        raise RuntimeError("Set MOJ_TEST_DATABASE_URL to a disposable PostgreSQL server")
    eng = DB.build_engine(create_pg_database(base), env={"DB_APPLICATION_NAME": "moj-tests"})
    if migrate:
        MIG.upgrade(eng, "head")
    return eng


def bind_app(engine: Engine) -> Engine:
    """اربط محرّك التطبيق وجلساته بهذه القاعدة (كما كانت الاختبارات تفعل يدوياً)."""
    DB.engine = engine
    DB.SessionLocal.configure(bind=engine)
    return engine


def use_temp_database(sqlite_dir: Optional[Path] = None) -> Engine:
    return bind_app(temp_database(sqlite_dir=sqlite_dir))
