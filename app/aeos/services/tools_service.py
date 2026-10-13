"""
إدارة أدوات الموظفين: البحث، خوادم MCP الخارجية، وربطها بكل موظف.

طبقة واحدة تستهلكها واجهة البطاقة (API) والمنسّق العام (الشات) معاً — حتى لا
تختلف القواعد بين المسارين (مثلاً: من يُسمح له باستدعاء أداة، وما يُعرض من أسرار).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

#: خوادم مدمجة يقرّر النظام وجودها ولا يفصلها المالك (فصلها يعطّل الموظف)
CORE_SERVERS = ("x", "playwright_x", "content", "memory")
#: خوادم مدمجة اختيارية يمكن للمالك ربطها/فصلها
OPTIONAL_BUILTIN = {
    "search": "بحث ويب (Tavily)",
    "news": "أخبار قوقل حسب التخصص",
    "browser": "قراءة صفحات الويب",
    "image": "توليد الصور",
}


def _platform():
    from app.aeos.bootstrap import get_platform

    return get_platform()


def registry():
    from app.aeos.mcp.registry import MCPRegistry

    return _platform().container.resolve(MCPRegistry)


def overview(cfg=None) -> Dict[str, Any]:
    from app.aeos.mcp.servers import external_mcp as X
    from app.aeos.mcp.servers.core_mcp import search_provider

    provider = search_provider()[0]
    attached = set(cfg.mcp_servers or []) if cfg else set()
    rules = (cfg.business_rules or {}) if cfg else {}
    builtin = []
    for name, label in OPTIONAL_BUILTIN.items():
        on = name in attached
        if name == "search":
            on = bool(provider) and ("search" in attached or rules.get("web_search", True))
        builtin.append({"name": name, "label": label, "attached": on,
                        "available": bool(provider) if name == "search" else True})
    external = []
    for c in employee_servers():
        view = X.public_view(c)
        # خوادم النظام متاحة تلقائياً لكل موظف ما لم يفصلها مالكه صراحةً
        view["attached"] = cfg is not None and _system_server_on(cfg, view["name"])
        view["private"] = False
        external.append(view)
    # أدوات هذا الموظف الخاصة (يضيفها مالكه، لا يراها غيره)
    private = []
    if cfg is not None:
        from app.aeos.mcp import employee_mcp as EM

        for c in EM.list_for(cfg.id):
            view = EM.public_view(c, connected=registry().has(X.PREFIX + c["slug"]))
            view["attached"] = view["name"] in attached
            private.append(view)
    return {"search": {"provider": provider or None, "configured": bool(provider)},
            "builtin": builtin, "external": external, "private": private}


def add_private(cfg, raw: Dict[str, Any], added_by: str = "") -> Dict[str, Any]:
    """أداة MCP فرعية لهذا الموظف فقط: تُكتشف أدواتها، تُسجَّل، وتُربط به فوراً."""
    from app.aeos.mcp import employee_mcp as EM
    from app.aeos.mcp.servers.external_mcp import PREFIX

    saved = EM.save(cfg.id, raw, registry(), added_by=added_by)
    name = PREFIX + saved["slug"]
    if name not in (cfg.mcp_servers or []):
        cfg.mcp_servers = [*(cfg.mcp_servers or []), name]
        _platform().repository.save(cfg)
    return EM.public_view(saved, connected=True)


def remove_private(cfg, name: str) -> bool:
    from app.aeos.mcp import employee_mcp as EM
    from app.aeos.mcp.servers.external_mcp import PREFIX

    cur = EM.get(cfg.id, name)
    if cur is None or not EM.delete(cfg.id, name, registry()):
        return False
    full = PREFIX + cur["slug"]
    if full in (cfg.mcp_servers or []):
        cfg.mcp_servers = [s for s in cfg.mcp_servers if s != full]
        _platform().repository.save(cfg)
    return True


def _system_server_on(cfg, name: str) -> bool:
    """خادم نظام (نطاق الموظفين) مفعّل لهذا الموظف؟ نعم افتراضياً — الفصل اختيار صريح."""
    return name not in set((cfg.business_rules or {}).get("tools_opt_out") or [])


def _private_names(cfg) -> set:
    from app.aeos.mcp import employee_mcp as EM
    from app.aeos.mcp.servers.external_mcp import PREFIX

    return {PREFIX + c["slug"] for c in EM.list_for(cfg.id)} if cfg is not None else set()


def employee_servers() -> List[Dict[str, Any]]:
    """خوادم النظام التي يجوز ربطها بالموظفين: مفعّلة ونطاقها يشمل الموظفين.
    المالك **يربط** منها فقط؛ إضافة خادم للنظام للمطوّر وحده (لوحة التحكم)."""
    from app.aeos.mcp.servers import external_mcp as X

    return [c for c in X.list_configs(enabled_only=True)
            if (c.get("scope") or {}).get("employees", True)]


def set_attached(cfg, server: str, attach: bool) -> Dict[str, Any]:
    """اربط/افصل خادماً عن موظف. يعيد الحالة الجديدة أو يرفع ValueError."""
    from app.aeos.mcp.servers import external_mcp as X

    if server in CORE_SERVERS:
        raise ValueError(f"'{server}' أساسي ولا يُفصل")
    # المتاح للربط: خوادم النظام المسموحة للموظفين + أدوات **هذا** الموظف الخاصة فقط
    known = set(OPTIONAL_BUILTIN) | {X.PREFIX + c["slug"] for c in employee_servers()} | _private_names(cfg)
    # الفصل مسموح دائماً (ولو عُطّل الخادم لاحقاً)، الربط من المتاح للموظفين فقط
    if server not in known and (attach or server not in (cfg.mcp_servers or [])):
        raise ValueError(f"خادم غير متاح للموظفين: {server}. المتاح: {', '.join(sorted(known))}")
    if server == "search" and attach:
        from app.aeos.mcp.servers.core_mcp import search_provider

        if not search_provider()[0]:
            raise ValueError("البحث غير مُهيّأ: أضف TAVILY_API_KEY إلى .env ثم أعد تشغيل الخادم")
    servers = [s for s in (cfg.mcp_servers or []) if s != server]
    if attach:
        servers.append(server)
    cfg.mcp_servers = servers
    if server == "search":  # الموظفون القدامى بلا «search» محفوظ: العلَم هو المرجع
        cfg.business_rules = {**(cfg.business_rules or {}), "web_search": bool(attach)}
    if server in {X.PREFIX + c["slug"] for c in employee_servers()}:
        # خوادم النظام مفعّلة تلقائياً: الفصل يُسجَّل «إلغاء اشتراك» لهذا الموظف، والربط يلغيه
        opt_out = [s for s in (cfg.business_rules or {}).get("tools_opt_out") or [] if s != server]
        if not attach:
            opt_out.append(server)
        cfg.business_rules = {**(cfg.business_rules or {}), "tools_opt_out": opt_out}
    _platform().repository.save(cfg)
    return {"employee": cfg.account_username, "server": server, "attached": attach}


def add_external(raw: Dict[str, Any]) -> Dict[str, Any]:
    from app.aeos.mcp.servers import external_mcp as X

    return X.public_view(X.save(raw, registry()), connected=True)


def remove_external(name: str) -> bool:
    from app.aeos.mcp.servers import external_mcp as X

    ok = X.delete(name, registry())
    if ok:  # افصله عن كل الموظفين حتى لا يبقى اسم يتيم في إعداداتهم
        full = name if name.startswith(X.PREFIX) else X.PREFIX + X.slug(name)
        repo = _platform().repository
        for c in repo.list_all():
            if full in (c.mcp_servers or []):
                c.mcp_servers = [s for s in c.mcp_servers if s != full]
                repo.save(c)
    return ok


def call_tool(cfg, server: str, tool: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """استدعاء يطلبه **المالك** صراحةً لأداة مربوطة بالموظف (لا يُستدعى من محتوى X)."""
    from app.aeos.mcp.base import MCPRequest

    system_names = {"ext_" + c["slug"] for c in employee_servers()}
    on = (server in (cfg.mcp_servers or [])
          or (server == "search" and (cfg.business_rules or {}).get("web_search", True))
          or (server in system_names and _system_server_on(cfg, server)))
    if not on:
        raise ValueError(f"الخادم '{server}' غير مربوط بالموظف @{cfg.account_username}")
    if not (server == "search" or server.startswith("ext_")):
        raise ValueError("الاستدعاء المباشر متاح للبحث والخوادم الخارجية فقط")
    if server.startswith("ext_") and server not in (system_names | _private_names(cfg)):
        raise ValueError(f"الخادم '{server}' معطّل أو غير متاح لهذا الموظف")
    reg = registry()
    if not reg.has(server):
        raise ValueError(f"الخادم '{server}' غير مسجّل")
    full = tool if server == "search" or tool.startswith(server + ".") else f"{server}.{tool}"
    r = reg.call(MCPRequest(employee_id=cfg.id, tool=full, params=dict(arguments or {}),
                            context={"server": server}))
    if not r.success:
        raise RuntimeError(r.error)
    return json.loads(json.dumps(r.data, ensure_ascii=False, default=str))


def web_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    from app.aeos.mcp.base import MCPRequest

    r = registry().call(MCPRequest(employee_id="", tool="web_search",
                                   params={"query": query, "limit": limit},
                                   context={"server": "search"}))
    if not r.success:
        raise RuntimeError(r.error)
    return r.data.get("results") or []
