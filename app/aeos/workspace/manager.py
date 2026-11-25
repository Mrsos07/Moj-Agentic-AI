"""
Isolated per-employee workspace.

Every employee owns a private directory tree — files, downloads, logs,
screenshots, generated images, temp files, knowledge, browser cache/cookies and
encrypted secrets. There is NO shared storage: paths are always rooted under the
employee's own folder and path-traversal is rejected, enforcing isolation.
"""
from __future__ import annotations

import re
import json
import logging
from pathlib import Path
from typing import Any, List, Optional

from ..core.exceptions import IsolationViolationError
from ..security.vault import ISecretVault

logger = logging.getLogger("aeos.workspace")

# All employee workspaces live under data/employees/<employee_id>/
_ROOT = Path(__file__).resolve().parents[3] / "data" / "employees"

_SUBDIRS = (
    "files", "downloads", "logs", "screenshots", "images",
    "tmp", "knowledge", "browser_cache", "cookies", "secrets",
)


class Workspace:
    """A single employee's sandboxed workspace."""

    def __init__(self, employee_id: str, vault: ISecretVault) -> None:
        if not isinstance(employee_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", employee_id):
            raise IsolationViolationError("workspace requires an employee_id")
        self.employee_id = employee_id
        self._vault = vault
        self.root = _ROOT / employee_id
        if self.root.resolve().parent != _ROOT.resolve() or self.root.is_symlink():
            raise IsolationViolationError("invalid workspace root")
        self._ensure()

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in _SUBDIRS:
            self.path(sub).mkdir(exist_ok=True)

    # ---- path safety ----------------------------------------------------
    def path(self, *parts: str) -> Path:
        """Resolve a path *within* this workspace, rejecting traversal."""
        target = (self.root.joinpath(*parts)).resolve()
        if self.root.resolve() not in target.parents and target != self.root.resolve():
            raise IsolationViolationError(
                f"path escapes workspace of employee {self.employee_id}: {target}"
            )
        return target

    def _authorize(self, action, arguments=None, sensitive=False):
        from ..security import gateway
        gateway.owner(self.employee_id)
        gateway.decision("workspace." + action, self.employee_id, arguments or {}, sensitive=sensitive)

    # ---- generic files --------------------------------------------------
    def write_text(self, relative: str, content: str) -> Path:
        from ..security.data import clean
        self._authorize("write", {"path": relative})
        content = clean(content)
        p = self._document(relative)
        if len(content.encode("utf-8")) > 2_000_000:
            raise ValueError("workspace file too large")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def _document(self, relative: str) -> Path:
        p = self.path(relative)
        if p.relative_to(self.root.resolve()).parts[:1] not in [
                (d,) for d in ("files", "downloads", "logs", "images", "screenshots", "tmp", "knowledge")]:
            raise IsolationViolationError("workspace file outside document directories")
        return p

    def read_text(self, relative: str) -> Optional[str]:
        self._authorize("read", {"path": relative})
        p = self._document(relative)
        if p.exists() and p.stat().st_size > 2_000_000:
            raise ValueError("workspace file too large")
        return p.read_text(encoding="utf-8") if p.exists() else None

    def list_files(self, subdir: str = "files") -> List[str]:
        self._authorize("list", {"directory": subdir})
        base = self._document(subdir)
        if not base.exists():
            return []
        return [str(f.relative_to(self.root)) for f in base.rglob("*") if f.is_file()]

    def append_log(self, line: str) -> None:
        from ..security.data import clean
        self._authorize("log")
        line = clean(line)[:8192]
        log = self.path("logs", "activity.log")
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")

    # ---- encrypted secrets ---------------------------------------------
    def _entry(self, directory, name, suffix):
        if not isinstance(name, str) or not re.fullmatch(r"[\w.-]{1,160}", name) or name in {".", ".."}:
            raise IsolationViolationError("invalid workspace entry")
        path = self.path(directory, name + suffix)
        if path.parent != (self.root / directory).resolve():
            raise IsolationViolationError("invalid workspace entry")
        return path

    def store_secret(self, name: str, value: Any) -> None:
        """Encrypt and persist a secret (cookie/token/api key/env var)."""
        from ..security.ledger import fingerprint
        self._authorize("secret.store", {"name": name, "value_digest": fingerprint(value)}, sensitive=True)
        enc = self._vault.encrypt_obj(value)
        self._entry("secrets", name, ".enc").write_text(enc, encoding="utf-8")

    def load_secret(self, name: str) -> Optional[Any]:
        self._authorize("secret.read", {"name": name}, sensitive=True)
        p = self._entry("secrets", name, ".enc")
        if not p.exists():
            return None
        return self._vault.decrypt_obj(p.read_text(encoding="utf-8"))

    def has_secret(self, name: str) -> bool:
        self._authorize("secret.exists", {"name": name})
        return self._entry("secrets", name, ".enc").exists()

    # ---- knowledge ------------------------------------------------------
    def store_knowledge(self, key: str, content: str) -> None:
        from ..security.data import clean
        from ..security.ledger import fingerprint
        content = clean(content)
        if len(content.encode()) > 2_000_000:
            raise ValueError("knowledge file too large")
        self._authorize("knowledge.write", {"key": key, "digest": fingerprint(content)}, sensitive=True)
        self._entry("knowledge", key, ".md").write_text(content, encoding="utf-8")

    def read_knowledge(self, key: str) -> Optional[str]:
        self._authorize("knowledge.read", {"key": key})
        p = self._entry("knowledge", key, ".md")
        if p.exists() and p.stat().st_size > 2_000_000:
            raise ValueError("knowledge file too large")
        return p.read_text(encoding="utf-8") if p.exists() else None


class WorkspaceManager:
    """Factory/registry for employee workspaces (DI-friendly)."""

    def __init__(self, vault: ISecretVault) -> None:
        self._vault = vault

    def get(self, employee_id: str) -> Workspace:
        return Workspace(employee_id, self._vault)

    def destroy_tmp(self, employee_id: str) -> None:
        """Clear only the temp area — called when a runtime is destroyed."""
        ws = self.get(employee_id)
        tmp = ws.path("tmp")
        for item in tmp.glob("*"):
            try:
                if item.is_file():
                    item.unlink()
                else:
                    import shutil

                    shutil.rmtree(item, ignore_errors=True)
            except Exception:  # noqa: BLE001
                logger.debug("failed to clean tmp item %s", item)
