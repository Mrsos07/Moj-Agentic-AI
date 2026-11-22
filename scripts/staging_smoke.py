"""
staging_smoke.py — فحص حي لبيئة مماثلة للإنتاج عبر البروكسي الكامل (caddy ← nginx ← backend).

    python scripts/staging_smoke.py --base http://127.0.0.1:18080 --email user1@example.test --password …
    python scripts/staging_smoke.py … --since 62     ← إعادة ما فات بعد رقم معيّن (بعد انقطاع)

يطبع JSON: الصحة، الدخول، الموظفون، المهام، وعبر WebSocket: auth_ok ورسائل الصندوق الصادر المُعادة
بعد ``since`` ولقطة المهام برقم آخر رسالة. لا يرسل رسالة شات (لا نموذج ولا X).
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx
from websocket import create_connection


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18080")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--since", type=int, default=0)
    args = ap.parse_args(argv)
    out = {}
    with httpx.Client(base_url=args.base, timeout=30) as c:
        out["health"] = c.get("/health").status_code
        r = c.post("/api/auth/login", json={"email": args.email, "password": args.password})
        out["login"] = r.status_code
        if r.status_code != 200:
            print(json.dumps(out, ensure_ascii=False))
            return 1
        tok = r.json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        emps = c.get("/api/aeos/employees", headers=h)
        out["employees"] = len(emps.json().get("employees", [])) if emps.status_code == 200 else emps.status_code
        out["verify"] = c.get("/api/auth/verify", headers=h).status_code
    ws_url = args.base.replace("http", "ws", 1) + "/ws/chat"
    ws = create_connection(ws_url, origin=args.base, timeout=20)
    try:
        ws.send(json.dumps({"type": "auth", "token": tok}))
        first = json.loads(ws.recv())
        out["ws_auth"] = first.get("type")
        ws.send(json.dumps({"type": "resume", "since": args.since}))
        replayed = []
        while True:
            m = json.loads(ws.recv())
            if m.get("type") == "tasks_snapshot":
                out["snapshot_seq"] = m.get("seq")
                out["tasks"] = sorted({t["status"] for t in m.get("tasks", [])})
                break
            if m.get("replayed"):
                replayed.append(m.get("seq"))
        out["replayed"] = len(replayed)
        out["replayed_seqs"] = replayed if len(replayed) <= 6 else replayed[:3] + ["…"] + replayed[-3:]
        out["replayed_ordered"] = replayed == sorted(replayed)
    finally:
        ws.close()
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["login"] == 200 and out["ws_auth"] == "auth_ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
