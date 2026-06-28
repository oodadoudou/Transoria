# Frontend Implementation Guide

Status: Active
Last reviewed: 2026-06-28

## Stack

- React 18
- TypeScript
- Vite
- Zustand
- CSS modules

The frontend is a local desktop UI. Python owns runtime, persistence, file I/O,
LLM calls, and packaging.

## Shell

Current shell:

- permanent left rail
- breadcrumb/subnav at top
- main content region
- status bar/inspector only on Translation, Glossary Extraction, and Glossary
  Review Run pages
- update modal and toast host at app root

Left rail modules:

- Model
- Translation
- Glossary Extraction
- Glossary Review
- General Tools
- App Settings

Translation pages:

- Run
- Glossary
- Proofreading
- Presets
- Prompt
- Settings
- Rules
  - Text Preserve
  - Pre-translation Replacement
  - Post-translation Replacement

Glossary pages:

- Run
- Presets
- Prompt
- Settings

Glossary Review pages:

- Run
- Review
- Presets
- Prompt
- Settings

General Tools pages:

- Batch Replacement
- EPUB Tools
- EPUB Compressor
- Document Merger
- EPUB to TXT
- TXT to EPUB
- EPUB Metadata
- EPUB Repair

## State

Production state starts empty/idle. Do not add realistic mock jobs, personal
paths, generated-looking glossary entries, or fake progress to production
stores.

Important stores:

- `useTaskStore`: route state and Translation glossary edit buffer
- `useSettingsStore`: settings drafts, auto-save, Save/Reset state
- `useRuntimeStore`: active task id, snapshots, failures, polling, auto-open
- `useModelProfilesStore`: API profile list and active selection
- `usePromptPresetsStore`: prompt preset list, body reads, active selection
- `useWorkflowPresetsStore`: per-module bundles of model, prompt, source
  language, and target language
- `useToastStore`: user-visible toasts

## Bridge

Components call typed methods from `frontend/src/bridge/client.ts`. The client
uses `frontend/src/bridge/transport.ts` for `/api/<method>` calls and
`frontend/src/bridge/native.ts` for native dialogs/open/reveal actions.

Do not duplicate backend parsing, validation, lifecycle, or artifact logic in
React. The frontend may shape UI state, filter visible rows, and debounce
settings writes.

## Localization

UI display text lives under `frontend/src/locales/`.

Rules:

- no new hardcoded user-facing text in components
- update English, Chinese, and locale types together
- keep message keys stable
- `AppSettings.interface_language` drives the active UI language

## Settings Pages

Settings pages use compact rows and shared controls:

- folder paths: `FolderPickerRow`
- numbers: `NumberField`
- toggles/options: `Segmented` or shared switch controls
- text: `TextField`
- save/reset: `SettingsToolbar`

Auto-save is debounced. Explicit Save uses `saveNow({ explicit: true })` and is
the path that should produce a saved toast. Auto-saves stay quiet unless there
is an error.

`FolderPickerRow` keeps up to five recently used folder paths in frontend
localStorage when a page supplies a history key. Histories are scoped by
module, tool, and field rather than shared globally. This is convenience UI
state only and is not written into backend settings schema.

## Model Page

The Model page manages the shared profile catalog:

- provider templates from `model_templates.list`
- create/edit/delete profile
- full key reads for edit mode
- set API keys
- test connection
- fetch model list
- select active profile for Translation, Glossary Extraction, or Glossary
  Review through quick-switch or model profile actions

Provider connection, rate/token limits, and profile-level timeout defaults
belong to model profiles. Workflow transport retry controls live on each module
Settings page. Each LLM module also exposes the timeout used for task
execution; it overrides the profile timeout for that run. Run pages stay
focused on execution and status.

## Prompt Pages

`PromptConfigPage` is shared by Translation, Glossary Extraction, and Glossary
Review.

Current behavior:

- list system presets matching current UI language plus all custom presets
- system presets are view-only and can be duplicated
- custom presets can be edited, duplicated, or deleted
- list rows preview the system prompt content
- active selection falls back to locale default when needed
- modal exposes name, description, and system prompt only
- thinking guidance is not editable prompt preset content

## Preset Pages

`WorkflowPresetsPage` is shared by Translation, Glossary Extraction, and
Glossary Review.

Current behavior:

- each preset belongs to one module
- a preset stores name, model profile id, prompt preset id, source language,
  and target language
- users can create presets in a modal, edit, duplicate, delete, or apply
  presets
- applying a preset updates active model, active prompt, source language, and
  target language together
- the preset list contains only user-created presets; defaults are used only
  as initial values in the create dialog
- empty states say there are no presets and point the user toward creating one

## Run Pages

Translation, Glossary Extraction, and Glossary Review Run pages are
execution/status surfaces:

- compact active configuration bar with preset, model, and prompt selectors
- quick-switch modals for preset, model, and prompt
- progress ring
- completed/failed/remaining/elapsed/speed stats
- processed/running subtask counter with the longest running subtask age
- chunk status grid (split-child placeholder rows are filtered out)
- failed subtask list
- `RunControls`
- a compact request-log toggle inside `RunControls`

Run pages do not contain folder selectors, language selectors, provider
credentials, runtime limit fields, or prompt editors.

When the request-log toggle is enabled, `RequestLogPanel` opens as a modal
window. It is shared by Translation, Glossary Extraction, and Glossary Review,
polls `read_request_events` while open, shows request
status/phase/duration/tokens/last activity, and can expand a row to inspect the
model response, throttled partial streaming response, provider error body, or
local failure/quality event. It does not occupy the main run-page layout and
does not display full prompts.

The Translation Run page also shows a single completion toast when the task
finishes with at least one low-confidence segment, surfacing total and
`source_residue` counts and pointing the user to the Proofreading page. The
run-page summary stays compact; richer risk grouping is built on the
Proofreading page from per-segment tags and reasons. Each task id triggers the
toast at most once per session.

The next-step guidance card is non-modal and user-dismissable. Dismissal is
persisted in app settings so "Don't show again" remains respected after
navigation or restart.

Auto-open of the output folder is gated on actually observing a
running→completed transition during the current session; this prevents
spurious folder pops on app restart when a task is already complete on disk.

## Status Bar

`StatusBar` shows the live token chip as a button. Clicking it opens a drop-up
panel with input/output/total tokens, per-minute throughput, and per-segment
average. The panel is read-only and auto-closes on outside click.

## Proofreading Page

`ProofreadingPage` lists the segments of a translation task for manual review:

- default filters for `low_confidence`, `source_residue`, and
  `possible_duplicate`
- risk cards and filter chips for low confidence, source residue, possible
  duplicate, glossary-not-applied, term inconsistency, model anomaly,
  untranslated, and format-rescue entries
- one-click clearing for active risk filters
- risk-priority ordering before original segment order
- row status chips for structural length/punctuation drift, source residue,
  glossary-not-applied, term inconsistency, possible duplicate, and model
  anomaly; structural drift uses a neutral chip and stays out of the
  model-anomaly category
- read-only original text in a `<textarea>` with full-text select on focus
- inline editable translation
- per-segment retranslate (isolated chunk_index=0 call)
- local retranslate preset/model/prompt switches; choosing a preset fills the
  proofreading retranslate model and prompt without changing the cached task
  language metadata
- batch retranslate for the current filtered list, with progress and
  success/failure counts
- next-risk navigation
- regenerate output files from current cache
- a collapsible term-audit panel that groups glossary risks by glossary term

The page calls `proofreading.*` bridge methods exclusively. It does not
duplicate translation runtime or cache logic.

## Glossary Review Pages

Glossary Review has a standalone module with settings, run, prompt, and final
table review pages:

- Settings discovers candidate XLSX and TXT files from the input folder.
- Run shows task id, round-aware progress, token stats, report entry points,
  and import-to-Translation actions when artifacts exist.
- Review reads and edits the final XLSX, supports sorting, multi-select,
  bulk delete, and save-back through bridge methods.
- The change report modal shows only changed rows, keeps cells copyable, and
  can restore deleted rows into the final table.

## Rule Tables

Translation Glossary, Text Preserve, Translation Replacement, and Batch
Replacement use shared table components where practical.

Search filters visible rows. Bulk operations must resolve filtered rows back to
stable item references or ids before mutating the full list.

## General Tools UI

General Tools has two top-level entries: Batch Replacement and EPUB Tools. EPUB
Tools opens a workspace with tool cards and dedicated tool pages for EPUB
Compressor, Document Merger, EPUB to TXT, TXT to EPUB, EPUB Metadata, and EPUB
Repair.

Batch Replacement owns its rules in component state. Settings persist input and
output folders plus replacement options. Import parses TXT rules, and readable
`.red` containers where supported, through the backend before validating the
parsed rules. Completed runs show output artifacts and a replacement occurrence
report when available.

EPUB Compressor, Document Merger, EPUB to TXT, and TXT to EPUB use bridge task
snapshots, reports, artifacts, and stop controls. They are single-pass tools and
do not expose resumable pause/continue UX.

EPUB Metadata and EPUB Repair are direct bridge actions. They should show clear
success/error states and not be represented as cached runtime tasks.

## Verification

After frontend or bridge type changes:

```bash
cd frontend && npx tsc -b
cd frontend && npm run build
```

Before finishing UI work, check:

- locale strings are wired
- no production mock data was added
- Run pages remain execution-only
- settings changes persist through the settings store
- bridge types match backend payloads
