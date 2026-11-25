"""
كتالوج قدرات النظام — طبقة واحدة تستهلكها لوحة التحكم (API) وتحترمها كل المسارات.

- **خوادم MCP العامة** (``system/mcps``): تُضاف برفع ملف إعداد JSON أو حزمة خادم
  (.py/.zip)، يتصل النظام بها فعلاً ويكتشف أدواتها قبل الحفظ، ثم تُسجَّل في سجل الخوادم
  فوراً: نطاق «المنسّق» ⇒ أدواتها في منسّق كل مستخدم، ونطاق «الموظفين» ⇒ يربطها المالك
  بموظفيه.
- **مهارات النظام** (``system/skills``): SKILL.md تظهر في منسّق كل مستخدم.

الصلاحيات تُفرض في طبقة الـAPI (app.auth.roles): العرض للمسؤول، التعديل للمطوّر.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.aeos.mcp.servers import external_mcp as X
from app.aeos.skills import system_skills as SS

from . import tools_service as TS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────── MCP ───────────────────────────
def mcp_overview() -> Dict[str, Any]:
    """قائمة واحدة لكل خوادم المجلد الرئيسي system/mcps: المدمجة الأصلية والمرفوعة سواء."""
    from app.aeos.mcp.catalog import builtin_manifests

    reg = TS.registry()
    usage = _employee_usage()
    servers = []
    for m in builtin_manifests():
        live = next((s for s in reg.list_servers() if s["name"] == m["slug"]), None)
        servers.append({
            "name": m["slug"], "slug": m["slug"], "label": m.get("label") or m["slug"],
            "description": m.get("description", ""), "note": m.get("note", ""),
            "kind": "builtin", "source": "builtin", "transport": "in-process",
            "entry": m.get("entry", "server.py"), "core": bool(m.get("core")),
            "enabled": bool(live), "connected": bool(live and live["healthy"]),
            "scope": m.get("scope") or {}, "tools": [{"name": t, "description": ""} for t in (live or {}).get("tools", [])],
            "coordinator_tools": [t["name"] for t in m.get("coordinator_tools") or []],
            "employees_attached": usage.get(m["slug"], 0), "env_keys": [], "header_keys": [],
            "allowed_tools": None, "editable": False,
        })
    for c in X.list_configs():
        view = X.public_view(c, connected=reg.has(X.PREFIX + c["slug"]))
        view.update(kind="uploaded", employees_attached=usage.get(view["name"], 0), editable=True,
                    coordinator_tools=X.ExternalMCPServer(c).allowed
                    if (c.get("scope") or {}).get("coordinator", True) else [])
        servers.append(view)
    return {"servers": servers, "directory": "system/mcps",
            # توافق مع الواجهة السابقة
            "system": [s for s in servers if s["kind"] == "uploaded"],
            "builtin": [{"name": s["name"], "tools": len(s["tools"]), "healthy": s["connected"],
                         "core": s["core"]} for s in servers if s["kind"] == "builtin"]}


def _employee_usage() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for cfg in TS._platform().repository.list_all():
        for s in cfg.mcp_servers or []:
            out[s] = out.get(s, 0) + 1
    return out


def add_from_upload(filename: str, data: bytes, *, added_by: str, name: str = "",
                    description: str = "", scope: Optional[Dict[str, bool]] = None,
                    env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """ملف JSON (خادم أو عدة خوادم) أو حزمة .py/.zip. نجاح جزئي ممكن: كل خادم يُتحقق
    منه وحده، والفاشل يُعاد بسببه دون إسقاط الناجح. ``env``: مفاتيح الحزمة (متغيّرات بيئة)."""
    lower = (filename or "").lower()
    meta = {"added_by": added_by, "added_at": _now()}
    if scope:
        meta["scope"] = scope
    if description:
        meta["description"] = description
    if lower.endswith(".json"):
        raws = X.parse_config_file(data)
        if name and len(raws) == 1:
            raws[0]["name"] = name
        saved, errors = [], []
        for raw in raws:
            try:
                cfg = X.save({**raw, **meta, "source": "config"}, TS.registry())
                saved.append(X.public_view(cfg, connected=True))
            except Exception as exc:  # noqa: BLE001
                errors.append({"name": raw.get("name") or "?", "error": str(exc)[:300]})
        return {"saved": saved, "errors": errors}
    if lower.endswith((".py", ".zip")):
        raw = X.install_package(filename, data, name=name)
        raw["env"] = {**(raw.get("env") or {}), **(env or {})}
        try:
            cfg = X.save({**raw, **meta}, TS.registry())
        except Exception:
            X._remove_dir(X.package_dir(X.slug(raw["name"])))   # لا نترك حزمة لا تعمل
            raise
        return {"saved": [X.public_view(cfg, connected=True)], "errors": []}
    raise ValueError("الملف: إعداد .json أو حزمة خادم .py/.zip")


AUTH_MODES = ("none", "bearer", "header", "query", "env")


def raw_from_form(form: Dict[str, Any]) -> Dict[str, Any]:
    """نموذج الإضافة السريعة ← إعداد خادم. المفتاح يوضع حيث يتوقعه الخادم:
    bearer: ترويسة Authorization · header: ترويسة باسم تختاره (X-API-Key…) ·
    query: معامل في الرابط (يُحفظ كسرّ لا في الرابط) · env: متغيّر بيئة لخادم stdio."""
    transport = str(form.get("transport") or "http").lower()
    raw: Dict[str, Any] = {
        "name": form.get("name"), "label": form.get("label") or form.get("name"),
        "description": form.get("description") or "", "transport": transport,
        "allowed_tools": form.get("allowed_tools"), "research_tool": form.get("research_tool") or "",
        "scope": form.get("scope") or {"coordinator": True, "employees": True},
        "headers": dict(form.get("headers") or {}), "env": dict(form.get("env") or {}),
    }
    if transport == "http":
        raw["url"] = form.get("url")
        raw["http_mode"] = form.get("http_mode") or ""
    else:
        raw["command"] = form.get("command")
        raw["args"] = [a for a in (form.get("args") or []) if str(a).strip()]
    auth = form.get("auth") or {}
    mode = str(auth.get("mode") or "none").lower()
    key = str(auth.get("key") or "").strip()
    pname = str(auth.get("name") or "").strip()
    if mode not in AUTH_MODES:
        raise ValueError(f"طريقة المفتاح: {', '.join(AUTH_MODES)}")
    if mode != "none" and not key:
        raise ValueError("أدخل المفتاح أو اختر «بلا مفتاح»")
    if mode == "bearer":
        raw["headers"]["Authorization"] = key if key.lower().startswith("bearer ") else f"Bearer {key}"
    elif mode in ("header", "query", "env"):
        if not pname:
            raise ValueError("اكتب اسم الترويسة/المعامل/المتغيّر الذي يُرسل فيه المفتاح")
        if mode == "env" and transport != "stdio":
            raise ValueError("متغيّر البيئة لخوادم stdio — لخادم HTTP استخدم ترويسة أو معاملاً")
        target = {"header": "headers", "query": "query_secrets", "env": "env"}[mode]
        raw.setdefault(target, {})[pname] = key
    return raw


#: أماكن المفتاح التي تجرّبها الاكتشاف التلقائي (بالترتيب الأشيع لدى المزوّدات)
_AUTH_CANDIDATES = [
    {"mode": "bearer", "name": ""},
    {"mode": "header", "name": "Authorization"},      # المفتاح بلا «Bearer» (TrendsMCP وغيره)
    {"mode": "header", "name": "X-API-Key"},
    {"mode": "query", "name": "apiKey"},
    {"mode": "query", "name": "api_key"},
    {"mode": "query", "name": "key"},
]
_MAX_ATTEMPTS = 12
_TIME_BUDGET = 70        # ثانية — الواجهة تنتظر 90


def _url_candidates(url: str) -> List[Dict[str, str]]:
    """الرابط كما أُدخل، ثم /mcp على نفس المضيف (واجهة REST أو صفحة الموقع)، ثم SSE."""
    from urllib.parse import urlsplit

    p = urlsplit(url)
    root = f"{p.scheme}://{p.netloc}"
    path = p.path.rstrip("/")
    out = [{"url": url, "http_mode": "sse" if path.endswith("/sse") else "streamable"}]
    if not path.endswith(("/mcp", "/sse")):
        out += [{"url": f"{root}/mcp", "http_mode": "streamable"}]
        if path:
            out += [{"url": f"{url.rstrip('/')}/mcp", "http_mode": "streamable"}]
    if not path.endswith("/sse"):
        out += [{"url": url, "http_mode": "sse"}]
        out += [{"url": f"{root}/sse", "http_mode": "sse"}] if path != "/sse" else []
    seen, uniq = set(), []
    for c in out:
        k = (c["url"].rstrip("/"), c["http_mode"])
        if k not in seen:
            seen.add(k)
            uniq.append(c)
    return uniq


def _bad_url(exc: Exception) -> bool:
    """الخطأ يدل على رابط/نقل خاطئ (لا جدوى من تجريب مفاتيح أخرى عليه)."""
    status = getattr(exc, "status", 0)
    text = str(exc)
    return status in (404, 405, 406, 415) or any(s in text for s in (
        "ليس JSON-RPC", "صفحة ويب", "نص غير JSON", "لا يتكلم", "SSE", "endpoint", "Connection", "timed out"))


def autodetect(form: Dict[str, Any]) -> Dict[str, Any]:
    """جرّب الإعداد كما هو، فإن فشل جرّب البدائل المعقولة (مسار /mcp، نقل SSE، مكان المفتاح)
    وأعد أول إعداد يعمل فعلاً بأدوات. لا يُحفظ شيء هنا.
    يعيد {cfg, form, adjusted: [وصف كل تعديل]} أو يرفع أوضح خطأ صادفه."""
    if str(form.get("transport") or "http") != "http":
        return {"cfg": X.discover(X.normalize_config(raw_from_form(form))), "form": form, "adjusted": []}
    auth = form.get("auth") or {}
    key = str(auth.get("key") or "").strip()
    given_auth = {"mode": str(auth.get("mode") or "none"), "name": str(auth.get("name") or "")}
    auths = [given_auth] + ([a for a in _AUTH_CANDIDATES if a != given_auth] if key else [])
    import time

    first_err: Optional[Exception] = None
    oauth_err: Optional[Exception] = None
    attempts, deadline = 0, time.monotonic() + _TIME_BUDGET
    for uc in _url_candidates(str(form.get("url") or "")):
        endpoint_ok = False      # رد MCP حقيقي (401/0 أداة): الرابط صحيح، لا نبدّله بغيره
        for i, a in enumerate(auths):
            if attempts >= _MAX_ATTEMPTS or (attempts and time.monotonic() > deadline):
                break
            attempts += 1
            trial = {**form, **uc, "auth": {**a, "key": key if a["mode"] != "none" else ""}}
            try:
                cfg = X.discover(X.normalize_config(raw_from_form(trial)))
            except Exception as exc:  # noqa: BLE001 - نجمع الأسباب ونجرّب التالي
                first_err = first_err or exc
                if "OAuth" in str(exc):
                    oauth_err = exc
                if _bad_url(exc):
                    break                       # الرابط/النقل نفسه خاطئ — انتقل للرابط التالي
                endpoint_ok = True
                continue
            adjusted = []
            if uc["url"].rstrip("/") != str(form.get("url") or "").rstrip("/"):
                adjusted.append(f"الرابط ← {uc['url']}")
            if uc["http_mode"] == "sse" and form.get("http_mode") != "sse":
                adjusted.append("نوع النقل ← SSE (الخادم يستخدم النقل القديم)")
            if a != given_auth:
                where = {"bearer": "ترويسة Authorization بصيغة Bearer",
                         "header": f"ترويسة {a['name']}", "query": f"معامل الرابط {a['name']}"}[a["mode"]]
                adjusted.append(f"طريقة إرسال المفتاح ← {where}")
            return {"cfg": cfg, "form": trial, "adjusted": adjusted}
        if endpoint_ok:
            break
    raise (oauth_err or first_err or ValueError("تعذّر الاتصال"))


def test_form(form: Dict[str, Any]) -> Dict[str, Any]:
    """اختبار الاتصال قبل الحفظ: يتصل ويكتشف الأدوات ولا يحفظ شيئاً.
    إن احتاج الإعداد تعديلاً (رابط/نقل/مكان المفتاح) يُعاد الإعداد الصحيح لتعتمده الواجهة."""
    res = autodetect(form)
    cfg, fixed = res["cfg"], res["form"]
    return {"server_info": cfg.get("server_info") or {},
            "tools": [{"name": t["name"], "description": (t.get("description") or "")[:200]}
                      for t in cfg["tools"]],
            "adjusted": res["adjusted"],
            "form": {"url": fixed.get("url", ""), "http_mode": fixed.get("http_mode") or cfg.get("http_mode", ""),
                     "auth": {"mode": fixed["auth"]["mode"], "name": fixed["auth"].get("name", "")}}}


def add_from_form(form: Dict[str, Any], *, added_by: str) -> Dict[str, Any]:
    form = autodetect(form)["form"]     # «تثبيت» مباشرة بلا اختبار يحصل على نفس التصحيح التلقائي
    raw = {**raw_from_form(form), "added_by": added_by, "added_at": _now(), "source": "config"}
    return X.public_view(X.save(raw, TS.registry()), connected=True)


#: قالب خادم MCP داخلي تبنيه بنفسك (stdio، مكتبة Python القياسية فقط) — ارفعه كحزمة .py
SERVER_TEMPLATE = '''"""
خادم MCP داخلي لنظام موج — قالب (stdio، بلا مكتبات خارجية).

أضف أدواتك في TOOLS ودالة لكل أداة في HANDLERS، ثم ارفع الملف من لوحة التحكم ← خوادم MCP
← «رفع خادم داخلي». المفاتيح التي تضيفها عند الرفع تصلك كمتغيّرات بيئة (os.environ).
لا تطبع شيئاً على stdout غير رسائل البروتوكول (استخدم sys.stderr للتشخيص).
"""
import json
import os
import sys

# UTF-8 للبروتوكول دائماً (على ويندوز الافتراضي cp1252 فينهار الخادم عند أول نص عربي)
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

TOOLS = [
    {"name": "hello", "description": "مثال: يرحّب بالاسم",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
]


def hello(args):
    return f"أهلاً {args.get('name', '')}"


HANDLERS = {"hello": hello}


def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
            "serverInfo": {"name": "my-internal-server", "version": "1.0"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        p = msg.get("params") or {}
        fn = HANDLERS.get(p.get("name"))
        try:
            text, err = (fn(p.get("arguments") or {}), False) if fn else (f"unknown tool {p.get('name')}", True)
        except Exception as exc:  # noqa: BLE001
            text, err = f"error: {exc}", True
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": str(text)}], "isError": err}})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "result": {}})
'''


def _guard_builtin(slug: str) -> None:
    if X.is_builtin(X.slug(slug)):
        raise ValueError(f"'{slug}' خادم مدمج أصلي (كود النظام) — يُعدَّل في system/mcps/{slug}/ لا من اللوحة")


def update_mcp(slug: str, patch: Dict[str, Any], rediscover: bool = False) -> Dict[str, Any]:
    _guard_builtin(slug)
    cfg = X.update(slug, patch, TS.registry(), rediscover=rediscover)
    return X.public_view(cfg, connected=TS.registry().has(X.PREFIX + cfg["slug"]))


def delete_mcp(slug: str) -> bool:
    _guard_builtin(slug)
    from app.aeos.security import gateway, ledger
    cfg = X.get(slug)
    if cfg is None:
        return False
    gateway.api_decision("admin.mcp.delete", "", {"server": slug, "revision": ledger.fingerprint(cfg)})
    return TS.remove_external(slug)   # يحذف ويفصله عن كل الموظفين


# ─────────────────────────── skills ───────────────────────────
def skills_overview() -> List[Dict[str, Any]]:
    return SS.list_skills()


# ─────────────────────────── overview ───────────────────────────
def overview(db) -> Dict[str, Any]:
    from collections import Counter

    from app.auth.roles import role_of
    from app.db.models import AIEmployee, User

    users = db.query(User).all()
    roles = Counter(role_of(u) for u in users)
    mcps = X.list_configs()
    skills = SS.list_skills()
    return {
        "users": {"total": len(users), "active": sum(1 for u in users if u.is_active),
                  "by_role": {r: roles.get(r, 0) for r in ("user", "admin", "developer")}},
        "employees": db.query(AIEmployee).count(),
        "mcps": {"system": len(mcps), "enabled": sum(1 for c in mcps if c.get("enabled", True)),
                 "tools": sum(len(X.ExternalMCPServer(c).allowed) for c in mcps if c.get("enabled", True)),
                 "builtin": len([s for s in TS.registry().list_servers()
                                 if not s["name"].startswith(X.PREFIX)])},
        "skills": {"total": len(skills), "enabled": sum(1 for s in skills if s.get("enabled")),
                   "builtin": sum(1 for s in skills if s.get("builtin")),
                   "coordinator": sum(1 for s in skills if s.get("kind", "coordinator") == "coordinator"),
                   "employee": sum(1 for s in skills if s.get("kind") == "employee")},
    }
