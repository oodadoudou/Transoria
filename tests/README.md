# Test Suite Layout

Default pytest collection is limited to `tests/unit/`.

## Unit Categories

- `app/`: app constants, path helpers, and package scaffold checks
- `bridge/`: bridge contracts, routers, handlers, and task-service behavior
- `formats/`: EPUB/TXT parsing and writing
- `llm/`: providers, client, retry, streaming, usage, and decoders
- `models/`: model profile store and templates
- `prompts/`: prompt store and preview behavior
- `runtime/`: task cache, executor, rate limits, key pools, and subtasks
- `settings/`: settings store
- `tools/`: deterministic EPUB/TXT/general tools
- `utils/`: shared test utilities and transport helpers
- `workflows/`: translation, glossary extraction, glossary review, confidence,
  statistics, and workflow orchestration

## Fixtures

- `tests/fixtures/public/`: small non-sensitive fixtures safe for automated unit tests
- `tests/private/fixtures/`: local real novel fixtures and other private assets
- `tests/private/smoke_out/`: generated live-smoke outputs

Do not move real novels or user cache exports into public fixtures.

## Git Tracking Policy

Track deterministic tests under `tests/unit/`, shared helpers under
`tests/helpers/`, opt-in live smoke scripts under `tests/smoke/live/`, and small
synthetic fixtures under `tests/fixtures/public/`.

Do not track `tests/private/`, real EPUB/TXT novels, task-cache exports, live
smoke outputs, API keys, or any file copied from a user's local run directory.

## Smoke

Live API smoke helpers live under `tests/smoke/live/` and are opt-in only.
They may spend tokens and read local model profiles from `.transoria-cache/`.
