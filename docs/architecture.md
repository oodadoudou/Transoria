# Architecture

Status: Active
Last reviewed: 2026-06-28

This document describes the implemented cross-cutting architecture. Module
pipelines live in `docs/modules/`.

## Runtime Shape

Transoria is a local desktop app with a Python backend and a React/Vite
frontend.

```text
frontend/
  React + TypeScript + Zustand + CSS modules
  frontend/src/bridge/client.ts
          |
          | POST /api/<method>
          v
transoria/bridge/
  http_server.py  -> router.py -> handlers/
  task_service.py -> runtime/workflows/tools
          |
          v
transoria/
  workflows/translation/
  workflows/glossary/
  workflows/glossary_review/
  tools/replacement.py
  tools/epub_compressor.py
  tools/epub_merger.py
  tools/epub_converter.py
  tools/txt_to_epub.py
  tools/epub_metadata.py
  tools/epub_repair.py
  runtime/
  llm/
  formats/
  settings/
  model_profiles/
  prompts.py
```

`app.py` is the launcher. Modes:

- `--dev`: Vite dev server + bridge + pywebview shell
- `--prod`: static `frontend/dist` + bridge + pywebview shell
- `--browser`: Vite dev server + bridge + default browser
- `--bridge-only`: bridge only for API testing

## Dependency Rules

- `transoria/domain.py` stays stdlib-only.
- `formats/` does not import workflows or LLM code.
- `workflows/` can compose formats, prompts, LLM clients, and runtime.
- `tools/` contains local, non-LLM utilities such as replacement and EPUB
  processing.
- `bridge/` owns JSON payload validation and calls stores/workflows/tools.
- `frontend/` talks to Python only through `frontend/src/bridge/`.

## Task Kinds

Implemented task kinds:

- `translation`
- `glossary`
- `glossary_review`
- `replacement`
- `epub_compress`
- `epub_merge`
- `epub_convert`
- `txt_to_epub`

Translation, Glossary Extraction, and Glossary Review use the async executor and
support resumable stop/continue in the current run UI. Replacement, EPUB Compression, EPUB
Merge, EPUB to TXT, and TXT to EPUB are synchronous single-pass task tools:
pause and continue return `task.invalid_transition` with
`details.reason = "single_pass"` where those bridge methods are exposed. EPUB
Metadata and EPUB Repair are direct bridge actions, not task kinds.

## Task States

`TaskStatus` values:

- `pending`
- `running`
- `stopping`
- `stopped`
- `pausing`
- `paused`
- `completed`
- `failed`

`SubtaskStatus` values:

- `pending`
- `running`
- `completed`
- `failed`
- `skipped`

`skipped` subtasks are split-parent placeholders, not active work units.
Snapshot `progress.total` and rates count real work units only: pending,
running, completed, and failed. The skipped count remains available for
diagnostics.

Frontend progress is polling-based. There is no implemented event stream.
`useRuntimeStore` polls snapshots and failures through the bridge. LLM request
records are also read through bridge polling when the user opens the request
log window.

## Lifecycle Controls

Task namespaces generally expose:

- `<kind>.start_task`
- `<kind>.stop_task`
- `<kind>.pause_task`
- `<kind>.continue_task`
- `<kind>.probe_continuable`
- `<kind>.read_snapshot`
- `<kind>.list_recent_tasks`
- `<kind>.list_failed_subtasks`
- `<kind>.read_artifacts`

TXT to EPUB does not expose pause/continue methods in its UI bridge surface.
Replacement also exposes `replacement.read_replacement_report`. EPUB
Compression, EPUB Merge, EPUB to TXT, and TXT to EPUB expose tool-specific
preview/report methods under their bridge namespaces. EPUB Metadata and EPUB
Repair do not use this shared task surface.

Behavior:

- `start_task` creates a new task id and replaces the active cache for that
  kind. User output files are not deleted.
- `stop_task` requests cooperative stop. Running subtasks are allowed to settle
  according to the executor path; pending work remains continuable for LLM
  tasks.
- `pause_task` is supported by Translation, Glossary Extraction, and Glossary
  Review. It gates new dispatch and lets in-flight LLM requests finish before
  `paused`.
- The current frontend run controls expose Stop/Continue, not a separate Pause
  button.
- `continue_task` restarts Translation, Glossary Extraction, or Glossary Review
  from an existing stopped, paused, or failed cache with pending/failed work.
- `probe_continuable` scans matching task cache under the current settings and
  reports whether there is remaining work.

The frontend confirms destructive user actions where needed. The backend
remains authoritative for invalid transitions and live-task conflicts.

When the on-disk record is in a terminal state (`completed`, `failed`,
`stopped`) but the in-process registry still tracks the task as live, the task
service clears the stale registry entry on next snapshot/continue/purge before
acting. When the on-disk record is transient (`pending`, `running`, `stopping`,
or `pausing`) and the registered live task has a stalled heartbeat beyond the
larger of 900 seconds or `timeout_seconds + 120` seconds, snapshot and continue
reconciliation mark that registry entry done, flip the cache to `stopped`, and
reset `running` subtasks to `pending`. This prevents continue/retranslate calls
from being blocked by a phantom live task without interrupting a merely slow
in-flight model request.

## Continuable Cache

Translation, Glossary Extraction, and Glossary Review are continuable when:

- a cache record for the current kind and settings exists
- status is `stopped`, `paused`, or `failed`
- there is at least one pending or failed subtask
- no healthy conflicting live task is still registered

The probe normalizes configured input/output paths before comparing them to
task metadata, so equivalent macOS path spellings do not hide a valid cache.

Single-pass tool tasks always probe as non-continuable.

## Storage

Default source-mode cache root is `.transoria-cache` at repo root. Packaged
macOS builds use `~/Library/Application Support/Transoria`; packaged Windows
builds prefer a portable `User Data` folder next to `Transoria.exe` and fall
back to `%LOCALAPPDATA%\Transoria` when the portable folder is not writable.

Important files:

```text
<cache_root>/
  settings.json
  model_profiles.json
  model_profile_keys.json
  prompts.translation.json
  prompts.glossary.json
  prompts.glossary_review.json
  workflow_presets.translation.json
  workflow_presets.glossary.json
  workflow_presets.glossary_review.json
  glossary_presets/
  tasks/
    <task_id>/
      task.json
      subtasks/
      result.json
      request-events.jsonl
      glossary-review-report.json
      replacement-report.json
      epub-compress-report.json
      epub-merge-report.json
      epub-convert-report.json
      txt-to-epub-report.json
      debug/
        <subtask_id>.json
```

`tasks/<task_id>/debug/` holds per-subtask raw LLM exchange logs (one JSON file
per subtask) used for diagnosing decode/retry failures. Each file contains the
list of attempts (user prompt, raw response) and, when the subtask ended in
failure, a `terminal_error` field. Filenames use the subtask id so split-child
runs do not overwrite their parent's log.

LLM tasks also append compact per-request events to `request-events.jsonl`.
The request-log UI uses this file to show request status, model metadata,
duration, token usage, cached input tokens, errors, and the final model
response. The same log also retains local workflow quality/failure events so
failed and re-run attempts remain visible for debugging. Appends are capped by
file size, and normal reads use only the recent tail to avoid repeatedly
parsing large task logs.

Settings, model profiles, prompt presets, workflow presets, task headers, and
result payloads are JSON-backed. Task headers live in `task.json`; workflow
result payloads live in `result.json` when the bridge needs a stable artifact
summary for `read_artifacts`. Writes use temporary files plus `os.replace`
where the store owns atomic persistence.

API keys are stored in `model_profile_keys.json`. Profile summaries expose key
status and masking. The edit modal may request full keys by calling
`model_profiles.read_full`.

Model profiles are provider connection profiles: endpoint, model id, API key
status, key rotation, profile timeout defaults, rate/token limits, sampling,
and thinking controls. Retry budgets belong to module settings so Translation,
Glossary Extraction, and Glossary Review can tune request retry behavior
independently. Workflow task execution also uses the module's
`timeout_seconds`; the task service resolves the active profile, then replaces
the profile timeout with that module timeout before launching the workflow.
Legacy retry/backoff keys in old model profile JSON are ignored on load.

## Settings

Settings modules:

- `app`
- `translation`
- `glossary`
- `glossary_review`
- `replacement`

`settings.load_all` returns the full tree. `settings.save_partial` accepts a
module name and patch, applies valid fields, and returns `rejected_fields` for
invalid fields. `settings.reset_module` restores one module's defaults.

Frontend module settings auto-save through a debounce and expose explicit Save
and Reset buttons.

## Models

Model profiles are shared, while active selections are per module:

- `active_translation_model_id`
- `active_glossary_model_id`
- `active_glossary_review_model_id`

Profiles include provider format, base URL, model id, provider connection/rate
limits, profile timeout defaults, optional sampling settings, custom headers,
thinking level, and force-thinking toggle. API keys are set through
`model_profiles.set_api_key`. Workflow retry budgets live in module settings,
not in model profiles. Workflow timeout also lives in each module's settings at
task execution time and overrides the profile timeout for that run.

Provider templates are read-only and exposed through `model_templates.list`.

## Prompts

Translation, Glossary Extraction, and Glossary Review use separate preset
stores. Seeded system presets are read-only. Custom presets store user-editable
system prompt and description content.

`thinking_prompt` may exist in older JSON payloads, but create/update paths do
not expose it as user content. Runtime reasoning guidance is assembled from
system-level code in `transoria/prompts.py`.

## Workflow Presets

Workflow presets are module-scoped bundles for Translation, Glossary
Extraction, and Glossary Review. Each preset stores a display name, model
profile id, prompt preset id, source language, target language, and enabled
flag.

`workflow_presets.apply` validates that the referenced model and prompt still
exist, then updates the module's active model, active prompt, source language,
and target language together. Presets are stored separately from prompt
presets and are not seeded with built-in defaults. Applying a preset is a
one-time settings update; it does not lock the module to that preset, and later
single-field changes simply make the current configuration a custom
combination.

## Bridge Errors

All bridge failures use a structured envelope:

```ts
type BridgeError = {
  code: string;
  message: string;
  message_key?: string;
  details?: Record<string, unknown>;
  retryable: boolean;
};
```

Common codes:

- `bridge.invalid_argument`
- `bridge.not_found`
- `bridge.conflict`
- `bridge.permission_denied`
- `bridge.io_error`
- `llm.request_failed`
- `llm.transport_error`
- `llm.http_error`
- `llm.malformed_response`
- `llm.unsupported_provider`
- `task.invalid_transition`
- `task.not_running`
- `task.artifact_corrupt`
- `update.network_unavailable`
- `update.malformed_response`

The frontend branches on `code`, not on the human message.

## Artifacts

Each task writes or mirrors an artifact payload for `read_artifacts`.

Translation artifacts include output folder, translated files, bilingual files,
statistics JSON/TXT paths, processed file list, completed segments, and total
segments.

Glossary artifacts include per-novel XLSX/JSON/references paths, optional
combined artifact, statistics JSON path, and optional decode issue path.

Replacement artifacts include output files, total replacement count, and
optional replacement report path. Completed replacement reports are mirrored in
memory so the UI can read them after clean cache cleanup.

EPUB Compressor artifacts include output files, compressed/failed counts, and a
detailed compression report. It rewrites EPUB archives but preserves book title
metadata.

EPUB Merge artifacts include merged EPUB/TXT output files, merged/failed counts,
and a detailed merge report.

EPUB to TXT artifacts include generated TXT output files, converted/failed
counts, and a detailed conversion report.

TXT to EPUB artifacts include generated EPUB output files, converted/failed
counts, and a detailed conversion report.

## Snapshot Low-Confidence Summary

Snapshot `progress.elapsed_seconds` is accumulated from active runtime
intervals. It is task running time, not wall-clock time since task creation, so
paused, stopped, completed, and old cached tasks do not continue aging in the
UI.

Translation snapshots include a `low_confidence` block summarising
proofreading-grade segments accumulated across all subtasks:

```ts
{
  total: number;          // segments flagged as low confidence
  source_residue: number; // subset where the model kept source-language
                          // characters (tagged "source_residue")
}
```

The block is computed on the fly while a task is running and frozen into the
record metadata at completion so post-purge reads keep the same numbers. It
drives the compact run-page completion toast. It is not the full proofreading
risk index: the Proofreading page derives its queue from each loaded segment's
`low_confidence`, `tags`, and `reasons`, including source residue, possible
duplicates, model-output anomalies, untranslated items, and format-rescue
entries.
