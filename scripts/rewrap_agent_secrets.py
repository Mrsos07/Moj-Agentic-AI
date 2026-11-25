"""Re-encrypt existing agent secret files during an offline maintenance window."""
import json
from pathlib import Path
import sys
import tempfile
import os

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
from app.aeos.security import sealed


def replace_envelope(path):
    value = sealed.envelope(sealed.read(path, None))
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            json.dump(value, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main():
    from app.aeos.mcp import employee_mcp
    from app.aeos.mcp.servers.external_mcp import _SECRETS_PATH
    stores = [(_SECRETS_PATH.parent, _SECRETS_PATH.name), (employee_mcp._DIR, "*.json"),
              (ROOT / "data/agent_sandbox", "*.json"), (ROOT / "data/agent_media", "*/*/*.json")]
    count = 0
    for directory, pattern in stores:
        for path in directory.glob(pattern):
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("unexpected secret path")
            replace_envelope(path)
            count += 1
    for directory in ("app/x/cookies", "app/x/loginx/CookiesBackup"):
        for path in (ROOT / directory).glob("*.json"):
            if path.is_symlink() or path.stat().st_nlink != 1:
                raise ValueError("linked session file")
            sealed.save_browser_state(path, sealed.load_browser_state(path))
            count += 1
    print(f"Re-encrypted {count} files.")


if __name__ == "__main__":
    main()
