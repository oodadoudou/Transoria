"""Bridge method handlers, grouped by domain.

Each handler module exposes a ``register(router)`` function that the router
factory calls. This keeps domain logic close to the data it owns and lets
tests register a subset for focused coverage.
"""
