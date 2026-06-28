# Glossary Extraction Module

Status: Active module documentation
Last reviewed: 2026-06-28

## Purpose

Glossary Extraction analyzes supported EPUB/TXT novels under a configured input
folder and produces terminology files that can be reviewed or imported into the
Translation glossary. It is independent from Translation and never starts a
translation run automatically.

## Navigation

Glossary pages:

- Run
- Presets
- Prompt
- Settings

The shared Model page owns API profiles. Glossary Extraction stores only its
active model id in app settings.

## Settings

Persisted under `GlossarySettings`:

- `input_folder`
- `output_folder`
- `source_language`
- `target_language`
- `chinese_output_form`
- `reference_examples_per_term`
- `max_term_display_length`
- `minimum_frequency`
- `chunk_token_limit`
- `merge_folder_glossary`
- `keep_identical_src_dst`
- `normalize_widths`
- `novel_background`
- `request_retry_attempts`
- `timeout_seconds`
- `auto_open_output_folder`

`merge_folder_glossary` defaults on. `normalize_widths` defaults on.
`keep_identical_src_dst` defaults off.

`request_retry_attempts` is the module-level transport retry budget for failed
LLM requests such as provider 5xx or transport errors. Backoff timing is
internal and fixed. Glossary Extraction also applies an internal same-key
transport retry cap of 3, so lower settings reduce the effective budget but
higher settings do not make one provider request retry more than that cap. HTTP
429 is handled by API-key rotation / key-pool cycling when multiple keys are
available. Extraction request timeouts use the split-rescue path described below
instead of repeatedly resending the same oversized prompt.

Input and output folder rows keep separate recent-folder histories in frontend
localStorage. Selecting a recent path only fills that field; the history itself
is not a backend setting.

## Run Page

The Run page is execution/status only.

It shows:

- compact active configuration bar with preset, model, and prompt selectors
- quick-switch modals for preset, model, and prompt
- progress ring
- completed, failed, remaining, elapsed, and average speed stats
- processed/running subtask counter with the longest running subtask age
- compact request-log toggle that opens a pop-out request log for model calls
  and retained failure records, including lifecycle phase, last activity,
  provider error bodies, and throttled partial streaming responses without
  recording full prompts
- chunk status grid
- failed subtask list
- Start, Stop, and Continue controls
- Send to Review action after a completed run has glossary artifacts

It does not contain folder pickers, language selectors, provider credentials,
runtime-limit fields, or prompt editors.

`timeout_seconds` remains a per-provider-request timeout. Glossary Extraction
uses a shorter internal soft cap of 90 seconds for extraction calls when the
configured timeout is higher; on timeout it prefers one bounded split-rescue
pass over retrying the same oversized prompt. The longest-running subtask age
shown on the Run page is wall-clock runtime for the whole workflow subtask and
can include bounded request retries/backoff or split-rescue work.

Send to Review is frontend-only. It copies Glossary Extraction's
`output_folder` into `glossary_review.input_folder`, clears the review page's
selected XLSX/reference paths so discovery reruns, copies
`glossary.novel_background` into Glossary Review, and navigates to Glossary
Review Settings. It does not start review automatically.

## Task Lifecycle

Glossary Extraction uses the shared async task runtime:

- `glossary.start_task`
- `glossary.pause_task`
- `glossary.stop_task`
- `glossary.continue_task`
- `glossary.probe_continuable`
- `glossary.read_snapshot`
- `glossary.read_request_events`
- `glossary.list_recent_tasks`
- `glossary.list_failed_subtasks`
- `glossary.read_artifacts`

Continue reuses task cache for stopped, paused, or failed tasks that still have
pending or failed subtasks.

## Prompt Page

Glossary prompt presets are managed through the shared prompt page with
`kind = "glossary"`.

Current behavior:

- system presets are read-only
- only the system preset matching the UI language is shown
- custom presets are editable
- prompt preview uses the same backend `build_prompt` path as the runner
- thinking guidance is system-level runtime behavior, not user preset text

## Presets Page

Glossary Extraction workflow presets are managed through the shared Presets page
with `kind = "glossary"`.

Current behavior:

- presets are user-created rows only
- each preset stores model, prompt, source language, and target language
- creating/editing uses a modal; model and prompt are selected from dropdowns
- applying a preset switches active model, active prompt, source language, and
  target language together
- presets are optional shortcuts; users can still change model, prompt, or
  languages individually after applying one
- duplicate creates a copy that can be edited before further use

## Backend Flow

1. Scan the input folder recursively for `.epub` and `.txt`.
2. Parse source segments and filter empty content.
3. Build chunks from source text.
4. Send extraction prompts through the active model profile.
5. Decode JSONL glossary rows, recording decode issues when present.
6. Normalize candidates.
7. Convert Chinese output form when target language is Chinese.
8. Drop empty, overly long, blacklisted, or disallowed identical entries.
9. Deduplicate and merge candidate rows.
10. Count frequencies and collect source references.
11. Filter by `minimum_frequency`.
12. Write per-source artifacts.
13. Optionally write a combined folder-level artifact.
14. Write extraction statistics, artifact metadata, progress snapshots, and
    task-scoped request events for request debugging.

## Output

Per-source filenames:

```text
<NovelName>-Glossary.xlsx
<NovelName>-Glossary.json
<NovelName>-Glossary-references.txt
```

Combined folder-level output uses the input folder name with the same suffixes.

XLSX/JSON rows contain:

- `src`
- `dst`
- `info`
- `regex`
- `frequency`

References TXT contains source context lines for each final term.

Artifact payload includes:

- `per_novel_artifacts`
- optional `combined_artifact`
- `statistics_json_path`
- optional `decode_issue_path`

## Import Into Translation

Generated XLSX/JSON files can be imported from the Translation Glossary page.
Import is explicit and user-controlled.

## Send To Review

Completed extraction artifacts can be sent directly to Glossary Review from the
Run page. The review Settings page then discovers candidate XLSX/TXT files from
that folder and lets the user confirm the selected glossary and references
before spending review tokens.

## Verification Focus

Glossary changes should usually verify:

- JSONL decoding and issue reporting
- candidate normalization and duplicate merging
- frequency/reference calculation
- per-source artifact naming
- combined artifact behavior
- Send to Review settings handoff
- stop/continue cache behavior
