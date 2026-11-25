"""Egress and per-context request limits for the authenticated X browser."""
from urllib.parse import urlsplit

_HOSTS = ("x.com", "twitter.com", "twimg.com")


def permitted(url, websocket=False):
    try:
        parts = urlsplit(url)
        return (parts.scheme == ("wss" if websocket else "https") and parts.username is None
                and parts.port in {443, None} and bool(parts.hostname)
                and any(parts.hostname == host or parts.hostname.endswith("." + host) for host in _HOSTS))
    except (ValueError, UnicodeError):
        return False


def restrict(context):
    count = 0

    def admit(url, websocket=False):
        nonlocal count
        count += 1
        return count <= 4096 and permitted(url, websocket)

    def http(route):
        if admit(route.request.url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    def socket(route):
        if admit(route.url, websocket=True):
            route.connect_to_server()
        else:
            route.close(code=1008, reason="destination denied")

    try:
        context.route("**/*", http)
        context.route_web_socket("**/*", socket)
    except Exception:
        context.close()
        raise
    return context
