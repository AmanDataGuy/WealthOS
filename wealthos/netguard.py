"""
# netguard.py - the one place that decides which hosts the server may reach for a user (feeds, database connections)

A user-supplied host is resolved and refused unless every address it resolves to is public (`is_global`), so a URL or
connection string can't point the server at itself, the cloud metadata endpoint, the private network or the app's own
database (which may well be public, e.g. Supabase: that is why it is checked by name and address, not by range).
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

BLOCKED_NAMES = {"localhost", "wealthos-db", "wealthos-api", "wealthos-web", "metadata.google.internal"}
NOT_ALLOWED = "that host is not allowed (it points at a private, local or internal address)"


# The addresses `host` resolves to; ValueError if it can't be resolved or any address is not public.
def public_ips(host: str, port: int) -> list[str]:
    host = (host or "").strip().lower().rstrip(".")
    if not host or host in BLOCKED_NAMES:
        raise ValueError(NOT_ALLOWED)
    try:
        ips = sorted({info[4][0].split("%")[0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})
    except (socket.gaierror, UnicodeError):
        raise ValueError(f"can't find the host {host}") from None
    if not ips or any(not ipaddress.ip_address(a).is_global for a in ips):
        raise ValueError(NOT_ALLOWED)
    return ips


# ValueError if `host` is the app's own database (WEALTHOS_DB_URL), by name or by any shared address.
def refuse_own_db(host: str, ips: list[str]) -> None:
    own = urlsplit(os.getenv("WEALTHOS_DB_URL", "")).hostname
    if not own:
        return
    try:
        own_ips = {info[4][0].split("%")[0] for info in socket.getaddrinfo(own, None)}
    except (socket.gaierror, UnicodeError):
        own_ips = set()
    if host.strip().lower().rstrip(".") == own.lower() or own_ips & set(ips):
        raise ValueError("that host is not allowed (it is this server's own database)")
