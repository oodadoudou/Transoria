# Translation Module

Status: Active module documentation
Last reviewed: 2026-06-28

## Purpose

Translation processes supported EPUB/TXT novels under a configured input folder
and writes translated outputs to the configured output folder. It preserves
source files and does not require user-managed project files.

## Navigation

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

The shared Model page owns API profiles. Translation stores only its active
model id in app settings.

## Settings

Persisted under `TranslationSettings`:

- `input_folder`
- `output_folder`
- `source_language`
- `target_language`
- `chinese_output_form`
- `bilingual_enabled`
- `bilingual_dedupe_identical`
- `bilingual_subfolder_name`
- `context_lines`
- `request_retry_attempts`
- `low_confidence_max_retries`
- `timeout_seconds`
- `auto_open_output_folder`
- `translation_glossary`
- `text_preserve_rules`
- `pre_replacements`
- `post_replacements`

Settings pages auto-save through the shared settings store and also expose
explicit Save and Reset controls.

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
- running subtask count and longest running subtask age
- chunk status grid
- failed subtask list
- request-log toggle that opens a pop-out request log for model calls and
  local workflow failure/quality records, including lifecycle phase, last
  activity, provider error bodies, and throttled partial streaming responses
  without recording full prompts
- Start, Stop, and Continue controls

It does not contain folder pickers, language selectors, provider credentials,
runtime-limit fields, or prompt editors.

## Task Lifecycle

Translation uses the shared async task runtime:

- `translation.start_task`
- `translation.pause_task`
- `translation.stop_task`
- `translation.continue_task`
- `translation.probe_continuable`
- `translation.read_snapshot`
- `translation.read_request_events`
- `translation.list_recent_tasks`
- `translation.list_failed_subtasks`
- `translation.read_artifacts`

Continue reuses task cache for stopped, paused, or failed tasks that still have
pending or failed subtasks. Clean completed tasks are not continuable.

## Glossary Page

Translation glossary entries are user-managed and persisted in
`translation_glossary`.

Entry fields:

- `src`
- `dst`
- `info`
- `regex`
- `case_sensitive`
- `enabled`
- `frequency`

The page supports:

- add/edit/delete
- inline cell editing
- bulk delete/duplicate
- search
- sort by source, translation, description, or frequency
- import from glossary XLSX/JSON
- export to XLSX/JSON
- statistics modal
- local glossary preset import
- non-blocking conflict warnings for duplicate source mappings, invalid regex,
  and overlapping regex/plain entries

Imported extraction frequency is preserved when present.

## Text Preserve

Text-preserve rules are persisted in `text_preserve_rules`.

Fields:

- `pattern`
- `note`
- `enabled`

During preprocessing, matching text is protected before translation and
restored after the model response.

## Replacement Pages

Pre-translation and post-translation replacement pages share the same table
component and persist to `pre_replacements` / `post_replacements`.

Fields:

- `src`
- `dst`
- `regex`
- `case_sensitive`
- `note`
- `enabled`

Pre-replacements run after text preserve masking and before prompt assembly.
Post-replacements run after protected text restoration and before writeback.

## Prompt Page

Translation prompt presets are managed through the shared prompt page with
`kind = "translation"`.

Current behavior:

- system presets are read-only
- only the system preset matching the UI language is shown
- custom presets are editable
- deleting the active custom preset clears the active id and falls back to the
  default
- prompt preview uses the same backend `build_prompt` path as the runner
- thinking guidance is system-level runtime behavior, not user preset text

## Presets Page

Translation workflow presets are managed through the shared Presets page with
`kind = "translation"`.

Current behavior:

- presets are user-created rows only
- each preset stores model, prompt, source language, and target language
- creating/editing uses a modal; model and prompt are selected from dropdowns
- applying a preset switches active model, active prompt, source language, and
  target language together
- duplicate creates a copy that can be edited before further use

## Proofreading Page

Proofreading can retranslate one segment, selected segments, or the current
filtered list. Its retranslation controls are local to the page:

- users can switch the retranslation model, prompt, or a Translation workflow
  preset
- choosing a preset fills the local retranslation model and prompt
- choosing a preset does not rewrite the completed task's cached source/target
  language metadata

## Backend Flow

1. Scan the input folder recursively for `.epub` and `.txt`.
2. Parse files into ordered segments.
3. Strip DRM-style invisible characters from source text.
4. Apply text preserve rules and pre-replacements.
5. Build context and glossary constraints for each chunk.
6. Send JSONL translation prompts through the active model profile. Supported
   OpenAI-compatible paths default to streaming and request streamed usage when
   the provider accepts it.
7. Decode JSONL responses with tolerant repair paths and accept by position
   when row count matches the expected segment count.
8. On line-count mismatch, accumulate present rows and retry only the missing
   indices under the fixed internal format-drift budget. Retry sub-chunks
   re-filter glossary entries against only the pending source lines.
9. Restore protected text, apply post-replacements, and run confidence checks.
10. For scattered low-confidence rows, run one compact micro-batch retry first
    when at least four rows are pending. Each micro-batch contains up to five
    rows and is also capped by estimated source tokens. Remaining rows fall
    through to isolated solo retries (chunk_index=0, empty context). The
    micro-batch and solo stages share one chunk-level paid rescue-call budget of
    `low_confidence_max_retries * 4`; later task-level subtask attempts receive
    only one bounded rescue call instead of refreshing the full budget. Retry
    chunks re-filter glossary entries against only the retried source lines.
    Pick the cleanest candidate. Preserved identifiers and title-like metadata,
    such as ISBN lines or volume headings that are intentionally unchanged, do
    not trigger rescue retries only because they still contain Latin/numeric
    identifiers.
11. If a batch response contains mass source echo / source-language residue,
    retry only those residue rows once in a micro-batch using the configured
    target language. If the retry budget is exhausted and the best remaining
    output still contains systemic source residue, keep that best output and
    tag the affected rows for proofreading instead of failing the whole
    subtask. Tiny split children receive the same protection after their
    isolated retries.
12. On terminal quality exhaustion for isolated or scattered low-confidence
    lines, fall back to the source text when the model echoed
    source or kept source-language residue (tagged `source_residue`); otherwise
    keep the cleanest model output and tag `force_accepted_after_max_retries`.
    Provider transport failures, malformed responses that cannot be decoded,
    and cancelled requests can still fail the subtask.
13. Write translated TXT/EPUB outputs.
14. Optionally write bilingual TXT/EPUB outputs.
15. Write translation statistics, low-confidence summary, artifact metadata,
    per-subtask debug logs under `<task_cache>/debug/`, and compact request
    events under `<task_cache>/request-events.jsonl`. Request events include
    provider calls, lifecycle phase, last activity, final or partial model
    responses, failed attempts, provider error bodies, and local workflow
    quality/failure records retained for debugging. Full system/user prompts
    are not stored in request events.

## Confidence Checks

Each segment is evaluated after restoration. Failure reasons include line-count
mismatch, duplicate-content drift, source-language residue, model-output
anomalies, length-ratio anomalies, and punctuation anomalies.

Korean residue is split into two classes:

- Hard residue (Hangul Syllables and halfwidth Hangul) — flagged on any
  occurrence.
- Soft residue (compatibility Jamo and Jamo blocks, often used as chat-style
  emoticons) — flagged only when the output contains no CJK ideographs and the
  jamo ratio exceeds the configured threshold. This lets mixed Chinese output
  with `ㅋㅋ` / `ㅠㅠ` style fragments pass.

Japanese kana stays strictly forbidden in Chinese output.

Duplicate-drift uses a length-aware threshold: short translations need at least
three distinct sources before flagging, longer translations flag from two
sources.

Model-output anomaly tags currently include:

- `function_word_residue` for suspicious English function words left inside a
  non-English target segment.
- `target_language_weak` when the translated text has too little expected
  target-language script.
- `model_chatter` when the model wraps the answer in explanations, refusal
  text, or other non-translation chatter.
- `verbatim_echo` when the output is effectively the source text.

`length_ratio_anomaly` and `punctuation_anomaly` are structural low-confidence
tags. They still require review and get a neutral structure-drift row chip, but
the proofreading UI does not group them under the user-facing model-anomaly chip
because legitimate translations can change length and punctuation
substantially.

## Retry Semantics

`request_retry_attempts` is the module-level transport retry budget for failed
network requests: timeout, provider 5xx, or transport errors. Backoff timing is
internal and fixed. Translation also applies an internal same-key transport
retry cap of 3, so lower settings reduce the effective budget but higher
settings do not make one provider request retry more than that cap. HTTP 429 is
handled by API-key rotation / key-pool cycling when multiple keys are
available; it is not treated as a normal same-key transport retry.

`timeout_seconds` is a per-provider-request timeout, not a maximum duration for
one translation chunk. A chunk can make multiple bounded internal requests
during line-count repair, low-confidence rescue, or transport retry, so the Run
page's longest-running subtask age may be higher than `timeout_seconds` without
implying that one HTTP request ignored the timeout. In high-concurrency
translation runs, batch requests are capped at 360 seconds and rescue requests
at 60 seconds when the configured timeout is higher. In that
high-concurrency path, provider transport timeouts are not repeatedly resent as
the same batch request; recovery stays on the bounded rescue, split, and
continue paths so one wedged request cannot monopolize the pool.

Partial-accept / line-count repair is quality recovery, not network recovery.
It uses a fixed internal format-drift cap so a malformed model response cannot
consume an unbounded user-configured transport retry budget.

Low-confidence rescue is a quality retry controlled by the advanced
`low_confidence_max_retries` setting. When at least four rows are pending, the
runner first retries compact micro-batches of up to five rows (also capped by
estimated source tokens), then falls back to isolated solo retries for the
remaining rows. Both stages share a total chunk budget of
`low_confidence_max_retries * 4` so one pathological chunk cannot launch
unbounded rescue calls. If the runtime re-runs a failed subtask, that subtask
retry does not receive a fresh low-confidence rescue budget.

The default low-confidence retry count is 3.

Subtasks that still fail after the partial-accept loop are split into smaller
chunks and re-run for `_SPLIT_ROUNDS` rounds (currently 1). The failed chunk is
halved once based on its actual segment count; the failed parent is marked
`skipped` and kept for diagnostics. Progress totals and rates count the child
work units, not that skipped parent.

## Proofreading Page

The Proofreading page surfaces translated segments per task for manual review.
It defaults to the broad, high-signal risks (`low_confidence`,
`source_residue`, and `possible_duplicate`), can also filter
`glossary_not_applied`, `term_inconsistency`, `model_anomaly`, `untranslated`,
and `format_rescue`, and orders the visible queue by risk priority before
original segment order. It shows the original text in a read-only textarea
(full-text selectable for copy/paste), and exposes:

- inline edits to the translation (`proofreading.update_segment`)
- single-segment retranslation in isolation
  (`proofreading.retranslate_segment` / `retranslate_status`)
- batch retranslation of the current filtered list with progress tracking
- clear active risk filters in one action
- regenerate output files from current cache
  (`proofreading.regenerate_outputs`)
- a collapsible term-audit panel that groups local glossary risks by term

Segments tagged `source_residue`, `glossary_not_applied`,
`term_inconsistency`, `possible_duplicate`, or any model-anomaly tag get
dedicated status chips so the user can prioritise items where the model
retained source-language characters, missed glossary mappings, repeated
content, or returned suspicious non-translation output. Length-ratio and
punctuation anomalies remain visible as low-confidence reasons, not model
anomaly chips. Row tooltips include backend reasons and tags for the detected
risks.

Term audit is local and token-free. It audits each glossary row as its own
source-term form, without merging aliases, surnames, organizations, or places.
It marks a missing glossary destination as `glossary_not_applied`; it marks
`term_inconsistency` only when the canonical destination is present in some
occurrences and absent in others.

## Output

Translated output filenames:

```text
<OriginalName>-<targetTag>.<ext>
```

Bilingual output is written under `bilingual_subfolder_name` when enabled.

Artifact payload includes:

- `translated_files`
- `bilingual_files`
- `statistics_json_path`
- `statistics_text_path`
- processed file list
- completed/total segment counts

## Verification Focus

Translation changes should usually verify:

- EPUB structure preservation
- TXT paragraph/order preservation
- glossary threading into prompt/config
- text preserve and replacement ordering
- stop/continue cache behavior
- artifact payloads and statistics paths
- partial-accept on line-count mismatch and solo low-confidence retries
- residue classification (hard vs soft Korean, Japanese kana)
- low-confidence summary numbers in `read_snapshot`
- proofreading edits, retranslate, and regenerate-output flows
