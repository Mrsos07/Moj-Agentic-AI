"""Agent authority is installed by authentication or a server-owned runtime job."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import uuid

from app.aeos.security import ledger
from app.aeos.security.data import clean, validate


@dataclass(frozen=True)
class Principal:
    user_id: int
    agent: str
    conversation: str
    correlation: str


current = ContextVar("agent_security_principal", default=None)


def authenticated(user):
    """Install a fresh request identity immediately after successful bearer authentication."""
    current.set(Principal(user.id, "", "", uuid.uuid4().hex))


def bind(user_id, agent="", conversation=""):
    parent = current.get()
    if type(user_id) is not int or user_id < 1:
        raise PermissionError("authenticated owner required")
    if parent and (parent.user_id != user_id or (parent.agent and parent.agent != agent)):
        raise PermissionError("principal mismatch")
    return current.set(Principal(user_id, agent, conversation or (parent.conversation if parent else ""),
                                 parent.correlation if parent else uuid.uuid4().hex))


@contextmanager
def execution(user_id, agent="", conversation=""):
    token = bind(user_id, agent, conversation)
    try:
        yield
    finally:
        current.reset(token)


def _deny(action):
    p = current.get()
    # Rejected action names may be attacker-controlled. Only log a fixed reason.
    with ledger.transaction() as db:
        ledger.audit(db, p.correlation if p else uuid.uuid4().hex, p.user_id if p else None, action, "deny")
    raise PermissionError("رفضت بوابة أمن الوكلاء العملية")


def owner(agent=""):
    from app.db import database
    from app.db.models import AIEmployee, User
    from app.auth.roles import has

    p = current.get()
    if not p or (p.agent and agent != p.agent):
        _deny("principal")
    with database.SessionLocal() as db:
        if not has(db.get(User, p.user_id), "own.manage"):
            _deny("account")
        if agent:
            row = db.get(AIEmployee, agent)
            if not row or row.user_id != p.user_id or row.status == "archived":
                _deny("ownership")
            return row
    return None


def _permitted(p, agent, action):
    """Operator policy can narrow the registered tool/role grant in each resource dimension."""
    import fnmatch
    path = os.environ.get("AGENT_SECURITY_POLICY")
    if not path:
        return True
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8"))
        keys = {"tenant": str(p.user_id), "user": str(p.user_id),
                "agent": agent or "coordinator", "workspace": agent or "chat"}
        if not isinstance(policy, dict) or not policy or set(policy) - keys.keys():
            return False
        for dimension, entries in policy.items():
            grants = entries.get(keys[dimension], entries.get("*", []))
            if not isinstance(grants, list) or not any(
                    isinstance(pattern, str) and fnmatch.fnmatchcase(action, pattern) for pattern in grants):
                return False
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def decision(action, agent, arguments, *, sensitive=False):
    p = current.get()
    if not p or not _permitted(p, agent, action):
        _deny("action-policy")
    if clean(arguments) != arguments:
        _deny("credential")
    ledger.decide(p.user_id, agent, p.conversation, action, arguments, p.correlation, sensitive)


def chat(name, args, spec, agent=""):
    owner(agent)
    try:
        validate(args, spec)
    except Exception:
        _deny("chat-schema")
    sensitive = name in {"order_publish", "order_mission", "order_engage", "update_employee",
                         "clone_identity", "set_employee_skill", "add_employee_skill", "attach_tool",
                         "control_employee", "remember"}
    decision("chat." + name, agent, args, sensitive=sensitive)


def capability(name, context, arguments, required):
    agent = context.config.id
    row = owner(agent)
    if row.user_id != context.config.user_id or getattr(context.workspace, "employee_id", None) != agent:
        _deny("workspace")
    if not set(required).issubset(json.loads(row.mcp_servers_json or "[]")):
        _deny("capability-server")
    decision("capability." + name, agent, arguments)


_approved_x_calls = ContextVar("approved_x_calls", default=None)


@contextmanager
def x_batch(agent, label, tool, params):
    """Convey only the exact calls displayed in one human-approved batch to its worker."""
    from collections import Counter
    from threading import Lock
    import time
    row = owner(agent)
    if not row or label != (row.cookie_label or row.account_username) or not 1 <= len(params) <= 100:
        _deny("batch-account")
    action = "mcp.x." + tool
    if not _permitted(current.get(), agent, action):
        _deny("action-policy")
    decision("account.batch", agent, {"account": label, "tool": tool, "calls": params}, sensitive=True)
    principal = current.get()
    counts = Counter(ledger.fingerprint(item) for item in params)
    token = _approved_x_calls.set((principal, agent, label, tool, counts, Lock(), time.monotonic() + ledger.TTL))
    try:
        yield
    finally:
        _approved_x_calls.reset(token)


def _take_x_call(p, agent, label, tool, params):
    import time
    batch = _approved_x_calls.get()
    if not batch or batch[:4] != (p, agent, label, tool) or time.monotonic() >= batch[6]:
        return False
    key = ledger.fingerprint(params)
    with batch[5]:
        if not batch[4][key]:
            return False
        batch[4][key] -= 1
        return True


def mcp(server, request):
    p = current.get()
    row = owner(request.employee_id)
    name, tool = server.name, request.tool
    if tool not in server.tools or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,160}", tool):
        _deny("mcp-tool")
    cfg = getattr(server, "cfg", None)
    if row:
        attached = json.loads(row.mcp_servers_json or "[]")
        rules = json.loads(row.business_rules_json or "{}")
        if cfg is not None:
            if (not cfg.get("enabled", True) or not (cfg.get("scope") or {}).get("employees")
                    or name in rules.get("tools_opt_out", []) or (cfg.get("private") and
                    (cfg.get("employee_id") != row.id or name not in attached))):
                _deny("mcp-scope")
        elif name not in attached and not (name == "search" and rules.get("web_search", True)):
            _deny("mcp-server")
    else:
        from app.aeos.mcp.catalog import coordinator_builtin_tools
        if cfg is None:
            if coordinator_builtin_tools().get(tool, {}).get("server") != name:
                _deny("coordinator-mcp")
        elif cfg.get("private") or not cfg.get("enabled") or not (cfg.get("scope") or {}).get("coordinator"):
            _deny("coordinator-mcp")
    if cfg is None:
        from . import builtin
        try:
            builtin.check(name, tool, request.params)
        except (ValueError, TypeError):
            _deny("builtin-schema")
    if name in {"x", "playwright_x"}:
        from app.auth.ownership import label_owner_ids
        label = request.context.get("cookie_label")
        if (not isinstance(label, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", label) or "cookie_label" in request.params or label_owner_ids(label) != {p.user_id}
                or (row and label != (row.cookie_label or row.account_username))):
            _deny("browser-account")
        if tool in {"list_cookies", "stats", "list_scheduled_posts"}:
            _deny("global-account-data")
        if tool == "delete_tweet" and str(request.params.get("expected_handle") or "").casefold() != (row.account_username.casefold() if row else None):
            _deny("delete-owner")
        if tool in {"cancel_scheduled_post", "schedule_post"}:
            from app.x.modules.db import get_scheduled_post
            existing = get_scheduled_post(request.params["event_id"])
            if (existing and existing["cookie_label"] != label) or (tool == "cancel_scheduled_post" and not existing):
                _deny("scheduled-owner")
    sensitive = name == "x" or (name == "memory" and tool == "store")
    arguments = request.params
    if name == "x":
        arguments = {"account": label, "arguments": arguments}
        sensitive = not _take_x_call(p, request.employee_id, label, tool, request.params)
    decision("mcp." + name + "." + tool, request.employee_id, arguments, sensitive=sensitive)


def external(cfg, tool, args):
    p = current.get()
    agent = cfg.get("employee_id") if cfg.get("private") else (p.agent if p else "")
    row = owner(agent)
    if row:
        rules = json.loads(row.business_rules_json or "{}")
        registry_name = "ext_" + cfg["slug"]
        if registry_name in rules.get("tools_opt_out", []) or (cfg.get("private") and
                registry_name not in json.loads(row.mcp_servers_json or "[]")):
            _deny("external-attachment")
    spec = next((s for s in cfg.get("tools", []) if s.get("name") == tool), None)
    if (not (cfg.get("scope") or {}).get("employees" if agent else "coordinator")
            or (cfg.get("private") and (not p.agent or p.agent != cfg.get("employee_id")))):
        _deny("external-scope")
    if not spec or tool not in (cfg.get("allowed_tools") or []) or not cfg.get("enabled", True):
        _deny("external-allowlist")
    try:
        validate(args, spec.get("inputSchema"))
    except Exception:
        _deny("external-schema")
    # Third-party tool names and descriptions cannot attest to read-only behavior.
    import hashlib
    fingerprint = hashlib.sha256(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()
    decision("external." + cfg["slug"] + "." + tool, agent,
             {"arguments": args, "configuration": fingerprint}, sensitive=True)

def conversation(user_id, session_id, action, identifier=None):
    p = current.get()
    employee = owner(p.agent if p else "")
    if p.user_id != user_id or (employee and (not session_id or session_id not in {
            p.conversation, f"emp_{employee.account_username}"})):
        _deny("conversation-owner")
    decision("conversation." + action, p.agent, {"id": identifier, "session": session_id})


def memory(scopes, action, item=None):
    from app.db import database
    from app.db.models import Conversation

    if not scopes:
        _deny("memory-scope")
    p = current.get()
    for scope in scopes:
        kind, sep, identifier = scope.partition(":")
        if not sep or not identifier:
            _deny("memory-scope")
        if kind == "employee":
            owner(identifier)
        elif kind == "coordinator":
            owner()
            if identifier != str(p.user_id):
                _deny("memory-owner")
        elif kind == "conv" and identifier.isascii() and identifier.isdecimal():
            owner(p.agent if p else "")
            with database.SessionLocal() as db:
                conv = db.get(Conversation, int(identifier))
                if not conv or conv.user_id != p.user_id or (p.agent and (
                        not p.conversation or p.conversation != conv.session_id)):
                    _deny("conversation-owner")
        else:
            _deny("memory-scope")
    decision("memory." + action, p.agent, {"scopes": list(scopes), "item": item},
             sensitive=action == "delete")


def carry(fn):
    """Capture request authority when scheduling a single background invocation."""
    from contextvars import copy_context
    from functools import partial
    return partial(copy_context().run, fn)

def account(action, label, arguments, *, sensitive=False, user_id=None, creating=False):
    from app.auth.ownership import label_owner_ids
    p = current.get()
    row = owner(p.agent if p else "")
    owners = label_owner_ids(label)
    if ((user_id is not None and user_id != p.user_id) or
            (owners != {p.user_id} and not (creating and not owners)) or
            (row and label != (row.cookie_label or row.account_username))):
        _deny("account-owner")
    decision("account." + action, p.agent, {"account": label, **arguments}, sensitive=sensitive)
    if creating:
        ledger.reserve_accounts(p.user_id, [label.casefold()])

def api_decision(action, agent, args, *, sensitive=True):
    from fastapi import HTTPException
    try:
        owner(agent)
        decision(action, agent, args, sensitive=sensitive)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None

def login_accounts(user_id, accounts):
    from app.auth.ownership import label_owner_ids
    owner()
    p = current.get()
    if user_id != p.user_id or not 1 <= len(accounts) <= 100:
        _deny("login-owner")
    labels = []
    for item in accounts:
        label = item["username"].lower().lstrip("@")
        if not re.fullmatch(r"[a-z0-9_]{1,15}", label) or label_owner_ids(label) - {p.user_id}:
            _deny("login-account")
        labels.append(label)
    decision("account.login", "", {"accounts": labels, "input_digest": ledger.fingerprint(accounts)}, sensitive=True)
    ledger.reserve_accounts(user_id, labels)
