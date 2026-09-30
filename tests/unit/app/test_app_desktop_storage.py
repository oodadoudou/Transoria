"""Desktop drafts must survive a normal process restart."""

from types import SimpleNamespace

import pytest

import app


@pytest.mark.parametrize("dev", [False, True])
def test_desktop_preserves_local_storage(monkeypatch, tmp_path, dev):
    calls = {}
    closed = []
    webview = SimpleNamespace(
        create_window=lambda *args, **kwargs: object(),
        start=lambda callback, **kwargs: calls.update(kwargs),
    )
    server = SimpleNamespace(
        shutdown=lambda: closed.append("shutdown"),
        server_close=lambda: closed.append("close"),
    )
    monkeypatch.setattr(app, "_require_pywebview", lambda: webview)
    monkeypatch.setattr(app, "_ensure_dist", lambda: None)
    monkeypatch.setattr(app, "_start_bridge_http", lambda *args, **kwargs: (server, 5019))
    monkeypatch.setattr(app, "_start_vite", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "default_cache_root", lambda: tmp_path)
    monkeypatch.setattr(app.signal, "signal", lambda *args: None)

    app._run_desktop(dev=dev, vite_port=5173, bridge_host="127.0.0.1", bridge_port=5019)

    assert calls == {
        "debug": dev,
        "private_mode": False,
        "storage_path": str(tmp_path / "desktop-webview"),
    }
    assert closed == ["shutdown", "close"]
