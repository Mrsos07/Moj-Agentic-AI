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
                    description: str = "", scope: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    """ملف JSON (خادم أو عدة خوادم) أو حزمة .py/.zip. نجاح جزئي ممكن: كل خادم يُتحقق
    منه وحده، والفاشل يُعاد بسببه دون إسقاط الناجح."""
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
        try:
            cfg = X.save({**raw, **meta}, TS.registry())
        except Exception:
            X._remove_dir(X.package_dir(X.slug(raw["name"])))   # لا نترك حزمة لا تعمل
            raise
        return {"saved": [X.public_view(cfg, connected=True)], "errors": []}
    raise ValueError("الملف: إعداد .json أو حزمة خادم .py/.zip")


def _guard_builtin(slug: str) -> None:
    if X.is_builtin(X.slug(slug)):
        raise ValueError(f"'{slug}' خادم مدمج أصلي (كود النظام) — يُعدَّل في system/mcps/{slug}/ لا من اللوحة")


def update_mcp(slug: str, patch: Dict[str, Any], rediscover: bool = False) -> Dict[str, Any]:
    _guard_builtin(slug)
    cfg = X.update(slug, patch, TS.registry(), rediscover=rediscover)
    return X.public_view(cfg, connected=TS.registry().has(X.PREFIX + cfg["slug"]))


def delete_mcp(slug: str) -> bool:
    _guard_builtin(slug)
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
