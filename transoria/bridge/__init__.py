"""Frontend ↔ backend bridge.

Defines a JSON-in / JSON-out RPC surface that the desktop shell exposes to
the React frontend. The contract lives at
``docs/active/frontend-backend-bridge-contract.md``; this package implements
it.

Public API:

- :class:`BridgeError` — raised by handlers; serialized to the structured
  error payload defined in the contract.
- :class:`BridgeRouter` — registers and dispatches handlers by method name.
- :func:`build_default_router` — wires the default handler set.
"""

from transoria.bridge.errors import BridgeError, BridgeErrorPayload
from transoria.bridge.router import BridgeHandler, BridgeRouter, build_default_router

__all__ = [
    "BridgeError",
    "BridgeErrorPayload",
    "BridgeHandler",
    "BridgeRouter",
    "build_default_router",
]
