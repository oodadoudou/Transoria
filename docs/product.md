# Product Requirements

Status: Active
Last reviewed: 2026-06-20

## Purpose

Transoria is a lightweight desktop application for novel translation workflows.
It processes EPUB and TXT novels from folders and writes results to output
folders without modifying originals.

Users do not manage project files. Runtime cache, prompt presets, model
profiles, settings, and API keys are internal application state.

## Product Areas

### Model Library

The Model page manages shared API profiles for Translation, Glossary
Extraction, and Glossary Review.

Requirements:

- support OpenAI-compatible, Google, Anthropic, Sakura, and custom providers
- store model profile bodies separately from API keys
- allow multiple API keys per profile
- show raw keys in the edit modal as an explicit product choice
- support inline test/fetch actions before a profile is saved
- allow separate active model selections for Translation, Glossary Extraction,
  and Glossary Review
- keep provider connection, rate/token limits, and profile-level timeout
  defaults on the model profile; keep workflow retry controls and
  task-execution timeout in module Settings, not Run pages
- show a first-use onboarding flow when no usable model is configured, so a
  user can choose a provider, paste an API key, and activate one profile for
  all LLM workflows

### Translation

Translation processes every supported EPUB/TXT file under the configured input
folder.

Requirements:

- preserve EPUB structure and non-text assets
- never translate filenames
- write translated files as `<OriginalName>-<targetTag>.<ext>`
- optionally write bilingual outputs to a shared bilingual subfolder
- allow user-managed glossary entries
- warn about conflicting glossary entries without blocking import or editing
- allow text-preserve rules
- allow pre-translation and post-translation replacement rules
- support start, stop, and continue
- report progress, token usage, failed subtasks, and artifacts
- expose a pop-out request log for task-scoped model calls, including request
  status, timing, token usage, cached input tokens, local workflow events,
  errors, and model responses
- report the count of low-confidence segments and how many kept
  source-language characters, so the user can decide whether to proofread;
  richer per-segment risk categories are available on the Proofreading page
- auto-open the output folder after successful completion when enabled, only
  when the current session observed the task transition from running to
  completed

### Proofreading

Proofreading provides a per-task review surface for translation runs with
cached subtasks, including completed, stopped, or failed tasks that still have
reviewable cached output.

Requirements:

- list translation tasks that have at least one cached subtask
- show segments per task with default focus on low confidence, source residue,
  and possible duplicates
- support filters for low confidence, source residue, possible duplicate, model
  anomaly, untranslated, format-rescue, glossary-not-applied, and term
  inconsistency risks
- allow clearing all active risk filters in one action
- order the visible queue by risk priority before original segment order
- mark segments where the model retained source-language characters, repeated
  translations, glossary misses, term consistency issues, or model-output
  anomalies with dedicated chips
- show a collapsible term-audit panel grouped by glossary entry
- expose original text as readable selectable text without making the source
  editable
- allow inline edits to the translation and write them back to cache
- allow per-segment retranslation in isolation, without rerunning the full
  task
- allow batch retranslation of the current filtered list with progress
- regenerate output files (TXT/EPUB/bilingual) from the current cache state
- never silently overwrite user edits during regeneration

### Glossary Extraction

Glossary Extraction analyzes source novels and generates terminology artifacts
that the user may later import into Translation.

Requirements:

- process all supported EPUB/TXT files under the configured input folder
- generate per-source XLSX, JSON, and references TXT files
- optionally generate a combined folder-level glossary
- use hyphenated output names such as `<NovelName>-Glossary.xlsx`
- write decode issue artifacts when response parsing problems occur
- support start, stop, and continue
- auto-open the output folder after successful completion when enabled
- send completed glossary artifacts and novel background to Glossary Review for
  confirmation before review starts
- never automatically trigger Translation

### Glossary Review

Glossary Review takes glossary XLSX files from Glossary Extraction, compares
them against reference TXT files, and writes a final reviewed XLSX.

Requirements:

- live as a standalone top-level module, not under General Tools
- support XLSX glossary input only
- discover candidate XLSX and TXT files from the configured input folder
- let the user choose one glossary XLSX when multiple candidates exist
- let the user choose zero or more reference TXT files, while recommending
  references in the normal workflow
- default the final output filename to `glossary-review-final.xlsx`, with a
  user-configurable filename setting
- run multi-round AI review with round-aware progress
- expose a pop-out request log for review model calls
- write only changed rows to the change report
- make report cells copyable and allow restoring deleted rows into the final
  sheet
- support in-app final XLSX editing, sorting, multi-select, bulk delete, and
  save-back to the final file
- import the final XLSX into the Translation glossary as replace or append
- avoid duplicate imported rows when all row fields are identical

### General Tools

General Tools contains deterministic local utilities that do not call LLMs:
Batch Replacement plus the EPUB Tools workspace.

Requirements:

- import TXT replacement rules and readable `.red` rule containers
- accept `src->dst`, `src#->#dst`, and `src# -> #dst`
- treat matched `#` markers adjacent to the arrow as delimiter decoration only
- preserve unrelated `#` characters elsewhere in a rule
- apply rules to TXT and EPUB files
- preserve EPUB structure
- write replacement outputs without overwriting source files unless configured
- produce an occurrence report for completed replacement runs
- compress EPUB files or folders while preserving archive validity
- merge selected EPUB or TXT documents in user-defined order
- convert EPUB files or folders to UTF-8 TXT outputs with reports
- convert TXT novels to EPUB with heading presets, optional cover, and validated
  output archives
- read and edit EPUB title, authors, and cover without requiring an LLM task
- repair common malformed EPUB XHTML/XML issues and report validation results

## Prompt Presets

Translation, Glossary Extraction, and Glossary Review each have their own
prompt preset library.

Requirements:

- seed Chinese and English system presets
- show only the system preset matching the current UI language, plus custom
  presets
- keep system presets read-only
- allow duplicating system presets into editable custom presets
- fall back to the seeded default when the active preset is unset, missing, or
  disabled
- keep thinking guidance out of user-editable prompt preset content
- build preview prompts with the same backend function used by the runners

## Task Runtime

Long-running Translation, Glossary Extraction, and Glossary Review tasks support
resumable cache, cooperative stop, continue-from-cache, retry,
failed-subtask reporting, token accounting, and progress snapshots.

Their progress snapshots report elapsed time as accumulated active runtime, not
wall-clock time since task creation. Each LLM task can also write a bounded
`request-events.jsonl` log for the request-log window. That log retains
provider requests, model responses, failed attempts, and local workflow events
such as quality-exhaustion preservation records.

Batch Replacement, EPUB Compression, EPUB Merge, EPUB to TXT, and TXT to EPUB
are single-pass tool tasks. They support start/stop and artifact/report reading;
pause and continue are rejected with a `single_pass` reason when exposed.

EPUB Metadata and EPUB Repair are direct bridge actions, not cached task runs.

## Updates

Update v1 checks releases/tags, compares versions, shows release notes, opens a
release page, and downloads a matching platform asset when one exists. Windows
packaged builds can stage a downloaded ZIP update into the current install
folder when the install root is writable and the ZIP payload has the expected
layout.

Rollback and background auto-install are out of scope.

## Non-Goals

- game engine formats
- subtitle formats
- user-visible project-file workflows
- copying page maps, names, or architecture from reference material
- exposing task-cache location controls in normal settings
