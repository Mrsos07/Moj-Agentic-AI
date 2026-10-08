"""
محمِّل كود النظام من ``system/`` — المجلد الرئيسي لكل خوادم MCP وكل المهارات.

``system/`` ليس حزمة Python (أسماء مجلداته تأتي من الرفع وقد تحوي شرطات)، فيُحمَّل كل
ملف بمساره تحت اسم قانوني ثابت (``moj_system.mcps.<dir>`` / ``moj_system.skills.<dir>``)
ويُخزَّن في ``sys.modules``. التحميل مرة واحدة: كل من يطلب الوحدة يحصل على **الكائن
نفسه** — وإلا صارت للخادم/المهارة نسختان بفئتين مختلفتين (isinstance يفشل والتعديل
على إحداهما لا يظهر في الأخرى).

المسارات القديمة (``app/aeos/mcp/servers/x_mcp.py``…) أسماء بديلة تشير لهذه الوحدات.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SYSTEM_DIR = Path(os.getenv("MOJ_SYSTEM_DIR") or ROOT / "system")
_lock = threading.RLock()


def load_module(path: Path, name: str) -> ModuleType:
    with _lock:
        mod = sys.modules.get(name)
        if mod is not None:
            return mod
        if not path.is_file():
            raise ModuleNotFoundError(f"system module not found: {path}")
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod          # قبل التنفيذ: يحمي من الاستيراد الدائري
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return mod


def _ident(dirname: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in dirname)


def mcp_module(dirname: str, entry: str = "server.py") -> ModuleType:
    return load_module(SYSTEM_DIR / "mcps" / dirname / entry, f"moj_system.mcps.{_ident(dirname)}")


def skill_module(dirname: str, entry: str = "skill.py") -> ModuleType:
    return load_module(SYSTEM_DIR / "skills" / dirname / entry, f"moj_system.skills.{_ident(dirname)}")
