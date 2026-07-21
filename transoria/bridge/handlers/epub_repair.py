from __future__ import annotations

from typing import Mapping

from transoria.bridge.errors import BridgeError
from transoria.bridge.handlers._utils import expect_string
from transoria.bridge.router import BridgeRouter
from transoria.tools.epub_repair import preview_epub_repair, repair_epub_file


def register(router: BridgeRouter) -> None:
    def preview(payload: Mapping[str, object]) -> dict[str, object]:
        try:
            return preview_epub_repair(
                expect_string(payload, "input_path"),
                expect_string(payload, "output_path", allow_empty=True),
            ).to_dict()
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise BridgeError.invalid_argument(str(exc)) from exc

    def apply(payload: Mapping[str, object]) -> dict[str, object]:
        try:
            return repair_epub_file(
                expect_string(payload, "input_path"),
                expect_string(payload, "output_path", allow_empty=True),
                overwrite=bool(payload.get("overwrite", False)),
            ).to_dict()
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise BridgeError.invalid_argument(str(exc)) from exc

    router.register("epub_repair.preview", preview)
    router.register("epub_repair.apply", apply)


__all__ = ["register"]
