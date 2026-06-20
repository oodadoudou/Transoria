# CLAUDE.md

Guidance for agents working in this repository.

## Project Status

Transoria is an active desktop app for Korean / Japanese novel translation into
Chinese. The backend is Python under `transoria/`; the frontend is React + Vite
under `frontend/`; packaging targets macOS `.app` / `.dmg` and Windows `.exe`.

The current user workflow is:

1. Extract a glossary from the source novel.
2. Review the glossary with reference text.
3. Import the reviewed glossary into the translation glossary table.
4. Run translation.
5. Proofread low-confidence, source-residue, and manually found issues.
6. Regenerate outputs from the edited cache.

Main product modules:

- Model profiles
- Translation
- Translation proofreading
- Glossary extraction
- Glossary review
- Prompt presets
- Replacement tools
- Workspace / app settings

## Start Here

Read these before changing behavior:

- `tasks/workflow.md` for the durable agent workflow and repository rules.
- `README.md` for user-facing workflow and release-facing feature summary.
- `docs/README.md` for documentation map.
- `docs/architecture.md` for backend/frontend boundaries and task lifecycle.
- `docs/bridge-contract.md` before touching bridge handlers or frontend bridge types.
- `docs/frontend.md` before UI work.
- `docs/modules/translation.md` for translation pipeline behavior.
- `docs/modules/glossary-extraction.md` for glossary extraction behavior.
- `docs/modules/glossary-review.md` for glossary review behavior.
- `tasks/lessons.md` for correction patterns the user has already called out.
- `tasks/todo.md` for the current active task or short-lived handoff state.
- `tasks/project-current-handover.md` for the current snapshot pointer.

`docs/` contains active project documentation that should stay aligned with the
current implementation and remain safe to track. `tasks/` is local workflow
space for active handoffs and short-lived notes. Code and tests remain the
source of truth.

`tasks/handover.md` is only a compatibility pointer. Use
`tasks/workflow.md` as the authoritative workflow file.

## Common Commands

Backend:

```bash
uv sync --extra dev
pytest -q
pytest -q tests/unit/workflows/test_file.py::test_name
```

Frontend:

```bash
cd frontend
npm install
npx tsc -b
npm run build
```

App:

```bash
python app.py
```

Use focused tests while iterating, then broader checks before a final handoff
when the change touches shared behavior.

## Architecture Notes

Translation pipeline:

```text
EPUB/TXT parsing
-> segment index
-> preprocessing
-> chunk building
-> LLM subtask runner
-> partial accept / retry / confidence checks
-> subtask cache
-> final metadata
-> output writer
-> proofreading edits
-> regenerate outputs
```

Glossary review pipeline:

```text
XLSX glossary + reference TXT candidates
-> task settings
-> cache task directory
-> multi-round review
-> changed-row report
-> final XLSX
-> in-app final table editing
-> optional import into translation glossary
```

Important implementation areas:

- `transoria/bridge/task_service.py`: task lifecycle, cache summaries, stale registry handling.
- `transoria/bridge/handlers/`: bridge surface called by the frontend.
- `transoria/workflows/translation/`: translation chunking, prompts, confidence checks, retries, output writing.
- `transoria/workflows/glossary/`: glossary extraction.
- `transoria/workflows/glossary_review/`: glossary review.
- `frontend/src/bridge/`: TypeScript bridge client and wire types.
- `frontend/src/store/`: app runtime/task state.
- `frontend/src/pages/translation/`: translation run, settings, proofreading.
- `frontend/src/pages/glossary-review/`: glossary review run, settings, report, final table editing.
- `frontend/src/locales/`: Chinese / English UI strings. Keep locale key parity.

## Release Checklist

When bumping the app version, check all version carriers:

- `pyproject.toml`
- `uv.lock` package entry for `name = "transoria"`
- `frontend/package.json`
- `frontend/package-lock.json` top-level package and root package entry
- `README.md` "What's new" / "最近更新" headings
- packaging metadata if the release task touches packaging scripts

Do not generate release-note files unless the user explicitly asks for a file.
If the user asks for GitHub release notes, write concise copy in the chat for
manual copy/paste.

Default release-note format, unless the user asks otherwise:

- Heading: `<version> Release Notes`
- Short user-facing bullets only; omit version bumps, internal refactors, and
  implementation minutiae.
- Sort by user impact / requirement size.
- Chinese bullets first, then matching English bullets.

## Working Rules

- Never commit or push unless the user explicitly asks.
- For each quick fix, provide a one-line conventional commit message.
- Keep changes surgical. Do not do unrelated refactors.
- For new features, discuss the plan before implementation unless the user has
  already approved the plan.
- Preserve auto-upgrade compatibility for existing user settings and caches.
  New, unreleased feature settings do not need legacy migration unless the user
  asks for it.
- Read files before editing. Formatting hooks or prior edits may have changed
  the file.
- Avoid adding config knobs unless there is a real current need.
- Comments default to none. Add only short WHY comments for non-obvious
  invariants or bug workarounds.
- Do not mention external reference project names in tracked files, code,
  comments, prompts, docs, commit messages, PR descriptions, or release copy.
  Describe the behavior or pattern directly.
- If the user says "continue", treat it as approval to proceed with the current
  discussed plan.
- Substantive replies should end with:
  - `**[直接执行]**`: what changed and where.
  - `**[深度交互]**`: assumptions, tradeoffs, and follow-up notes.

## Quality Priorities

Translation quality has two hard goals:

- Avoid source-language residue as much as possible.
- Preserve one-to-one alignment between source segments and translated segments.

When changing translation logic, pay special attention to:

- line-count mismatch behavior
- positional decode versus model-provided keys
- low-confidence retry behavior
- source-residue detection
- duplicate drift detection
- debug logs under the task cache
- proofreading cache writes and output regeneration

When changing glossary review logic, pay special attention to:

- XLSX-only glossary input for now
- reference TXT selection and multi-select behavior
- resumable task cache
- round-aware progress display
- report rows containing only changed entries
- final XLSX edits and import into the translation glossary
