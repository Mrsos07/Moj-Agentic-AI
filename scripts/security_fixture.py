"""Authenticated principals and explicit human consent for offline integration fixtures."""
from pathlib import Path
import tempfile
from unittest.mock import patch

from app.aeos.security import gateway, ledger
from app.db import database
from app.db.models import User, AIEmployee
from app.db.testing import use_temp_database


def setup(user_id, employees=(), *, create_db=True):
    root = Path(tempfile.mkdtemp(prefix="moj-verify-security-"))
    ledger._PATH = root / "ledger.db"
    if create_db:
        use_temp_database(root)
    with database.SessionLocal() as db:
        if not db.get(User, user_id):
            db.add(User(id=user_id, email=f"owner{user_id}@example.com", password_hash="fixture"))
            db.flush()
        for eid, handle in employees:
            if not db.get(AIEmployee, eid):
                db.add(AIEmployee(id=eid, user_id=user_id, account_username=handle,
                                  mcp_servers_json='["search"]'))
        db.commit()
    return gateway.bind(user_id)


def reviewed_fixture_actions(user_id, actions):
    """For scripted success cases, simulate a reviewer of the specified action types."""
    decide = ledger.decide

    def reviewed(owner, agent, conversation, action, args, correlation, sensitive):
        try:
            return decide(owner, agent, conversation, action, args, correlation, sensitive)
        except PermissionError:
            if not sensitive or owner != user_id or action not in actions:
                raise
            for cid in ledger.pending(owner):
                preview = ledger.view(owner, cid)
                if (preview["action"], preview["agent"], preview["conversation"], preview["arguments"]) == (
                        action, agent, conversation, args):
                    ledger.approve(owner, cid, preview["digest"])
                    return decide(owner, agent, conversation, action, args, correlation, sensitive)
            raise

    return patch.object(ledger, "decide", reviewed)
