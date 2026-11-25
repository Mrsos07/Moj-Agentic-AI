"""Exercise legacy service imports and their production callers on disposable PostgreSQL."""
from contextlib import closing
from pathlib import Path
import hashlib
import sqlite3
import tempfile

from app.aeos.security import ledger
from app.db import database
from app.db.testing import use_temp_database
from app.services.x_timeline_service import get_saved_tweets
from app.x.modules import db as xsuite
from scripts.import_service_store import migrate


def fixture(path, statements):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(statements)


def verify():
    engine = use_temp_database()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        x = root / "xsuite.db"
        fixture(x, """
            CREATE TABLE cookies(id INTEGER PRIMARY KEY AUTOINCREMENT,label TEXT,filename TEXT,created_at TEXT);
            CREATE TABLE operations(id INTEGER PRIMARY KEY,action,cookie_label,status,message,meta_json,created_at);
            CREATE TABLE tweets(id INTEGER PRIMARY KEY,cookie_label,tweet_url,tweet_text,created_at);
            CREATE TABLE scheduled_posts(id INTEGER PRIMARY KEY,event_id,cookie_label,content,media_url,run_at,status,
                attempts,max_attempts,tweet_url,error_message,callback_url,callback_payload,callback_status,published_at,created_at,updated_at);
            INSERT INTO cookies VALUES(5,'fixture','fixture.json','2026-01-01');
            INSERT INTO cookies VALUES(15,'removed','removed.json','2026-01-01');
            DELETE FROM cookies WHERE id=15;
        """)
        before = hashlib.sha256(x.read_bytes()).digest()
        assert migrate(x, "xsuite")["cookies"]["count"] == 1
        assert migrate(x, "xsuite", verify_only=True) == migrate(x, "xsuite")
        assert hashlib.sha256(x.read_bytes()).digest() == before
        assert xsuite.get_cookie_by_id(5)["label"] == "fixture"
        assert xsuite.upsert_cookie("new_fixture","new.json") == 16
        assert xsuite.log_operation("read","fixture","ok","fixture") > 0
        assert xsuite.save_tweet("fixture","https://example.com","fixture") > 0
        xsuite.insert_scheduled_post("event", "fixture", "test", "2099-01-01")
        assert xsuite.update_scheduled_post_status("event","CANCELLED")
        assert xsuite.get_scheduled_post("event")["status"] == "CANCELLED"
        try:
            migrate(x, "xsuite")
        except ValueError:
            assert xsuite.get_cookie_by_id(16)["label"] == "new_fixture"
        else:
            raise AssertionError("mismatched import was accepted")
        timeline = root / "timeline.db"
        fixture(timeline, """
            CREATE TABLE x_timeline_tweets(id INTEGER PRIMARY KEY,account_username,user_id,tweet_id,full_text,
                author_screen_name,author_name,created_at,favorite_count,retweet_count,reply_count,quote_count,
                views_count,tweet_url,lang,is_retweet,is_reply,is_quote,media_urls,raw_data,fetched_at);
            INSERT INTO x_timeline_tweets(id,account_username,user_id,tweet_id,full_text,media_urls)
                VALUES(42,'fixture',1,'987','رسالة','[]');
        """)
        assert migrate(timeline, "timeline")["x_timeline_tweets"]["count"] == 1
        assert get_saved_tweets("fixture",user_id=1)[0]["full_text"] == "رسالة"
        assert not get_saved_tweets("fixture",user_id=2)

        security = root / "ledger.db"
        fixture(security, """
            CREATE TABLE audit(seq INTEGER PRIMARY KEY,event TEXT,tag TEXT);
            CREATE TABLE grants(id TEXT PRIMARY KEY,owner INTEGER,scope TEXT,expires INTEGER,state TEXT,preview BLOB,proof TEXT);
            CREATE TABLE limits(owner INTEGER,route TEXT,window INTEGER,count INTEGER);
            INSERT INTO limits VALUES(1,'fixture',12,2);
            INSERT INTO audit VALUES(1,'fixture','wrong');
        """)
        try:
            migrate(security,"agent_security")
        except ValueError:
            with ledger.transaction() as pg:
                assert pg.execute("SELECT count(*) FROM audit").fetchone()[0] == 0
        else:
            raise AssertionError("invalid audit chain accepted")
        import hmac
        tag = hmac.digest(ledger._key(b"audit"), b"fixture", "sha256").hex()
        with closing(sqlite3.connect(security)) as db, db:
            db.execute("UPDATE audit SET tag=?", (tag,))
        assert migrate(security, "agent_security")["limits"]["count"] == 1
        assert ledger.verify() == tag
        with ledger.transaction() as pg:
            ledger.audit(pg,"fixture",1,"verify","allow")
        assert ledger.verify() != tag
        from unittest.mock import patch
        with patch.object(database, "legacy_paths", return_value=(x,)):
            for start in (database.init_db, xsuite.init_db):
                try:
                    start()
                except RuntimeError as exc:
                    assert "Legacy SQLite" in str(exc)
                else:
                    raise AssertionError("startup accepted an unarchived legacy store")
        assert database.is_postgres()
    engine.dispose()


if __name__ == "__main__":
    verify()
    print("PostgreSQL service imports, sequence floors, idempotence, audit chain and runtime readers passed.")
