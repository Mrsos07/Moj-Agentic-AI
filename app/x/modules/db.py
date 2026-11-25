import os
from typing import Any, Dict, List, Optional

from app.db.stores import transaction


def connect():
    return transaction('xsuite')


def init_db():
    from app.db.database import ensure_legacy_archived
    ensure_legacy_archived()
    with connect() as db:
        db.execute('SELECT 1 FROM scheduled_posts LIMIT 0')


def now_iso() -> str:
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def upsert_cookie(label: str, filename: str) -> int:
    with connect() as con:
        con.execute(
            "INSERT INTO cookies(label,filename,created_at) VALUES(%s,%s,%s) "
            "ON CONFLICT(label) DO UPDATE SET filename=excluded.filename",
            (label, filename, now_iso()),
        )
        row = con.execute("SELECT id FROM cookies WHERE label=%s", (label,)).fetchone()
        return int(row['id'])


def _cookies_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies')


def _sync_cookie_files() -> None:
    """سجّل في القاعدة أي ملف كوكيز موجود على القرص بلا صف.

    مصدر الحقيقة الفعلي هو الملفات في ``app/x/cookies/``: مسار تسجيل الدخول
    (loginx) ينسخ الملف ثم يسجّله في القاعدة، وقد رأينا حالة وصل فيها الملف
    ولم يصل الصف — فصار كل نشر يرجع «cookie not found» رغم أن الحساب مسجّل
    دخوله فعلاً (liketoday5، 2026-08-05). المزامنة هنا تشفي هذا الانقسام
    تلقائياً بدل الاعتماد على نجاح خطوتين منفصلتين دائماً.
    """
    try:
        files = [f for f in os.listdir(_cookies_dir()) if f.endswith('.json')]
    except OSError:
        return
    with connect() as con:
        known = {r['filename'] for r in con.execute("SELECT filename FROM cookies")}
    for filename in files:
        if filename not in known:
            upsert_cookie(filename[:-5], filename)


def list_cookies() -> List[Dict[str, Any]]:
    _sync_cookie_files()
    with connect() as con:
        rows = con.execute("SELECT * FROM cookies ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_cookie_by_label(label: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM cookies WHERE label=%s", (label,)).fetchone()
        if r:
            return dict(r)
    # فولباك للملفات: الملف موجود والصف مفقود ⇒ سجّله وأعد المحاولة
    label = (label or "").strip()
    filename = f"{label}.json"
    if label and os.path.isfile(os.path.join(_cookies_dir(), filename)):
        upsert_cookie(label, filename)
        with connect() as con:
            r = con.execute("SELECT * FROM cookies WHERE label=%s", (label,)).fetchone()
            return dict(r) if r else None
    return None


def get_cookie_by_id(cookie_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM cookies WHERE id=%s", (cookie_id,)).fetchone()
        return dict(r) if r else None


def delete_cookie(cookie_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM cookies WHERE id=%s", (cookie_id,)).fetchone()
        if not r:
            return None
        con.execute("DELETE FROM cookies WHERE id=%s", (cookie_id,))
        return dict(r)


def log_operation(action: str, cookie_label: Optional[str], status: str, message: str, meta_json: str = "") -> int:
    import json
    # Fix encoding issue for Arabic text
    if meta_json and isinstance(meta_json, dict):
        meta_json = json.dumps(meta_json, ensure_ascii=False)
    with connect() as con:
        row = con.execute(
            "INSERT INTO operations(action,cookie_label,status,message,meta_json,created_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
            (action, cookie_label, status, message, meta_json, now_iso()),
        ).fetchone()
        return int(row['id'])


def get_operation(op_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM operations WHERE id=%s", (op_id,)).fetchone()
        return dict(r) if r else None


def list_operations(limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT * FROM operations ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
        return [dict(r) for r in rows]


def stats() -> Dict[str, int]:
    with connect() as con:
        cookies = con.execute("SELECT COUNT(*) c FROM cookies").fetchone()["c"]
        posts = con.execute("SELECT COUNT(*) c FROM operations WHERE action='post' AND status='success'").fetchone()["c"]
        profiles = con.execute("SELECT COUNT(*) c FROM operations WHERE action='profile' AND status='success'").fetchone()["c"]
        logins = con.execute("SELECT COUNT(*) c FROM operations WHERE action='login' AND status='success'").fetchone()["c"]
        tweets = con.execute("SELECT COUNT(*) c FROM tweets").fetchone()["c"]
    return {"cookies": int(cookies), "posts": int(posts), "profiles": int(profiles), "logins": int(logins), "tweets": int(tweets)}


def save_tweet(cookie_label: str, tweet_url: str, tweet_text: str = "") -> int:
    with connect() as con:
        row = con.execute(
            "INSERT INTO tweets(cookie_label, tweet_url, tweet_text, created_at) VALUES(%s,%s,%s,%s) RETURNING id",
            (cookie_label, tweet_url, tweet_text, now_iso()),
        ).fetchone()
        return int(row['id'])


def list_tweets(limit: int = 100, cookie_label: Optional[str] = None) -> List[Dict[str, Any]]:
    with connect() as con:
        if cookie_label:
            rows = con.execute(
                "SELECT * FROM tweets WHERE cookie_label=%s ORDER BY id DESC LIMIT %s",
                (cookie_label, limit)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM tweets ORDER BY id DESC LIMIT %s", (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_tweet(tweet_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM tweets WHERE id=%s", (tweet_id,)).fetchone()
        return dict(r) if r else None


def delete_tweet_from_db(tweet_id: int) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM tweets WHERE id=%s", (tweet_id,)).fetchone()
        if not r:
            return None
        con.execute("DELETE FROM tweets WHERE id=%s", (tweet_id,))
        return dict(r)


# ───────────────────────── Scheduled Posts ─────────────────────────
def insert_scheduled_post(
    event_id: str,
    cookie_label: str,
    content: str,
    run_at: str,
    media_url: Optional[str] = None,
    callback_url: Optional[str] = None,
    callback_payload: Optional[str] = None,
    max_attempts: int = 3,
) -> int:
    """يدرج تغريدة مجدولة جديدة. run_at بصيغة ISO UTC (YYYY-MM-DD HH:MM:SS)."""
    with connect() as con:
        row = con.execute(
            "INSERT INTO scheduled_posts("
            "event_id, cookie_label, content, media_url, run_at, status, attempts, max_attempts, "
            "callback_url, callback_payload, created_at, updated_at"
            ") VALUES(%s,%s,%s,%s,%s,'SCHEDULED',0,%s,%s,%s,%s,%s) RETURNING id",
            (event_id, cookie_label, content, media_url, run_at, max_attempts,
             callback_url, callback_payload, now_iso(), now_iso()),
        ).fetchone()
        return int(row['id'])


def get_scheduled_post(event_id: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM scheduled_posts WHERE event_id=%s", (event_id,)).fetchone()
        return dict(r) if r else None


def list_scheduled_posts(status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as con:
        if status:
            rows = con.execute(
                "SELECT * FROM scheduled_posts WHERE status=%s ORDER BY run_at DESC LIMIT %s",
                (status, limit)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM scheduled_posts ORDER BY run_at DESC LIMIT %s", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_due_scheduled_posts(now_iso_str: str, batch_size: int = 10) -> List[Dict[str, Any]]:
    """يرجع التغريدات المُستحقة للنشر (SCHEDULED + run_at <= now)."""
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM scheduled_posts WHERE status='SCHEDULED' AND run_at <= %s "
            "ORDER BY run_at ASC LIMIT %s",
            (now_iso_str, batch_size)
        ).fetchall()
        return [dict(r) for r in rows]


def update_scheduled_post_status(
    event_id: str,
    status: str,
    tweet_url: Optional[str] = None,
    error_message: Optional[str] = None,
    increment_attempts: bool = False,
    mark_published: bool = False,
    callback_status: Optional[str] = None,
) -> bool:
    with connect() as con:
        sets = ["status=%s", "updated_at=%s"]
        params: list = [status, now_iso()]
        if tweet_url is not None:
            sets.append("tweet_url=%s"); params.append(tweet_url)
        if error_message is not None:
            sets.append("error_message=%s"); params.append(error_message)
        if increment_attempts:
            sets.append("attempts=attempts+1")
        if mark_published:
            sets.append("published_at=%s"); params.append(now_iso())
        if callback_status is not None:
            sets.append("callback_status=%s"); params.append(callback_status)
        params.append(event_id)
        cur = con.execute(
            f"UPDATE scheduled_posts SET {', '.join(sets)} WHERE event_id=%s",
            params
        )
        return cur.rowcount > 0


def delete_scheduled_post(event_id: str) -> Optional[Dict[str, Any]]:
    with connect() as con:
        r = con.execute("SELECT * FROM scheduled_posts WHERE event_id=%s", (event_id,)).fetchone()
        if not r:
            return None
        con.execute("DELETE FROM scheduled_posts WHERE event_id=%s", (event_id,))
        return dict(r)
