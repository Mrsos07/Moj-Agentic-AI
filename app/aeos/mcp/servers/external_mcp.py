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
from urllib.parse import urlsplit

from ..base import IMCPServer, MCPRequest, MCPResponse

logger = logging.getLogger("aeos.mcp.external")

PROTOCOL_VERSION = "2025-06-18"
PREFIX = "ext_"
_DEFAULT_TIMEOUT = 30.0
_MAX_RESULT_CHARS = 6000
_ROOT = Path(__file__).resolve().parents[4]
#: خوادم MCP العامة للنظام: مجلد لكل خادم فيه ``mcp.json`` (بلا قيم أسرار) + كود الحزمة
#: إن كان مرفوعاً كحزمة. يديرها **المطوّر** من لوحة التحكم (app.auth.roles).
_CATALOG_DIR = Path(os.getenv("SYSTEM_MCP_DIR") or _ROOT / "system" / "mcps")
#: قيم الأسرار (env/headers) خارج المستودع
_SECRETS_PATH = Path(os.getenv("SYSTEM_MCP_SECRETS") or _ROOT / "data" / "system_mcp_secrets.json")
#: المخزن القديم (ملف JSON واحد يضيف إليه أي مستخدم) — يُرحَّل مرة واحدة إلى الكتالوج
_LEGACY_PATH = Path(os.getenv("EXTERNAL_MCP_CONFIG") or _ROOT / "data" / "external_mcp.json")
MANIFEST = "mcp.json"
#: حقول الأسرار ← اسم قائمة مفاتيحها في manifest. القيم في _SECRETS_PATH فقط.
#: query_secrets: معاملات الرابط السرية (مثل ?tavilyApiKey=…) — تُنزع من الرابط المحفوظ
#: وتُضاف وقت الاتصال، وإلا انتهى المفتاح في mcp.json المرفوع إلى git.
SECRET_FIELDS = {"env": "env_keys", "headers": "header_keys", "query_secrets": "query_keys"}
_SECRET_PARAM_RE = re.compile(r"(?i)(key|token|secret|password|passwd|auth|sig|credential)")
#: رموز تُحلّ وقت التشغيل في manifest الحزم (لا مسارات مطلقة في المستودع)
PY_TOKEN, PKG_TOKEN = "{python}", "{package}"


class MCPError(RuntimeError):
    """``status``/``www_auth``: رمز HTTP وترويسة WWW-Authenticate إن وُجدا — يستخدمهما
    الاكتشاف التلقائي ليميّز «رابط خاطئ» (404/HTML) عن «مفتاح خاطئ» (401/403) عن «OAuth»."""

    def __init__(self, message: str, status: int = 0, www_auth: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.www_auth = www_auth


# ─────────────────────────── transports ───────────────────────────
class _StdioTransport:
    def __init__(self, command: str, args: List[str], env: Dict[str, str],
                 cwd: Optional[str] = None) -> None:
        exe = shutil.which(command) or command  # Windows: npx → npx.cmd
        # UTF-8 افتراضياً للعملية الابنة: على ويندوز stdout = cp1252، فخادم Python يطبع عربياً
        # (json.dumps(..., ensure_ascii=False)) ينهار بـ UnicodeEncodeError ويموت قبل أول رد.
        self._proc = subprocess.Popen(
            [exe, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", **os.environ, **env},
            text=True, encoding="utf-8", bufsize=1, cwd=cwd or None,
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
        # بلا تتبّع تحويلات: خادم عام كان يستطيع تحويل الطلب لعنوان داخلي فيتجاوز فحص
        # العنوان (SSRF عبر redirect). خادم MCP لا يحتاج تحويلاً أصلاً.
        resp = requests.post(self._url, json=msg, headers=headers, timeout=timeout,
                             stream=True, allow_redirects=False)
        _raise_for_http(resp)
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
        ctype = resp.headers.get("Content-Type", "")
        try:
            data = resp.json()
        except ValueError:
            kind = "صفحة ويب (HTML)" if "html" in ctype else f"نص غير JSON ({ctype or 'بلا نوع'})"
            raise MCPError(f"الرابط أعاد {kind} لا رد MCP — تأكد أنه عنوان خادم MCP "
                           "(غالباً ينتهي بـ /mcp) لا صفحة الموقع أو التوثيق") from None
        return data

    def close(self) -> None:
        if not self._session_id:
            return
        try:
            import requests

            requests.delete(self._url, headers={**self._headers, "Mcp-Session-Id": self._session_id},
                            timeout=5)
        except Exception:  # noqa: BLE001
            pass


def _raise_for_http(resp) -> None:
    code = resp.status_code
    if 300 <= code < 400:
        raise MCPError(f"HTTP {code}: تحويلات الخادم غير مسموحة", status=code)
    if code >= 400:
        www = resp.headers.get("WWW-Authenticate", "")
        if code == 401 and "resource_metadata" in www:
            raise MCPError("HTTP 401: هذا الرابط يتطلب تسجيل دخول OAuth (حساب المزوّد في المتصفح) — "
                           "النظام يتصل بمفتاح API؛ خذ من توثيق المزوّد رابط MCP الذي يقبل مفتاح API",
                           status=code, www_auth=www)
        hint = {401: " — المفتاح مفقود أو غير صحيح أو أُرسل بطريقة غير المتوقعة",
                403: " — المفتاح لا يملك الصلاحية", 404: " — المسار غير موجود (تأكد من الرابط)",
                405: " — الخادم لا يقبل POST على هذا المسار"}.get(code, "")
        raise MCPError(f"HTTP {code}{hint}", status=code, www_auth=www)


class _SseTransport:
    """نقل MCP القديم (HTTP+SSE، إصدار 2024-11-05): ``GET`` يفتح بثاً يرسل أولاً حدث
    ``endpoint`` (عنوان POST)، ثم تصل الردود أحداثَ ``message`` على البث نفسه.
    خوادم كثيرة (روابط تنتهي بـ ``/sse``) ما زالت تعمل به فقط — كانت تُرفض تماماً."""

    def __init__(self, url: str, headers: Dict[str, str], timeout: float = _DEFAULT_TIMEOUT) -> None:
        import queue

        import requests

        self._headers = dict(headers)
        self._pending: Dict[Any, "queue.Queue"] = {}
        self._plock = threading.Lock()
        self._endpoint = ""
        self._ready = threading.Event()
        self.alive = True
        self._resp = requests.get(url, headers={"Accept": "text/event-stream", **self._headers},
                                  stream=True, timeout=(timeout, None), allow_redirects=False)
        _raise_for_http(self._resp)
        if "text/event-stream" not in self._resp.headers.get("Content-Type", ""):
            self._resp.close()
            raise MCPError("الرابط لا يفتح بث SSE (text/event-stream) — ليس خادم MCP بنقل SSE")
        self._base = url
        threading.Thread(target=self._reader, daemon=True, name="mcp-sse").start()
        # خوادم Streamable HTTP تقبل GET لبث إشعارات بلا حدث endpoint — لا ننتظرها 30 ثانية
        if not self._ready.wait(min(timeout, 10)):
            self.close()
            raise MCPError("خادم SSE لم يرسل حدث endpoint — ليس خادم MCP بنقل SSE")

    def _reader(self) -> None:
        from urllib.parse import urljoin, urlsplit

        event, data = "message", []
        try:
            # readline لا iter_lines: الأخيرة تنتظر امتلاء كتلة 512 بايت قبل أن تُخرج أي سطر،
            # وحدث endpoint أقصر من ذلك فيبقى عالقاً حتى المهلة (قيس: «لم يرسل endpoint»)
            for raw_b in iter(self._resp.raw.readline, b""):
                line = raw_b.rstrip(b"\r\n").decode("utf-8", "replace")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif not line and data:
                    payload, data, ev, event = "\n".join(data), [], event, "message"
                    if ev == "endpoint":
                        ep = urljoin(self._base, payload.strip())
                        a, b = urlsplit(ep), urlsplit(self._base)
                        # عنوان POST من نفس الأصل فقط: وإلا يستطيع خادم عام توجيهنا لعنوان داخلي
                        if (a.scheme, a.netloc) == (b.scheme, b.netloc):
                            self._endpoint = ep
                            self._ready.set()
                        continue
                    try:
                        msg = json.loads(payload)
                    except ValueError:
                        continue
                    with self._plock:
                        q = self._pending.pop(msg.get("id"), None) if isinstance(msg, dict) else None
                    if q is not None:
                        q.put(msg)
        except Exception:  # noqa: BLE001 - انقطاع البث = جلسة ميتة، تُعاد عند الاستخدام التالي
            pass
        finally:
            self.alive = False

    def send(self, msg: Dict[str, Any], timeout: float) -> Optional[Dict[str, Any]]:
        import queue

        import requests

        q: "queue.Queue" = queue.Queue(maxsize=1)
        if "id" in msg:
            with self._plock:
                self._pending[msg["id"]] = q
        resp = requests.post(self._endpoint, json=msg, timeout=timeout, allow_redirects=False,
                             headers={"Content-Type": "application/json", **self._headers})
        _raise_for_http(resp)
        if "id" not in msg:
            return None
        if "json" in resp.headers.get("Content-Type", ""):     # بعض الخوادم ترد في جسم POST مباشرة
            try:
                data = resp.json()
                if isinstance(data, dict) and data.get("id") == msg["id"]:
                    return data
            except ValueError:
                pass
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            raise MCPError("لم يصل الرد على بث SSE في الوقت المحدد") from None
        finally:
            with self._plock:
                self._pending.pop(msg["id"], None)

    def close(self) -> None:
        self.alive = False
        try:
            self._resp.close()
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────────── client ───────────────────────────
#: بيئة تشغيل تُحسب وقت الاتصال لخادم stdio بعينه (slug ← دالة تعيد env إضافية).
#: السبب: خادم نواه يحتاج مفتاح النموذج وعنوانه من ``resolve_llm()`` — مصدر الحقيقة الوحيد
#: للمزوّد — لا قيمة ثابتة في ملف الأسرار تتجمّد حين يبدّل المالك المزوّد.
_ENV_PROVIDERS: Dict[str, Any] = {}


def register_env_provider(slug_: str, provider) -> None:
    _ENV_PROVIDERS[slug_] = provider


def runtime_env(cfg: Dict[str, Any]) -> Dict[str, str]:
    """env الطفل = env المحفوظة (الأسرار) + ما تعيده دالة المزوّد الآن (إن وُجدت)."""
    env = {k: str(v) for k, v in (cfg.get("env") or {}).items()}
    provider = _ENV_PROVIDERS.get(cfg.get("slug") or "")
    if provider is not None:
        try:
            env.update({k: str(v) for k, v in (provider() or {}).items()})
        except Exception:  # noqa: BLE001 - بيئة ناقصة أفضل من خادم لا يعمل أبداً
            logger.exception("env provider for %s failed", cfg.get("slug"))
    return env


def _env_signature(env: Dict[str, str]) -> int:
    return hash(tuple(sorted(env.items())))


class MCPClient:
    """جلسة MCP واحدة: تتصل عند أول استخدام وتعيد الاتصال إن ماتت العملية."""

    def __init__(self, cfg: Dict[str, Any]) -> None:
        self._cfg = cfg
        self._t = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._env_sig: Optional[int] = None
        self.server_info: Dict[str, Any] = {}
        self.capabilities: Dict[str, Any] = {}

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
        if not isinstance(reply, dict) or not ("result" in reply or "error" in reply):
            # خادم JSON عادي (REST) يرد 200 على أي POST: كان يُقرأ «اتصال ناجح — 0 أداة»
            raise MCPError("الرد ليس JSON-RPC (لا result ولا error) — هذا الرابط ليس خادم MCP؛ "
                           "إن أعطاك المزوّد أمر curl لواجهة REST فهي ليست MCP")
        if reply.get("error"):
            err = reply["error"]
            raise MCPError(f"{err.get('code')}: {err.get('message')}")
        return reply.get("result") or {}

    def _connect(self) -> None:
        cfg = self._cfg
        if cfg.get("transport") == "http" and cfg.get("http_mode") == "sse":
            self._t = _SseTransport(runtime_url(cfg), cfg.get("headers") or {})
        elif cfg.get("transport") == "http":
            self._t = _HttpTransport(runtime_url(cfg), cfg.get("headers") or {})
        else:
            command, args, cwd = runtime_command(cfg)
            env = runtime_env(cfg)
            self._env_sig = _env_signature(env)
            self._t = _StdioTransport(command, args, env, cwd)
        try:
            res = self._rpc("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "moj-aeos", "version": "1.0"},
            })
        except MCPError as exc:
            parts = urlsplit(cfg.get("url") or "") if cfg.get("transport") == "http" else None
            if parts and "JSON-RPC" in str(exc) and not parts.path.rstrip("/").endswith("/mcp"):
                raise MCPError(f"{exc}. جرّب: {parts.scheme}://{parts.netloc}/mcp") from None
            raise
        if not isinstance(res, dict) or not ("protocolVersion" in res or "capabilities" in res):
            raise MCPError("رد «initialize» لا يحوي protocolVersion ولا capabilities — الخادم لا يتكلم "
                           "بروتوكول MCP (ربما واجهة JSON-RPC أخرى أو رابط خاطئ)")
        self.server_info = res.get("serverInfo") or {}
        self.capabilities = res.get("capabilities") if isinstance(res.get("capabilities"), dict) else {}
        if isinstance(self._t, _HttpTransport):
            self._t.protocol = res.get("protocolVersion") or PROTOCOL_VERSION
        self._rpc("notifications/initialized", notify=True)

    def _ensure(self) -> None:
        # خادم stdio ببيئة محسوبة: إن تغيّرت (بدّل المالك المزوّد/المفتاح) نعيد تشغيله بالجديدة
        if (self._t is not None and self._t.alive and self._cfg.get("transport") != "http"
                and (self._cfg.get("slug") or "") in _ENV_PROVIDERS
                and _env_signature(runtime_env(self._cfg)) != self._env_sig):
            self.close()
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


def split_secret_params(url: str) -> "tuple[str, Dict[str, str]]":
    """انزع معاملات الرابط التي تبدو أسراراً (apiKey، token…) — الرابط يُحفظ بدونها."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    keep, secret = [], {}
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        (secret.__setitem__(k, v) if _SECRET_PARAM_RE.search(k) else keep.append((k, v)))
    return urlunsplit(parts._replace(query=urlencode(keep))), secret


def runtime_url(cfg: Dict[str, Any]) -> str:
    """الرابط وقت الاتصال = المحفوظ + معاملاته السرية (لا يُكتب في أي ملف أو سجل)."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    extra = cfg.get("query_secrets") or {}
    if not extra:
        return cfg["url"]
    parts = urlsplit(cfg["url"])
    query = parse_qsl(parts.query, keep_blank_values=True) + list(extra.items())
    return urlunsplit(parts._replace(query=urlencode(query)))


def package_dir(slug_: str) -> Path:
    return _CATALOG_DIR / slug_


def runtime_command(cfg: Dict[str, Any]) -> "tuple[str, List[str], Optional[str]]":
    """حلّ رموز manifest الحزم: ``{python}`` = مفسّر البيئة الحالية، ``{package}`` = مجلد
    الحزمة. ولا يُسمح لمجلد العمل بالخروج عن الكتالوج."""
    import sys

    pkg = package_dir(cfg["slug"])
    command = sys.executable if cfg.get("command") == PY_TOKEN else cfg["command"]
    args = [str(a).replace(PKG_TOKEN, str(pkg)) for a in cfg.get("args") or []]
    cwd = None
    if cfg.get("cwd") == PKG_TOKEN:
        cwd = str(pkg)
    return command, args, cwd


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


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:  # noqa: BLE001
        logger.exception("unreadable: %s", path)
        return default


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _migrate_legacy() -> None:
    """المخزن القديم (data/external_mcp.json) ← الكتالوج، مرة واحدة دون فقد أي خادم."""
    legacy = _json(_LEGACY_PATH, None) if _LEGACY_PATH.exists() else None
    if not legacy:
        return
    existing = {p.parent.name for p in _CATALOG_DIR.glob(f"*/{MANIFEST}")}
    items = [c for c in legacy if isinstance(c, dict) and c.get("slug") and c["slug"] not in existing]
    if items:
        _write_items(items)
    try:
        _LEGACY_PATH.replace(_LEGACY_PATH.with_suffix(".json.migrated"))
    except OSError:
        logger.warning("could not rename legacy MCP store")


def _read() -> List[Dict[str, Any]]:
    """كل خوادم الكتالوج مع أسرارها مدمجة (للاستخدام الداخلي فقط — لا تُعاد للواجهة)."""
    if _LEGACY_PATH.exists():
        with _store_lock:
            _migrate_legacy()
    secrets = _json(_SECRETS_PATH, {}) or {}
    out = []
    for manifest in sorted(_CATALOG_DIR.glob(f"*/{MANIFEST}")):
        cfg = _json(manifest, None)
        # الخوادم المدمجة (kind: builtin) تحمّلها app/aeos/mcp/catalog.py داخل العملية
        if not isinstance(cfg, dict) or not cfg.get("slug") or cfg.get("kind") == "builtin":
            continue
        sec = secrets.get(cfg["slug"]) or {}
        for field, keys in SECRET_FIELDS.items():
            cfg[field] = {k: sec.get(field, {}).get(k, "") for k in cfg.pop(keys, [])}
        cfg.setdefault("enabled", True)
        cfg.setdefault("scope", {"coordinator": True, "employees": True})
        out.append(cfg)
    return out


def _write_items(items: List[Dict[str, Any]]) -> None:
    """اكتب manifest كل خادم (أسماء المفاتيح فقط) والقيم إلى ملف الأسرار."""
    secrets = _json(_SECRETS_PATH, {}) or {}
    for cfg in items:
        manifest = {k: v for k, v in cfg.items() if k not in SECRET_FIELDS}
        for field, keys in SECRET_FIELDS.items():
            manifest[keys] = sorted((cfg.get(field) or {}).keys())
        _atomic_write(package_dir(cfg["slug"]) / MANIFEST, manifest)
        secrets[cfg["slug"]] = {field: dict(cfg.get(field) or {}) for field in SECRET_FIELDS}
    _atomic_write(_SECRETS_PATH, secrets)
    try:
        os.chmod(_SECRETS_PATH, 0o600)
    except OSError:
        pass


def _write(items: List[Dict[str, Any]]) -> None:
    """استبدل الكتالوج بالقائمة: ما ليس فيها يُحذف manifest-ه (ومجلده إن كان حزمة)."""
    keep = {c["slug"] for c in items}
    for manifest in _CATALOG_DIR.glob(f"*/{MANIFEST}"):
        if manifest.parent.name not in keep and not is_builtin(manifest.parent.name):
            _remove_dir(manifest.parent)
    secrets = {k: v for k, v in (_json(_SECRETS_PATH, {}) or {}).items() if k in keep}
    _atomic_write(_SECRETS_PATH, secrets)
    if items:
        _write_items(items)


def is_builtin(slug_: str) -> bool:
    m = _json(package_dir(slug_) / MANIFEST, None)
    return isinstance(m, dict) and m.get("kind") == "builtin"


def _remove_dir(path: Path) -> None:
    """احذف مجلد خادم — فقط إن كان داخل الكتالوج فعلاً، وليس خادماً مدمجاً أبداً."""
    path = path.resolve()
    if path.parent == _CATALOG_DIR.resolve() and path.is_dir() and not is_builtin(path.name):
        shutil.rmtree(path, ignore_errors=True)


def list_configs(enabled_only: bool = False) -> List[Dict[str, Any]]:
    items = _read()
    return [c for c in items if c.get("enabled", True)] if enabled_only else items


def public_view(cfg: Dict[str, Any], connected: Optional[bool] = None) -> Dict[str, Any]:
    """للواجهة/الشات: بلا قيم الأسرار (أسماء المفاتيح فقط)."""
    return {
        "name": PREFIX + cfg["slug"], "slug": cfg["slug"], "label": cfg.get("label") or cfg["slug"],
        "description": cfg.get("description", ""),
        "transport": cfg.get("transport"), "command": cfg.get("command", ""),
        "args": cfg.get("args") or [], "url": cfg.get("url", ""),
        "http_mode": cfg.get("http_mode") or ("streamable" if cfg.get("transport") == "http" else ""),
        "source": cfg.get("source", "config"), "entry": cfg.get("entry", ""),
        "enabled": bool(cfg.get("enabled", True)),
        "scope": cfg.get("scope") or {"coordinator": True, "employees": True},
        "added_by": cfg.get("added_by", ""), "added_at": cfg.get("added_at", ""),
        "env_keys": sorted((cfg.get("env") or {}).keys()),
        "header_keys": sorted((cfg.get("headers") or {}).keys()),
        "query_keys": sorted((cfg.get("query_secrets") or {}).keys()),
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
    if is_builtin(s):
        raise ValueError(f"'{s}' اسم خادم مدمج في النظام — اختر اسماً آخر")
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
        cwd = raw.get("cwd")
        if cwd not in (None, "", PKG_TOKEN):   # مجلد العمل: مجلد الحزمة فقط، لا مسار حرّ
            raise ValueError("cwd is set automatically for uploaded packages only")
        if cwd == PKG_TOKEN:
            cfg["cwd"] = PKG_TOKEN
    else:
        url = str(raw.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            raise ValueError("url must start with http:// or https://")
        url, found = split_secret_params(url)
        cfg["url"] = url
        mode = str(raw.get("http_mode") or "").lower()
        if mode not in ("streamable", "sse"):
            mode = "sse" if urlsplit(url).path.rstrip("/").endswith("/sse") else "streamable"
        cfg["http_mode"] = mode
        cfg["headers"] = {str(k): str(v) for k, v in (raw.get("headers") or {}).items()}
        cfg["query_secrets"] = {**found, **{str(k): str(v) for k, v in (raw.get("query_secrets") or {}).items()}}
    allowed = raw.get("allowed_tools")
    cfg["allowed_tools"] = None if allowed is None else [str(t) for t in allowed]
    cfg["research_tool"] = str(raw.get("research_tool") or "").strip()
    cfg["research_arg"] = str(raw.get("research_arg") or "").strip()
    cfg["description"] = str(raw.get("description") or "")[:300]
    cfg["enabled"] = bool(raw.get("enabled", True))
    scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
    cfg["scope"] = {"coordinator": bool(scope.get("coordinator", True)),
                    "employees": bool(scope.get("employees", True))}
    for k in ("source", "entry", "added_by", "added_at"):
        if raw.get(k):
            cfg[k] = str(raw[k])[:120]
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
        info, caps = client.server_info, client.capabilities
    finally:
        client.close()
    if not any(isinstance(t, dict) and t.get("name") for t in tools):
        # خادم بلا أدوات لا فائدة منه للمنسّق ولا للموظفين — كان يُحفظ بنجاح «0 أداة»
        who = f"«{info.get('name')}» " if info.get("name") else ""
        if "tools" not in caps:
            raise ValueError(f"الخادم {who}يعمل لكنه لا يقدّم أدوات (لا يعلن قدرة tools) — "
                             "ربما يقدّم موارد أو قوالب فقط، والنظام يستخدم الأدوات")
        raise ValueError(
            f"الاتصال بالخادم {who}نجح لكنه أعاد 0 أداة. الأسباب الشائعة: المفتاح ناقص أو بصلاحيات "
            "لا تكشف أدوات، أو أُرسل بطريقة غير التي يتوقعها المزوّد (Bearer / ترويسة / معامل)، أو "
            "حسابك لدى المزوّد بلا أدوات مفعّلة (مثل Zapier وComposio: فعّل الإجراءات من لوحتهم أولاً).")
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


def _sync_registry(cfg: Dict[str, Any], registry) -> None:
    if registry is None:
        return
    registry.unregister(PREFIX + cfg["slug"])
    if cfg.get("enabled", True):
        registry.register(ExternalMCPServer(cfg))


def save(raw: Dict[str, Any], registry=None) -> Dict[str, Any]:
    cfg = discover(normalize_config(raw))
    with _store_lock:
        _write_items([cfg])
    _sync_registry(cfg, registry)
    return cfg


def get(name: str) -> Optional[Dict[str, Any]]:
    s = slug(name[len(PREFIX):] if name.startswith(PREFIX) else name)
    return next((c for c in _read() if c["slug"] == s), None)


def update(name: str, patch: Dict[str, Any], registry=None, rediscover: bool = False) -> Dict[str, Any]:
    """تعديل خادم قائم: التفعيل، الوصف، النطاق، القائمة البيضاء، قيم الأسرار. الأسرار
    الفارغة في الطلب تعني «أبقِ القيمة الحالية» (الواجهة لا تعرض القيم أصلاً)."""
    cur = get(name)
    if cur is None:
        raise KeyError(name)
    cfg = dict(cur)
    for k in ("label", "description", "enabled", "allowed_tools", "research_tool", "research_arg"):
        if k in patch:
            cfg[k] = patch[k]
    if isinstance(patch.get("scope"), dict):
        cfg["scope"] = {**cfg.get("scope", {}), **{k: bool(v) for k, v in patch["scope"].items()
                                                   if k in ("coordinator", "employees")}}
    for field in SECRET_FIELDS:
        if isinstance(patch.get(field), dict):
            merged = dict(cfg.get(field) or {})
            for k, v in patch[field].items():
                if v is None:
                    merged.pop(k, None)          # حذف مفتاح
                elif str(v) != "":
                    merged[str(k)] = str(v)      # قيمة جديدة
                else:
                    merged.setdefault(str(k), "")
            cfg[field] = merged
    cfg = normalize_config({**cfg, "name": cfg["slug"]})
    cfg["tools"], cfg["server_info"] = cur.get("tools") or [], cur.get("server_info") or {}
    if rediscover or any(k in patch for k in SECRET_FIELDS):
        cfg = discover(cfg)
    with _store_lock:
        _write_items([cfg])
    _sync_registry(cfg, registry)
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


# ─────────────────────────── upload parsing (developer) ───────────────────────────
_MAX_PACKAGE_BYTES = 20 * 1024 * 1024
_MAX_PACKAGE_FILES = 500


def parse_config_file(data: bytes) -> List[Dict[str, Any]]:
    """ملف إعداد JSON: صيغة ``mcpServers`` القياسية (Claude Desktop/Cursor) أو كائن واحد
    أو قائمة. يعيد مدخلات خام تمرّ بعدها بـ normalize_config كأي إضافة."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"ملف JSON غير صالح: {exc}") from exc
    if isinstance(doc, dict) and isinstance(doc.get("mcpServers"), dict):
        items = [{"name": name, **(spec or {})} for name, spec in doc["mcpServers"].items()]
    elif isinstance(doc, list):
        items = doc
    elif isinstance(doc, dict):
        items = [doc]
    else:
        raise ValueError("الصيغة المدعومة: {\"mcpServers\": {...}} أو كائن خادم أو قائمة")
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("كل خادم يجب أن يكون كائناً")
        t = str(raw.get("type") or raw.get("transport") or "").lower()
        if t == "sse":
            raw = {**raw, "transport": "http", "http_mode": "sse"}
        elif t in ("http", "streamable-http", "streamable_http"):
            raw = {**raw, "transport": "http"}
        out.append(raw)
    if not out:
        raise ValueError("الملف لا يحوي أي خادم")
    return out


def install_package(filename: str, data: bytes, name: str = "") -> Dict[str, Any]:
    """حزمة خادم (.py أو .zip) ← مجلد في الكتالوج + إعداد stdio يشغّلها بمفسّر البيئة.

    الحزمة كودٌ سيُنفَّذ على هذا الجهاز، فالرفع للمطوّر وحده (app.auth.roles)، ومع ذلك:
    لا مسارات مطلقة ولا ``..`` داخل الـzip، وحدّ للحجم وعدد الملفات، ولا كتابة خارج مجلدها.
    إن احتوى الـzip ملف ``mcp.json`` أُخذت منه الإعدادات (entry/args/env/description).
    """
    import io
    import zipfile

    if len(data) > _MAX_PACKAGE_BYTES:
        raise ValueError("الحزمة أكبر من 20MB")
    base = Path(filename or "").name
    stem, ext = os.path.splitext(base)
    s = slug(name or stem)
    if not s:
        raise ValueError("اسم الخادم مطلوب")
    target = package_dir(s)
    if is_builtin(s):
        raise ValueError(f"'{s}' اسم خادم مدمج في النظام — اختر اسماً آخر")
    if (target / MANIFEST).exists():
        raise ValueError(f"خادم باسم '{s}' موجود — احذفه أولاً أو غيّر الاسم")
    meta: Dict[str, Any] = {}
    tmp = _CATALOG_DIR / f".upload_{s}"
    _remove_dir(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        if ext.lower() == ".py":
            (tmp / "server.py").write_bytes(data)
            entry = "server.py"
        elif ext.lower() == ".zip":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                if len(infos) > _MAX_PACKAGE_FILES:
                    raise ValueError("الحزمة تحوي ملفات كثيرة جداً")
                if sum(i.file_size for i in infos) > _MAX_PACKAGE_BYTES * 3:
                    raise ValueError("حجم الحزمة بعد فكّها كبير جداً")
                # مجلد جذر واحد داخل الـzip (شائع) يُتجاهل
                names = [i.filename.replace("\\", "/") for i in infos]
                roots = {n.split("/", 1)[0] for n in names}
                strip = (roots.pop() + "/") if len(roots) == 1 and all("/" in n for n in names) else ""
                for info, n in zip(infos, names):
                    rel = n[len(strip):] if strip and n.startswith(strip) else n
                    dest = (tmp / rel).resolve()
                    if not rel or rel.startswith("/") or ".." in Path(rel).parts \
                            or tmp.resolve() not in dest.parents:
                        raise ValueError(f"مسار غير آمن داخل الحزمة: {n}")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(info))
            meta = _json(tmp / MANIFEST, {}) if (tmp / MANIFEST).exists() else {}
            entry = str(meta.get("entry") or "")
            if not entry:
                entry = next((c for c in ("server.py", "main.py", "__main__.py") if (tmp / c).exists()), "")
            if not entry:
                pys = [p.name for p in tmp.glob("*.py")]
                entry = pys[0] if len(pys) == 1 else ""
            if not entry or not (tmp / entry).is_file() or ".." in Path(entry).parts:
                raise ValueError("لم أجد ملف التشغيل: ضع server.py أو حدّد entry في mcp.json داخل الحزمة")
        else:
            raise ValueError("الحزمة: ملف .py أو .zip")
        if target.exists():
            _remove_dir(target)
        tmp.replace(target)
    except Exception:
        _remove_dir(tmp)
        raise
    return {
        "name": s, "label": meta.get("label") or name or stem,
        "description": meta.get("description", ""), "transport": "stdio",
        "command": PY_TOKEN, "args": [f"{PKG_TOKEN}/{entry}", *[str(a) for a in meta.get("args") or []]],
        "cwd": PKG_TOKEN, "env": meta.get("env") or {}, "source": "package", "entry": entry,
        "allowed_tools": meta.get("allowed_tools"), "scope": meta.get("scope") or {},
    }


def register_all(registry) -> int:
    """عند الإقلاع: سجّل الخوادم المفعّلة (الاتصال كسول — عند أول استدعاء)."""
    n = 0
    for cfg in list_configs(enabled_only=True):
        try:
            registry.register(ExternalMCPServer(cfg))
            n += 1
        except Exception:  # noqa: BLE001
            logger.exception("external MCP %s failed to register", cfg.get("slug"))
    return n
