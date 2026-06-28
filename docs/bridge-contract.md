# Frontend ↔ Backend Bridge Contract

Status: Active
Last reviewed: 2026-06-28

The React frontend calls the Python backend through a local HTTP bridge:

```text
POST /api/<method>
request:  one JSON object
success:  { "ok": true, "data": ... }
failure:  { "ok": false, "error": BridgeErrorPayload }
```

The typed frontend wrapper lives in `frontend/src/bridge/client.ts`; wire types
live in `frontend/src/bridge/types.ts`. Backend registration happens in
`transoria/bridge/router.py` and `transoria/bridge/handlers/`.

## Conventions

- Methods are dot-separated lower-snake-case names.
- Payload keys are lower-snake-case.
- Filesystem paths are POSIX-style strings on the wire.
- Timestamps are ISO 8601 strings.
- Methods with no input still take `{}`.
- Long-running task progress is polled; no event stream is implemented.
- Translation, Glossary Extraction, and Glossary Review expose task-scoped LLM
  request logs through `read_request_events`; these are polled only when the
  request-log window is open.
- Native dialogs/open/reveal are wrapped by `frontend/src/bridge/native.ts`.

## Error Envelope

```ts
type BridgeError = {
  code: string;
  message: string;
  message_key?: string;
  details?: Record<string, unknown>;
  retryable: boolean;
};
```

Frontend code must branch on `code`, not `message`.

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

## App

### `app.get_metadata`

Request: `{}`

Response:

```ts
{
  app_version: string;
  platform: "darwin" | "win32" | "linux";
  build_mode: "dev" | "packaged";
  python_version: string;
  cache_root: string;
}
```

`cache_root` is diagnostic display data, not an editable setting.

## Settings

### `settings.load_all`

Returns:

```ts
{
  app: AppSettings;
  translation: TranslationSettings;
  glossary: GlossarySettings;
  glossary_review: GlossaryReviewSettings;
  replacement: ReplacementSettings;
}
```

### `settings.save_partial`

Request:

```ts
{
  module: "app" | "translation" | "glossary" | "glossary_review" | "replacement";
  patch: Record<string, unknown>;
}
```

Response:

```ts
{
  saved_at: string;
  rejected_fields: Array<{ field: string; reason: string }>;
}
```

The backend applies valid fields and reports rejected fields instead of dropping
the whole patch when only part of it is invalid.

### `settings.reset_module`

Request:

```ts
{ module: "app" | "translation" | "glossary" | "glossary_review" | "replacement" }
```

Response: the module's default settings object.

### Settings Shapes

`AppSettings`:

```ts
{
  interface_language: "en" | "zh";
  color_theme: "light" | "dark";
  ui_scale: number;
  proxy_url: string;
  task_sound_notifications: boolean;
  active_translation_model_id: string | null;
  active_glossary_model_id: string | null;
  active_glossary_review_model_id: string | null;
  active_translation_prompt_id: string | null;
  active_glossary_prompt_id: string | null;
  active_glossary_review_prompt_id: string | null;
  skipped_update_version: string;
  workflow_next_step_dismissed: boolean;
}
```

`TranslationSettings` includes folders, languages, Chinese output form,
bilingual options, `context_lines`, `request_retry_attempts`,
`low_confidence_max_retries`, `timeout_seconds`,
`auto_open_output_folder`, `translation_glossary`, `text_preserve_rules`,
`pre_replacements`, and `post_replacements`.

`GlossarySettings` includes folders, languages, Chinese output form,
`reference_examples_per_term`, `max_term_display_length`, `minimum_frequency`,
`chunk_token_limit`, `merge_folder_glossary`, `keep_identical_src_dst`,
`normalize_widths`, `novel_background`, `request_retry_attempts`,
`timeout_seconds`, and `auto_open_output_folder`.

`GlossaryReviewSettings` includes `input_folder`, `selected_xlsx_path`,
`selected_reference_paths`, `source_language`, `target_language`, `output_filename`,
`novel_background`,
`review_rounds`, `batch_size`, `retry_attempts`, `timeout_seconds`, and
`auto_open_output_folder`. Review output is written under `input_folder` using
`output_filename`; there is no separate review output folder.

`ReplacementSettings` includes folders, `allow_same_folder`,
`output_naming_suffix`, `overwrite_existing`, `apply_to_epub_titles`, and
`stop_on_first_error`.

## Dialogs

Registered methods:

- `dialogs.choose_input_directory`
- `dialogs.choose_output_directory`
- `dialogs.choose_glossary_file`
- `dialogs.choose_replacement_rules_file`
- `dialogs.choose_save_path`
- `dialogs.open_directory`
- `dialogs.reveal_file`

The frontend normally reaches these through native wrappers. Browser/dev
fallbacks raise typed errors for unsupported native actions.

## Model Templates

### `model_templates.list`

Request: `{}`

Response:

```ts
{ templates: ProviderTemplate[] }
```

Templates are read-only provider starting points with defaults, hint models,
fetch support flags, and field hints.

## Model Profiles

Registered methods:

- `model_profiles.list`
- `model_profiles.read_full`
- `model_profiles.create`
- `model_profiles.update`
- `model_profiles.delete`
- `model_profiles.set_api_key`
- `model_profiles.select_active`
- `model_profiles.test_connection`
- `model_profiles.fetch_model_list`

Profile summaries never include raw API keys. `read_full` returns raw keys for
the edit modal. `test_connection` and `fetch_model_list` accept either an
existing profile id or inline draft credentials.

Model profiles describe provider connection, model id, key rotation, profile
timeout defaults, rate/token limits, sampling overrides, and thinking controls.
They do not own workflow retry budgets. Translation and Glossary Extraction use
`request_retry_attempts`; Glossary Review keeps its existing `retry_attempts`
field. Translation and Glossary Extraction apply an internal same-key transport
retry cap of 3 even if the setting is higher; lower settings still reduce the
effective budget. Workflow task execution uses the module's `timeout_seconds`:
the task service resolves the active profile, then replaces its timeout with
the module setting before launching the workflow. Legacy persisted model
profile keys named `retry_attempts`, `retry_initial_backoff_seconds`, or
`retry_max_backoff_seconds` are ignored on load.

Active model selection is per module:

```ts
{
  module: "translation" | "glossary" | "glossary_review";
  profile_id: string | null;
}
```

## Prompts

Registered methods:

- `prompts.list`
- `prompts.read`
- `prompts.create`
- `prompts.update`
- `prompts.duplicate`
- `prompts.delete`
- `prompts.select_active`
- `prompts.preview`
- `prompts.reset_to_default`

Prompt kind is `"translation"`, `"glossary"`, or `"glossary_review"`.

Prompt body fields exposed to the frontend:

```ts
{
  id: string;
  name: string;
  kind: "translation" | "glossary" | "glossary_review";
  description: string;
  enabled: boolean;
  is_default: boolean;
  is_system: boolean;
  system_prompt: string;
}
```

`thinking_prompt` is not a user-editable field. `prompts.create` ignores it,
`prompts.update` rejects it, and `prompts.duplicate` clears it.

`prompts.preview` returns the rendered prompt plus thinking clamp metadata:

```ts
{
  prompt: string;
  thinking: boolean;
  clamped: boolean;
  active_thinking_level: "off" | "low" | "medium" | "high" | null;
}
```

## Workflow Presets

Registered methods:

- `workflow_presets.list`
- `workflow_presets.create`
- `workflow_presets.update`
- `workflow_presets.duplicate`
- `workflow_presets.delete`
- `workflow_presets.apply`

Workflow presets are module-scoped bundles of model profile, prompt preset,
source language, and target language. Preset kind is `"translation"`, `"glossary"`, or
`"glossary_review"`.

Preset shape:

```ts
{
  id: string;
  name: string;
  kind: "translation" | "glossary" | "glossary_review";
  model_profile_id: string;
  prompt_preset_id: string;
  source_language: Language;
  target_language: Language;
  enabled: boolean;
}
```

`workflow_presets.list` takes `{ kind }` and returns:

```ts
{
  presets: WorkflowPreset[];
  matched_id: string | null;
}
```

`matched_id` is the enabled preset whose model, prompt, source language, and
target language all match current settings. It is `null` when the user is on a
custom combination or has no presets.

`workflow_presets.apply` takes `{ kind, id }`, validates referenced model and
prompt ids still exist, then updates active model id, active prompt id, and the
module source/target language settings. It returns the updated app settings and
updated module settings so the frontend can refresh immediately. Applying a
preset does not create a locked mode; later individual model, prompt, or
language changes are valid and will make `matched_id` return `null` until the
settings match a saved preset again.

## Translation Rules

Registered methods:

- `rules.import_rules`
- `rules.export_rules`

These import and export Translation text-preserve, pre-replacement, and
post-replacement rules. Request `kind` is `"text_preserve"`,
`"pre_replacement"`, or `"post_replacement"`. Imports read `.txt` rule files
or readable `.red` containers and return frontend rule payloads; exports write
the current UI rule table to the requested path.

## Task Snapshot Types

```ts
type TaskKind =
  | "translation"
  | "glossary"
  | "glossary_review"
  | "replacement"
  | "epub_compress"
  | "epub_merge"
  | "epub_convert"
  | "txt_to_epub";
type TaskStatus =
  | "pending" | "running" | "stopping" | "stopped"
  | "pausing" | "paused" | "completed" | "failed";
```

`read_snapshot` returns:

```ts
{
  snapshot: {
    header: TaskHeader;
    progress: {
      total: number;
      pending: number;
      running: number;
      completed: number;
      failed: number;
      skipped: number;
      elapsed_seconds: number;
      rate_per_second: number;
      longest_running_seconds: number;
    };
    usage: {
      input_tokens: number;
      output_tokens: number;
      cached_input_tokens: number;
      total_tokens: number;
    };
    low_confidence?: {
      total: number;
      source_residue: number;
    };
    round_progress?: GlossaryReviewRoundProgress | null;
    subtasks: Array<{
      id: string;
      status: string;
      attempts?: number;
      started_at?: string;
      last_error?: string;
    }>;
    active_model_id: string | null;
    active_prompt_id: string | null;
    metadata: Record<string, unknown>;
  }
}
```

## Task Cache Maintenance

Registered methods:

- `tasks.summarize_caches`
- `tasks.purge_caches`

`summarize_caches` returns task count, total cache bytes, and cache root for
the App Settings cache panel. `purge_caches` accepts scope `"all"`,
`"older_than_days"`, or `"completed"` plus optional `days`, removes matching
inactive task caches, and reports removed ids plus active-task skips.

`progress.total` excludes `skipped` subtasks. In Translation split recovery,
`skipped` marks the failed parent after child subtasks are created; it remains a
diagnostic count and should not be added to completed work for percentages or
rates.

`progress.elapsed_seconds` is runtime duration accumulated while the task is
active. It is not `now - created_at` for stopped, paused, or completed tasks.

`low_confidence` is emitted for translation tasks. `total` is the number of
segments that ended a subtask flagged as low confidence (line-count mismatch,
duplicate drift, source-language residue, model-output anomaly, or post-retry
force-accept). `source_residue` is the subset whose final stored translation
kept source-language characters and was tagged `"source_residue"`.

This summary is intentionally compact for run-page progress and completion
toasts. The Proofreading page builds its richer risk queue from each segment's
`low_confidence`, `tags`, and `reasons` fields after loading a snapshot.

## Translation Tasks

Registered methods:

- `translation.start_task`
- `translation.pause_task`
- `translation.stop_task`
- `translation.continue_task`
- `translation.probe_continuable`
- `translation.read_snapshot`
- `translation.list_recent_tasks`
- `translation.read_artifacts`
- `translation.list_failed_subtasks`
- `translation.read_request_events`

`start_task` takes `{ request_id: string }`. The backend builds config from
persisted settings, active model, and active prompt.

`read_artifacts` returns translated files, bilingual files, statistics paths,
and segment counts.

## Glossary Tasks

Registered methods:

- `glossary.start_task`
- `glossary.pause_task`
- `glossary.stop_task`
- `glossary.continue_task`
- `glossary.probe_continuable`
- `glossary.read_snapshot`
- `glossary.list_recent_tasks`
- `glossary.read_artifacts`
- `glossary.list_failed_subtasks`
- `glossary.read_request_events`
- `glossary.import_rules`
- `glossary.export_rules`
- `glossary.list_presets`

`read_artifacts` returns per-novel artifact sets, optional combined artifact,
statistics path, and optional decode issue path.

`import_rules` / `export_rules` read and write Translation glossary table rows
from XLSX or JSON files.

## Glossary Review Tasks

Registered methods:

- `glossary_review.start_task`
- `glossary_review.pause_task`
- `glossary_review.stop_task`
- `glossary_review.continue_task`
- `glossary_review.probe_continuable`
- `glossary_review.read_snapshot`
- `glossary_review.list_recent_tasks`
- `glossary_review.read_artifacts`
- `glossary_review.list_failed_subtasks`
- `glossary_review.read_request_events`
- `glossary_review.discover_inputs`
- `glossary_review.read_report`
- `glossary_review.read_final`
- `glossary_review.update_final_row`
- `glossary_review.delete_final_rows`
- `glossary_review.restore_deleted_report_row`

`discover_inputs` accepts an input folder and optional current selections. It
returns XLSX glossary candidates and TXT reference candidates so the frontend
can ask the user to choose instead of guessing.

`read_artifacts` returns final XLSX path, report path when present, and summary
metadata. Report entry points should only be shown when the report artifact
exists.

`read_report` returns changed rows only. `restore_deleted_report_row` restores a
deleted report row into the final XLSX.

`read_final`, `update_final_row`, and `delete_final_rows` operate on the final
XLSX used for import into the Translation glossary.

## LLM Request Events

`translation.read_request_events`, `glossary.read_request_events`, and
`glossary_review.read_request_events` accept:

```ts
{
  task_id: string;
  limit?: number;
  offset?: number;
  status?: "all" | "running" | "completed" | "failed" | "cancelled";
}
```

The response is:

```ts
{
  events: RequestLogEvent[];
  total: number;
  truncated?: boolean;
}
```

Events are merged by `request_id`, newest first. Default reads use the recent
tail of `request-events.jsonl`; offset/status queries may scan the full
retained file. Events may represent provider requests or local workflow
records, such as preserved quality-exhausted outputs or terminal failure
details, so HTTP/token fields can be absent. Request events never store full
system or user prompts. They store operational metadata, lifecycle phase,
provider response text when available, and throttled partial response text for
in-flight streaming requests.

```ts
type RequestLogPhase =
  | "sent"
  | "headers_received"
  | "first_token"
  | "streaming"
  | "validation"
  | "completed"
  | "failed"
  | "cancelled";

type RequestLogEvent = {
  schema_version: number;
  request_id: string;
  timestamp: string;
  task_id: string;
  subtask_id: string;
  subtask_attempt: number;
  status: "running" | "completed" | "failed" | "cancelled";
  phase?: RequestLogPhase;
  last_activity_at?: string;
  label?: string;
  model_profile_id?: string;
  model_id?: string;
  provider_format?: ProviderFormat;
  provider_attempt?: number;
  prompt_chars?: number;
  timeout_seconds?: number;
  http_status?: number;
  duration_seconds?: number;
  input_tokens?: number;
  output_tokens?: number;
  cached_input_tokens?: number;
  total_tokens?: number;
  usage_estimated?: boolean;
  response_chars?: number;
  response_text?: string;
  partial_response_text?: string;
  error?: string;
};
```

## Replacement

Registered methods:

- `replacement.import_rules`
- `replacement.validate_rules`
- `replacement.start_task`
- `replacement.stop_task`
- `replacement.pause_task`
- `replacement.continue_task`
- `replacement.probe_continuable`
- `replacement.read_snapshot`
- `replacement.list_failed_subtasks`
- `replacement.list_recent_tasks`
- `replacement.read_artifacts`
- `replacement.read_replacement_report`

`replacement.import_rules` accepts `.txt` files and readable `.red` rule
containers. The TXT parser supports:

- `src->dst`
- `src#->#dst`
- `src# -> #dst`

The delimiter-adjacent `#` pair is removed only when both sides have it.

`replacement.start_task` takes `{ request_id, rules }` plus optional
`input_folder` / `output_folder` overrides; rules are the current UI table
state, not persisted settings. If the resolved output folder is blank,
replacement writes outputs into the input folder. `pause_task` and
`continue_task` return `task.invalid_transition` because replacement is
single-pass.

## EPUB Compressor

Registered methods:

- `epub_compress.preview`
- `epub_compress.start_task`
- `epub_compress.stop_task`
- `epub_compress.pause_task`
- `epub_compress.continue_task`
- `epub_compress.probe_continuable`
- `epub_compress.read_snapshot`
- `epub_compress.list_failed_subtasks`
- `epub_compress.list_recent_tasks`
- `epub_compress.read_artifacts`
- `epub_compress.read_report`

`preview` takes `{ input_path, mode, options }` where `mode` is `"file"` or
`"folder"`. It returns proposed compression actions and localized output
filenames. `start_task` takes `{ request_id, input_path, mode, options,
actions }`; selected actions become compression subtasks. The tool rewrites
EPUB archives, removes font files, optimizes images, preserves the first
`mimetype` entry, and does not edit metadata title. `pause_task` and
`continue_task` return `task.invalid_transition` because compression is
single-pass.

## Document Merger

Registered methods:

- `epub_merge.preview`
- `epub_merge.start_task`
- `epub_merge.stop_task`
- `epub_merge.pause_task`
- `epub_merge.continue_task`
- `epub_merge.probe_continuable`
- `epub_merge.read_snapshot`
- `epub_merge.list_failed_subtasks`
- `epub_merge.list_recent_tasks`
- `epub_merge.read_artifacts`
- `epub_merge.read_report`

`preview` takes `{ input_dir, options }` and returns detected EPUB/TXT files,
default output path, and orderable merge actions. `start_task` takes
`{ request_id, input_dir, output_path, options, actions }`; at least two
selected actions are required. For EPUB output, the tool rewrites a merged
archive, deduplicates images, rebuilds `content.opf` / `nav.xhtml` / `toc.ncx`,
keeps `mimetype` first, and uses the configured output filename for merged
`dc:title` metadata. For TXT output, it concatenates selected text inputs in UI
order.
`pause_task` and `continue_task` return `task.invalid_transition` because merge
is single-pass.

## EPUB to TXT

Registered methods:

- `epub_convert.preview`
- `epub_convert.start_task`
- `epub_convert.stop_task`
- `epub_convert.pause_task`
- `epub_convert.continue_task`
- `epub_convert.probe_continuable`
- `epub_convert.read_snapshot`
- `epub_convert.list_failed_subtasks`
- `epub_convert.list_recent_tasks`
- `epub_convert.read_artifacts`
- `epub_convert.read_report`

`preview` takes `{ input_path, mode, options }` where `mode` is `"file"` or
`"folder"`. It returns detected EPUB actions, proposed TXT output paths, and
localized defaults. `start_task` takes `{ request_id, input_path, mode, options,
actions }`; selected actions become conversion subtasks. The tool extracts
spine text into UTF-8 TXT files and writes a report with segment, character, and
spine counts. `pause_task` and `continue_task` return
`task.invalid_transition` because conversion is single-pass.

## TXT to EPUB

Registered methods:

- `txt_to_epub.list_styles`
- `txt_to_epub.list_presets`
- `txt_to_epub.scan_toc`
- `txt_to_epub.locate_toc_entry`
- `txt_to_epub.preview`
- `txt_to_epub.start_task`
- `txt_to_epub.stop_task`
- `txt_to_epub.read_snapshot`
- `txt_to_epub.list_failed_subtasks`
- `txt_to_epub.list_recent_tasks`
- `txt_to_epub.probe_continuable`
- `txt_to_epub.read_artifacts`
- `txt_to_epub.read_report`

`list_styles` and `list_presets` return backend-defined EPUB style presets and
heading detection presets. `scan_toc` previews detected headings for one TXT
file, and `locate_toc_entry` returns the source location for a detected entry.
`preview` takes `{ input_path, mode, options }` where `mode` is `"file"` or
`"folder"`. `start_task` takes `{ request_id, input_path, mode, options,
actions }`; selected actions become TXT-to-EPUB subtasks.

Markdown is the only heading preset that does not add implicit numeric-title
fallback matching. Custom CSS is accepted only after backend validation; remote
or absolute resource URLs are rejected. TXT to EPUB is single-pass and
`probe_continuable` reports `continuable = false`.

## EPUB Metadata

Registered methods:

- `epub_metadata.read`
- `epub_metadata.cover_preview`
- `epub_metadata.apply`

These are direct bridge actions, not task runs. `read` returns current title,
authors, and cover metadata for one EPUB. `cover_preview` returns preview data
for a chosen cover image. `apply` writes title, author, and optional cover
changes, with overwrite behavior controlled by the request.

## EPUB Repair

Registered methods:

- `epub_repair.apply`

This is a direct bridge action, not a task run. `apply` repairs common EPUB
XHTML/XML issues, writes the requested output file, and returns repair counts
plus archive/structure validation details.

## Proofreading

Registered methods:

- `proofreading.list_tasks`
- `proofreading.load_snapshot`
- `proofreading.update_segment`
- `proofreading.regenerate_outputs`
- `proofreading.retranslate_segment`
- `proofreading.retranslate_status`
- `proofreading.resume_retranslate`

`list_tasks` returns translation task headers that have at least one cached
subtask, ordered for the proofreading entry list.

`load_snapshot` aggregates per-segment translations, low-confidence flags, and
optional classification tags across all subtasks in a single translation task.
Latest write wins, so split-child results override their parent.

`retranslate_segment` starts a single proofreading retranslation request,
`retranslate_status` polls it, and `resume_retranslate` reattaches to an
existing request id after UI refresh or navigation.

```ts
{
  task_id: string;
  task_status: TaskStatus;
  input_dir: string;
  output_dir: string;
  items: Array<{
    segment_id: string;
    src: string;
    dst: string;
    low_confidence: boolean;
    tags?: string[];
    reasons?: string[];
  }>;
}
```

Known proofreading tags include:

- `"source_residue"`: the final translation kept source-language characters.
- `"possible_duplicate"`: the same or near-same translation appears for
  multiple distinct source segments.
- `"function_word_residue"`: English function words appear inside a non-English
  target segment where that is likely model drift rather than normal content.
- `"target_language_weak"`: the output has too little expected target-language
  script for the configured target language.
- `"model_chatter"`: the model returned wrapper text, explanations, or refusal
  style chatter instead of only the translated segment.
- `"verbatim_echo"`: the model output is effectively the source text.
- `"length_ratio_anomaly"`: source and target lengths are outside the accepted
  ratio bounds.
- `"punctuation_anomaly"`: punctuation distribution changed enough to require
  manual review.
- `"glossary_not_applied"`: a source segment contains a glossary term but the
  final translation does not contain the glossary destination form.
- `"term_inconsistency"`: the same glossary term form is applied in some
  occurrences and missing in others.

The frontend groups `function_word_residue`, `target_language_weak`,
`model_chatter`, and `verbatim_echo` under the user-facing "model anomaly"
risk. `length_ratio_anomaly` and `punctuation_anomaly` remain low-confidence
structural-review signals rather than model-anomaly chips; the UI may show them
with a neutral structure-drift chip. Snapshot items may carry multiple tags and
reasons; the UI counts all matching risk categories, then orders the review
queue by highest priority risk.

`update_segment` writes a manual edit back to cache without retriggering the
LLM.

`regenerate_outputs` re-renders TXT/EPUB/bilingual outputs from current cache.

`retranslate_segment` queues a single isolated LLM call (chunk_index=0, no
context) for one segment and returns a request id. `retranslate_status` polls
that request and returns the new translation when ready.

## Updates

Registered methods:

- `updates.check_latest`
- `updates.open_release_page`
- `updates.download_asset`
- `updates.apply_update_windows`

`check_latest` takes a request id and optional channel (`stable` or
`prerelease`). It returns current/latest versions, release notes, release URL,
published timestamp, and an optional matching asset.

`apply_update_windows` is available for packaged Windows builds. It stages a
downloaded ZIP update only when the current install folder is writable and the
ZIP contains a valid `Transoria.exe` plus `_internal` payload.
