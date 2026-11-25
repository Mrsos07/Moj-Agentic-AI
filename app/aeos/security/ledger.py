"""Authenticated security decisions and single-use grants in PostgreSQL.

A transaction-level advisory lock serializes grant consumption and appends even across backend subprocesses.
The HMAC key and encryption key are derived outside the ledger. Verification
requires the deployment key. An external checkpoint is needed to detect truncation.
"""
from __future__ import annotations

import base64
from contextvars import ContextVar
import hashlib
import hmac
import json
import os
from app.db import stores
import time
import uuid

from cryptography.fernet import Fernet

TTL = 600
challenge = ContextVar("agent_pending_approval", default=None)


def _key(label):
    from app.auth.security import SECRET_KEY
    root = os.environ.get("AGENT_SECURITY_KEY") or SECRET_KEY
    return hmac.digest(root.encode(), b"moj/agent-security/v1/" + label, "sha256")


def _json(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def transaction():
    return stores.transaction("agent_security")


def audit(db, correlation, owner, action, decision, grant=None):
    previous = db.execute("SELECT tag FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    event = _json([int(time.time()), correlation, owner, action, decision, grant])
    tag = hmac.new(_key(b"audit"), ((previous[0] if previous else "") + event).encode(), hashlib.sha256).hexdigest()
    db.execute("INSERT INTO audit(event,tag) VALUES(%s,%s)", (event, tag))


def verify():
    with transaction() as db:
        return verify_chain(db)


def verify_chain(db):
    previous = ""
    for event, tag in db.execute("SELECT event,tag FROM audit ORDER BY seq"):
        expected = hmac.new(_key(b"audit"), (previous + event).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, tag):
            raise ValueError("security audit integrity failure")
        previous = tag
    return previous


def _signature(cid, owner, scope, expires):
    return hmac.new(_key(b"grant"), _json([cid, owner, scope, expires]).encode(), hashlib.sha256).hexdigest()


def decide(owner, agent, conversation, action, args, correlation, sensitive):
    scope = hmac.new(_key(b"scope"), _json([owner, agent, conversation, action, args]).encode(),
                     hashlib.sha256).hexdigest()
    now = int(time.time())
    pending = None
    with transaction() as db:
        if sensitive:
            db.execute("DELETE FROM grants WHERE expires<=%s", (now,))
            row = db.execute("SELECT id,state,expires,proof FROM grants WHERE owner=%s AND scope=%s",
                             (owner, scope)).fetchone()
            if row and row[1] == "approved" and hmac.compare_digest(row[3], _signature(row[0], owner, scope, row[2])):
                db.execute("DELETE FROM grants WHERE id=%s", (row[0],))
                audit(db, correlation, owner, action, "consume", row[0])
                if challenge.get() == row[0]:
                    challenge.set(None)
                return
            if row:
                pending = row[0]
            else:
                if db.execute("SELECT count(*) FROM grants WHERE owner=%s", (owner,)).fetchone()[0] >= 64:
                    raise PermissionError("عدد طلبات الموافقة المعلّقة تجاوز الحد")
                pending = uuid.uuid4().hex
                preview = Fernet(base64.urlsafe_b64encode(_key(b"preview"))).encrypt(
                    _json({"agent": agent, "conversation": conversation, "action": action, "arguments": args}).encode())
                db.execute("INSERT INTO grants VALUES(%s,%s,%s,%s,%s,%s,%s)",
                           (pending, owner, scope, now + TTL, "pending", preview, ""))
        audit(db, correlation, owner, action, "approval-required" if pending else "allow", pending)
    if pending:
        challenge.set(pending)
        raise PermissionError(f"تتطلب العملية موافقة المالك. أرسل في الشات: معاينة {pending}")


def view(owner, cid):
    with transaction() as db:
        row = db.execute("SELECT preview,expires,state,scope FROM grants WHERE owner=%s AND id=%s AND expires>%s",
                         (owner, cid, int(time.time()))).fetchone()
    if not row:
        raise LookupError("approval unavailable")
    cipher = Fernet(base64.urlsafe_b64encode(_key(b"preview")))
    preview = json.loads(cipher.decrypt(bytes(row[0])))
    expected = hmac.new(_key(b"scope"), _json([owner, preview["agent"], preview["conversation"],
                        preview["action"], preview["arguments"]]).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, row[3]):
        raise LookupError("invalid approval")
    return {**preview, "id": cid, "expires": row[1], "state": row[2], "digest": row[3]}


def approve(owner, cid, digest):
    with transaction() as db:
        row = db.execute("SELECT scope,expires FROM grants WHERE owner=%s AND id=%s AND expires>%s AND state='pending'",
                         (owner, cid, int(time.time()))).fetchone()
        if not row or not hmac.compare_digest(row[0], digest):
            raise LookupError("approval unavailable")
        db.execute("UPDATE grants SET state='approved',proof=%s WHERE id=%s", (_signature(cid, owner, *row), cid))
        audit(db, uuid.uuid4().hex, owner, "approval", "approved", cid)

def pending(owner):
    with transaction() as db:
        return [row[0] for row in db.execute(
            "SELECT id FROM grants WHERE owner=%s AND state='pending' AND expires>%s ORDER BY expires",
            (owner, int(time.time())))]

def fingerprint(data):
    return hmac.new(_key(b"parameter"), _json(data).encode(), hashlib.sha256).hexdigest()


def admit(owner, route, limit):
    minute = int(time.time()) // 60
    with transaction() as db:
        db.execute("DELETE FROM limits WHERE minute<%s", (minute,))
        db.execute("INSERT INTO limits VALUES(%s,%s,%s,1) ON CONFLICT(owner,route,minute) "
                   "DO UPDATE SET count=limits.count+1", (owner, route, minute))
        count, = db.execute("SELECT count FROM limits WHERE owner=%s AND route=%s AND minute=%s",
                            (owner, route, minute)).fetchone()
        if count == limit + 1:
            audit(db, uuid.uuid4().hex, owner, route, "rate-limit")
        return count <= limit

def reserve_accounts(owner, labels):
    now = int(time.time())
    with transaction() as db:
        db.execute("DELETE FROM account_claims WHERE expires<=%s", (now,))
        for label in labels:
            existing = db.execute("SELECT owner FROM account_claims WHERE label=%s", (label,)).fetchone()
            if existing and existing[0] != owner:
                raise PermissionError("الحساب قيد الربط")
            db.execute("INSERT INTO account_claims VALUES(%s,%s,%s) ON CONFLICT(label) DO UPDATE SET owner=excluded.owner,expires=excluded.expires", (label, owner, now + 3600))


def login_session(owner, sid, *, register=False):
    if not isinstance(sid, str) or not sid or len(sid) > 120 or not all(c.isascii() and
            (c.isalnum() or c in "_-") for c in sid):
        raise PermissionError("جلسة غير صالحة")
    with transaction() as db:
        db.execute("DELETE FROM login_sessions WHERE expires<=%s", (int(time.time()),))
        if register:
            db.execute("INSERT INTO login_sessions VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", (sid, owner, int(time.time()) + 86400))
        row = db.execute("SELECT owner FROM login_sessions WHERE sid=%s", (sid,)).fetchone()
        if not row or row[0] != owner:
            raise PermissionError("الجلسة غير متاحة")


def claim_delivery(channel, update_id):
    now = int(time.time())
    with transaction() as db:
        db.execute("DELETE FROM deliveries WHERE expires<=%s", (now,))
        return db.execute("INSERT INTO deliveries VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                          (channel, update_id, now + 172800)).rowcount == 1
