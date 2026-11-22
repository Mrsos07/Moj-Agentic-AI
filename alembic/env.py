"""Alembic environment — كل النماذج، والرابط من DATABASE_URL (أو اتصال يمرّره app.db.migrations)."""
from logging.config import fileConfig
import sys
from pathlib import Path

from sqlalchemy import text

from alembic import context

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env")

# Import database configuration and ALL models (autogenerate يرى كل الجداول)
from app.db.database import Base, build_engine, resolve_database_url  # noqa: E402
import app.db.models  # noqa: E402,F401
import app.trend_detector.models  # noqa: E402,F401

config = context.config

# اتصال مُمرَّر برمجياً (ensure_schema/الاختبارات) ⇒ لا نلمس إعدادات السجلات في التطبيق
_passed = config.attributes.get("connection")
if _passed is None and config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

#: ترقيتان متزامنتان (migrate + إقلاع يدوي) لا تتسابقان على المخطط
_PG_MIGRATION_LOCK = 0x4D4F4A00  # "MOJ\0"


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )


def _run(connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PG_MIGRATION_LOCK})
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=resolve_database_url().startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    if _passed is not None:
        _run(_passed)
        return
    engine = build_engine(resolve_database_url())
    try:
        from app.db.migrations import needs_adoption

        if needs_adoption(engine):
            # alembic/migrate.py مباشرة على قاعدة create_all غير مختومة: 6e5dcbbbdac8 سيحاول إنشاء users الموجود
            raise RuntimeError("قاعدة SQLite قديمة غير مختومة — رقِّها بالتبنّي الآمن (نسخة احتياطية + مقارنة "
                               "مخطط): python scripts/db_migrate.py upgrade")
        with engine.begin() as connection:
            _run(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
