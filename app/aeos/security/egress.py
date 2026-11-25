"""Direct, pinned connections for untrusted browser and MCP destinations."""
from __future__ import annotations

import ipaddress
import os
import socket
import time
from urllib.parse import urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter


def destination(url: str):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 33 or c == "\\" for c in url):
        raise ValueError("invalid destination")
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None or parts.fragment:
        raise ValueError("public HTTP(S) destination required")
    host = parts.hostname.encode("idna").decode("ascii")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if "%" in host or port not in {80, 443}:
        raise ValueError("destination outside egress policy")
    allowed = os.getenv("AGENT_EGRESS_HOSTS", "").strip()
    if allowed and host.lower() not in {name.strip().lower() for name in allowed.split(",")}:
        raise ValueError("host outside egress policy")
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for _, _, _, _, endpoint in addresses:
        ip = ipaddress.ip_address(endpoint[0])
        if not ip.is_global or ip.is_multicast or (ip.version == 6 and (
                ip.ipv4_mapped or ip.sixtofour or ip.teredo or ip in ipaddress.ip_network("64:ff9b::/96"))):
            raise ValueError("non-public destination")
    if not addresses:
        raise ValueError("unresolved destination")
    return parts, host, addresses[0][4][0]


class _TLSAdapter(HTTPAdapter):
    def __init__(self, hostname):
        self.hostname = hostname
        super().__init__(max_retries=0)

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, server_hostname=self.hostname, assert_hostname=self.hostname, **kwargs)


def public_request(method, url, *, headers=None, **kwargs):
    parts, host, ip = destination(url)
    # Connect only to the vetted numeric address. SNI and certificate checks use the DNS name.
    literal = f"[{ip}]" if ":" in ip else ip
    port = f":{parts.port}" if parts.port else ""
    path = urlunsplit((parts.scheme, literal + port, parts.path, parts.query, ""))
    headers = requests.structures.CaseInsensitiveDict(headers or {})
    if any(key in headers for key in ("Host", "Proxy-Authorization", "Transfer-Encoding", "Content-Length", "Connection")):
        raise ValueError("unsafe transport header")
    if parts.scheme == "http" and (parts.query or any(k.lower() not in {
            "accept", "user-agent", "content-type", "mcp-protocol-version"} for k in headers)):
        raise ValueError("HTTPS required for credentials or session data")
    headers["Host"] = (f"[{host}]" if ":" in host else host) + port
    headers["Accept-Encoding"] = "identity"
    with requests.Session() as session:
        session.trust_env = False
        if parts.scheme == "https":
            session.mount("https://", _TLSAdapter(host))
        return session.request(method, path, headers=headers, allow_redirects=False, **kwargs)


def read_page(url, limit=1_048_576):
    deadline = time.monotonic() + 25
    with public_request("GET", url, timeout=(4, 8), stream=True) as response:
        if not 200 <= response.status_code < 300:
            raise ValueError("page unavailable")
        data = bytearray()
        for chunk in response.iter_content(16384):
            if time.monotonic() >= deadline:
                raise ValueError("page deadline exceeded")
            if len(data) + len(chunk) > limit:
                raise ValueError("page too large")
            data.extend(chunk)
        return bytes(data)
