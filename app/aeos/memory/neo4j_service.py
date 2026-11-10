# -*- coding: utf-8 -*-
"""تشغيل Neo4j Community محلياً بشكل تلقائي — مثل بقية خدمات التطبيق.

لماذا Neo4j: قاعدة الرسم المعرفي كانت Kuzu المدمج، ومحركه الأصلي ينهار
بـ0xC0000005 أثناء كتابات Graphiti (علة داخلية، والمشروع مؤرشف بلا إصلاح
قادم). Neo4j هو السائق الأساسي لدى graphiti-core ويعمل كخادم منفصل، فانهياره
— إن حدث — لا يمس الباك إند أصلاً.

التثبيت محلي بلا صلاحيات إدارية: JDK 21 + Neo4j Community مفكوكان في
``%LOCALAPPDATA%\\Moj-Agentic-AI``. ‏``ensure_neo4j()`` يشغّل الخادم إن لم يكن
منفذ bolt (7687) مفتوحاً، وينتظر جاهزيته. تُستدعى من GraphitiStore عند التهيئة
(داخل عملية الغراف المعزولة) وهي idempotent: خادم يعمل ⇒ عودة فورية.

متغيرات البيئة:
- ``NEO4J_URI`` / ``NEO4J_USER`` / ``NEO4J_PASSWORD`` — تتجاوز الافتراضيات
  المحلية (bolt://127.0.0.1:7687، neo4j، القيمة في ``_DEFAULT_PASSWORD``).
- ``NEO4J_AUTOSTART=0`` — لا تشغّل الخادم تلقائياً (خادم خارجي مُدار يدوياً).
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("aeos.memory.neo4j_service")

_BASE = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "Moj-Agentic-AI"
#: كلمة سرّ محلية فقط (الخادم يستمع على 127.0.0.1 حصراً) — قابلة للتجاوز بالبيئة
_DEFAULT_PASSWORD = "mojai-local-graph"

DEFAULT_URI = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
DEFAULT_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("NEO4J_PASSWORD", _DEFAULT_PASSWORD)


def _bolt_port() -> int:
    try:
        return int(DEFAULT_URI.rsplit(":", 1)[-1])
    except ValueError:
        return 7687


def _bolt_host() -> str:
    """مضيف bolt من NEO4J_URI (في الدوكر خدمة منفصلة: bolt://neo4j:7687 — لا 127.0.0.1)."""
    from urllib.parse import urlsplit

    return urlsplit(DEFAULT_URI).hostname or "127.0.0.1"


def _port_open(port: int, host: str = "", timeout: float = 1.0) -> bool:
    host = host or _bolt_host()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


def _find_home(pattern: str) -> Path | None:
    if not _BASE.exists():
        return None
    matches = sorted(_BASE.glob(pattern))
    return matches[-1] if matches else None


def neo4j_home() -> Path | None:
    return _find_home("neo4j-community-*")


def java_home() -> Path | None:
    return _find_home("jdk-*")


def ensure_neo4j(wait_seconds: float = 120.0) -> bool:
    """شغّل الخادم المحلي إن لزم وانتظر جاهزية bolt. تعيد False بلا استثناء."""
    port = _bolt_port()
    if _port_open(port):
        return True
    if os.getenv("NEO4J_AUTOSTART", "1").strip().lower() in ("0", "false", "no"):
        logger.warning("Neo4j not reachable on %s and autostart disabled", port)
        return False

    home, jdk = neo4j_home(), java_home()
    if home is None or jdk is None:
        logger.warning(
            "Neo4j autostart: install missing under %s (neo4j=%s, jdk=%s)",
            _BASE, home, jdk)
        return False

    env = dict(os.environ)
    env["JAVA_HOME"] = str(jdk)
    env["PATH"] = str(jdk / "bin") + os.pathsep + env.get("PATH", "")
    # ‎console‎ لا ‎start‎: عملية ابنة مباشرة نرى لوقها في stderr، ومنفصلة عن
    # وحدة التحكم كي لا يقتلها Ctrl+C الموجّه للتطبيق (الخادم يخدم أي عملية
    # غراف تُعاد ولادتها، فبقاؤه بعد موت من أطلقه هو المطلوب).
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            [str(home / "bin" / "neo4j.bat"), "console"],
            cwd=str(home), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neo4j autostart failed: %s", exc)
        return False

    logger.info("Neo4j starting (home=%s) — waiting for bolt:%s ...", home, port)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if _port_open(port):
            logger.info("Neo4j ready on port %s", port)
            return True
        time.sleep(1.5)
    logger.warning("Neo4j did not become ready within %.0fs", wait_seconds)
    return False
