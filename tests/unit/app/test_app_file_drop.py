import json
from types import SimpleNamespace
from unittest.mock import Mock

import app


class _Events:
    def __init__(self):
        self.handlers = {"dragover": [], "drop": []}

    @property
    def dragover(self):
        return _Hook(self.handlers["dragover"])

    @dragover.setter
    def dragover(self, _value):
        pass

    @property
    def drop(self):
        return _Hook(self.handlers["drop"])

    @drop.setter
    def drop(self, _value):
        pass


class _Hook:
    def __init__(self, sink):
        self.sink = sink

    def __iadd__(self, handler):
        self.sink.append(handler)
        return self


def _window():
    events = _Events()
    window = Mock()
    window.dom = SimpleNamespace(document=SimpleNamespace(events=events))
    return window, events


def test_file_drop_forwards_full_paths_to_the_page():
    window, events = _window()
    app._bind_file_drop(window)

    assert len(events.handlers["dragover"]) == 1
    assert len(events.handlers["drop"]) == 1
    drop = events.handlers["drop"][0]
    assert drop.prevent_default is True

    drop.callback(
        {
            "dataTransfer": {
                "files": [
                    {"name": "术语表.xlsx", "pywebviewFullPath": "/books/术语表.xlsx"},
                    {"name": "no-path.json"},
                ]
            }
        }
    )

    script = window.evaluate_js.call_args.args[0]
    assert "transoria-files-dropped" in script
    detail = script.split("detail: ", 1)[1].rsplit("}))", 1)[0]
    assert json.loads(detail) == ["/books/术语表.xlsx"]


def test_drop_without_native_paths_does_not_notify_the_page():
    window, events = _window()
    app._bind_file_drop(window)

    events.handlers["drop"][0].callback({"dataTransfer": {"files": [{"name": "a.xlsx"}]}})
    events.handlers["drop"][0].callback({})

    window.evaluate_js.assert_not_called()
