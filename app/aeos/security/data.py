"""Bounded, inert data at the model, memory and tool boundaries."""
from __future__ import annotations

import json
import re

from jsonschema import Draft202012Validator
from referencing import Registry
from app.core.log_redaction import redact

_SECRET = re.compile(r"(?i)(?:password|passwd|api[_-]?key|authorization|cookie|secret|auth_token|ct0|access_token|refresh_token|private_key)")
_FIELD = re.compile(r"""(?i)(["']?(?:password|passwd|api[_-]?key|authorization|cookie|secret|auth_token|ct0|access_token|refresh_token)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^,\s;}]+)""")
_PEM = re.compile(r"-----BEGIN [^\n]*PRIVATE KEY-----.*?(?:-----END [^\n]*PRIVATE KEY-----|$)", re.S)
_RESERVED = {"scope", "tenant_id", "workspace_id", "user_id", "agent_id", "employee_id", "conversation_id", "cookie_label"}
_KEYWORDS = {"type", "properties", "items", "required", "additionalProperties", "enum", "const",
             "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
             "minItems", "maxItems", "minProperties", "maxProperties", "description", "title", "default",
             "examples", "anyOf", "oneOf", "allOf", "nullable", "$schema", "format"}


def clean(value):
    if isinstance(value, str):
        return redact(_PEM.sub("[redacted]", _FIELD.sub(r"\1[redacted]", redact(value))))
    if isinstance(value, dict):
        return {k: "[redacted]" if _SECRET.fullmatch(str(k)) or (isinstance(v, str) and _SECRET.search(str(k))) else clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def evidence(kind, value):
    return {"role": "user", "content": json.dumps({"untrusted_data": kind, "value": clean(value)},
                                                   ensure_ascii=False, default=str)}


def _inspect(value, depth=0):
    if depth > 12:
        raise ValueError("tool input too deep")
    if isinstance(value, dict):
        if _RESERVED.intersection(value):
            raise ValueError("identity parameters are server-owned")
        for item in value.values():
            _inspect(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _inspect(item, depth + 1)


def checked_schema(schema, depth=0):
    if not isinstance(schema, dict) or depth > 8 or set(schema) - _KEYWORDS:
        raise ValueError("unsupported tool schema")
    result = {k: v for k, v in schema.items() if k not in {"description", "title", "default", "examples", "$schema"}}
    if "properties" in schema:
        result["properties"] = {k: checked_schema(v, depth + 1) for k, v in schema["properties"].items()}
    for k in ("additionalProperties", "items"):
        if isinstance(schema.get(k), dict):
            result[k] = checked_schema(schema[k], depth + 1)
    for k in ("anyOf", "allOf", "oneOf"):
        if k in schema:
            if depth >= 3 or len(schema[k]) > 8:
                raise ValueError("complex tool schema")
            result[k] = [checked_schema(v, depth + 1) for v in schema[k]]
    return result


def validate(arguments, schema):
    if not isinstance(arguments, dict) or len(json.dumps(arguments, allow_nan=False)) > 131072:
        raise ValueError("invalid tool arguments")
    _inspect(arguments)
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("missing object schema")
    if len(json.dumps(schema)) > 65536:
        raise ValueError("tool schema too large")
    safe = checked_schema(schema)
    safe.setdefault("additionalProperties", False)
    Draft202012Validator.check_schema(safe)
    if not Draft202012Validator(safe, registry=Registry()).is_valid(arguments):
        raise ValueError("tool arguments do not match the approved schema")
    if clean(arguments) != arguments:
        raise ValueError("credentials in tool input")


def tool_output(value, cfg):
    secrets = set()
    for group in ("headers", "env", "query_secrets"):
        for key, val in (cfg.get(group) or {}).items():
            if val and (group != "env" or _SECRET.search(key) or "token" in key.lower() or "key" in key.lower()):
                secrets.add(str(val))
                if str(val).lower().startswith("bearer "):
                    secrets.add(str(val)[7:])
    import base64
    from urllib.parse import quote
    for secret in tuple(secrets):
        raw = secret.encode()
        secrets.update((quote(secret, safe=""), raw.hex(), base64.b64encode(raw).decode(),
                        base64.urlsafe_b64encode(raw).decode().rstrip("=")))
    def scrub(item):
        if isinstance(item, list):
            return [scrub(v) for v in item]
        if isinstance(item, dict):
            return {scrub(k): scrub(v) for k, v in item.items()}
        if isinstance(item, str):
            for secret in sorted(secrets, key=len, reverse=True):
                if secret:
                    item = item.replace(secret, "[redacted]")
        return item
    return clean(scrub(value))
