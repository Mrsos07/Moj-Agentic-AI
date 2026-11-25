"""Transactional PostgreSQL cursors for the isolated service schemas."""
from contextlib import contextmanager
from psycopg2.extras import DictCursor
from app.db import database


class _Cursor(DictCursor):
    def execute(self, statement, parameters=None):
        super().execute(statement, parameters)
        return self


@contextmanager
def transaction(schema):
    if schema not in {"agent_security", "xsuite", "timeline"} or not database.is_postgres():
        raise RuntimeError("A PostgreSQL service schema is required")
    connection = database.engine.raw_connection()
    try:
        with connection.cursor(cursor_factory=_Cursor) as cursor:
            cursor.execute(f'SET LOCAL search_path TO "{schema}", pg_catalog')
            if schema == "agent_security":
                # Serialize chain appends and one-use grant consumption across processes.
                cursor.execute("SELECT pg_advisory_xact_lock(1297041922)")
            yield cursor
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
