"""``model_templates.*`` bridge handlers.

Read-only catalog of provider templates. The frontend's
``+ Add API Profile`` modal calls ``model_templates.list`` once on
mount to populate the template picker step. Architecture § 3.4
defines the data shape and rationale; templates live in
:mod:`transoria.model_profiles.templates`.
"""

from __future__ import annotations

from typing import Mapping

from transoria.bridge.router import BridgeRouter
from transoria.model_profiles import list_templates


def _list(_payload: Mapping[str, object]) -> dict[str, object]:
    return {"templates": [t.to_dict() for t in list_templates()]}


def register(router: BridgeRouter) -> None:
    router.register("model_templates.list", _list)


__all__ = ["register"]
