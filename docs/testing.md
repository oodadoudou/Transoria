# Test Strategy

Status: Active
Last reviewed: 2026-06-20

## Goal

Tests should prove documented behavior with the smallest useful surface. Add
tests when a behavior can regress, especially parser, writer, bridge, runtime,
LLM decoding, and user-facing workflow contracts.

## Layers

## Test Suite Layout

Default pytest collection is limited to `tests/unit/`. Unit tests are grouped
by implementation area:

- `tests/unit/app/`
- `tests/unit/bridge/`
- `tests/unit/formats/`
- `tests/unit/llm/`
- `tests/unit/models/`
- `tests/unit/prompts/`
- `tests/unit/runtime/`
- `tests/unit/settings/`
- `tests/unit/tools/`
- `tests/unit/utils/`
- `tests/unit/workflows/`

Public, non-sensitive fixtures live under `tests/fixtures/public/`. Real novel
fixtures, local smoke outputs, and other personal/private assets live under
`tests/private/` and must not be part of default automated collection. Live API
smoke helpers live under `tests/smoke/live/` and must be run explicitly.

## Git Tracking Policy

Keep public, deterministic test code trackable:

- `tests/README.md`
- `tests/conftest.py`
- `tests/helpers/`
- `tests/unit/`
- `tests/fixtures/public/`
- `tests/smoke/live/` helper scripts

Keep private and generated test material untracked:

- `tests/private/`
- real EPUB/TXT novels or user-provided source files
- copied task-cache directories, request logs, and generated smoke output
- API keys, model-profile key stores, tokens, and machine-local paths

When adding a new fixture, make it small, synthetic, and non-sensitive before
placing it under `tests/fixtures/public/`. Otherwise keep it under
`tests/private/` and document only the reproduction procedure.

### Pure Logic

Use focused unit tests for deterministic functions:

- prompt assembly
- JSONL decoders
- glossary normalization/combining/frequency
- translation preprocessing/postprocessing
- replacement rule parsing/application
- EPUB compression/merge/convert utilities
- TXT to EPUB heading detection, style validation, and EPUB writing
- confidence heuristics
- retry classification

Prefer parametrized tests when one behavior has several input spellings.

### Stateful Components

Use fake transports/runners for:

- task cache
- task executor
- rate limiters
- key rotation
- model profile and prompt stores
- settings store
- LLM client retry/streaming behavior
- LLM request-event logging, lifecycle/partial-response diagnostics, and
  capped/tail reads

Do not hit real networks in automated tests.

### Bridge Contracts

Bridge tests verify:

- registered method names
- request validation
- error codes/details
- settings partial-save behavior
- active model/prompt selection
- task lifecycle methods
- request log methods for Translation, Glossary Extraction, and Glossary Review
- artifact recovery
- import/export handlers

Frontend bridge types should stay aligned with these payloads.

### Orchestrators

Use synthetic files and fake LLM transports to verify:

- Translation TXT/EPUB output
- bilingual output
- glossary threading
- text preserve and replacement ordering
- low-confidence retry behavior
- request-log event emission for LLM calls, local workflow failure records,
  provider error bodies, and streaming progress without storing full prompts
- runtime elapsed-time accounting as active runtime rather than task age
- Glossary artifact generation
- Glossary Extraction Send to Review handoff
- Glossary Review input discovery, multi-round progress, report/final XLSX
  artifacts, and final table editing
- General Tools plans, task reports, artifact recovery, and direct EPUB
  metadata/repair actions
- decode issue reporting
- combined glossary output

### Real Fixtures

Keep real-fixture tests sparse and high-signal. Use them for parser/writeback
behavior that synthetic samples cannot cover well, especially EPUB structure
preservation and mixed encodings. Keep them under `tests/private/` so they are
opt-in and cannot accidentally become part of the public unit suite.

## Adding Tests

1. Identify the user-visible or contract behavior.
2. Check whether an existing test can be extended.
3. Pick the narrowest layer that proves the behavior.
4. Use fake transports for LLM paths.
5. Run the focused test file first, then a broader suite when the blast radius
   is high.

## Command Patterns

Backend focused tests:

```bash
pytest tests/unit/tools/test_tools_replacement.py -q
pytest tests/unit/tools/test_tools_epub_compressor.py -q
pytest tests/unit/tools/test_tools_epub_merger.py -q
pytest tests/unit/tools/test_tools_epub_converter.py -q
pytest tests/unit/tools/test_tools_txt_to_epub.py -q
pytest tests/unit/bridge/test_bridge_contract_surface.py -q
pytest tests/unit/bridge/test_bridge_task_service.py -q
pytest tests/unit/llm/test_llm_client.py -q
pytest tests/unit/workflows/test_workflows_translation_runner.py -q
```

Frontend checks:

```bash
cd frontend && npx tsc -b
cd frontend && npm run build
```

Packaging smoke:

```bash
python build_macos.py --skip-frontend
python build_windows.py --skip-frontend
```

Run packaging only when the task touches packaging or release behavior.

## Out Of Scope

- line/branch coverage targets as a goal
- tests for dataclass/enum mechanics
- real API calls in automated test runs
- performance benchmarks in the unit suite
