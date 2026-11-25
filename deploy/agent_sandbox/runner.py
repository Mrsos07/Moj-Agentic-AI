"""PID 1. Serve one MCP operation then exit, destroying every process in this container."""
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import threading

LIMIT = 8 * 1024 * 1024
ROOT = Path("/tmp/agent")
STATE = ROOT / "state" / "clones"


def receive(stream, limit=LIMIT):
    line = stream.readline(limit + 1)
    if not line or len(line) > limit or not line.endswith(b"\n"):
        raise ValueError("invalid frame")
    return json.loads(line)


def send(stream, obj):
    line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    if len(line) > LIMIT:
        raise ValueError("frame too large")
    stream.write(line + b"\n")
    stream.flush()


def owner_directory(path):
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, 65534, 65534)


def clone_name(name):
    return isinstance(name, str) and re.fullmatch(r"[\w .@-]{1,150}\.json", name)


def run(source, sink):
    request = receive(source)
    signal.alarm(min(600, max(5, int(request["timeout"]))))
    packages = ROOT / "package"
    packages.mkdir(parents=True)
    for filename, data in request["files"].items():
        path = (packages / filename).resolve()
        if not path.is_relative_to(packages) or path == packages:
            raise ValueError("invalid package path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data, validate=True))
    entry = (packages / request["entry"]).resolve()
    if not entry.is_file() or not entry.is_relative_to(packages):
        raise ValueError("invalid entry")
    cmd = {".py": ["/opt/mcp/bin/python", "-B"], ".mjs": ["node"], ".js": ["node"]}[entry.suffix]
    STATE.mkdir(parents=True)
    for name, data in request.get("state", {}).items():
        if not clone_name(name):
            raise ValueError("invalid state")
        path = STATE / name
        path.write_bytes(base64.b64decode(data, validate=True))
        os.chown(path, 65534, 65534)
    owner_directory(STATE)
    owner_directory(ROOT / "state")
    env = {**request.get("env", {}), "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(ROOT / "state"),
           "NAWAH_DATA_DIR": str(STATE.parent), "PYTHONIOENCODING": "utf-8"}
    if request.get("model_relay"):
        class Relay(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                size = int(self.headers.get("Content-Length", "-1"))
                if self.path != "/v1/chat/completions" or not 0 < size <= 512_000:
                    return self.send_error(403)
                send(sink, {"kind": "model", "body": json.loads(self.rfile.read(size))})
                reply = receive(source)
                body = json.dumps(reply["body"]).encode()
                self.send_response(reply["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        relay = HTTPServer(("127.0.0.1", 0), Relay)
        threading.Thread(target=relay.serve_forever, daemon=True).start()
        env.update(NAWAH_LLM_BASE_URL=f"http://127.0.0.1:{relay.server_port}/v1", NAWAH_LLM_API_KEY="relay")
    proc = subprocess.Popen([*cmd, str(entry), *request.get("args", [])], cwd=packages, env=env,
                            user=65534, group=65534, extra_groups=[], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    counter = 0

    def rpc(method, params=None, notification=False):
        nonlocal counter
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notification:
            counter += 1
            msg["id"] = counter
        send(proc.stdin, msg)
        if notification:
            return
        for _ in range(128):
            reply = receive(proc.stdout)
            if reply.get("id") == counter:
                if "error" in reply or "result" not in reply:
                    raise ValueError("MCP rejected operation")
                return reply["result"]
        raise ValueError("excess MCP notifications")

    info = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "moj", "version": "1"}})
    if "capabilities" not in info:
        raise ValueError("invalid MCP initialization")
    rpc("notifications/initialized", notification=True)
    method = request["method"]
    if method == "tools/list":
        result = []
        cursor = None
        for _ in range(10):
            batch = rpc(method, {"cursor": cursor} if cursor else {})
            result.extend(batch["tools"])
            cursor = batch.get("nextCursor")
            if not cursor:
                break
    elif method == "tools/call":
        result = rpc(method, request["params"])
    else:
        raise ValueError("unknown method")
    proc.terminate()
    proc.wait(timeout=3)
    snapshot = {}
    if request.get("model_relay") and STATE.resolve() == STATE:
        total = 0
        for path in STATE.iterdir():
            if clone_name(path.name) and stat.S_ISREG(path.lstat().st_mode):
                with open(
                        path, "rb", opener=lambda p, flags: os.open(p, flags | os.O_NOFOLLOW)) as fh:
                    data = fh.read(2_000_001)
                total += len(data)
                if total > 2_000_000 or len(snapshot) >= 128:
                    raise ValueError("state budget exceeded")
                snapshot[path.name] = base64.b64encode(data).decode()
    send(sink, {"kind": "done", "result": result, "info": info, "state": snapshot})


def serve():
    if os.getpid() != 1:
        raise RuntimeError("runner requires a private container PID namespace")
    if "--pipe" in sys.argv:
        run(sys.stdin.buffer, sys.stdout.buffer)
        return
    path = Path("/run/moj-agent/runner.sock")
    path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        os.chmod(path, 0o600)
        os.chown(path, 1000, 1000)
        listener.listen(1)
        conn, _ = listener.accept()
        with conn:
            signal.alarm(15)
            run(conn.makefile("rb"), conn.makefile("wb"))


if __name__ == "__main__":
    try:
        serve()
    except Exception as exc:
        import traceback
        line = traceback.extract_tb(exc.__traceback__)[-1].lineno
        print(f"sandbox failure: {type(exc).__name__} at line {line}", file=sys.stderr)
        sys.exit(1)
