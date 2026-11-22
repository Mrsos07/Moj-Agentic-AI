"""
mask_sqlite.py — نسخة مموّهة من data/app.db للاختبار وStaging (الأصل يُقرأ فقط، والناتج في backups/).

    python scripts/mask_sqlite.py --source data/app.db --out backups/masked/app.db [--encryption-key KEY]

ما يُموَّه (حتمياً: نفس المدخل ⇒ نفس الناتج):
* البريد ⇒ user<id>@example.test، وكل كلمات المرور ⇒ hash لـ ``masked-pass-123``.
* المعرّفات على المنصات (username/cookie_label/cookie_filename/account_username) ⇒ acct_<sha8> متّسقة
  بين الجداول.
* النصوص (الرسائل، الذكريات، العناوين، الأخطاء) ⇒ نص بالطول نفسه وفئات الأحرف نفسها (عربي ⇒ عربي،
  لاتيني ⇒ لاتيني، رقم ⇒ رقم؛ المسافات والترقيم والإيموجي تبقى) — اختبارات الطول والترميز تبقى حقيقية.
* JSON النصي ⇒ تُموَّه القيم النصية فقط؛ المفاتيح والأرقام والتواريخ والمعرّفات البرمجية تبقى (الكود يحلّلها).
* الحقول المشفّرة ⇒ سرّ وهمي مشفّر بمفتاح الاختبار (``--encryption-key``، وإلا ENCRYPTION_KEY) بالمخطّطين
  المستخدمين في الكود: ``auth.security`` (Fernet بالمفتاح نفسه) و``FernetVault`` (sha256 للمفتاح).
* توكنات webhook وchat_id وروابطها ⇒ قيم وهمية. جداول ``td_*`` (أخبار عامة من Tavily) تبقى.
في النهاية يُتحقق أن لا بريد ولا معرّف أصلي باقٍ في أي عمود نصي، وإلا فشل.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

AR = "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"
_KEEP = (re.compile(r"^\d{4}-\d{2}-\d{2}([T ][\d:.]+)?([+-]\d{2}:?\d{2}|Z)?$"),  # تاريخ ISO
         re.compile(r"^[0-9a-f-]{8,64}$"),                                     # uuid/hex
         re.compile(r"^[a-z][a-z0-9_.:-]{0,40}$"),                             # معرّف برمجي/حالة
         re.compile(r"^-?\d+(\.\d+)?$"))
_URL = re.compile(r"^(https?://[^/\s]+)(.*)$", re.S)


def _rng(*parts) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big"))


def mask_text(value: str, *seed) -> str:
    if not value:
        return value
    m = _URL.match(value)
    if m:
        return m.group(1) + mask_text(m.group(2), *seed, "url")
    r = _rng(*seed)
    out = []
    for ch in value:
        if "\u0621" <= ch <= "\u064a":
            out.append(r.choice(AR))
        elif "a" <= ch <= "z":
            out.append(chr(97 + r.randrange(26)))
        elif "A" <= ch <= "Z":
            out.append(chr(65 + r.randrange(26)))
        elif ch.isdigit():
            out.append(str(r.randrange(10)))
        else:
            out.append(ch)
    return "".join(out)


def mask_json_leaves(obj, *seed):
    if isinstance(obj, dict):
        return {k: mask_json_leaves(v, *seed, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_json_leaves(v, *seed, i) for i, v in enumerate(obj)]
    if isinstance(obj, str) and not any(p.match(obj) for p in _KEEP):
        return mask_text(obj, *seed)
    return obj


def mask_json_text(value, *seed):
    if value is None or value == "":
        return value
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return mask_text(value, *seed)
    if not isinstance(data, (dict, list)):
        return mask_text(value, *seed) if isinstance(data, str) else value
    return json.dumps(mask_json_leaves(data, *seed), ensure_ascii=False)


def handle(orig) -> str:
    return orig if not orig else "acct_" + hashlib.sha256(str(orig).encode()).hexdigest()[:8]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "data" / "app.db"))
    ap.add_argument("--out", default=str(ROOT / "backups" / "masked" / "app.db"))
    ap.add_argument("--encryption-key", default=os.environ.get("ENCRYPTION_KEY"))
    args = ap.parse_args(argv)
    from cryptography.fernet import Fernet
    import bcrypt

    out = Path(args.out)
    if out.resolve() == Path(args.source).resolve():
        ap.error("--out يساوي --source")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    s = sqlite3.connect(f"file:{Path(args.source).as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(str(out))
    s.backup(d)
    s.close()

    key = args.encryption_key or Fernet.generate_key().decode()
    cipher = Fernet(key.encode())                                                      # auth.security
    vault = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))    # FernetVault
    pw = bcrypt.hashpw(b"masked-pass-123", bcrypt.gensalt()).decode()

    con = d
    con.row_factory = sqlite3.Row
    originals = set()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def rows(table, pk="id"):
        return con.execute(f'SELECT * FROM "{table}" ORDER BY "{pk}"').fetchall() if table in tables else []

    def upd(table, pk, pkv, **vals):
        sets = ", ".join(f'"{k}"=?' for k in vals)
        con.execute(f'UPDATE "{table}" SET {sets} WHERE "{pk}"=?', (*vals.values(), pkv))

    def t(table, r, col):
        return mask_text(r[col], table, r["id"], col) if r[col] else r[col]

    def j(table, r, col, pk="id"):
        return mask_json_text(r[col], table, r[pk], col)

    for r in rows("users"):
        originals.add(r["email"])
        upd("users", "id", r["id"], email=f"user{r['id']}@example.test", password_hash=pw,
            name=t("users", r, "name"), profile_picture="/static/uploads/masked.png" if r["profile_picture"] else None)
    for r in rows("x_accounts"):
        originals.add(r["username"])
        upd("x_accounts", "id", r["id"], username=handle(r["username"]),
            encrypted_credentials=cipher.encrypt(f"masked-credentials-{r['id']}".encode()).decode()
            if r["encrypted_credentials"] else None)
    for r in rows("social_accounts"):
        originals.update({r["username"], r["display_name"]})
        upd("social_accounts", "id", r["id"], username=handle(r["username"]),
            display_name=t("social_accounts", r, "display_name"), account_label=t("social_accounts", r, "account_label"),
            cookie_filename=(handle(r["username"]) + ".json") if r["cookie_filename"] else None,
            error_message=t("social_accounts", r, "error_message"), extra_metadata=j("social_accounts", r, "extra_metadata"))
    for r in rows("conversations"):
        upd("conversations", "id", r["id"], title=t("conversations", r, "title"))
    for r in rows("messages"):
        upd("messages", "id", r["id"], content=t("messages", r, "content") or "", extra_data=j("messages", r, "extra_data"))
    for r in rows("schedule_events"):
        upd("schedule_events", "id", r["id"], username=handle(r["username"]), content=t("schedule_events", r, "content") or "",
            tweet_url=t("schedule_events", r, "tweet_url"), error_message=t("schedule_events", r, "error_message"))
    for r in rows("telegram_integrations"):
        upd("telegram_integrations", "id", r["id"],
            encrypted_bot_token=cipher.encrypt(f"000000:masked-bot-{r['id']}".encode()).decode(),
            chat_id=str(100000 + r["id"]), bot_username=f"masked_bot_{r['id']}",
            bot_display_name=t("telegram_integrations", r, "bot_display_name"),
            webhook_token=secrets.token_hex(24), webhook_url=f"https://example.test/api/telegram/webhook/{r['id']}",
            last_error=t("telegram_integrations", r, "last_error"))
    for r in rows("user_llm_settings"):
        keys = json.loads(r["keys_json"] or "{}") or {}
        upd("user_llm_settings", "id", r["id"],
            keys_json=json.dumps({p: vault.encrypt(f"sk-masked-{p}-{r['id']}".encode()).decode() for p in keys}),
            status_json=j("user_llm_settings", r, "status_json"))
    for r in rows("ai_employees"):
        originals.update({r["account_username"], r["cookie_label"]})
        upd("ai_employees", "id", r["id"], account_username=handle(r["account_username"]),
            cookie_label=handle(r["cookie_label"]),
            **{c: j("ai_employees", r, c) for c in ("persona_json", "goals_json", "knowledge_json",
                                                   "business_rules_json", "runtime_state_json", "scheduler_json")})
    for table in ("agent_memories", "employee_memories"):
        for r in rows(table):
            key = r["key"]
            if key and not re.match(r"^conv:\d+$", key):
                key = mask_text(key, table, r["id"], "key")
            upd(table, "id", r["id"], content=t(table, r, "content") or "", key=key,
                extra_metadata=j(table, r, "extra_metadata"))
    for r in rows("execution_jobs"):
        upd("execution_jobs", "id", r["id"], event_payload=j("execution_jobs", r, "event_payload"),
            error_message=t("execution_jobs", r, "error_message"))
    for r in rows("execution_logs"):
        upd("execution_logs", "id", r["id"], decision=j("execution_logs", r, "decision"), outcome=j("execution_logs", r, "outcome"))
    for r in rows("task_checkpoints"):
        upd("task_checkpoints", "id", r["id"], title=t("task_checkpoints", r, "title"),
            request=j("task_checkpoints", r, "request"), state=j("task_checkpoints", r, "state"),
            error=t("task_checkpoints", r, "error"))
    for r in rows("realtime_outbox"):
        upd("realtime_outbox", "id", r["id"], payload=j("realtime_outbox", r, "payload"))
    con.commit()

    # المعرّفات الأصلية داخل نصوص أخرى (JSON، session_id مثل employee:<handle>) ⇒ البديل نفسه في كل مكان
    originals = {o for o in originals if o and len(o) >= 4}
    subs = [(re.compile(re.escape(o), re.I), handle(o) if "@" not in o else "user@example.test")
            for o in sorted(originals, key=len, reverse=True)]
    for table in sorted(tables):
        if table.startswith(("td_", "sqlite_")):
            continue
        info = con.execute(f'PRAGMA table_info("{table}")').fetchall()
        pk = next((c[1] for c in info if c[5]), "rowid")
        for col in (c[1] for c in info):
            for r in con.execute(f'SELECT "{pk}", "{col}" FROM "{table}"').fetchall():
                v = r[1]
                if not isinstance(v, str):
                    continue
                new = v
                for rx, rep in subs:
                    new = rx.sub(rep, new)
                if new != v:
                    con.execute(f'UPDATE "{table}" SET "{col}"=? WHERE "{pk}"=?', (new, r[0]))
    con.commit()

    # لا أثر للأصل: كل بريد ومعرّف أصلي غائب عن كل عمود نصي (عدا td_* العامة)
    leaks = []
    for table in sorted(tables):
        if table.startswith(("td_", "sqlite_")):
            continue
        cols = [c[1] for c in con.execute(f'PRAGMA table_info("{table}")')]
        for col in cols:
            for o in originals:
                n = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE instr(lower(CAST("{col}" AS TEXT)), lower(?)) > 0',
                                (o,)).fetchone()[0]
                if n:
                    leaks.append(f"{table}.{col}")
    con.execute("VACUUM")
    con.close()
    if leaks:
        print(f"❌ معرّفات أصلية باقية في: {sorted(set(leaks))}")
        return 1
    print(f"✅ نسخة مموّهة: {out}  (لا بريد/معرّف أصلي باقٍ؛ كلمة المرور masked-pass-123)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
