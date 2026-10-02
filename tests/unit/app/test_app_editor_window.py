from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest

import app


@pytest.fixture(autouse=True)
def immediate_threads(monkeypatch):
    monkeypatch.setattr(app.threading, "Thread", lambda target, **kwargs: SimpleNamespace(start=target))


class Event:
    def __init__(self, set_=False):
        self.handlers = []
        self.set_ = set_

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def is_set(self):
        return self.set_

    def fire(self):
        return [handler() for handler in self.handlers]


def editor():
    events = SimpleNamespace(**{name: Event(name == "loaded") for name in ["loaded", "closing", "closed", "minimized", "restored"]})
    window = Mock(events=events)
    window.evaluate_js.return_value = True
    webview = Mock()
    webview.create_window.return_value = window
    return app._EditorWindow(webview, "http://127.0.0.1:5019"), webview, window


def test_editor_singleton_focus_retains_fullscreen_and_escapes_path():
    manager, webview, window = editor()
    path = "/test/书 & # ? %.epub"
    manager.open(path)
    url = webview.create_window.call_args.args[1]
    assert parse_qs(urlsplit(url).query) == {"desktop": ["1"], "epub-editor": ["1"], "path": [path]}
    api = webview.create_window.call_args.kwargs["js_api"]
    assert api.toggle_editor_fullscreen() == {"ok": True}
    window.toggle_fullscreen.assert_called_once()
    manager.open("/another.epub")
    webview.create_window.assert_called_once()
    window.show.assert_called_once()
    window.restore.assert_not_called()
    window.events.minimized.fire()
    manager.open()
    window.restore.assert_called_once()


def test_editor_os_close_requests_frontend_and_explicit_close_releases_window():
    manager, webview, window = editor()
    manager.open()
    assert window.events.closing.fire() == [False]
    window.destroy.assert_not_called()
    api = webview.create_window.call_args.kwargs["js_api"]
    assert api.close_epub_editor() == {"closed": True}
    window.destroy.assert_not_called()
    window.evaluate_js.call_args.kwargs["callback"](False)
    window.destroy.assert_not_called()
    window.evaluate_js.call_args.kwargs["callback"](True)
    window.destroy.assert_called_once()
    assert window.events.closing.fire() == [None]
    window.events.closed.fire()
    assert manager.window is None
    manager.open()
    assert webview.create_window.call_count == 2
    assert not manager.allow_close


@pytest.mark.parametrize("loaded, handled", [(False, True), (True, False)])
def test_editor_can_close_before_frontend_close_handler_is_ready(loaded, handled):
    manager, _, window = editor()
    manager.open()
    window.events.loaded.set_ = loaded
    window.evaluate_js.return_value = handled
    assert window.events.closing.fire() == ([False] if loaded else [None])
    if loaded:
        window.destroy.assert_called_once()


def test_editor_close_fails_safe_if_live_page_is_unreachable():
    manager, _, window = editor()
    manager.open()
    window.evaluate_js.side_effect = RuntimeError("unreachable")
    assert window.events.closing.fire() == [False]


def test_editor_dialogs_use_editor_window_not_parent(monkeypatch):
    manager, webview, window = editor()
    manager.open()
    provider = Mock()
    monkeypatch.setattr(app, "_PywebviewDialogProvider", lambda target: provider if target is window else pytest.fail("wrong parent"))
    # Each new window receives its own activated dialog provider.
    manager.window = None
    manager.open()
    api = webview.create_window.call_args.kwargs["js_api"]
    provider.choose_file.return_value = "test.epub"
    assert api.choose_file({"extensions": ["epub"]}) == {"path": "test.epub"}
    provider.choose_file.assert_called_once_with(initial_path=None, extensions=("epub",))
