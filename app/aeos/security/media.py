"""Private generated media exchanged with the internal X service by scoped reference."""
import base64
from io import BytesIO
import json
from pathlib import Path
import re
import uuid

from . import gateway, sealed

_ROOT = Path(__file__).resolve().parents[3] / "data" / "agent_media"
_ORIGIN = "https://media.moj.invalid/"
_REF = re.compile(r"https://media[.]moj[.]invalid/([1-9][0-9]{0,18})/([a-zA-Z0-9_-]{1,64})/([a-f0-9]{32})[.]png")


def save(agent, encoded):
    employee = gateway.owner(agent)
    if not employee or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", agent) or len(encoded) > 28_000_000:
        raise ValueError("invalid image")
    gateway.decision("media.save", agent, {})
    raw = base64.b64decode(encoded, validate=True)
    if not raw.startswith((b"\x89PNG", b"\xff\xd8\xff", b"RIFF")):
        raise ValueError("unexpected image format")
    directory = _ROOT / str(employee.user_id) / agent
    directory.mkdir(parents=True, exist_ok=True)
    name = uuid.uuid4().hex
    with (directory / (name + ".json")).open("x", encoding="ascii") as fh:
        json.dump(sealed.envelope(encoded), fh)
    existing = []
    for entry in directory.glob("*.json"):
        try:
            existing.append((entry.stat().st_mtime_ns, entry))
        except FileNotFoundError:
            continue
    for _, old in sorted(existing, reverse=True)[60:]:
        old.unlink(missing_ok=True)
    return f"{_ORIGIN}{employee.user_id}/{agent}/{name}.png"


def read(ref, label=None):
    from app.auth.ownership import label_owner_ids
    from app.db import database
    from app.db.models import AIEmployee
    match = _REF.fullmatch(ref)
    if not match:
        raise ValueError("invalid media reference")
    uid, agent, name = match.groups()
    uid = int(uid)
    if label is not None:
        # xsuite receives the target account from its authenticated supervisor.
        with database.SessionLocal() as db:
            row = db.get(AIEmployee, agent)
            if (not row or row.user_id != uid or (row.cookie_label or row.account_username) != label
                    or label_owner_ids(label) != {uid}):
                raise PermissionError("media account mismatch")
    else:
        p = gateway.current.get()
        if not p or (p.user_id, p.agent) != (uid, agent):
            raise PermissionError("media principal mismatch")
    with gateway.execution(uid, agent):
        gateway.owner(agent)
        gateway.decision("media.read", agent, {"asset": name})
        data = sealed.read(_ROOT / str(uid) / agent / (name + ".json"), "")
        if not data:
            raise FileNotFoundError("media unavailable")
        return BytesIO(base64.b64decode(data, validate=True))
