"""Persistent envelopes for MCP settings, including legacy plaintext reads."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.aeos.security.vault import FernetVault


def _vault():
    from app.auth.security import ENCRYPTION_KEY
    return FernetVault(ENCRYPTION_KEY, os.getenv("AEOS_PREVIOUS_ENCRYPTION_KEYS", "").split(","))


def read(path: Path, default):
    if not path.exists():
        return default
    obj = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(obj, dict) and obj.get("format") == "moj-fernet-v1":
        return _vault().decrypt_obj(obj["ciphertext"])
    return obj


def envelope(value):
    return {"format": "moj-fernet-v1", "ciphertext": _vault().encrypt_obj(value)}


def load_browser_state(path):
    path = Path(path)
    state = read(path, None)
    if isinstance(state, dict) and "browser_account" in state:
        if state["browser_account"] != path.stem.casefold():
            raise PermissionError("browser account mismatch")
        return state["state"]
    if state is None:
        raise FileNotFoundError("browser state unavailable")
    return state  # Existing installations can read their original state during migration.


def save_browser_state(path, state, *, replace=True):
    path = Path(path)
    data = envelope({"browser_account": path.stem.casefold(), "state": state})
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        if replace:
            os.replace(name, path)
        else:
            os.link(name, path)  # Atomically refuse a competing claim to the same label.
    finally:
        Path(name).unlink(missing_ok=True)
