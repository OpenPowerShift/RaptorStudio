"""MCP over HTTP (Streamable HTTP), with the safety rules a network listener needs.

* Loopback by default. A bearer token is REQUIRED unless
  `--allow-unauthenticated` is given (loopback only): any local user or
  process can otherwise reach `127.0.0.1` and read and edit the workspace.
  Any non-loopback bind always needs one.
* A token (from a file or an environment variable) of at least
  `MIN_TOKEN_CHARS` characters is required on every request; a wrong or
  missing one gets 401, and a client address that sends too many wrong ones
  is locked out for a minute (429).
* Browser cross-origin calls are blocked unless the Origin is listed
  (`--allow-origin`), and the Host header is checked (DNS-rebinding
  protection from the MCP SDK).
* `--port 0` binds a free port and prints `http://HOST:PORT/PATH` as the
  FIRST line on stdout, so scripts and tests can find it.
"""
from __future__ import annotations

import collections
import hmac
import ipaddress
import logging
import os
import socket
import sys
import time
from typing import Any

log = logging.getLogger(__name__)

#: A bearer token shorter than this is refused: a short one can be guessed.
MIN_TOKEN_CHARS = 16
#: Wrong-token attempts from one client address allowed per window, and the
#: window (also the lock-out time) in seconds.
MAX_AUTH_FAILURES = 10
AUTH_WINDOW_S = 60.0


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def read_token(args: Any) -> str | None:
    if args.token_file:
        _warn_if_group_or_world_readable(args.token_file, "token file")
        with open(args.token_file, encoding="utf-8") as fh:
            tok = fh.read().strip()
        if not tok:
            raise ValueError(f"token file {args.token_file} is empty")
        return tok
    if args.token_env:
        tok = os.environ.get(args.token_env, "").strip()
        if not tok:
            raise ValueError(f"environment variable {args.token_env} is "
                             f"not set or empty")
        return tok
    return None


def _warn_if_group_or_world_readable(path: str, what: str) -> None:
    """A token/key readable by another local user is a credential leak."""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    if mode & 0o077:
        log.warning("%s %s is readable by group/others (mode %o); set it "
                    "to 0600", what, path, mode & 0o777)


def check_args(args: Any) -> str | None:
    """A human-readable problem with the http options, or None."""
    if bool(args.tls_cert) != bool(args.tls_key):
        return "--tls-cert and --tls-key go together"
    if args.token_file and args.token_env:
        return "give --token-file or --token-env, not both"
    try:
        token = read_token(args)
    except (OSError, ValueError) as exc:
        return str(exc)
    return _token_problem(args, token) or _listen_problem(args)


def _token_problem(args: Any, token: str | None) -> str | None:
    """Whether the token (or its absence) is acceptable for this address."""
    if token is not None and len(token) < MIN_TOKEN_CHARS:
        return (f"the token is too short ({len(token)} characters); use at "
                f"least {MIN_TOKEN_CHARS}, e.g. "
                "`head -c 32 /dev/urandom | base64 > ~/.raptor-token`")
    if not _is_loopback(args.host) and token is None:
        return (f"refusing to listen on {args.host!r} without a token: "
                f"use --token-file or --token-env, or bind 127.0.0.1")
    if token is None and not getattr(args, "allow_unauthenticated", False):
        return ("refusing to serve HTTP without a token: any local user or "
                "process could reach the workspace. Create one "
                "(`head -c 32 /dev/urandom | base64 > ~/.raptor-token; "
                "chmod 600 ~/.raptor-token`) and pass --token-file, or pass "
                "--allow-unauthenticated to accept that on a single-user "
                "machine.")
    return None


def _listen_problem(args: Any) -> str | None:
    """Whether the address, TLS, port and path options are acceptable."""
    if (not _is_loopback(args.host) and not args.tls_cert
            and not getattr(args, "allow_insecure_http", False)):
        return ("refusing plaintext HTTP on a non-loopback address: the "
                "bearer token and model data would cross the network in "
                "clear text.  Use --tls-cert/--tls-key, or pass "
                "--allow-insecure-http to accept that risk explicitly.")
    if args.tls_key:
        _warn_if_group_or_world_readable(args.tls_key, "TLS key")
    if not (0 <= args.port <= 65535):
        return f"bad port {args.port}"
    if not args.path.startswith("/"):
        return "--path must start with '/'"
    return None


class BearerTokenMiddleware:
    """ASGI middleware: 401 unless `Authorization: Bearer <token>` matches.

    A client address that sends `MAX_AUTH_FAILURES` wrong tokens within
    `AUTH_WINDOW_S` seconds is answered 429 (even with the right token) until
    the window has passed, so a token cannot be guessed at network speed."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self._token = token.encode()
        self._failures: dict[str, collections.deque[float]] = {}

    def _locked_out(self, client: str, now: float) -> bool:
        times = self._failures.get(client)
        if not times:
            return False
        while times and now - times[0] > AUTH_WINDOW_S:
            times.popleft()
        if not times:
            self._failures.pop(client, None)
            return False
        return len(times) >= MAX_AUTH_FAILURES

    def _record_failure(self, client: str, now: float) -> None:
        if len(self._failures) > 10_000:       # never grow without bound
            self._failures.clear()
        self._failures.setdefault(client, collections.deque()).append(now)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            client = str((scope.get("client") or ("?",))[0])
            now = time.monotonic()
            if self._locked_out(client, now):
                await send({"type": "http.response.start", "status": 429,
                            "headers": [(b"content-type", b"text/plain"),
                                        (b"retry-after",
                                         str(int(AUTH_WINDOW_S)).encode())]})
                await send({"type": "http.response.body",
                            "body": b"too many failed attempts\n"})
                return
            supplied = b""
            for k, v in scope.get("headers", []):
                if k == b"authorization":
                    val = v.decode("latin-1")
                    if val.lower().startswith("bearer "):
                        supplied = val[7:].strip().encode()
            if not hmac.compare_digest(supplied, self._token):
                self._record_failure(client, now)
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body",
                            "body": b"unauthorized\n"})
                return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    """Refuse an HTTP request body larger than *max_bytes*.

    MCP JSON-RPC requests are small; an unbounded body is a trivial memory
    DoS.  A declared Content-Length is checked before reading.  A chunked
    body is buffered up to the limit, then replayed to the app as one
    message (the app still sees a normal ASGI stream).
    """

    def __init__(self, app: Any, max_bytes: int):
        self.app = app
        self.max_bytes = int(max_bytes)

    async def _too_large(self, send: Any) -> None:
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body",
                    "body": b"request body too large\n"})

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:  # noqa: C901
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                await self._too_large(send)
                return
            if length > self.max_bytes:
                await self._too_large(send)
                return
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                await self._too_large(send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        replayed = False

        async def replay() -> dict:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body,
                        "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def build_app(server: Any, args: Any) -> Any:
    from mcp.server.transport_security import TransportSecuritySettings
    from raptor.api.limits import MAX_REQUEST_BYTES
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    if not _is_loopback(args.host):
        hosts.append(f"{args.host}:*")
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts, allowed_origins=list(args.allow_origin))
    app = server.streamable_http_app(
        streamable_http_path=args.path, transport_security=security,
        host=args.host)
    token = read_token(args)
    if token:
        app = BearerTokenMiddleware(app, token)
    # Auth is the outer layer: an unauthenticated caller should not be able
    # to make us buffer a huge body before the token check.
    app = BodyLimitMiddleware(app, MAX_REQUEST_BYTES)
    return app


def serve(server: Any, args: Any) -> int:
    import anyio
    import uvicorn

    app = build_app(server, args)
    family = socket.AF_INET6 if ":" in args.host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.host, args.port))
    except OSError as exc:
        print(f"raptor-mcp: cannot listen on {args.host}:{args.port}: {exc}",
              file=sys.stderr)
        return 1
    # Listening before the URL is printed means a client that reads the URL
    # and connects at once queues instead of being refused.
    sock.listen(128)
    port = sock.getsockname()[1]
    scheme = "https" if args.tls_cert else "http"
    # The FIRST stdout line, machine-readable.
    print(f"{scheme}://{args.host}:{port}{args.path}", flush=True)
    config = uvicorn.Config(
        app, log_level="warning",
        limit_concurrency=64,
        ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key)
    srv = uvicorn.Server(config)

    async def main() -> None:
        await srv.serve(sockets=[sock])

    try:
        anyio.run(main)
    except KeyboardInterrupt:
        pass
    return 0
