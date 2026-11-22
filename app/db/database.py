"""
اتصال قاعدة البيانات — SQLite (الافتراضي، كما كان) أو PostgreSQL عبر ``DATABASE_URL``.

* ``DATABASE_URL`` فارغ ⇒ ``sqlite:///data/app.db`` حرفياً كما قبل.
* ``DATABASE_URL=postgresql+psycopg2://…`` ⇒ PostgreSQL بمجمّع اتصالات وحدود زمنية، وجلسة بتوقيت UTC
  (التواريخ تُخزَّن naive UTC؛ قيمة واعية بالمنطقة تُحوَّل حسب منطقة الجلسة — فـUTC شرط صحة).
* لا رجوع صامت إلى SQLite أبداً: إن ضُبط ``DATABASE_URL`` وتعذّر الاتصال يفشل الإقلاع (كتابات على
  قاعدتين = بيانات منقسمة).
* المخطط يُبنى ويُرقّى بـAlembic فقط (``app/db/migrations.py``)، لا بـ ``create_all`` ولا ``ALTER``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import declarative_base, sessionmaker

logger = logging.getLogger("app.db")

# SQLite database path
DB_PATH = Path(__file__).parent.parent.parent / "data" / "app.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SQLITE_URL = f"sqlite:///{DB_PATH}"


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(str(env.get(name, "")).strip() or default)
    except ValueError:
        raise ValueError(f"{name} يجب أن يكون رقماً صحيحاً") from None


def resolve_database_url(env: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if env is None else env
    raw = (env.get("DATABASE_URL") or "").strip()
    if not raw:
        return SQLITE_URL
    url = make_url(raw)
    if url.drivername in ("postgres", "postgresql"):
        url = url.set(drivername="postgresql+psycopg2")
    if url.get_backend_name() not in ("postgresql", "sqlite"):
        raise ValueError(f"DATABASE_URL غير مدعوم: {url.get_backend_name()} (المدعوم postgresql أو sqlite)")
    return url.render_as_string(hide_password=False)


def engine_options(url: str, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    env = os.environ if env is None else env
    if make_url(url).get_backend_name() == "sqlite":
        return {"connect_args": {"check_same_thread": False}}
    options = " ".join((
        "-c timezone=UTC",
        f"-c statement_timeout={_int(env, 'DB_STATEMENT_TIMEOUT_MS', 30000)}",
        f"-c lock_timeout={_int(env, 'DB_LOCK_TIMEOUT_MS', 10000)}",
        f"-c idle_in_transaction_session_timeout={_int(env, 'DB_IDLE_TX_TIMEOUT_MS', 60000)}",
    ))
    return {
        "pool_pre_ping": True,
        "pool_size": _int(env, "DB_POOL_SIZE", 10),
        "max_overflow": _int(env, "DB_MAX_OVERFLOW", 20),
        "pool_timeout": _int(env, "DB_POOL_TIMEOUT", 30),
        "pool_recycle": _int(env, "DB_POOL_RECYCLE", 1800),
        "connect_args": {"connect_timeout": _int(env, "DB_CONNECT_TIMEOUT", 10),
                         "application_name": env.get("DB_APPLICATION_NAME") or "moj-backend",
                         "options": options},
    }


def build_engine(url: Optional[str] = None, env: Optional[Mapping[str, str]] = None) -> Engine:
    url = url or resolve_database_url(env)
    return create_engine(url, **engine_options(url, env))


def safe_url(bind: Optional[Engine] = None) -> str:
    """الرابط بلا كلمة المرور — للسجلات وفحص الصحة."""
    return (bind or engine).url.render_as_string(hide_password=True)


def is_postgres(bind: Any = None) -> bool:
    bind = bind if bind is not None else engine
    if hasattr(bind, "get_bind"):
        bind = bind.get_bind()
    return bind.dialect.name == "postgresql"


SQLALCHEMY_DATABASE_URL = resolve_database_url()

engine = build_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """ابنِ المخطط أو رقِّه بـAlembic (الاسم باقٍ للتوافق: main.py وcreate_user.py وinit_database.py).

    SQLite (``DB_AUTO_MIGRATE`` افتراضياً 1): تبنّي القاعدة القديمة إن لزم ثم ``upgrade head``.
    PostgreSQL (افتراضياً 0): تحقق فقط — مخطط ليس على head يوقف الإقلاع برسالة الأمر المطلوب.
    """
    from app.db import migrations
    from app.db import models  # noqa: F401 - تسجيل الجداول في Base.metadata
    import app.trend_detector.models  # noqa: F401

    info = migrations.ensure_schema(engine)
    logger.info("database %s at %s%s", safe_url(engine), info.get("revision"),
                " (migrated)" if info.get("changed") else "")
    print(f"Database schema at {info.get('revision')} ({engine.dialect.name})"
          + (f" — adopted legacy DB, backup: {info['backup']}" if info.get("adopted") else ""))
    return info
