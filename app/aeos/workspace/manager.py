"""
Isolated per-employee workspace.

Every employee owns a private directory tree — files, downloads, logs,
screenshots, generated images, temp files, knowledge, browser cache/cookies and
encrypted secrets. There is NO shared storage: paths are always rooted under the
employee's own folder and path-traversal is rejected, enforcing isolation.
"""
from __future__ import annotations

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
        if not employee_id:
            raise IsolationViolationError("workspace requires an employee_id")
        self.employee_id = employee_id
        self._vault = vault
        self.root = _ROOT / employee_id
        self._ensure()

    def _ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in _SUBDIRS:
            (self.root / sub).mkdir(exist_ok=True)

    # ---- path safety ----------------------------------------------------
    def path(self, *parts: str) -> Path:
        """Resolve a path *within* this workspace, rejecting traversal."""
        target = (self.root.joinpath(*parts)).resolve()
        if self.root.resolve() not in target.parents and target != self.root.resolve():
            raise IsolationViolationError(
                f"path escapes workspace of employee {self.employee_id}: {target}"
            )
        return target

    # ---- generic files --------------------------------------------------
    def write_text(self, relative: str, content: str) -> Path:
        p = self.path(relative)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def read_text(self, relative: str) -> Optional[str]:
        p = self.path(relative)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def list_files(self, subdir: str = "files") -> List[str]:
        base = self.path(subdir)
        if not base.exists():
            return []
        return [str(f.relative_to(self.root)) for f in base.rglob("*") if f.is_file()]

    def append_log(self, line: str) -> None:
        log = self.path("logs", "activity.log")
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")

    # ---- encrypted secrets ---------------------------------------------
    def store_secret(self, name: str, value: Any) -> None:
        """Encrypt and persist a secret (cookie/token/api key/env var)."""
        enc = self._vault.encrypt_obj(value)
        self.path("secrets", f"{name}.enc").write_text(enc, encoding="utf-8")

    def load_secret(self, name: str) -> Optional[Any]:
        p = self.path("secrets", f"{name}.enc")
        if not p.exists():
            return None
        return self._vault.decrypt_obj(p.read_text(encoding="utf-8"))

    def has_secret(self, name: str) -> bool:
        return self.path("secrets", f"{name}.enc").exists()

    # ---- knowledge ------------------------------------------------------
    def store_knowledge(self, key: str, content: str) -> None:
        self.path("knowledge", f"{key}.md").write_text(content, encoding="utf-8")

    def read_knowledge(self, key: str) -> Optional[str]:
        p = self.path("knowledge", f"{key}.md")
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
