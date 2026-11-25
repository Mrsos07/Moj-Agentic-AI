"""
خوادم MCP **خاصة بموظف واحد** — يضيفها مالكه، ولا يراها ولا يستخدمها غيره.

مستويان للأدوات في النظام:
- **أدوات النظام** (``system/mcps``): يثبّتها المطوّر من لوحة التحكم، مدمجة لكل مالك.
- **أدوات الموظف** (هنا): أداة فرعية مخصّصة يضيفها المالك لموظف بعينه.

حدود الأمان (مالك عادي ≠ مطوّر):
- **HTTP فقط** (Streamable HTTP). لا stdio ولا حزم: كلاهما يشغّل أمراً/كوداً على هذا الجهاز،
  وهذا للمطوّر وحده (أدوات النظام).
- **لا عناوين داخلية**: يُرفض أي رابط يشير لهذا الجهاز أو الشبكة الخاصة (127.0.0.1، 10.x،
  192.168.x، 169.254.x…) — وإلا وصل أي مستخدم إلى خوادم النظام الداخلية (X Suite،
  loginx) أو خدمات شبكته عبر خادمنا (SSRF). يُفحص العنوان بعد تحليل DNS، وعند كل اتصال.
- **عزل**: اسم الخادم في السجل يحمل معرّف الموظف (``ext_e<id>_<slug>``)، ولا يُربط إلا به.
- الأسرار (headers) في ``data/employee_mcps/<id>.json`` خارج git، وتُحذف مع الموظف.
"""
from __future__ import annotations

import hashlib
from app.aeos.security import sealed
import ipaddress
import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .servers import external_mcp as X

logger = logging.getLogger("aeos.mcp.employee")

_DIR = Path(os.getenv("EMPLOYEE_MCP_DIR") or X._ROOT / "data" / "employee_mcps")
_MAX_PER_EMPLOYEE = 10
_lock = threading.Lock()


def _tag(employee_id: str) -> str:
    return "e" + hashlib.sha256(str(employee_id).encode()).hexdigest()[:24]


def registry_slug(employee_id: str, local: str) -> str:
    return f"{_tag(employee_id)}_{X.slug(local)}"


def is_private(name: str) -> bool:
    return name.startswith(X.PREFIX + "e") and "_" in name[len(X.PREFIX):]


def belongs_to(name: str, employee_id: str) -> bool:
    return any(name == X.PREFIX + cfg["slug"] for cfg in _read(employee_id))


# ─────────────────────────── SSRF guard ───────────────────────────
def check_public_url(url: str) -> None:
    """ارفض أي رابط لا يشير لعنوان عام على الإنترنت (بعد تحليل DNS)."""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("الرابط يجب أن يبدأ بـ http:// أو https://")
    if p.username or p.password:
        raise ValueError("لا تضع اسم مستخدم/كلمة مرور في الرابط — استخدم الترويسات")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"تعذّر تحليل العنوان {p.hostname}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise ValueError(f"العنوان {p.hostname} يشير لشبكة داخلية ({ip}) — أدوات الموظف "
                             "للخوادم العامة فقط. الخوادم الداخلية يثبّتها المطوّر كأدوات للنظام")


class EmployeeMCPServer(X.ExternalMCPServer):
    """خادم موظف: نفس عميل MCP، مع إعادة فحص العنوان قبل كل اتصال (تغيّر DNS)."""

    def call(self, request):
        if not request.employee_id or self.cfg.get("employee_id") != request.employee_id:
            return X.MCPResponse.fail("private MCP owner mismatch")
        try:
            check_public_url(self.cfg.get("url", ""))
        except ValueError as exc:
            return X.MCPResponse.fail(str(exc))
        return super().call(request)


# ─────────────────────────── store ───────────────────────────
def _path(employee_id: str) -> Path:
    return _DIR / f"{_tag(employee_id)}.json"


def _read(employee_id: str) -> List[Dict[str, Any]]:
    path = _path(employee_id)
    if not path.exists():
        legacy = "e" + "".join(ch for ch in str(employee_id) if ch.isalnum())[:10].lower()
        path = _DIR / f"{legacy}.json"
    return [c for c in sealed.read(path, []) if c.get("employee_id") == employee_id]


def _write(employee_id: str, items: List[Dict[str, Any]]) -> None:
    path = _path(employee_id)
    X._atomic_write(path, sealed.envelope(items))
    # Old short IDs could collide. Remove only records matching the complete owner ID.
    legacy_tag = "e" + "".join(ch for ch in str(employee_id) if ch.isalnum())[:10].lower()
    legacy = _DIR / (legacy_tag + ".json")
    if legacy != path and legacy.exists():
        old = sealed.read(legacy, [])
        kept = [cfg for cfg in old if cfg.get("employee_id") != employee_id]
        if len(kept) != len(old):
            X._atomic_write(legacy, sealed.envelope(kept))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def list_for(employee_id: str) -> List[Dict[str, Any]]:
    return _read(employee_id)


def get(employee_id: str, name: str) -> Optional[Dict[str, Any]]:
    return next((c for c in _read(employee_id)
                 if X.PREFIX + c["slug"] == name or c.get("local_slug") == X.slug(name)), None)


def save(employee_id: str, raw: Dict[str, Any], registry=None, added_by: str = "") -> Dict[str, Any]:
    """أضف/حدّث خادماً خاصاً بموظف: HTTP عام فقط، اتصال واكتشاف الأدوات قبل الحفظ."""
    from datetime import datetime, timezone

    transport = str(raw.get("transport") or raw.get("type") or ("http" if raw.get("url") else "")).lower()
    if transport not in ("http", "streamable-http", "streamable_http", "sse"):
        raise ValueError("أدوات الموظف: خوادم HTTP فقط. خوادم stdio والحزم يثبّتها المطوّر كأدوات للنظام")
    http_mode = "sse" if transport == "sse" else str(raw.get("http_mode") or "")
    local = X.slug(raw.get("name") or raw.get("label") or "")
    if not local:
        raise ValueError("اسم الخادم مطلوب")
    check_public_url(str(raw.get("url") or ""))
    cfg = X.normalize_config({
        "name": registry_slug(employee_id, local), "label": raw.get("label") or raw.get("name") or local,
        "transport": "http", "url": raw.get("url"), "http_mode": http_mode, "headers": raw.get("headers") or {},
        "allowed_tools": raw.get("allowed_tools"), "research_tool": raw.get("research_tool") or "",
        "research_arg": raw.get("research_arg") or "", "description": raw.get("description") or "",
        "scope": {"coordinator": False, "employees": True},
    })
    cfg["private"] = True
    cfg = X.discover(cfg)
    cfg.update(local_slug=local, employee_id=str(employee_id), private=True, added_by=added_by,
               added_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    with _lock:
        items = [c for c in _read(employee_id) if c["slug"] != cfg["slug"]]
        if len(items) >= _MAX_PER_EMPLOYEE:
            raise ValueError(f"حدّ أدوات الموظف الخاصة {_MAX_PER_EMPLOYEE} خوادم")
        _write(employee_id, items + [cfg])
    if registry is not None:
        registry.unregister(X.PREFIX + cfg["slug"])
        registry.register(EmployeeMCPServer(cfg))
    return cfg


def parse_upload(data: bytes) -> List[Dict[str, Any]]:
    """ملف إعداد JSON (صيغة mcpServers أو خادم واحد) — نفس محلّل أدوات النظام."""
    return X.parse_config_file(data)


def delete(employee_id: str, name: str, registry=None) -> bool:
    cur = get(employee_id, name)
    if cur is None:
        return False
    with _lock:
        _write(employee_id, [c for c in _read(employee_id) if c["slug"] != cur["slug"]])
    if registry is not None:
        registry.unregister(X.PREFIX + cur["slug"])
    return True


def purge(employee_id: str, registry=None) -> int:
    """عند حذف الموظف: أزل خوادمه الخاصة وأسرارها."""
    items = _read(employee_id)
    if registry is not None:
        for c in items:
            registry.unregister(X.PREFIX + c["slug"])
    _write(employee_id, [])
    return len(items)


def register_all(registry) -> int:
    n = 0
    for path in sorted(_DIR.glob("*.json")):
        for cfg in sealed.read(path, []) or []:
            if path != _path(cfg["employee_id"]) and _path(cfg["employee_id"]).exists():
                continue
            try:
                registry.register(EmployeeMCPServer(cfg))
                n += 1
            except Exception:  # noqa: BLE001
                logger.exception("employee MCP %s failed to register", cfg.get("slug"))
    return n


def public_view(cfg: Dict[str, Any], connected: Optional[bool] = None) -> Dict[str, Any]:
    view = X.public_view(cfg, connected)
    view.update(private=True, local_slug=cfg.get("local_slug", ""), employee_id=cfg.get("employee_id", ""))
    return view
