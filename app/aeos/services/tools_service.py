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
    "search": "بحث ويب (Tavily/Brave/SerpAPI)",
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
    for c in X.list_configs():
        view = X.public_view(c)
        view["attached"] = view["name"] in attached
        external.append(view)
    return {"search": {"provider": provider or None, "configured": bool(provider)},
            "builtin": builtin, "external": external}


def set_attached(cfg, server: str, attach: bool) -> Dict[str, Any]:
    """اربط/افصل خادماً عن موظف. يعيد الحالة الجديدة أو يرفع ValueError."""
    from app.aeos.mcp.servers import external_mcp as X

    if server in CORE_SERVERS:
        raise ValueError(f"'{server}' أساسي ولا يُفصل")
    known = set(OPTIONAL_BUILTIN) | {X.PREFIX + c["slug"] for c in X.list_configs()}
    if server not in known:
        raise ValueError(f"خادم غير معروف: {server}. المتاح: {', '.join(sorted(known))}")
    if server == "search" and attach:
        from app.aeos.mcp.servers.core_mcp import search_provider

        if not search_provider()[0]:
            raise ValueError("البحث غير مُهيّأ: أضف TAVILY_API_KEY أو BRAVE_SEARCH_API_KEY "
                             "أو SERPAPI_KEY إلى .env ثم أعد تشغيل الخادم")
    servers = [s for s in (cfg.mcp_servers or []) if s != server]
    if attach:
        servers.append(server)
    cfg.mcp_servers = servers
    if server == "search":  # الموظفون القدامى بلا «search» محفوظ: العلَم هو المرجع
        cfg.business_rules = {**(cfg.business_rules or {}), "web_search": bool(attach)}
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

    if server not in (cfg.mcp_servers or []) and not (
            server == "search" and (cfg.business_rules or {}).get("web_search", True)):
        raise ValueError(f"الخادم '{server}' غير مربوط بالموظف @{cfg.account_username}")
    if not (server == "search" or server.startswith("ext_")):
        raise ValueError("الاستدعاء المباشر متاح للبحث والخوادم الخارجية فقط")
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
