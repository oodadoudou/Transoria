"""In-process HTTP server that hosts the bridge under ``/api/`` and the
built frontend at ``/``.

One same-origin HTTP server provides both the API and the static SPA,
so the page can call ``fetch('/api/<method>')`` without CORS, port
juggling, or a JS bridge race.

Routes (stdlib ``http.server``, threaded so concurrent calls don't block):

- ``GET /api/health`` → ``{"ok": True}``
- ``GET /api/_methods`` → ``{"methods": [...]}``
- ``OPTIONS /api/<method>`` → CORS preflight
- ``POST /api/<method>`` → ``router.call(method, body)`` (JSON in / JSON out)
- ``GET /`` and friends → ``frontend/dist/`` static files (when present)

Errors propagate as the bridge ``BridgeError`` envelope with HTTP status
400 (validation/not-found) or 500 (unhandled). Non-JSON request bodies
return ``bridge.invalid_argument``.

Run from the command line::

    python -m transoria.bridge.http_server --port 5000
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Callable

from transoria.bridge.errors import BridgeError
from transoria.bridge.router import BridgeRouter, build_default_router

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIST = REPO_ROOT / "frontend" / "dist"
API_PREFIX = "/api/"


def _make_handler(
    router: BridgeRouter,
    *,
    static_root: Path | None,
):
    """Build a handler class bound to ``router`` and optional ``static_root``.

    The handler is constructed per request by stdlib's ``http.server``;
    we close over ``router`` and ``static_root`` here. A single
    ``Lock`` serializes router calls so handlers that touch shared
    state (settings store, profile store) see consistent reads.
    """

    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A002
            # Log on stderr (stdlib's default for ``BaseHTTPRequestHandler``)
            # rather than stdout. Putting it on stderr means request paths
            # containing CJK characters never compete with response data
            # for the same stream, and Windows console UTF-8 reconfigure
            # (see ``app.py:_reconfigure_stdio_utf8``) keeps non-ASCII
            # paths from raising UnicodeEncodeError when running standalone.
            print(
                f"[http] {self.address_string()} - {format % args}",
                file=sys.stderr,
                flush=True,
            )

        def _add_cors_headers(self) -> None:
            origin = self.headers.get("Origin", "*")
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers", "Content-Type, X-Request-Id"
            )
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")

        def _send_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self._add_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_static(self, path: Path) -> None:
            try:
                data = path.read_bytes()
            except OSError as exc:
                self._send_json(
                    500,
                    {
                        "code": "bridge.io_error",
                        "message": f"cannot read static asset: {exc}",
                        "retryable": False,
                    },
                )
                return
            mime, _ = mimetypes.guess_type(path.name)
            self.send_response(200)
            self._add_cors_headers()
            self.send_header(
                "Content-Type", mime or "application/octet-stream"
            )
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._add_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/health":
                self._send_json(200, {"ok": True})
                return
            if self.path in ("/api/_methods", "/api/"):
                self._send_json(200, {"methods": list(router.methods())})
                return
            if self.path.startswith(API_PREFIX):
                # GET on a bridge method is not allowed — methods take POST.
                self._send_json(
                    405,
                    {
                        "code": "bridge.invalid_argument",
                        "message": (
                            "bridge methods accept POST requests with a JSON body."
                        ),
                        "retryable": False,
                    },
                )
                return
            # Static file route. SPA fallback: any unknown non-asset path
            # serves index.html so React Router can resolve client-side.
            if static_root is None:
                self._send_json(
                    404,
                    {
                        "code": "bridge.not_found",
                        "message": (
                            f"unknown path {self.path!r} and no static root"
                            " configured (run npm run build first or use --browser)"
                        ),
                        "retryable": False,
                    },
                )
                return
            target = self._resolve_static(self.path)
            if target is None:
                self._send_json(
                    404,
                    {
                        "code": "bridge.not_found",
                        "message": f"unknown path: {self.path}",
                        "retryable": False,
                    },
                )
                return
            self._send_static(target)

        def do_POST(self) -> None:  # noqa: N802
            if not self.path.startswith(API_PREFIX):
                self._send_json(
                    404,
                    {
                        "code": "bridge.not_found",
                        "message": f"POST requires a {API_PREFIX} path",
                        "retryable": False,
                    },
                )
                return
            method = self.path[len(API_PREFIX) :]
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except json.JSONDecodeError as exc:
                self._send_json(
                    400,
                    {
                        "code": "bridge.invalid_argument",
                        "message": f"request body is not valid JSON: {exc}",
                        "retryable": False,
                    },
                )
                return
            if not isinstance(payload, dict):
                self._send_json(
                    400,
                    {
                        "code": "bridge.invalid_argument",
                        "message": "request body must be a JSON object",
                        "retryable": False,
                    },
                )
                return
            try:
                with lock:
                    response = router.call(method, payload)
            except BridgeError as exc:
                self._send_json(400, exc.payload.to_dict())
                return
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    500,
                    {
                        "code": "bridge.io_error",
                        "message": f"unhandled error: {exc}",
                        "retryable": False,
                        "details": {
                            "method": method,
                            "exception": type(exc).__name__,
                        },
                    },
                )
                return
            self._send_json(200, response)

        def _resolve_static(self, url_path: str) -> Path | None:
            """Map an HTTP path to a file under ``static_root``.

            - ``/`` and unknown extensions return ``index.html`` so client
              routers (React Router) can resolve the path themselves.
            - Direct asset paths (``/assets/foo.js``) return the file or
              ``None`` if it doesn't exist.
            - Refuses paths with ``..`` traversal.
            """

            assert static_root is not None
            if ".." in url_path:
                return None
            stripped = url_path.lstrip("/")
            if not stripped or stripped.endswith("/"):
                return static_root / "index.html"
            candidate = (static_root / stripped).resolve()
            try:
                candidate.relative_to(static_root.resolve())
            except ValueError:
                return None
            if candidate.is_file():
                return candidate
            # SPA fallback: for paths without an extension, serve index.html.
            if "." not in Path(stripped).name:
                return static_root / "index.html"
            return None

    return Handler


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 5000,
    cache_root: Path | None = None,
    static_root: Path | None = None,
    router_factory: Callable[[], BridgeRouter] | None = None,
) -> ThreadingHTTPServer:
    """Build the router + handler and start a threaded HTTP server.

    The caller is responsible for ``server_close()`` and joining
    threads. Returns the running server immediately; use
    ``server.serve_forever()`` for a blocking foreground loop or
    ``threading.Thread(target=server.serve_forever).start()`` to run it
    in the background.
    """

    if router_factory is None:
        router = build_default_router(cache_root=cache_root)
    else:
        router = router_factory()
    handler_cls = _make_handler(router, static_root=static_root)
    server = ThreadingHTTPServer((host, port), handler_cls)
    return server


def _reconfigure_stdio_utf8() -> None:
    # Mirror the launcher's reconfigure so running this module
    # standalone (``python -m transoria.bridge.http_server``) doesn't
    # crash on non-ASCII access-log lines under a non-UTF-8 console
    # codepage on Windows.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def main() -> None:
    _reconfigure_stdio_utf8()
    parser = argparse.ArgumentParser(description="Transoria HTTP bridge server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=None,
        help="Override cache root (default: ./.transoria-cache).",
    )
    parser.add_argument(
        "--static-root",
        type=Path,
        default=DEFAULT_DIST,
        help=(
            f"Static directory served at /. Default: {DEFAULT_DIST}. "
            "Pass --no-static to disable static serving."
        ),
    )
    parser.add_argument(
        "--no-static",
        action="store_true",
        help="Disable static serving (for headless API testing).",
    )
    args = parser.parse_args()

    static_root: Path | None = None
    if not args.no_static:
        if args.static_root.exists() and (args.static_root / "index.html").exists():
            static_root = args.static_root
        else:
            print(
                f"[http] note: static root {args.static_root} not found; "
                "serving /api only. Run `cd frontend && npm run build` first.",
                file=sys.stderr,
                flush=True,
            )

    server = serve(
        host=args.host,
        port=args.port,
        cache_root=args.cache_root,
        static_root=static_root,
    )
    print(
        f"[http] Transoria server on http://{args.host}:{args.port}",
        file=sys.stderr,
        flush=True,
    )
    print("[http] GET  /api/health      → liveness", file=sys.stderr, flush=True)
    print("[http] GET  /api/_methods    → list methods", file=sys.stderr, flush=True)
    print("[http] POST /api/<method>    → call method", file=sys.stderr, flush=True)
    if static_root is not None:
        print(
            f"[http] GET  /                → {static_root}/",
            file=sys.stderr,
            flush=True,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[http] shutting down", file=sys.stderr, flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
