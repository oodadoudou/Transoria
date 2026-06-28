# Transoria Documentation

Status: Active
Last reviewed: 2026-06-28

This directory describes the current implementation. Code and tests remain the
source of truth; update these docs in the same change when behavior changes.

## Start Here

- Agent workflow: `tasks/workflow.md`
- Product scope: `docs/product.md`
- Architecture: `docs/architecture.md`
- Bridge surface: `docs/bridge-contract.md`
- Frontend implementation: `docs/frontend.md`
- Test strategy: `docs/testing.md`
- Current task status: `tasks/todo.md`
- Correction lessons: `tasks/lessons.md`

## Module Docs

- Translation: `docs/modules/translation.md`
- Glossary Extraction: `docs/modules/glossary-extraction.md`
- Glossary Review: `docs/modules/glossary-review.md`
- General Tools / EPUB Tools / Batch Replacement:
  `docs/modules/general-tools.md`

## What To Read

Frontend UI work:

- `docs/frontend.md`
- the relevant module doc
- locale files in `frontend/src/locales/`

Bridge/backend work:

- `docs/architecture.md`
- `docs/bridge-contract.md`
- the matching handler under `transoria/bridge/handlers/`
- bridge client/types under `frontend/src/bridge/`

Workflow work:

- the matching `docs/modules/*.md`
- workflow code under `transoria/workflows/`
- task-service lifecycle code in `transoria/bridge/task_service.py`

Testing work:

- `docs/testing.md`
- categorized unit tests under `tests/unit/<area>/`
- public fixtures under `tests/fixtures/public/`
- private/local real fixtures under `tests/private/`
- manual/API smoke helpers under `tests/smoke/live/`

## Runtime Notes

- LLM runtime stability and provider compatibility:
  `docs/llm-runtime-stability-design.md`
- Translation runtime experiments:
  `docs/translation-runtime-experiments-plan.md`

## Historical Material

`docs/archive/` is non-authoritative. Read it only when investigating why an
old decision was made. Do not use archived material as implementation guidance.

## Documentation Rules

- Active docs describe implemented behavior and current requirements only.
- Active docs are intended to be trackable; keep them free of API keys,
  personal local paths, private source text, task-cache exports, and reference
  project implementation details.
- Keep `tasks/todo.md` for current active work only; delete completed task
  details once active docs, tests, or code cover the behavior.
- Delete temporary task plans once active docs describe the shipped behavior.
- Do not mention reference project names in tracked files.
- When a behavior changes, update the relevant active doc.
- Keep release-note copy in README/chat concise and user-facing. Use the
  workflow format from `tasks/workflow.md`: Chinese bullets first, English
  bullets second, sorted by impact, with no version-bump-only entries.
