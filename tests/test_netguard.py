# A workspace database connection can't reach this server, its network, the metadata endpoint or our own database
# (the connector could otherwise ATTACH the app's own Postgres and read the users table).
import socket

import pytest

from wealthos import connections, netguard

PUBLIC = "93.184.216.34"


# Fake DNS: names ending in .private resolve to 10.0.0.7, everything else to PUBLIC; IP literals resolve to themselves.
def fake_dns(host, port, *a, **k):
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host)
        ip = host
    except OSError:
        ip = "10.0.0.7" if host.endswith(".private") else PUBLIC
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]


@pytest.fixture(autouse=True)
def no_dns(monkeypatch):
    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake_dns)
    monkeypatch.delenv("WEALTHOS_DB_URL", raising=False)
    monkeypatch.delenv("WEALTHOS_ALLOW_PRIVATE_DB", raising=False)
    monkeypatch.delenv("WEALTHOS_ENV", raising=False)


@pytest.mark.parametrize("url", [
    "postgresql://u:p@127.0.0.1:5432/x", "postgresql://u:p@localhost/x", "postgresql://u:p@10.0.0.5/x",
    "postgresql://u:p@169.254.169.254/x", "postgresql://u:p@[::1]/x", "postgresql://u:p@wealthos-db:5432/wealthos",
    "mysql://u:p@192.168.1.10/x", "postgresql://u:p@db.private/x", "postgresql://u:p@db.example.com:22/x",
    # libpq query parameters that would override the checked host
    "postgresql://u:p@db.example.com/x?host=127.0.0.1", "postgresql://u:p@db.example.com/x?hostaddr=10.0.0.1",
    "postgresql://u:p@db.example.com/x?service=internal", "postgresql://u:p@db.example.com/x?sslmode=require&port=6432",
])
def test_db_connect_refuses_private_hosts(url):
    with pytest.raises(ValueError):
        connections._target(url)


def test_refuses_own_database_even_when_public(monkeypatch):
    monkeypatch.setenv("WEALTHOS_DB_URL", "postgresql://app:pw@app-db.example.com:5432/wealthos")
    with pytest.raises(ValueError, match="own database"):
        connections._target("postgresql://u:p@other-name.example.com/x")  # different name, same address


def test_public_host_is_pinned_to_the_checked_address():
    kind, target = connections._target("postgresql://u:p@db.example.com/x")
    assert kind == "postgres" and f"hostaddr={PUBLIC}" in target and "@db.example.com" in target
    assert "sslmode=require" in connections._target("postgresql://u:p@db.example.com/x?sslmode=require")[1]
    assert f"host={PUBLIC}" in connections._target("mysql://u:p@db.example.com/x")[1]


def test_allow_private_only_in_dev(monkeypatch):
    monkeypatch.setenv("WEALTHOS_ALLOW_PRIVATE_DB", "1")
    monkeypatch.setenv("WEALTHOS_ENV", "production")
    with pytest.raises(ValueError):
        connections._target("postgresql://u:p@127.0.0.1/x")
    monkeypatch.setenv("WEALTHOS_ENV", "dev")
    assert "hostaddr" not in connections._target("postgresql://u:p@127.0.0.1/x")[1]


# The driver's own message (host, port, refused/timed out) would turn the connector into a port scanner.
def test_db_connect_error_message_is_generic(monkeypatch):
    monkeypatch.setattr(connections, "_target", lambda url: ("postgres", "host=10.1.2.3 port=notaport dbname=x"))
    with pytest.raises(ValueError) as e:
        connections._attach("postgresql://u:p@whatever/x")
    assert str(e.value) == connections.CONNECT_FAILED
