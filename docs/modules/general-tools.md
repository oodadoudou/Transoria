# General Tools Module

Status: Active module documentation
Last reviewed: 2026-06-20

## Purpose

General Tools contains local utility workflows that operate on novel files
without using LLMs. Implemented tools are Batch Replacement and the EPUB Tools
workspace: EPUB Compressor, Document Merger, EPUB to TXT, TXT to EPUB, EPUB
Metadata, and EPUB Repair.

## Navigation

General Tools pages:

- Batch Replacement
- EPUB Tools
- EPUB Compressor
- Document Merger
- EPUB to TXT
- TXT to EPUB
- EPUB Metadata
- EPUB Repair

## Folder History

Folder picker rows keep up to five recent paths in frontend localStorage when
the page supplies a history key. General Tools scopes these histories by tool
and field, so input/output folders and unrelated tools do not share one list.
This history is UI convenience state only and does not enter backend settings
schema.

## Batch Replacement Settings

Persisted under `ReplacementSettings`:

- `input_folder`
- `output_folder`
- `allow_same_folder`
- `output_naming_suffix`
- `overwrite_existing`
- `apply_to_epub_titles`
- `stop_on_first_error`

The page also keeps the imported rule table in component state. Rules are sent
with `replacement.start_task`; they are not persisted as replacement settings.

## Rule Import

Supported import formats: `.txt` and readable `.red` containers. Private or
encrypted containers are not bypassed.

Each non-empty, non-comment line defines one rule:

```text
source text->replacement text
```

Compatibility forms:

```text
source text#->#replacement text
source text# -> #replacement text
```

The parser splits on the first `->`. If the source side ends with `#` and the
destination side starts with `#`, exactly those delimiter-adjacent markers are
removed. Other `#` characters remain ordinary text.

Imported rules default to:

- `regex = false`
- `case_sensitive = false`
- `enabled = true`

The UI allows inline editing of `src` and `dst`, plus toggling regex and
case-sensitive behavior.

## Validation

`replacement.validate_rules` reports issues such as:

- empty source
- empty destination
- invalid regex
- duplicate source text

The Execute button requires input folder, output folder, and at least one rule.

## Task Lifecycle

Batch Replacement exposes the shared task surface:

- `replacement.start_task`
- `replacement.stop_task`
- `replacement.pause_task`
- `replacement.continue_task`
- `replacement.probe_continuable`
- `replacement.read_snapshot`
- `replacement.list_recent_tasks`
- `replacement.list_failed_subtasks`
- `replacement.read_artifacts`
- `replacement.read_replacement_report`

Replacement is single-pass:

- `pause_task` returns `task.invalid_transition` with reason `single_pass`
- `continue_task` returns `task.invalid_transition` with reason `single_pass`
- `probe_continuable` always reports `continuable = false`

## Backend Flow

1. Scan the input folder for supported `.epub` and `.txt` files.
2. Seed one subtask per source file.
3. Apply enabled rules in table order.
4. Write replacement outputs under the configured output folder.
5. Record per-file replacement counts.
6. Collect per-rule occurrence samples with context.
7. Write `replacement-report.json`.
8. Write artifact metadata for the UI.

## TXT Replacement

TXT files are decoded through the shared text parser, so BOM/UTF detection and
common CJK/Korean/Japanese encodings are handled consistently. Outputs are
written as UTF-8.

## EPUB Replacement

EPUB replacement uses the shared EPUB parser/writer foundation and preserves
package structure. Only intended text slots are changed.

## Output

Default suffix:

```text
<OriginalName>-Replaced.<ext>
```

Artifact payload includes:

- `output_folder`
- `output_files`
- `total_replacements`
- optional `replacement_report_path`

The replacement report includes totals, per-file counts, per-rule counts, and
captured occurrences with surrounding context. The completed report is mirrored
in memory so the UI can reopen it after clean task-cache cleanup.

## Verification Focus

General Tools changes should usually verify the affected tool plus shared task
surface behavior:

- TXT rule parser compatibility forms
- unrelated `#` preservation
- regex validation behavior
- TXT replacement count and output naming
- EPUB structure preservation
- replacement report availability after completion
- EPUB compression keeps `mimetype` first and preserves title metadata
- Document Merger preserves selected order and rebuilds EPUB navigation
- EPUB to TXT follows spine order and writes UTF-8 text
- TXT to EPUB Markdown preset does not promote unmarked numeric body lines to
  headings
- TXT to EPUB numeric/chapter presets detect expected chapter headings
- EPUB Metadata writes requested title/author/cover changes to a copy unless
  overwrite is explicit
- EPUB Repair reports repair counts and validation details without weakening
  parser validation

## EPUB Compressor

EPUB Compressor supports one file or a folder batch. Folder mode scans
recursively by default and skips already-marked compressed outputs unless the
user chooses replace-original mode.

### Behavior

Compression rewrites the EPUB archive without changing book title metadata:

- `mimetype` is written first and stored uncompressed
- duplicate font files (`.ttf`, `.otf`, `.woff`, `.woff2`, `.eot`) are removed
- OPF manifest font hrefs and CSS `url(...)` references are rewritten when a
  duplicate font is replaced by the kept copy
- users can switch font handling to remove all fonts; in that mode OPF font
  manifest entries and CSS `@font-face` rules are removed
- images are optimized through Pillow and kept only when the optimized bytes
  are smaller
- the first likely cover image can be preserved

Output filenames use the current UI language's default marker unless the user
edits it. In replace-original mode, the backend writes a temporary file next to
the source and swaps it into place only after the output archive validates.

### Task Lifecycle

EPUB Compressor exposes the shared task surface:

- `epub_compress.preview`
- `epub_compress.start_task`
- `epub_compress.stop_task`
- `epub_compress.pause_task`
- `epub_compress.continue_task`
- `epub_compress.probe_continuable`
- `epub_compress.read_snapshot`
- `epub_compress.list_recent_tasks`
- `epub_compress.list_failed_subtasks`
- `epub_compress.read_artifacts`
- `epub_compress.read_report`

The task seeds one subtask per selected EPUB. It is single-pass:

- `pause_task` returns `task.invalid_transition` with reason `single_pass`
- `continue_task` returns `task.invalid_transition` with reason `single_pass`
- `probe_continuable` always reports `continuable = false`

### Output

Artifact payload includes:

- `output_folder`
- `output_files`
- optional `report_path`
- `compressed_count`
- `failed_count`

The detailed report lists each source/output file, original and output size,
saved bytes/percent, deduplicated fonts, compressed/skipped image counts, and the
error message for failed files.

## Document Merger

Document Merger scans a folder for EPUB or TXT files, lets the user select and
reorder them, then writes one merged EPUB or TXT output. For merged EPUB output,
the configured output title/filename is written into `dc:title` metadata.

### Behavior

- folder mode scans recursively by default
- files are pre-sorted by common Korean chapter/volume markers
- selected files can be reordered before execution
- the output filename uses the current UI language's default marker unless the
  user edits it
- TXT output concatenates selected text inputs in UI order and writes UTF-8
  text
- images are deduplicated by bytes and optionally compressed through Pillow
- font files and `@font-face` rules are removed
- CSS/image/resource references are rewritten to the merged archive layout
- `content.opf`, `nav.xhtml`, and `toc.ncx` are generated fresh
- `mimetype` is written first and stored uncompressed

The backend reads source EPUB entries directly from ZIP archives instead of
extracting them into the destination tree. The final file is written to a
temporary EPUB next to the target path, validated, then atomically swapped into
place.

### Task Lifecycle

Document Merger exposes the shared task surface:

- `epub_merge.preview`
- `epub_merge.start_task`
- `epub_merge.stop_task`
- `epub_merge.pause_task`
- `epub_merge.continue_task`
- `epub_merge.probe_continuable`
- `epub_merge.read_snapshot`
- `epub_merge.list_recent_tasks`
- `epub_merge.list_failed_subtasks`
- `epub_merge.read_artifacts`
- `epub_merge.read_report`

The task uses one merge subtask because the output is atomic. It is single-pass:

- `pause_task` returns `task.invalid_transition` with reason `single_pass`
- `continue_task` returns `task.invalid_transition` with reason `single_pass`
- `probe_continuable` always reports `continuable = false`

### Output

Artifact payload includes:

- `output_folder`
- `output_files`
- optional `report_path`
- `merged_count`
- `failed_count`

The detailed report lists each source EPUB, result status, chapter/resource
counts, removed fonts, image dedupe/compression stats, and warnings for missing
or unreadable package entries. TXT merge reports list selected text inputs and
output statistics.

## EPUB to TXT

EPUB to TXT converts one EPUB or a folder of EPUB files into UTF-8 TXT outputs.
Folder mode can scan recursively and skips output-name collisions by generating
unique output paths.

### Behavior

- preview lists detected EPUB files and proposed TXT output paths
- conversion follows the EPUB spine order
- source archive structure is inspected through the shared EPUB format layer
- output text is UTF-8
- reports include converted/failed counts plus segment, character, and spine
  counts

### Task Lifecycle

EPUB to TXT exposes the shared single-pass task surface:

- `epub_convert.preview`
- `epub_convert.start_task`
- `epub_convert.stop_task`
- `epub_convert.pause_task`
- `epub_convert.continue_task`
- `epub_convert.probe_continuable`
- `epub_convert.read_snapshot`
- `epub_convert.list_recent_tasks`
- `epub_convert.list_failed_subtasks`
- `epub_convert.read_artifacts`
- `epub_convert.read_report`

`pause_task` and `continue_task` return `task.invalid_transition` with reason
`single_pass`; `probe_continuable` always reports `continuable = false`.

## TXT to EPUB

TXT to EPUB converts one TXT file or a folder of TXT files into validated EPUB
outputs. It provides backend-defined style presets and heading detection
presets.

### Behavior

- `list_styles` returns available EPUB visual style presets
- `list_presets` returns heading detection presets
- `scan_toc` previews detected headings for a TXT file
- `locate_toc_entry` maps a detected heading back to source text location
- Markdown headings are the only preset that does not implicitly include
  numeric-title fallback matching
- numeric/chapter presets can match full-line chapter titles and numeric lines
  according to their preset rules
- non-empty body lines are written as separate XHTML `<p>` elements instead of
  being joined with `<br/>`
- optional cover images are embedded when valid
- custom CSS is validated by the backend; remote or absolute resource URLs are
  rejected
- output EPUB archives are validated before success is reported

### Task Lifecycle

TXT to EPUB exposes:

- `txt_to_epub.list_styles`
- `txt_to_epub.list_presets`
- `txt_to_epub.scan_toc`
- `txt_to_epub.locate_toc_entry`
- `txt_to_epub.preview`
- `txt_to_epub.start_task`
- `txt_to_epub.stop_task`
- `txt_to_epub.read_snapshot`
- `txt_to_epub.list_recent_tasks`
- `txt_to_epub.list_failed_subtasks`
- `txt_to_epub.probe_continuable`
- `txt_to_epub.read_artifacts`
- `txt_to_epub.read_report`

It is single-pass. The UI does not expose pause/continue controls, and
`probe_continuable` always reports `continuable = false`.

## EPUB Metadata

EPUB Metadata reads and edits metadata for one EPUB without creating a cached
task.

Registered bridge methods:

- `epub_metadata.read`
- `epub_metadata.cover_preview`
- `epub_metadata.apply`

The tool reads title, authors, and cover information; previews a selected cover
image; and applies title, author, and optional cover changes to the requested
output path. Overwrite behavior is controlled by the request.

## EPUB Repair

EPUB Repair fixes common malformed EPUB XHTML/XML issues without creating a
cached task.

Registered bridge method:

- `epub_repair.apply`

The tool writes a repaired output file, reports repair counts, and returns
archive/structure validation details. It should not relax parser validation just
to hide malformed source EPUB errors.
