"""Run with a dedicated Compose test project; exercise the production Unix socket."""
import base64
import json
from pathlib import Path
import os
import secrets
import subprocess
import uuid

PROJECT = "moj-security-test-" + uuid.uuid4().hex[:10]
COMPOSE = ["docker", "compose", "-p", PROJECT, "--env-file", ".env.docker.example"]

CHILD = """
import json, os, sys
from pathlib import Path
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg["method"] == "initialize":
        out = {"capabilities": {"tools": {}}, "protocolVersion": "2025-06-18"}
    else:
        marker = Path("/tmp/previous-tenant")
        out = {"uid": os.geteuid(), "devices": os.listdir("/sys/class/net"),
               "app_visible": Path("/app/data").exists(),
               "retained": marker.exists(), "secret_inherited": "JWT_SECRET_KEY" in os.environ}
        marker.write_text("fixture")
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": out}), flush=True)
"""
CLIENT = """
import json,socket,sys,time
data = sys.stdin.buffer.read()
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(10)
        s.connect("/run/moj-agent/runner.sock")
        break
    except (ConnectionRefusedError,FileNotFoundError):
        s.close()
        time.sleep(.25)
else:
    raise RuntimeError("sandbox unavailable")
s.sendall(data)
print(s.makefile("r").readline())
"""


def run():
    os.environ.setdefault("POSTGRES_PASSWORD", secrets.token_hex(24))
    frame = {"method": "tools/call", "params": {"name": "report", "arguments": {}},
             "entry": "fixture.py", "files": {"fixture.py": base64.b64encode(CHILD.encode()).decode()},
             "timeout": 20}
    try:
        subprocess.run(COMPOSE + ["up", "-d", "--no-deps", "agent-sandbox"], check=True)
        for _ in range(2):
            cmd = ["docker", "run", "--rm", "-i", "--network=none", "--read-only", "--user=1000:1000",
                   "--cap-drop=ALL", "-v", PROJECT + "_moj_agent_socket:/run/moj-agent:ro",
                   "--entrypoint=python3", "moj-agent-sandbox:local", "-I", "-c", CLIENT]
            output = subprocess.run(cmd, input=(json.dumps(frame) + "\n").encode(),
                                    stdout=subprocess.PIPE, check=True, timeout=40)
            result = json.loads(output.stdout)["result"]
            assert result == {"uid": 65534, "devices": ["lo"], "app_visible": False,
                              "retained": False, "secret_inherited": False}, result
        print("Two Docker sessions passed filesystem, environment, network and lifetime checks.")
    finally:
        subprocess.run(COMPOSE + ["down", "-v"], check=True)


if __name__ == "__main__":
    run()
