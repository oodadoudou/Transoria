"""Tests for ``transoria.bridge.handlers.dialogs``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from transoria.bridge import BridgeError, BridgeRouter
from transoria.bridge.handlers.dialogs import (
    DialogProvider,
    NullDialogProvider,
    register,
)


@dataclass
class StubDialogProvider:
    directory_path: str | None = None
    file_path: str | None = None
    captured: dict[str, Any] = field(default_factory=dict)

    def choose_directory(self, *, initial_path: str | None) -> str | None:
        self.captured["directory_initial"] = initial_path
        return self.directory_path

    def choose_file(
        self, *, initial_path: str | None, extensions: tuple[str, ...]
    ) -> str | None:
        self.captured["file_initial"] = initial_path
        self.captured["file_extensions"] = extensions
        return self.file_path

    def open_directory(self, path: str) -> None:
        self.captured["opened"] = path

    def reveal_file(self, path: str) -> None:
        self.captured["revealed"] = path


@pytest.fixture
def router_and_stub() -> tuple[BridgeRouter, StubDialogProvider]:
    provider = StubDialogProvider()
    router = BridgeRouter()
    register(router, provider=provider)
    return router, provider


def test_choose_input_directory_returns_path(router_and_stub):
    router, provider = router_and_stub
    provider.directory_path = "/tmp/in"

    response = router.call(
        "dialogs.choose_input_directory", {"initial_path": "/home"}
    )

    assert response == {"path": "/tmp/in"}
    assert provider.captured["directory_initial"] == "/home"


def test_choose_input_directory_returns_null_on_cancel(router_and_stub):
    router, _ = router_and_stub

    response = router.call("dialogs.choose_input_directory", {})

    assert response == {"path": None}


def test_choose_glossary_file_infers_format(router_and_stub):
    router, provider = router_and_stub
    provider.file_path = "/tmp/glossary.xlsx"

    response = router.call(
        "dialogs.choose_glossary_file",
        {"allow_xlsx": True, "allow_json": True},
    )

    assert response == {"path": "/tmp/glossary.xlsx", "format": "xlsx"}
    assert "xlsx" in provider.captured["file_extensions"]
    assert "json" in provider.captured["file_extensions"]


def test_choose_glossary_file_validates_magic_bytes_xlsx(
    router_and_stub, tmp_path
):
    """Real files: XLSX is a zip starting with ``PK\\x03\\x04``."""

    router, provider = router_and_stub
    fake_xlsx = tmp_path / "real.xlsx"
    fake_xlsx.write_bytes(b"PK\x03\x04rest-of-zip-bytes")
    provider.file_path = str(fake_xlsx)

    response = router.call("dialogs.choose_glossary_file", {})
    assert response == {"path": str(fake_xlsx), "format": "xlsx"}


def test_choose_glossary_file_validates_magic_bytes_json(
    router_and_stub, tmp_path
):
    router, provider = router_and_stub
    fake_json = tmp_path / "real.json"
    fake_json.write_text("[{\"src\":\"a\",\"dst\":\"b\"}]", encoding="utf-8")
    provider.file_path = str(fake_json)

    response = router.call("dialogs.choose_glossary_file", {})
    assert response == {"path": str(fake_json), "format": "json"}


def test_choose_glossary_file_rejects_renamed_csv_as_xlsx(
    router_and_stub, tmp_path
):
    """A CSV renamed to ``.xlsx`` (no PK header) is rejected with
    ``format: None`` so the caller can show a typed error."""

    router, provider = router_and_stub
    fake = tmp_path / "fake.xlsx"
    fake.write_text("a,b,c\n1,2,3\n", encoding="utf-8")
    provider.file_path = str(fake)

    response = router.call("dialogs.choose_glossary_file", {})
    assert response["path"] == str(fake)
    assert response["format"] is None


def test_choose_glossary_file_rejects_garbage_as_json(
    router_and_stub, tmp_path
):
    router, provider = router_and_stub
    fake = tmp_path / "fake.json"
    fake.write_bytes(b"\x00\x01\x02 not json")
    provider.file_path = str(fake)

    response = router.call("dialogs.choose_glossary_file", {})
    assert response["format"] is None


def test_choose_glossary_file_null_when_cancelled(router_and_stub):
    router, _ = router_and_stub

    response = router.call("dialogs.choose_glossary_file", {})

    assert response == {"path": None, "format": None}


def test_choose_replacement_rules_file_uses_txt(router_and_stub):
    router, provider = router_and_stub
    provider.file_path = "/tmp/rules.txt"

    response = router.call("dialogs.choose_replacement_rules_file", {})

    assert response["path"] == "/tmp/rules.txt"
    assert provider.captured["file_extensions"] == ("txt",)


def test_open_directory_validates_existence(router_and_stub, tmp_path: Path):
    router, _ = router_and_stub

    with pytest.raises(BridgeError) as caught:
        router.call("dialogs.open_directory", {"path": str(tmp_path / "nope")})

    assert caught.value.code == "bridge.not_found"


def test_open_directory_calls_provider(router_and_stub, tmp_path: Path):
    router, provider = router_and_stub
    target = tmp_path / "real"
    target.mkdir()

    response = router.call("dialogs.open_directory", {"path": str(target)})

    assert response == {}
    assert provider.captured["opened"] == str(target)


def test_open_directory_rejects_paths_outside_allowed_roots(tmp_path: Path):
    """When the router supplies an ``allowed_roots_provider`` with a
    non-empty list, ``open_directory`` must refuse any path outside
    those roots. Without this check the bridge could be coerced into
    shelling out arbitrary host paths from a compromised renderer."""

    provider = StubDialogProvider()
    output_root = tmp_path / "output"
    output_root.mkdir()
    foreign = tmp_path / "elsewhere"
    foreign.mkdir()

    router = BridgeRouter()
    register(
        router,
        provider=provider,
        allowed_roots_provider=lambda: [output_root],
    )

    # Inside the allowed root → permitted.
    router.call("dialogs.open_directory", {"path": str(output_root)})
    assert provider.captured["opened"] == str(output_root)

    # Outside → bridge.invalid_argument.
    with pytest.raises(BridgeError) as caught:
        router.call("dialogs.open_directory", {"path": str(foreign)})
    assert caught.value.code == "bridge.invalid_argument"


def test_reveal_file_validates_existence(router_and_stub, tmp_path: Path):
    router, _ = router_and_stub

    with pytest.raises(BridgeError) as caught:
        router.call(
            "dialogs.reveal_file", {"path": str(tmp_path / "missing.txt")}
        )

    assert caught.value.code == "bridge.not_found"


def test_null_dialog_provider_returns_none_and_raises(tmp_path: Path):
    provider: DialogProvider = NullDialogProvider()
    router = BridgeRouter()
    register(router, provider=provider)

    cancel = router.call("dialogs.choose_input_directory", {})
    assert cancel == {"path": None}

    target = tmp_path / "exists"
    target.mkdir()
    with pytest.raises(BridgeError) as caught:
        router.call("dialogs.open_directory", {"path": str(target)})
    assert caught.value.code == "bridge.io_error"
