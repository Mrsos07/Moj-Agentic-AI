"""
استيراد إعداد خادم MCP من «مقتطف اتصال» كما تنشره المزوّدات: أمر curl، أو كود Python،
أو أمر CLI، أو JSON — إلى نموذج الإضافة السريعة في لوحة التحكم.

المزوّدات تعرض طريقة الاتصال بأشكال مختلفة («Connect via cURL / Python»)، والمطوّر كان
عليه أن يستخرج الرابط والمفتاح يدوياً. هنا تُقرأ كلها **قراءةً فقط** — لا يُنفَّذ أي كود
ولا أمر (تحليل نحوي بـ ast وshlex)، ثم يمرّ الناتج بنفس مسار «اختبار الاتصال» و«التثبيت».

الأشكال المدعومة:
- ``curl … -H "Authorization: Bearer …" https://…/mcp`` (+ ``-u``، ``--oauth2-bearer``)
- Python: ``streamablehttp_client(url, headers=…)``، ``Client("https://…")``،
  ``httpx.AsyncClient(headers=…)`` + ``streamable_http_client(url, http_client=…)``،
  ``MultiServerMCPClient({...})``، ``StdioServerParameters(command, args, env)``،
  ``MCPServerStreamableHttp(params={...})`` — مع حلّ المتغيّرات البسيطة وf-strings.
- ``claude mcp add [--transport http] name url --header …`` و``npx mcp-remote url --header …``
- أوامر stdio: ``npx -y pkg``، ``uvx pkg``، ``python server.py`` (مع ``KEY=value`` قبلها)
- JSON: ``{"mcpServers": {...}}`` أو خادم واحد.
"""
from __future__ import annotations

import ast
import json
import re
import shlex
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

#: قيم تدل على «ضع مفتاحك هنا» لا مفتاحاً حقيقياً
_PLACEHOLDER_RE = re.compile(
    r"(?i)^(<[^>]*>|\{[^}]*\}|\$\{?[A-Z_][A-Z0-9_]*\}?|\.\.\.|x{4,}|\*{3,}|"
    r"(your|my|insert|replace|put)[\w\- ]*(key|token|secret|password).*|.*[-_ ]here$|"
    r"(api[-_]?key|token|secret|key))$")
_ENV_MARK = "⟨env:{}⟩"
_KEY_HEADER_RE = re.compile(r"(?i)(api[-_]?key|token|secret|auth)")
_URL_RE = re.compile(r"https?://[^\s\"'`<>)\]},]+")


def is_placeholder(value: str) -> bool:
    v = (value or "").strip()
    if v.lower().startswith("bearer "):
        v = v[7:].strip()
    return not v or bool(_PLACEHOLDER_RE.match(v)) or v.startswith("⟨env:")


# ─────────────────────────── entry point ───────────────────────────
def parse(text: str) -> Dict[str, Any]:
    """مقتطف ← {servers: [...], detected, warnings}. كل خادم بصيغة إعداد خام."""
    text = _strip_fences(text or "")
    if not text.strip():
        raise ValueError("الصق أمر curl أو كود Python أو إعداد JSON")
    warnings: List[str] = []
    detected = ""
    servers: List[Dict[str, Any]] = []
    stripped = text.strip()
    if stripped[:1] in "{[":
        try:
            doc = json.loads(stripped)
            detected = "json"
            from .servers.external_mcp import parse_config_file

            servers = parse_config_file(json.dumps(doc).encode())
        except ValueError:
            pass
    if not servers and re.search(r"(?m)^\s*(curl|claude\s+mcp|npx|uvx|bunx|pipx|python3?|node|docker|deno)\b|^\s*[A-Z_]+=\S+\s+\S", stripped):
        detected, servers = _parse_shell(stripped, warnings)
    if not servers:
        py = _parse_python(stripped, warnings)
        if py:
            detected, servers = "python", py
    if not servers:
        url = _URL_RE.search(stripped)
        if url:
            detected, servers = "url", [{"transport": "http", "url": url.group(0)}]
            warnings.append("لم أتعرّف على صيغة المقتطف؛ أخذت الرابط فقط — أضف المفتاح يدوياً إن لزم")
    if not servers:
        raise ValueError("لم أجد رابط خادم ولا أمر تشغيل في المقتطف")
    for s in servers:
        _finalize(s, warnings)
    return {"detected": detected, "servers": servers, "warnings": list(dict.fromkeys(warnings))}


def to_form(server: Dict[str, Any]) -> Dict[str, Any]:
    """إعداد خام ← حقول نموذج الإضافة السريعة (المفتاح وطريقة إرساله مفصولان)."""
    from .servers.external_mcp import split_secret_params

    transport = server.get("transport") or ("http" if server.get("url") else "stdio")
    headers = dict(server.get("headers") or {})
    env = dict(server.get("env") or {})
    auth = {"mode": "none", "name": "", "key": ""}
    url = server.get("url") or ""
    if transport == "http":
        url, qs = split_secret_params(url)
        qs = {**qs, **(server.get("query_secrets") or {})}
        auth_h = next((k for k in headers if k.lower() == "authorization"), None)
        if auth_h and headers[auth_h].lower().startswith("bearer "):
            auth = {"mode": "bearer", "name": "", "key": _clean_key(headers.pop(auth_h)[7:])}
        elif qs:
            name, key = next(iter(qs.items()))
            auth = {"mode": "query", "name": name, "key": _clean_key(key)}
        else:
            key_h = next((k for k in headers if _KEY_HEADER_RE.search(k)), None)
            if key_h:
                auth = {"mode": "header", "name": key_h, "key": _clean_key(headers.pop(key_h))}
    else:
        key_e = next((k for k in env if _KEY_HEADER_RE.search(k)), None)
        if key_e:
            auth = {"mode": "env", "name": key_e, "key": _clean_key(env.pop(key_e))}
    headers = {k: v for k, v in headers.items() if k.lower() not in ("content-type", "accept", "mcp-protocol-version")}
    return {
        "name": server.get("name") or "", "description": server.get("description") or "",
        "transport": transport, "url": url, "http_mode": server.get("http_mode") or "",
        "command": server.get("command") or "",
        "args": list(server.get("args") or []), "auth": auth, "headers": headers, "env": env,
        "needs_key": auth["mode"] != "none" and not auth["key"],
    }


def _clean_key(v: str) -> str:
    v = (v or "").strip()
    return "" if is_placeholder(v) else v


# ─────────────────────────── helpers ───────────────────────────
def _strip_fences(text: str) -> str:
    m = re.search(r"```[\w+-]*\n(.*?)```", text, re.S)
    return m.group(1) if m else text


def _guess_name(server: Dict[str, Any]) -> str:
    from .servers.external_mcp import slug

    if server.get("url"):
        host = (urlsplit(server["url"]).hostname or "").split(".")
        host = [h for h in host if h not in ("www", "mcp", "api", "server", "com", "net", "io", "ai", "dev", "app", "org")]
        if host:
            return slug(host[0])
    for a in server.get("args") or []:
        a = str(a)
        if a.startswith("-"):
            continue
        base = re.sub(r"(@[\d.]+)?$", "", a.split("/")[-1])
        base = re.sub(r"^(server|mcp)[-_]|[-_](server|mcp)$|\.py$", "", base)
        base = re.sub(r"^(server|mcp)[-_]", "", base)
        if base:
            return slug(base)
    return slug(server.get("command") or "mcp")


def _finalize(server: Dict[str, Any], warnings: List[str]) -> None:
    url = server.get("url") or ""
    if url:
        server["transport"] = "http"
        path = urlsplit(url).path.rstrip("/")
        server["http_mode"] = "sse" if (server.pop("_sse", False) or path.endswith("/sse")) else "streamable"
    elif server.get("command"):
        server["transport"] = "stdio"
    server.setdefault("name", _guess_name(server))
    for field in ("headers", "env", "query_secrets"):
        for k, v in list((server.get(field) or {}).items()):
            m = re.match(r"^⟨env:(\w+)⟩$", str(v)) or re.search(r"⟨env:(\w+)⟩", str(v))
            if m:
                warnings.append(f"المقتطف يقرأ {k} من متغيّر البيئة {m.group(1)} — ضع قيمته في خانة المفتاح")
            elif is_placeholder(str(v)):
                warnings.append(f"قيمة {k} في المقتطف مثال وليست مفتاحاً حقيقياً — ضع مفتاحك في خانة المفتاح")


# ─────────────────────────── shell (curl / CLI / stdio) ───────────────────────────
def _parse_shell(text: str, warnings: List[str]):
    # استمرار السطر: \ (bash) · ` (PowerShell) · ^ (cmd)
    joined = re.sub(r"[\\`^]\s*\r?\n", " ", text)
    first = next((ln for ln in joined.splitlines() if ln.strip() and not ln.strip().startswith(("#", "pip ", "npm i"))), "")
    try:
        tokens = shlex.split(first, posix=True)
    except ValueError as exc:
        raise ValueError(f"تعذّر قراءة الأمر: {exc}") from exc
    env: Dict[str, str] = {}
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        k, v = tokens.pop(0).split("=", 1)
        env[k] = v
    if not tokens:
        return "", []
    cmd = re.sub(r"\.(exe|cmd)$", "", tokens[0].lower().replace("\\", "/").split("/")[-1])  # curl.exe على ويندوز
    if cmd == "curl":
        return "curl", [_curl(tokens[1:], warnings)]
    if cmd == "claude" and tokens[1:3] == ["mcp", "add"]:
        return "claude-cli", [_claude_add(tokens[3:])]
    if cmd in ("npx", "bunx") and any(t in ("mcp-remote", "@modelcontextprotocol/mcp-remote") or t.startswith("mcp-remote@")
                                      for t in tokens):
        return "mcp-remote", [_mcp_remote(tokens)]
    return "stdio", [{"transport": "stdio", "command": tokens[0], "args": tokens[1:], "env": env}]


def _header_pair(h: str):
    if ":" not in h:
        return None
    k, v = h.split(":", 1)
    return k.strip(), v.strip()


def _rest_to_mcp(url: str, body: str, warnings: List[str]) -> str:
    """أمر curl لواجهة REST (جسم JSON بلا jsonrpc) ليس خادم MCP — كان «اتصال ناجح — 0 أداة».
    المزوّدات تضع خادم MCP عادةً على نفس المضيف بمسار /mcp (مثل TrendsMCP: /api ← /mcp)."""
    try:
        data = json.loads(body) if body.strip().startswith("{") else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or "jsonrpc" in data:
        return url
    parts = urlsplit(url)
    if parts.path.rstrip("/").endswith("/mcp"):
        return url
    guess = f"{parts.scheme}://{parts.netloc}/mcp"
    warnings.append(f"هذا أمر curl لواجهة REST ({parts.path or '/'}) لا لخادم MCP — جسمه ليس JSON-RPC. "
                    f"غيّرت الرابط إلى {guess} (المسار المعتاد لخادم MCP لدى المزوّد)؛ "
                    "«اختبار الاتصال» يؤكد صحته، وإلا فخذ رابط MCP من توثيق المزوّد")
    return guess


def _curl(tokens: List[str], warnings: List[str]) -> Dict[str, Any]:
    url, headers, body = "", {}, ""
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if t in ("-H", "--header"):
            pair = _header_pair(nxt)
            if pair:
                headers[pair[0]] = pair[1]
            i += 2
            continue
        if t.startswith("-H") and len(t) > 2:
            pair = _header_pair(t[2:])
            if pair:
                headers[pair[0]] = pair[1]
        elif t in ("-u", "--user"):
            import base64

            headers["Authorization"] = "Basic " + base64.b64encode(nxt.encode()).decode()
            i += 2
            continue
        elif t == "--oauth2-bearer":
            headers["Authorization"] = f"Bearer {nxt}"
            i += 2
            continue
        elif t == "--url":
            url = nxt
            i += 2
            continue
        elif t in ("-d", "--data", "--data-raw", "--data-binary", "--json"):
            body = nxt
            i += 2
            continue
        elif t in ("-X", "--request", "-o", "-w", "--max-time", "-m", "--connect-timeout"):
            i += 2
            continue
        elif not url and re.match(r"^https?://", t):
            url = t
        i += 1
    if not url:
        raise ValueError("لم أجد رابط الخادم في أمر curl")
    return {"transport": "http", "url": _rest_to_mcp(url, body, warnings), "headers": headers}


def _claude_add(tokens: List[str]) -> Dict[str, Any]:
    transport, headers, env, pos, cmd = "", {}, {}, [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if t == "--":
            cmd = tokens[i + 1:]
            break
        if t in ("--transport", "-t"):
            transport = nxt.lower()
            i += 2
            continue
        if t in ("--header", "-H"):
            pair = _header_pair(nxt)
            if pair:
                headers[pair[0]] = pair[1]
            i += 2
            continue
        if t in ("--env", "-e"):
            if "=" in nxt:
                k, v = nxt.split("=", 1)
                env[k] = v
            i += 2
            continue
        if t in ("--scope", "-s"):
            i += 2
            continue
        pos.append(t)
        i += 1
    name = pos[0] if pos else ""
    if cmd:
        return {"name": name, "transport": "stdio", "command": cmd[0], "args": cmd[1:], "env": env}
    url = next((p for p in pos[1:] if re.match(r"^https?://", p)), "")
    return {"name": name, "transport": "http", "url": url, "headers": headers, "_sse": transport == "sse"}


def _mcp_remote(tokens: List[str]) -> Dict[str, Any]:
    url = next((t for t in tokens if re.match(r"^https?://", t)), "")
    headers = {}
    for i, t in enumerate(tokens):
        if t == "--header" and i + 1 < len(tokens):
            pair = _header_pair(tokens[i + 1].replace(":", ": ", 1) if ": " not in tokens[i + 1] else tokens[i + 1])
            if pair:
                headers[pair[0]] = pair[1]
    # mcp-remote جسر stdio→HTTP: نتصل بالرابط مباشرة بدلاً منه
    return {"transport": "http", "url": url, "headers": headers}


# ─────────────────────────── Python ───────────────────────────
_HTTP_CALLS = {"streamablehttp_client", "streamable_http_client", "sse_client", "client", "streamablehttptransport",
               "ssetransport", "mcpserverstreamablehttp", "mcpserversse", "mcpclient"}


def _parse_python(text: str, warnings: List[str]) -> List[Dict[str, Any]]:
    try:
        tree = compile(text, "<snippet>", "exec", flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError:
        return _python_regex(text)
    consts: Dict[str, Any] = {}

    def ev(node) -> Any:
        """قيمة ثابتة للعقدة بلا تنفيذ: نصوص، أرقام، قواميس، قوائم، f-strings، متغيّرات بسيطة،
        os.getenv/os.environ (تصير علامة «من البيئة»)."""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.JoinedStr):
            out = ""
            for part in node.values:
                if isinstance(part, ast.Constant):
                    out += str(part.value)
                else:
                    v = ev(part.value)
                    out += str(v) if v is not None else "⟨?⟩"
            return out
        if isinstance(node, ast.Name):
            return consts.get(node.id)
        if isinstance(node, ast.Dict):
            out = {}
            for k, v in zip(node.keys, node.values):
                kk = ev(k) if k is not None else None
                if isinstance(kk, str):
                    out[kk] = ev(v)
            return out
        if isinstance(node, (ast.List, ast.Tuple)):
            return [ev(e) for e in node.elts]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = ev(node.left), ev(node.right)
            return f"{a}{b}" if a is not None and b is not None else None
        if isinstance(node, ast.Call):
            fn = _call_name(node)
            if fn in ("getenv", "get") and node.args and isinstance(node.args[0], ast.Constant) \
                    and _is_environ(node.func):
                return _ENV_MARK.format(node.args[0].value)
            if fn == "dict":
                return {kw.arg: ev(kw.value) for kw in node.keywords if kw.arg}
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            key = node.slice.value if isinstance(node.slice, ast.Constant) else None
            return _ENV_MARK.format(key) if key else None
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = ev(node.value)
            if v is not None:
                consts[node.targets[0].id] = v
    # إعادة تمرير: متغيّرات تعتمد على متغيّرات عُرّفت بعدها في walk
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            v = ev(node.value)
            if v is not None:
                consts[node.targets[0].id] = v

    servers: List[Dict[str, Any]] = []
    shared_headers: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = _call_name(node)
        kw = {k.arg: ev(k.value) for k in node.keywords if k.arg}
        if fn in ("asyncclient", "client") and isinstance(kw.get("headers"), dict) and not node.args:
            shared_headers.update({str(k): str(v) for k, v in kw["headers"].items() if v is not None})
        if fn == "multiservermcpclient" and node.args:
            cfg = ev(node.args[0])
            if isinstance(cfg, dict):
                for name, spec in cfg.items():
                    if isinstance(spec, dict):
                        servers.append(_from_dict(spec, name))
            continue
        if fn == "stdioserverparameters":
            servers.append({"transport": "stdio", "command": kw.get("command") or (ev(node.args[0]) if node.args else ""),
                            "args": [str(a) for a in (kw.get("args") or []) if a is not None],
                            "env": {str(k): str(v) for k, v in (kw.get("env") or {}).items() if v is not None}})
            continue
        if isinstance(kw.get("params"), dict):
            servers.append(_from_dict(kw["params"], kw.get("name") or ""))
            continue
        url = kw.get("url") or (ev(node.args[0]) if node.args else None)
        if fn == "post" and isinstance(url, str) and url.startswith("http"):   # requests/httpx.post لواجهة REST
            body = kw.get("json")
            servers.append({"transport": "http",
                            "url": _rest_to_mcp(url, json.dumps(body) if isinstance(body, dict) else "", warnings),
                            "headers": {str(k): str(v) for k, v in (kw.get("headers") or {}).items() if v is not None}})
            continue
        if fn in _HTTP_CALLS and isinstance(url, str) and url.startswith("http"):
            servers.append({"transport": "http", "url": url,
                            "headers": {str(k): str(v) for k, v in (kw.get("headers") or {}).items() if v is not None},
                            "_sse": fn in ("sse_client", "ssetransport", "mcpserversse")})
    for node in ast.walk(tree):   # قواميس إعداد بلا دالة معروفة: {"url": …} / {"command": …}
        if isinstance(node, ast.Dict) and not servers:
            d = ev(node)
            if isinstance(d, dict) and (isinstance(d.get("url"), str) or isinstance(d.get("command"), str)):
                servers.append(_from_dict(d, ""))
    for s in servers:
        if s.get("transport") == "http" and shared_headers:
            s["headers"] = {**shared_headers, **(s.get("headers") or {})}
    # إزالة المكرّر (نفس الرابط/الأمر)
    seen, out = set(), []
    for s in servers:
        key = s.get("url") or (s.get("command"), tuple(s.get("args") or []))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def _from_dict(spec: Dict[str, Any], name: str) -> Dict[str, Any]:
    t = str(spec.get("transport") or spec.get("type") or "").lower()
    if spec.get("url"):
        return {"name": name, "transport": "http", "url": str(spec["url"]),
                "headers": {str(k): str(v) for k, v in (spec.get("headers") or {}).items() if v is not None},
                "_sse": t == "sse"}
    return {"name": name, "transport": "stdio", "command": str(spec.get("command") or ""),
            "args": [str(a) for a in (spec.get("args") or []) if a is not None],
            "env": {str(k): str(v) for k, v in (spec.get("env") or {}).items() if v is not None}}


def _call_name(node: ast.Call) -> str:
    f = node.func
    return (f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else "").lower()


def _is_environ(node) -> bool:
    src = ast.unparse(node) if hasattr(ast, "unparse") else ""
    return "environ" in src or "getenv" in src


def _python_regex(text: str) -> List[Dict[str, Any]]:
    """كود Python غير مكتمل (مقتطف من وسط ملف): الرابط + ترويسة Authorization إن وُجدت."""
    url = _URL_RE.search(text)
    if not url:
        return []
    headers = {}
    m = re.search(r"[\"']Authorization[\"']\s*:\s*[f]?[\"']([^\"']+)[\"']", text)
    if m:
        headers["Authorization"] = m.group(1)
    return [{"transport": "http", "url": url.group(0), "headers": headers}]
