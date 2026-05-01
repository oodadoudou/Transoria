"""Transoria desktop launcher — one-button frontend + backend startup.

Modes (pick one with a flag; default is ``--dev``):

- ``--dev`` (default): Start the Vite dev server (``frontend/``) and open a
  pywebview window pointing at it. Hot-reload edits land instantly.
- ``--prod``: Serve the prebuilt ``frontend/dist/`` from the pywebview shell.
  If ``dist/`` is missing, run ``npm run build`` first.
- ``--bridge-only``: Skip the desktop shell entirely and start the stdlib
  HTTP server (``transoria.bridge.http_server``) so you can test the bridge
  with curl/Postman from another tool.

Pre-flight checks:

- ``--dev`` / ``--prod`` need ``pywebview`` (install via ``uv sync --extra gui``
  or ``pip install -e ".[gui]"``).
- ``--dev`` needs ``npm`` on PATH and ``frontend/node_modules`` populated;
  the launcher tells you how to fix either if missing.

Shutdown is cooperative: SIGINT / window-close terminates the Vite child
cleanly so no dangling processes remain.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from transoria.app_paths import APP_NAME, resource_root
from transoria.bridge.handlers.dialogs import DialogProvider, NullDialogProvider
from transoria.bridge.handlers.updates import GithubReleaseChecker
from transoria.bridge.router import build_default_router


def _reconfigure_stdio_utf8() -> None:
    """Force stdout / stderr to UTF-8 with errors='replace'.

    Without this, ``print()`` of any non-ASCII character (Korean
    filenames, Vite's status emoji, HTTP access logs touching CJK
    paths) raises ``UnicodeEncodeError`` on Windows because the
    console codepage defaults to cp1252 / cp936 / cp949 depending on
    locale. ``errors='replace'`` keeps a single bad byte from killing
    the launcher; the user sees ``?`` instead of a crash. Best-effort:
    some bundled environments hand back a stream that doesn't expose
    ``reconfigure``; in that case we silently skip and rely on the
    process-wide default.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


_reconfigure_stdio_utf8()


ROOT = resource_root()
FRONTEND_DIR = ROOT / "frontend"
DIST_DIR = FRONTEND_DIR / "dist"
NODE_MODULES = FRONTEND_DIR / "node_modules"
DEFAULT_VITE_PORT = 5173
DEFAULT_BRIDGE_PORT = 5018
NPM_CMD = "npm.cmd" if sys.platform == "win32" else "npm"
DEFAULT_UPDATE_REPOSITORY = "oodadoudou/Transoria"


# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


def _abort(message: str, *, hint: str | None = None) -> "te.Never":  # noqa: F821
    print(f"[transoria] error: {message}", file=sys.stderr)
    if hint:
        print(f"[transoria] hint:  {hint}", file=sys.stderr)
    sys.exit(1)


def _require_pywebview():
    try:
        import webview  # noqa: PLC0415

        return webview
    except ImportError:
        _abort(
            "pywebview is not installed.",
            hint=(
                "install GUI extras: `uv sync --extra gui` "
                "or `pip install -e \".[gui]\"`"
            ),
        )


def _require_npm() -> None:
    if shutil.which(NPM_CMD) is None:
        _abort(
            "npm not found on PATH.",
            hint="install Node.js from https://nodejs.org",
        )


def _require_node_modules() -> None:
    if not NODE_MODULES.exists():
        _abort(
            f"frontend dependencies missing: {NODE_MODULES}",
            hint=f"run `cd {FRONTEND_DIR.name} && npm install` first",
        )


def _ensure_dist() -> None:
    if DIST_DIR.exists() and (DIST_DIR / "index.html").exists():
        return
    print("[transoria] frontend/dist/ missing — running `npm run build`…", flush=True)
    _require_npm()
    _require_node_modules()
    result = subprocess.run(
        [NPM_CMD, "run", "build"], cwd=FRONTEND_DIR, check=False
    )
    if result.returncode != 0:
        _abort("`npm run build` failed; see output above.")


# ---------------------------------------------------------------------------
# Vite dev server lifecycle
# ---------------------------------------------------------------------------


def _wait_for_vite(port: int, *, timeout: int = 30) -> str:
    url = f"http://localhost:{port}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as _:
                return url
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.3)
    raise RuntimeError(
        f"Vite dev server did not become ready on {url} within {timeout}s"
    )


def _start_vite(port: int, *, bridge_port: int) -> subprocess.Popen:
    _require_npm()
    _require_node_modules()
    print(f"[transoria] starting Vite dev server on port {port}…", flush=True)
    env = os.environ.copy()
    env.setdefault("BROWSER", "none")  # don't auto-open Vite's default browser
    env["TRANSORIA_BRIDGE_PORT"] = str(bridge_port)
    # Force UTF-8 in the Vite child process and on the pipe we read
    # back. Without explicit ``encoding`` Python uses the platform
    # locale (cp1252 on en-US Windows, cp936 on zh-CN), which crashes
    # the vite-pump thread the first time Vite emits an emoji or path
    # char outside that codepage. ``errors='replace'`` keeps a single
    # rogue byte from killing the launcher.
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [NPM_CMD, "run", "dev", "--", "--port", str(port), "--strictPort"],
        cwd=FRONTEND_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    def _pump() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            print(f"[vite] {line.rstrip()}", flush=True)

    threading.Thread(target=_pump, name="vite-pump", daemon=True).start()
    try:
        url = _wait_for_vite(port)
    except RuntimeError as exc:
        proc.terminate()
        _abort(str(exc))
    print(f"[transoria] Vite ready at {url}", flush=True)
    return proc


# ---------------------------------------------------------------------------
# pywebview adapters
# ---------------------------------------------------------------------------


class _DeferredDialogProvider:
    """Forwards to ``NullDialogProvider`` until the pywebview window opens.

    This lets ``build_default_router`` wire a real ``DialogProvider`` instance
    *before* the window exists — the moment the window is shown, ``activate``
    swaps the inner provider to the live pywebview adapter.
    """

    def __init__(self) -> None:
        self._inner: DialogProvider = NullDialogProvider()

    def activate(self, window) -> None:
        self._inner = _PywebviewDialogProvider(window)

    def choose_directory(self, *, initial_path: str | None = None) -> str | None:
        return self._inner.choose_directory(initial_path=initial_path)

    def choose_file(
        self, *, initial_path: str | None = None, extensions: tuple[str, ...] = ()
    ) -> str | None:
        return self._inner.choose_file(initial_path=initial_path, extensions=extensions)

    def save_file(
        self,
        *,
        default_filename: str = "",
        extensions: tuple[str, ...] = (),
    ) -> str | None:
        return self._inner.save_file(
            default_filename=default_filename, extensions=extensions
        )

    def open_directory(self, path: str) -> None:
        self._inner.open_directory(path)

    def reveal_file(self, path: str) -> None:
        self._inner.reveal_file(path)


class _PywebviewDialogProvider:
    """Real dialog provider backed by pywebview + OS file managers."""

    def __init__(self, window) -> None:
        self._w = window

    def choose_directory(self, *, initial_path: str | None = None) -> str | None:
        import webview  # noqa: PLC0415

        result = self._w.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=initial_path or "",
        )
        return result[0] if result else None

    def choose_file(
        self, *, initial_path: str | None = None, extensions: tuple[str, ...] = ()
    ) -> str | None:
        import webview  # noqa: PLC0415

        if extensions:
            pattern = ";".join(f"*.{ext}" for ext in extensions)
            file_types = (f"Files ({pattern})", "All files (*.*)")
        else:
            file_types = ()
        result = self._w.create_file_dialog(
            webview.OPEN_DIALOG,
            directory=initial_path or "",
            file_types=file_types,
        )
        return result[0] if result else None

    def save_file(
        self,
        *,
        default_filename: str = "",
        extensions: tuple[str, ...] = (),
    ) -> str | None:
        import webview  # noqa: PLC0415

        if extensions:
            pattern = ";".join(f"*.{ext}" for ext in extensions)
            file_types = (f"Files ({pattern})", "All files (*.*)")
        else:
            file_types = ()
        result = self._w.create_file_dialog(
            webview.SAVE_DIALOG,
            save_filename=default_filename,
            file_types=file_types,
        )
        if not result:
            return None
        return result if isinstance(result, str) else result[0]

    def open_directory(self, path: str) -> None:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])

    def reveal_file(self, path: str) -> None:
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        elif sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", path])
        else:
            subprocess.Popen(["xdg-open", str(Path(path).parent)])


def _build_native_api(dialog_provider: DialogProvider) -> object:
    """Expose only native OS operations that fetch cannot perform."""

    class _NativeApi:
        def choose_directory(self, payload: dict | None = None) -> dict:
            body = payload or {}
            path = dialog_provider.choose_directory(
                initial_path=body.get("initial_path")
                if isinstance(body.get("initial_path"), str)
                else None
            )
            return {"path": path}

        def choose_file(self, payload: dict | None = None) -> dict:
            body = payload or {}
            raw_extensions = body.get("extensions", ())
            extensions = (
                tuple(str(item) for item in raw_extensions)
                if isinstance(raw_extensions, (list, tuple))
                else ()
            )
            path = dialog_provider.choose_file(
                initial_path=body.get("initial_path")
                if isinstance(body.get("initial_path"), str)
                else None,
                extensions=extensions,
            )
            return {"path": path}

        def save_file(self, payload: dict | None = None) -> dict:
            body = payload or {}
            default_filename = (
                body.get("default_filename")
                if isinstance(body.get("default_filename"), str)
                else ""
            )
            raw_extensions = body.get("extensions", ())
            extensions = (
                tuple(str(item) for item in raw_extensions)
                if isinstance(raw_extensions, (list, tuple))
                else ()
            )
            path = dialog_provider.save_file(
                default_filename=default_filename or "",
                extensions=extensions,
            )
            return {"path": path}

        def open_directory(self, payload: dict | None = None) -> dict:
            body = payload or {}
            path = body.get("path")
            if isinstance(path, str):
                dialog_provider.open_directory(path)
            return {"ok": True}

        def reveal_file(self, payload: dict | None = None) -> dict:
            body = payload or {}
            path = body.get("path")
            if isinstance(path, str):
                dialog_provider.reveal_file(path)
            return {"ok": True}

    return _NativeApi()


def _update_checker() -> GithubReleaseChecker:
    repository = os.environ.get("TRANSORIA_UPDATE_REPOSITORY") or DEFAULT_UPDATE_REPOSITORY
    return GithubReleaseChecker(repository=repository)


# ---------------------------------------------------------------------------
# Mode runners
# ---------------------------------------------------------------------------


def _run_desktop(
    *, dev: bool, vite_port: int, bridge_host: str, bridge_port: int
) -> None:
    webview = _require_pywebview()

    vite_proc: subprocess.Popen | None = None
    dialog_provider = _DeferredDialogProvider()
    if not dev:
        _ensure_dist()
    http_server, actual_bridge_port = _start_bridge_http(
        bridge_host,
        bridge_port,
        static_root=DIST_DIR if not dev else None,
    )
    js_api = _build_native_api(dialog_provider)

    if dev:
        vite_proc = _start_vite(vite_port, bridge_port=actual_bridge_port)
        url = f"http://127.0.0.1:{vite_port}"
    else:
        url = f"http://127.0.0.1:{actual_bridge_port}"

    window = webview.create_window(
        APP_NAME,
        url,
        js_api=js_api,
        width=1280,
        height=800,
        min_size=(960, 600),
    )

    def _on_shown() -> None:
        dialog_provider.activate(window)

    def _terminate_vite() -> None:
        if vite_proc is None or vite_proc.poll() is not None:
            return
        print("[transoria] terminating Vite…", flush=True)
        vite_proc.terminate()
        try:
            vite_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            vite_proc.kill()

    # Clean up the Vite child on Ctrl+C even if pywebview swallows the signal.
    def _shutdown() -> None:
        _terminate_vite()
        http_server.shutdown()
        http_server.server_close()

    signal.signal(signal.SIGINT, lambda *_: (_shutdown(), sys.exit(0)))

    try:
        webview.start(_on_shown, debug=dev)
    finally:
        _shutdown()


def _start_bridge_http(
    host: str,
    port: int,
    *,
    static_root: Path | None = None,
):
    """Start the HTTP bridge in a background thread; return ``(server, port)``."""

    from transoria.bridge.http_server import serve  # noqa: PLC0415

    def router_factory():
        return build_default_router(update_checker=_update_checker())

    attempts = (port, port + 1)
    last_error: OSError | None = None
    server = None
    actual_port = port
    for candidate in attempts:
        try:
            server = serve(
                host=host,
                port=candidate,
                static_root=static_root,
                router_factory=router_factory,
            )
            actual_port = candidate
            break
        except OSError as exc:
            last_error = exc
            continue
    if server is None:
        raise last_error or OSError(f"cannot bind {host}:{port}")

    thread = threading.Thread(
        target=server.serve_forever,
        name="bridge-http",
        daemon=True,
    )
    thread.start()
    print(
        f"[transoria] HTTP bridge listening on http://{host}:{actual_port}/api",
        flush=True,
    )
    return server, actual_port


def _run_bridge_only(*, port: int, host: str) -> None:
    """Skip the desktop shell; just run the HTTP bridge harness."""

    from transoria.bridge.http_server import serve  # noqa: PLC0415

    def router_factory():
        return build_default_router(update_checker=_update_checker())

    server = None
    actual_port = port
    for candidate in (port, port + 1):
        try:
            server = serve(
                host=host,
                port=candidate,
                static_root=None,
                router_factory=router_factory,
            )
            actual_port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise OSError(f"cannot bind {host}:{port} or {host}:{port + 1}")
    print(f"[transoria] bridge-only mode on http://{host}:{actual_port}", flush=True)
    print("[transoria] GET /api/health      → liveness", flush=True)
    print("[transoria] GET /api/_methods    → list methods", flush=True)
    print("[transoria] POST /api/<method>    → call method", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[transoria] shutting down bridge server", flush=True)
    finally:
        server.server_close()


def _run_browser(*, vite_port: int, bridge_port: int, bridge_host: str) -> None:
    """Browser dev mode: Vite + HTTP bridge, opens default browser. No pywebview."""

    import webbrowser  # noqa: PLC0415

    bridge_server, actual_bridge_port = _start_bridge_http(
        bridge_host,
        bridge_port,
        static_root=None,
    )
    vite_proc: subprocess.Popen | None = None
    try:
        vite_proc = _start_vite(vite_port, bridge_port=actual_bridge_port)
        url = f"http://127.0.0.1:{vite_port}"
        print(f"[transoria] opening {url} in your default browser", flush=True)
        print(
            f"[transoria] frontend bridge transport will fetch "
            f"http://{bridge_host}:{actual_bridge_port}/api",
            flush=True,
        )
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[transoria] could not open browser automatically: {exc}", flush=True)

        def _shutdown(*_args) -> None:
            print("\n[transoria] shutting down…", flush=True)
            if vite_proc is not None and vite_proc.poll() is None:
                vite_proc.terminate()
            bridge_server.shutdown()
            sys.exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        signal.signal(signal.SIGTERM, _shutdown)

        # Block forever; SIGINT triggers _shutdown.
        threading.Event().wait()
    finally:
        if vite_proc is not None and vite_proc.poll() is None:
            vite_proc.terminate()
            try:
                vite_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                vite_proc.kill()
        bridge_server.server_close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="transoria",
        description="Launch the Transoria desktop app (frontend + backend).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dev",
        dest="mode",
        action="store_const",
        const="dev",
        help="Default. Start Vite dev server + pywebview window.",
    )
    mode.add_argument(
        "--prod",
        dest="mode",
        action="store_const",
        const="prod",
        help="Serve frontend/dist/ in pywebview (builds it if missing).",
    )
    mode.add_argument(
        "--bridge-only",
        dest="mode",
        action="store_const",
        const="bridge",
        help="Run only the HTTP bridge harness (no UI). Useful for API testing.",
    )
    mode.add_argument(
        "--browser",
        dest="mode",
        action="store_const",
        const="browser",
        help=(
            "Browser dev mode: Vite + HTTP bridge, opens default browser. "
            "No pywebview required. Recommended for active frontend dev."
        ),
    )
    parser.add_argument(
        "--vite-port",
        type=int,
        default=DEFAULT_VITE_PORT,
        help=f"Vite dev server port (default: {DEFAULT_VITE_PORT}).",
    )
    parser.add_argument(
        "--bridge-port",
        "--port",
        dest="bridge_port",
        type=int,
        default=DEFAULT_BRIDGE_PORT,
        help=f"HTTP bridge port (default: {DEFAULT_BRIDGE_PORT}).",
    )
    parser.add_argument(
        "--bridge-host",
        default="127.0.0.1",
        help="Bridge HTTP host for --bridge-only (default: 127.0.0.1).",
    )
    args = parser.parse_args(argv)
    if args.mode is None:
        args.mode = "prod" if getattr(sys, "frozen", False) else "dev"
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    print(f"[transoria] mode: {args.mode}", flush=True)
    if args.mode == "bridge":
        _run_bridge_only(port=args.bridge_port, host=args.bridge_host)
    elif args.mode == "browser":
        _run_browser(
            vite_port=args.vite_port,
            bridge_port=args.bridge_port,
            bridge_host=args.bridge_host,
        )
    else:
        _run_desktop(
            dev=(args.mode == "dev"),
            vite_port=args.vite_port,
            bridge_port=args.bridge_port,
            bridge_host=args.bridge_host,
        )


if __name__ == "__main__":
    main()
