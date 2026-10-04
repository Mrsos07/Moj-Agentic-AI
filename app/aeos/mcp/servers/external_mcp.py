"""
خوادم MCP خارجية حقيقية (Model Context Protocol) — يضيفها المالك ويربطها بموظف.

لماذا عميل مكتوب هنا لا حزمة ``mcp``؟ تثبيت الـSDK كان سيرقّي 13 حزمة في بيئة
التطبيق الحيّة، منها ``python-multipart`` (0.0.6 → 0.0.32) الذي تعتمد عليه
رفعات FastAPI، و``idna`` المثبّت في requirements.txt. والجزء الذي نحتاجه من
البروتوكول صغير ومستقر: JSON-RPC 2.0 بثلاث رسائل — ``initialize`` ثم
``tools/list`` ثم ``tools/call``. يُتحقّق منه أمام خادم SDK رسمي في
``verify_tools.py``.

النقلان المدعومان:
- **stdio**: عملية فرعية، رسالة JSON واحدة لكل سطر (بلا shell أبداً).
- **Streamable HTTP**: ``POST`` لعنوان واحد؛ الرد JSON أو ``text/event-stream``،
  مع ترويسة ``Mcp-Session-Id`` إن أعادها الخادم.

حدود الأمان (مقصودة):
- الأدوات **قائمة بيضاء** لكل خادم (``allowed_tools``)؛ غيرها مرفوض.
- لا يختار محتوى X (تغريدة) أداةً ولا وسائطها: الاستدعاء إما من المالك مباشرة
  (الشات/البطاقة)، أو «أداة بحث» عيّنها المالك تُستدعى بنص الموضوع فقط.
- الأسرار (env/headers) تُحفظ في ``data/`` (خارج git) ولا تُعاد للواجهة.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.external")

PROTOCOL_VERSION = "2025-06-18"
PREFIX = "ext_"
_DEFAULT_TIMEOUT = 30.0
_MAX_RESULT_CHARS = 6000
_CONFIG_PATH = Path(os.getenv("EXTERNAL_MCP_CONFIG") or
                    Path(__file__).resolve().parents[4] / "data" / "external_mcp.json")


class MCPError(RuntimeError):
    pass


# ─────────────────────────── transports ───────────────────────────
class _StdioTransport:
    def __init__(self, command: str, args: List[str], env: Dict[str, str]) -> None:
        exe = shutil.which(command) or command  # Windows: npx → npx.cmd
        self._proc = subprocess.Popen(
            [exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env={**os.environ, **env},
            text=True, encoding="utf-8", bufsize=1,
        )
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._cond = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        for line in self._proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # سجلّات يطبعها بعض الخوادم على stdout
            if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                with self._cond:
                    self._pending[msg["id"]] = msg
                    self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()

    @property
    def alive(self) -> bool:
        return self._proc.poll() is None

    def send(self, msg: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        if not self.alive:
            raise MCPError("server process exited")
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
        self._proc.stdin.flush()  # type: ignore[union-attr]
        if "id" not in msg:
            return None
        with self._cond:
            ok = self._cond.wait_for(lambda: msg["id"] in self._pending or not self.alive, timeout)
            if msg["id"] in self._pending:
                return self._pending.pop(msg["id"])
        raise MCPError("timeout" if not ok else "server process exited")

    def close(self) -> None:
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self._proc.kill()


class _HttpTransport:
    def __init__(self, url: str, headers: Dict[str, str]) -> None:
        self._url = url
        self._headers = dict(headers)
        self._session_id = ""
        self.protocol = ""

    alive = True

    def send(self, msg: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        import requests

        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", **self._headers}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        resp = requests.post(self._url, json=msg, headers=headers, timeout=timeout,
                             stream=True)
        if resp.status_code >= 400:
            raise MCPError(f"HTTP {resp.status_code}")
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        if "id" not in msg:
            resp.close()
            return None
        if "text/event-stream" in resp.headers.get("Content-Type", ""):
            # بايتات ثم UTF-8 يدوياً: decode_unicode يفكّ البث كـlatin-1 (لا charset
            # في الترويسة)، وفي latin-1 البايت 0x85 «سطر جديد» (NEL) — وهو البايت
            # الثاني لحرف «م» في UTF-8، فكان JSON العربي ينقسم في منتصفه.
            for raw_b in resp.iter_lines():
                raw = raw_b.decode("utf-8", "replace") if raw_b else ""
                if raw.startswith("data:"):
                    try:
                        data = json.loads(raw[5:].strip())
                    except ValueError:
                        continue
                    if isinstance(data, dict) and data.get("id") == msg["id"]:
                        resp.close()
                        return data
            raise MCPError("stream ended without a response")
        return resp.json()

    def close(self) -> None:
        if not self._session_id:
            return
        try:
            import requests

            requests.delete(self._url, headers={**self._headers, "Mcp-Session-Id": self._session_id},
                            timeout=5)
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────────── client ───────────────────────────
class MCPClient:
    """جلسة MCP واحدة: تتصل عند أول استخدام وتعيد الاتصال إن ماتت العملية."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self._cfg = cfg
        self._t = None
        self._next_id = 0
        self._lock = threading.Lock()
        self.server_info: Dict[str, Any] = {}

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
             timeout: float = _DEFAULT_TIMEOUT, notify: bool = False) -> Dict[str, Any]:
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._next_id += 1
            msg["id"] = self._next_id
        reply = self._t.send(msg, timeout)  # type: ignore[union-attr]
        if notify:
            return {}
        if reply.get("error"):
            err = reply["error"]
            raise MCPError(f"{err.get('code')}: {err.get('message')}")
        return reply.get("result") or {}

    def _connect(self) -> None:
        cfg = self._cfg
        if cfg.get("transport") == "http":
            self._t = _HttpTransport(cfg["url"], cfg.get("headers") or {})
        else:
            self._t = _StdioTransport(cfg["command"], list(cfg.get("args") or []),
                                      dict(cfg.get("env") or {}))
        res = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "moj-aeos", "version": "1.0"},
        })
        self.server_info = res.get("serverInfo") or {}
        if isinstance(self._t, _HttpTransport):
            self._t.protocol = res.get("protocolVersion") or PROTOCOL_VERSION
        self._rpc("notifications/initialized", notify=True)

    def _ensure(self) -> None:
        if self._t is None or not self._t.alive:
            self._connect()

    def list_tools(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure()
            tools, cursor = [], None
            for _ in range(10):  # ترقيم صفحات
                res = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
                tools += res.get("tools") or []
                cursor = res.get("nextCursor")
                if not cursor:
                    break
            return tools

    def call_tool(self, name: str, arguments: Dict[str, Any],
                  timeout: float = _DEFAULT_TIMEOUT) -> Dict[str, Any]:
        with self._lock:
            try:
                self._ensure()
                return self._rpc("tools/call", {"name": name, "arguments": arguments}, timeout)
            except MCPError:
                self.close()  # جلسة تالفة: الاستدعاء التالي يعيد الاتصال
                raise

    def close(self) -> None:
        if self._t is not None:
            self._t.close()
        self._t = None


def result_text(result: Dict[str, Any]) -> str:
    """نصّ نتيجة ``tools/call`` (كتل text + المحتوى المنظّم إن وُجد)."""
    parts = [c.get("text", "") for c in result.get("content") or []
             if isinstance(c, dict) and c.get("type") == "text"]
    if not parts and result.get("structuredContent") is not None:
        parts = [json.dumps(result["structuredContent"], ensure_ascii=False)]
    return "\n".join(p for p in parts if p)[:_MAX_RESULT_CHARS]


# ─────────────────────────── server adapter ───────────────────────────
def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")
    return s[:40]


class ExternalMCPServer(IMCPServer):
    """يعرض خادماً خارجياً داخل ``MCPRegistry`` كأي خادم مدمج.

    أسماء الأدوات تُسجَّل بمسافة اسم (``ext_<slug>.<tool>``) حتى لا تصطدم بأداة
    مدمجة تحمل الاسم نفسه (``web_search`` مثلاً) فتسرق توجيهها.
    """

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        self.name = PREFIX + cfg["slug"]
        self.client = MCPClient(cfg)

    @property
    def allowed(self) -> List[str]:
        names = [t["name"] for t in self.cfg.get("tools") or []]
        allow = self.cfg.get("allowed_tools")
        return names if allow is None else [n for n in names if n in allow]

    @property
    def tools(self) -> List[str]:
        return [f"{self.name}.{n}" for n in self.allowed]

    def health(self) -> bool:
        return bool(self.cfg.get("tools"))

    def call(self, request: MCPRequest) -> MCPResponse:
        tool = request.tool.split(".", 1)[1] if request.tool.startswith(self.name + ".") else request.tool
        if tool not in self.allowed:
            return MCPResponse.fail(f"tool '{tool}' is not allowed on {self.name}")
        try:
            res = self.client.call_tool(tool, dict(request.params or {}))
        except Exception as exc:  # noqa: BLE001
            return MCPResponse.fail(f"{self.name}.{tool} failed: {exc}")
        text = result_text(res)
        if res.get("isError"):
            return MCPResponse.fail(text[:400] or f"{tool} returned an error")
        return MCPResponse.ok(text=text, structured=res.get("structuredContent"), tool=tool)

    def research(self, query: str) -> str:
        """استدعاء «أداة البحث» التي عيّنها المالك بنص الموضوع فقط."""
        tool = self.cfg.get("research_tool") or ""
        if not tool or tool not in self.allowed:
            return ""
        arg = self.cfg.get("research_arg") or "query"
        r = self.call(MCPRequest(employee_id="", tool=tool, params={arg: query}))
        return r.data.get("text", "") if r.success else ""


# ─────────────────────────── config store ───────────────────────────
_store_lock = threading.Lock()


def _read() -> List[Dict[str, Any]]:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8")) or []
    except FileNotFoundError:
        return []
    except Exception:  # noqa: BLE001
        logger.exception("external MCP config unreadable")
        return []


def _write(items: List[Dict[str, Any]]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_CONFIG_PATH)


def list_configs() -> List[Dict[str, Any]]:
    return _read()


def public_view(cfg: Dict[str, Any], connected: Optional[bool] = None) -> Dict[str, Any]:
    """للواجهة/الشات: بلا قيم الأسرار (أسماء المفاتيح فقط)."""
    return {
        "name": PREFIX + cfg["slug"], "label": cfg.get("label") or cfg["slug"],
        "transport": cfg.get("transport"), "command": cfg.get("command", ""),
        "args": cfg.get("args") or [], "url": cfg.get("url", ""),
        "env_keys": sorted((cfg.get("env") or {}).keys()),
        "header_keys": sorted((cfg.get("headers") or {}).keys()),
        "tools": [{"name": t["name"], "description": (t.get("description") or "")[:200]}
                  for t in cfg.get("tools") or []],
        "allowed_tools": cfg.get("allowed_tools"),
        "research_tool": cfg.get("research_tool") or "",
        "research_arg": cfg.get("research_arg") or "",
        "server_info": cfg.get("server_info") or {},
        **({"connected": connected} if connected is not None else {}),
    }


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """تحقّق صارم من مدخلات المالك — الحدّ الأمني الوحيد قبل تشغيل عملية/طلب."""
    s = slug(raw.get("name") or raw.get("label") or "")
    if not s:
        raise ValueError("name is required")
    transport = (raw.get("transport") or ("http" if raw.get("url") else "stdio")).lower()
    if transport not in ("stdio", "http"):
        raise ValueError("transport must be stdio or http")
    cfg: Dict[str, Any] = {"slug": s, "label": str(raw.get("label") or raw.get("name") or s)[:60],
                           "transport": transport}
    if transport == "stdio":
        command = str(raw.get("command") or "").strip()
        if not command or any(ch in command for ch in "|&;<>`$"):
            raise ValueError("command is required (an executable name/path, no shell syntax)")
        cfg["command"] = command
        cfg["args"] = [str(a) for a in (raw.get("args") or [])][:30]
        cfg["env"] = {str(k): str(v) for k, v in (raw.get("env") or {}).items()}
    else:
        url = str(raw.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            raise ValueError("url must start with http:// or https://")
        cfg["url"] = url
        cfg["headers"] = {str(k): str(v) for k, v in (raw.get("headers") or {}).items()}
    allowed = raw.get("allowed_tools")
    cfg["allowed_tools"] = None if allowed is None else [str(t) for t in allowed]
    cfg["research_tool"] = str(raw.get("research_tool") or "").strip()
    cfg["research_arg"] = str(raw.get("research_arg") or "").strip()
    return cfg


def _guess_research_arg(tool: Dict[str, Any]) -> str:
    props = ((tool.get("inputSchema") or {}).get("properties") or {})
    for name in ("query", "q", "search", "search_query", "topic", "text", "prompt"):
        if name in props:
            return name
    strings = [k for k, v in props.items() if isinstance(v, dict) and v.get("type") == "string"]
    return strings[0] if len(strings) == 1 else ""


def discover(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """اتصل فعلاً واجلب الأدوات — لا يُحفظ خادم لم نتحقق أنه يعمل."""
    client = MCPClient(cfg)
    try:
        tools = client.list_tools()
        info = client.server_info
    finally:
        client.close()
    cfg = {**cfg, "server_info": {k: info.get(k) for k in ("name", "version") if k in info},
           "tools": [{"name": t.get("name"), "description": t.get("description", ""),
                      "inputSchema": t.get("inputSchema") or {}}
                     for t in tools if t.get("name")]}
    if cfg.get("research_tool"):
        match = next((t for t in cfg["tools"] if t["name"] == cfg["research_tool"]), None)
        if match is None:
            raise ValueError(f"research_tool '{cfg['research_tool']}' not offered by the server")
        cfg["research_arg"] = cfg.get("research_arg") or _guess_research_arg(match)
        if not cfg["research_arg"]:
            raise ValueError("could not infer the query argument; pass research_arg")
    return cfg


def save(raw: Dict[str, Any], registry=None) -> Dict[str, Any]:
    cfg = discover(normalize_config(raw))
    with _store_lock:
        items = [c for c in _read() if c["slug"] != cfg["slug"]] + [cfg]
        _write(items)
    if registry is not None:
        registry.unregister(PREFIX + cfg["slug"])
        registry.register(ExternalMCPServer(cfg))
    return cfg


def delete(name: str, registry=None) -> bool:
    s = slug(name[len(PREFIX):] if name.startswith(PREFIX) else name)
    with _store_lock:
        items = _read()
        kept = [c for c in items if c["slug"] != s]
        if len(kept) == len(items):
            return False
        _write(kept)
    if registry is not None:
        registry.unregister(PREFIX + s)
    return True


def register_all(registry) -> int:
    """عند الإقلاع: سجّل الخوادم المحفوظة (الاتصال كسول — عند أول استدعاء)."""
    n = 0
    for cfg in _read():
        try:
            registry.register(ExternalMCPServer(cfg))
            n += 1
        except Exception:  # noqa: BLE001
            logger.exception("external MCP %s failed to register", cfg.get("slug"))
    return n
