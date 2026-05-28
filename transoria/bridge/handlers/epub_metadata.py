from __future__ import annotations

from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.tools.epub_metadata import apply_epub_metadata, read_epub_metadata


def register(router: BridgeRouter) -> None:
    def read(payload: Mapping[str, object]) -> dict[str, object]:
        try:
            return read_epub_metadata(expect_string(payload, "input_path")).to_dict()
        except (FileNotFoundError, ValueError) as exc:
            raise BridgeError.invalid_argument(str(exc)) from exc

    def apply(payload: Mapping[str, object]) -> dict[str, object]:
        try:
            return apply_epub_metadata(
                expect_string(payload, "input_path"),
                expect_string(payload, "output_path"),
                title=expect_string(payload, "title", allow_empty=True),
                author=expect_string(payload, "author", allow_empty=True),
                cover_path=expect_string(payload, "cover_path", allow_empty=True),
            ).to_dict()
        except (FileNotFoundError, ValueError) as exc:
            raise BridgeError.invalid_argument(str(exc)) from exc

    router.register("epub_metadata.read", read)
    router.register("epub_metadata.apply", apply)


__all__ = ["register"]
