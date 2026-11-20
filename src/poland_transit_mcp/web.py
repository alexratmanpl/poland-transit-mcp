"""Streamable HTTP (`--http`): the MCP SDK's app, with its DNS-rebinding check set here, behind a guard that answers
only POST, run by uvicorn with a limit on connections.

- **Host and Origin check.** The SDK turns its DNS-rebinding check on by itself only for the exact strings
  127.0.0.1, localhost and ::1. Every other loopback address (127.0.0.2, 127.1, ...) got no check, so a web page
  that rebinds its name to that address could call the tools. The settings are therefore always passed: the check is
  on for every loopback address, and off (stated, not by default) for any other bind, which sits behind a gateway
  whose host name the server cannot know.
- **POST only.** The server answers each call in the POST's own JSON reply (stateless, JSON responses), so it never
  pushes anything on a GET stream. The SDK still opens one for a GET and holds it for ever; a few thousand idle
  ones use up the process's file descriptors. GET and DELETE on the MCP path are answered 405 instead.
- **Connections.** No cap by default. uvicorn's cap (`max_connections`, opt-in) counts every open connection,
  also one that never sends a request, so a few idle sockets per slot would turn every call away with 503; without
  it, a flood needs about as many sockets as the process has file descriptors. Either way one process only bounds
  a connection flood: a server shared with others belongs behind a gateway (SECURITY.md). A connection idle for
  KEEP_ALIVE_S seconds after a reply is closed.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any

MCP_PATH = "/mcp"
DEFAULT_MAX_CONNECTIONS: int | None = None  # no cap: see the module text
KEEP_ALIVE_S = 5
# what the SDK allows by itself for its three loopback names
SDK_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
SDK_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def is_loopback(host: str) -> bool:
    """localhost, or any loopback address, in any form the bind accepts (127.0.0.2, ::1, and short IPv4 such as
    127.1)."""
    host = host.strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    try:  # short or numeric IPv4 forms, as the system resolver reads them
        return ipaddress.ip_address(socket.inet_aton(host)).is_loopback
    except (OSError, ValueError):
        return False


def transport_security(host: str):
    """The SDK's Host/Origin check for a bind address: on for every loopback address (the Host header must name a
    loopback name, and an Origin, when sent, too), off for any other address."""
    # imported when --http is used only
    from mcp.server.transport_security import TransportSecuritySettings
    host = host.strip("[]")
    if not is_loopback(host):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    shown = f"[{host}]" if ":" in host else host
    hosts = list(dict.fromkeys([*SDK_HOSTS, f"{shown}:*", shown]))
    origins = list(dict.fromkeys([*SDK_ORIGINS, f"http://{shown}:*", f"http://{shown}"]))
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                     allowed_origins=origins)


class PostOnly:
    """GET and DELETE on the MCP path get 405: a stateless server with JSON replies has no stream to open and no
    session to end. Everything else (POST, other paths, the app's lifespan) goes to the SDK's app."""

    def __init__(self, app: Any, path: str = MCP_PATH):
        self.app, self.path = app, path

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["method"] in ("GET", "DELETE") \
                and scope["path"].rstrip("/") == self.path:
            body = b"this server answers POST only: each reply comes in the POST's own response"
            await send({"type": "http.response.start", "status": 405,
                        "headers": [(b"allow", b"POST"), (b"content-type", b"text/plain; charset=utf-8"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def app(server, host: str):
    """The ASGI app `run` serves: the SDK's stateless JSON app, with the Host/Origin check for `host`, behind
    PostOnly."""
    return PostOnly(server.streamable_http_app(streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
                                               transport_security=transport_security(host), host=host))


def config(server, host: str, port: int, max_connections: int | None = DEFAULT_MAX_CONNECTIONS):
    """The uvicorn settings `run` serves with (the tests start a real server from them)."""
    import uvicorn
    return uvicorn.Config(app(server, host), host=host, port=port, limit_concurrency=max_connections,
                          timeout_keep_alive=KEEP_ALIVE_S, log_level=server.settings.log_level.lower())


def run(server, host: str, port: int, max_connections: int | None = DEFAULT_MAX_CONNECTIONS) -> None:
    import anyio
    import uvicorn
    anyio.run(uvicorn.Server(config(server, host, port, max_connections)).serve)
