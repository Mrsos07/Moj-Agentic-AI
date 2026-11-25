"""
كتالوج خوادم MCP الموحّد: **كل** خادم في النظام مجلدٌ في ``system/mcps/<slug>/``.

نوعان في المجلد نفسه، والفرق تقني فقط (طريقة التشغيل)، لا في المكانة:
- ``kind: builtin`` — كود Python يعمل داخل العملية (``server.py`` + ``class`` في mcp.json).
- غير ذلك — خادم MCP مرفوع (إعداد stdio/HTTP أو حزمة) يعمل في عملية منفصلة عبر البروتوكول
  (``external_mcp.py``).

كلاهما «مدمج في النظام»: يُحمَّل عند الإقلاع، ويظهر في قائمة واحدة، ويتصل به منسّق كل مالك
حسب ``scope.coordinator``، والموظفون حسب ``scope.employees``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.system_loader import mcp_module

logger = logging.getLogger("aeos.mcp.catalog")

MANIFEST = "mcp.json"


def _dir() -> Path:
    from .servers import external_mcp as X

    return X._CATALOG_DIR


_TRUSTED_CLASSES = {
    "browser": "BrowserMCP", "content": "ContentMCP", "image": "ImageMCP", "memory": "MemoryMCP",
    "news": "NewsMCP", "pixabay": "PixabayMCP", "playwright_x": "PlaywrightXMCP",
    "search": "SearchMCP", "x": "XPlatformMCP",
}


def builtin_manifests(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    out = []
    for path in sorted((directory or _dir()).glob(f"*/{MANIFEST}")):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            logger.exception("unreadable MCP manifest %s", path)
            continue
        if (isinstance(m, dict) and m.get("kind") == "builtin" and path.parent.name in _TRUSTED_CLASSES
                and m.get("class") == _TRUSTED_CLASSES[path.parent.name] and not path.parent.is_symlink()):
            m["slug"] = path.parent.name
            out.append(m)
    out.sort(key=lambda m: (int(m.get("order", 100)), m["slug"]))
    return out


def builtin_manifest(slug: str) -> Optional[Dict[str, Any]]:
    return next((m for m in builtin_manifests() if m["slug"] == slug), None)


def register_builtins(registry, deps: Dict[str, Any]) -> List[str]:
    """حمّل الخوادم المدمجة من المجلد وسجّلها. فشل خادم أساسي (core) يُرفع — النظام بدونه
    لا يعمل، والأفضل أن يفشل الإقلاع بوضوح من أن يعمل الموظف بلا X."""
    loaded = []
    for m in builtin_manifests():
        if not m.get("enabled", True) and not m.get("core"):
            continue
        try:
            mod = mcp_module(m["slug"], m.get("entry") or "server.py")
            cls = getattr(mod, m["class"])
            server = cls(*[deps[k] for k in m.get("inject") or []])
            registry.register(server)
            loaded.append(server.name)
        except Exception:
            logger.exception("built-in MCP server %s failed to load", m["slug"])
            if m.get("core"):
                raise
    return loaded


def coordinator_builtin_tools() -> Dict[str, Dict[str, Any]]:
    """أدوات الخوادم المدمجة المتاحة للمنسّق مباشرة: الاسم الأصلي ← {server, tool, spec, owner_param}."""
    out: Dict[str, Dict[str, Any]] = {}
    for m in builtin_manifests():
        if not m.get("enabled", True) or not (m.get("scope") or {}).get("coordinator"):
            continue
        for spec in m.get("coordinator_tools") or []:
            out[spec["name"]] = {"server": m["slug"], "tool": spec["name"], "spec": spec,
                                 "owner_param": m.get("owner_param") or "", "label": m.get("label") or m["slug"]}
    return out
