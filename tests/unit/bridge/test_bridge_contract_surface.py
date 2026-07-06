"""Cross-boundary contract tests.

Drives every registered bridge method against a freshly-built router and
asserts:

1. Each method declared in the contract is registered (and conversely no
   stray methods sneak in).
2. The frontend bridge client exposes a wrapper for every backend method
   (parses ``frontend/src/bridge/client.ts`` and reconciles call strings).
3. Every method, when called with a minimum-viable payload, returns either
   a JSON-mapping response or a typed ``BridgeError``. No method may raise
   an unwrapped ``Exception`` or return non-mapping data.

This is the safety net that catches drift between
``docs/bridge-contract.md`` and the running router
without having to spin up the full UI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from transoria.bridge import BridgeError, build_default_router

REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENT_TS = REPO_ROOT / "frontend" / "src" / "bridge" / "client.ts"

# Every method the backend must register. Generated from the contract; if
# this list drifts from `build_default_router().methods()`, one of the two
# sides changed and we want the test to flag it.
EXPECTED_METHODS: tuple[str, ...] = (
    # app
    "app.get_metadata",
    # dialogs
    "dialogs.choose_input_directory",
    "dialogs.choose_output_directory",
    "dialogs.choose_glossary_file",
    "dialogs.choose_replacement_rules_file",
    "dialogs.choose_save_path",
    "dialogs.open_directory",
    "dialogs.reveal_file",
    # settings
    "settings.load_all",
    "settings.save_partial",
    "settings.reset_module",
    # model profiles
    "model_profiles.list",
    "model_profiles.read_full",
    "model_profiles.create",
    "model_profiles.update",
    "model_profiles.delete",
    "model_profiles.duplicate",
    "model_profiles.set_api_key",
    "model_profiles.test_connection",
    "model_profiles.fetch_model_list",
    "model_profiles.select_active",
    # model templates
    "model_templates.list",
    # prompts
    "prompts.list",
    "prompts.read",
    "prompts.create",
    "prompts.update",
    "prompts.duplicate",
    "prompts.delete",
    "prompts.select_active",
    "prompts.preview",
    "prompts.reset_to_default",
    # workflow presets
    "workflow_presets.list",
    "workflow_presets.create",
    "workflow_presets.update",
    "workflow_presets.duplicate",
    "workflow_presets.delete",
    "workflow_presets.apply",
    # translation
    "translation.start_task",
    "translation.pause_task",
    "translation.stop_task",
    "translation.continue_task",
    "translation.probe_continuable",
    "translation.read_snapshot",
    "translation.list_recent_tasks",
    "translation.read_artifacts",
    "translation.list_failed_subtasks",
    "translation.read_request_events",
    # glossary
    "glossary.start_task",
    "glossary.pause_task",
    "glossary.stop_task",
    "glossary.continue_task",
    "glossary.probe_continuable",
    "glossary.read_snapshot",
    "glossary.list_recent_tasks",
    "glossary.read_artifacts",
    "glossary.list_failed_subtasks",
    "glossary.read_request_events",
    "glossary.import_rules",
    "glossary.export_rules",
    "glossary.list_presets",
    # glossary review
    "glossary_review.start_task",
    "glossary_review.pause_task",
    "glossary_review.stop_task",
    "glossary_review.continue_task",
    "glossary_review.probe_continuable",
    "glossary_review.read_snapshot",
    "glossary_review.list_recent_tasks",
    "glossary_review.read_artifacts",
    "glossary_review.list_failed_subtasks",
    "glossary_review.read_request_events",
    "glossary_review.discover_inputs",
    "glossary_review.read_report",
    "glossary_review.read_final",
    "glossary_review.update_final_row",
    "glossary_review.delete_final_rows",
    "glossary_review.restore_deleted_report_row",
    # rules (translation-side text-preserve / pre / post replacement)
    "rules.import_rules",
    "rules.export_rules",
    # cache management (Settings page)
    "tasks.summarize_caches",
    "tasks.purge_caches",
    # proofreading (translation校对 page)
    "proofreading.list_tasks",
    "proofreading.load_snapshot",
    "proofreading.update_segment",
    "proofreading.regenerate_outputs",
    "proofreading.retranslate_segment",
    "proofreading.retranslate_status",
    "proofreading.resume_retranslate",
    # replacement
    "replacement.import_rules",
    "replacement.validate_rules",
    "replacement.start_task",
    "replacement.stop_task",
    "replacement.pause_task",
    "replacement.continue_task",
    "replacement.probe_continuable",
    "replacement.read_snapshot",
    "replacement.list_recent_tasks",
    "replacement.read_artifacts",
    "replacement.read_replacement_report",
    "replacement.list_failed_subtasks",
    # EPUB compressor
    "epub_compress.preview",
    "epub_compress.start_task",
    "epub_compress.stop_task",
    "epub_compress.pause_task",
    "epub_compress.continue_task",
    "epub_compress.probe_continuable",
    "epub_compress.read_snapshot",
    "epub_compress.list_recent_tasks",
    "epub_compress.read_artifacts",
    "epub_compress.read_report",
    "epub_compress.list_failed_subtasks",
    # EPUB merger
    "epub_merge.preview",
    "epub_merge.start_task",
    "epub_merge.stop_task",
    "epub_merge.pause_task",
    "epub_merge.continue_task",
    "epub_merge.probe_continuable",
    "epub_merge.read_snapshot",
    "epub_merge.list_recent_tasks",
    "epub_merge.read_artifacts",
    "epub_merge.read_report",
    "epub_merge.list_failed_subtasks",
    # EPUB converter
    "epub_convert.preview",
    "epub_convert.start_task",
    "epub_convert.stop_task",
    "epub_convert.pause_task",
    "epub_convert.continue_task",
    "epub_convert.probe_continuable",
    "epub_convert.read_snapshot",
    "epub_convert.list_recent_tasks",
    "epub_convert.read_artifacts",
    "epub_convert.read_report",
    "epub_convert.list_failed_subtasks",
    # TXT to EPUB
    "txt_to_epub.list_styles",
    "txt_to_epub.list_presets",
    "txt_to_epub.scan_toc",
    "txt_to_epub.locate_toc_entry",
    "txt_to_epub.preview",
    "txt_to_epub.start_task",
    "txt_to_epub.stop_task",
    "txt_to_epub.probe_continuable",
    "txt_to_epub.read_snapshot",
    "txt_to_epub.list_recent_tasks",
    "txt_to_epub.read_artifacts",
    "txt_to_epub.read_report",
    "txt_to_epub.list_failed_subtasks",
    # EPUB metadata editor
    "epub_metadata.read",
    "epub_metadata.cover_preview",
    "epub_metadata.apply",
    # EPUB repair
    "epub_repair.apply",
    # updates
    "updates.check_latest",
    "updates.open_release_page",
    "updates.download_asset",
    "updates.apply_update_windows",
)


@pytest.fixture(scope="module")
def router(tmp_path_factory: pytest.TempPathFactory):
    cache_root = tmp_path_factory.mktemp("contract-cache")
    return build_default_router(cache_root=cache_root)
# Test 1 — backend exposes exactly the contract surface


def test_backend_registers_full_contract(router):
    actual = set(router.methods())
    expected = set(EXPECTED_METHODS)
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"backend missing methods: {sorted(missing)}"
    assert not extra, f"backend has unexpected methods: {sorted(extra)}"
    # 54 baseline + 5 added in D.1 (lifecycle continue/probe) +
    # 1 added in G.1 (model_templates.list) + 5 added 2026-04-29 / 30
    # (glossary.import_rules / export_rules / list_presets +
    # model_profiles.read_full + dialogs.choose_save_path) +
    # 1 added 2026-05-02 (updates.apply_update_windows) +
    # 2 added 2026-05-03 (rules.import_rules / export_rules) +
    # 2 added 2026-05-04 (tasks.summarize_caches / purge_caches) +
    # 4 added 2026-05-04 (proofreading.{list_tasks, load_snapshot,
    # update_segment, regenerate_outputs}) +
    # 3 added 2026-05-04/06-14 (proofreading retranslate queue surface) +
    # 10 added 2026-05-04 (glossary_review task/report surface) +
    # 2 added 2026-05-04 (glossary_review final XLSX editor) +
    # 1 added 2026-05-04 (glossary_review input discovery) +
    # 1 added 2026-05-04 (glossary_review final XLSX bulk delete) +
    # 1 added 2026-05-04 (restore deleted glossary-review report row) +
    # 3 added 2026-06-18 (LLM request logs for LLM workflows) +
    # 6 added 2026-06-28 (workflow presets) +
    # 1 added 2026-07-05 (model_profiles.duplicate).
    # 11 removed before 1.1.0 release (file organizer pulled from scope).
    assert len(actual) == 153
# Test 2 — frontend bridge wraps every backend method


_CALL_PATTERN = re.compile(r'call(?:<[^>]+>)?\(\s*"([^"]+)"')


def test_frontend_client_wraps_every_backend_method():
    if not CLIENT_TS.exists():
        pytest.skip(f"frontend client not found at {CLIENT_TS}")
    text = CLIENT_TS.read_text(encoding="utf-8")
    referenced = set(_CALL_PATTERN.findall(text))
    # Dialogs are now native pywebview helpers in the frontend. The backend
    # still registers the methods for headless tests and future browser
    # fallback, but the production UI does not call them through HTTP.
    referenced.update(method for method in EXPECTED_METHODS if method.startswith("dialogs."))
    backend = set(EXPECTED_METHODS)
    missing_in_frontend = backend - referenced
    extra_in_frontend = referenced - backend
    assert not missing_in_frontend, (
        "frontend bridge missing wrappers for backend methods: "
        f"{sorted(missing_in_frontend)}"
    )
    assert not extra_in_frontend, (
        "frontend bridge calls methods not registered on backend: "
        f"{sorted(extra_in_frontend)}"
    )
# Test 3 — every method either succeeds or raises a typed BridgeError


# Minimal payloads that satisfy each method's input contract. The values are
# deliberately invalid where the response is expected to be a typed
# BridgeError (e.g. "missing-id"), which still exercises the validation path.
MIN_PAYLOADS: dict[str, dict[str, object]] = {
    "app.get_metadata": {},
    "dialogs.choose_input_directory": {},
    "dialogs.choose_output_directory": {},
    "dialogs.choose_glossary_file": {},
    "dialogs.choose_save_path": {},
    "dialogs.choose_replacement_rules_file": {},
    "dialogs.open_directory": {"path": "/nonexistent-path-for-test"},
    "dialogs.reveal_file": {"path": "/nonexistent-path-for-test"},
    "settings.load_all": {},
    "settings.save_partial": {"module": "app", "patch": {}},
    "settings.reset_module": {"module": "app"},
    "model_profiles.list": {},
    "model_profiles.read_full": {"id": "missing-id"},
    "model_profiles.create": {"profile": {"display_name": "x"}},
    "model_profiles.update": {"id": "missing-id", "patch": {}},
    "model_profiles.delete": {"id": "missing-id"},
    "model_profiles.duplicate": {"id": "missing-id"},
    "model_profiles.set_api_key": {"id": "missing-id", "api_keys": []},
    "model_profiles.test_connection": {"id": "preset-deepseek", "request_id": "rid"},
    "model_profiles.fetch_model_list": {"id": "preset-deepseek", "request_id": "rid"},
    "model_profiles.select_active": {"module": "translation", "profile_id": None},
    "model_templates.list": {},
    "prompts.list": {"kind": "translation"},
    "prompts.read": {"id": "default-translation-en"},
    "prompts.create": {"kind": "translation", "preset": {"name": "x"}},
    "prompts.update": {"id": "missing-id", "patch": {}},
    "prompts.duplicate": {"id": "missing-id"},
    "prompts.delete": {"id": "missing-id"},
    "prompts.select_active": {"kind": "translation", "preset_id": None},
    "prompts.preview": {
        "preset_id": "default-translation-en",
        "context": {"source_language": "Korean", "target_language": "Chinese"},
    },
    "prompts.reset_to_default": {"id": "missing-id"},
    "workflow_presets.list": {"kind": "translation"},
    "workflow_presets.create": {
        "kind": "translation",
        "preset": {
            "name": "x",
            "model_profile_id": "missing-id",
            "prompt_preset_id": "default-translation-en",
            "source_language": "kr",
        },
    },
    "workflow_presets.update": {"id": "missing-id", "patch": {}},
    "workflow_presets.duplicate": {"id": "missing-id"},
    "workflow_presets.delete": {"id": "missing-id"},
    "workflow_presets.apply": {"kind": "translation", "id": "missing-id"},
    "translation.start_task": {"request_id": "rid"},
    "translation.pause_task": {"task_id": "missing"},
    "translation.stop_task": {"task_id": "missing"},
    "translation.continue_task": {"task_id": "missing"},
    "translation.probe_continuable": {},
    "translation.read_snapshot": {"task_id": "missing"},
    "translation.list_recent_tasks": {},
    "translation.read_artifacts": {"task_id": "missing"},
    "translation.list_failed_subtasks": {"task_id": "missing"},
    "translation.read_request_events": {"task_id": "missing"},
    "glossary.start_task": {"request_id": "rid"},
    "glossary.pause_task": {"task_id": "missing"},
    "glossary.stop_task": {"task_id": "missing"},
    "glossary.continue_task": {"task_id": "missing"},
    "glossary.probe_continuable": {},
    "glossary.read_snapshot": {"task_id": "missing"},
    "glossary.list_recent_tasks": {},
    "glossary.read_artifacts": {"task_id": "missing"},
    "glossary.list_failed_subtasks": {"task_id": "missing"},
    "glossary.read_request_events": {"task_id": "missing"},
    "glossary.import_rules": {"path": "/nonexistent-glossary-file"},
    "glossary.export_rules": {
        "path": "/tmp/transoria-test-export.json",
        "entries": [],
    },
    "glossary.list_presets": {},
    "glossary_review.start_task": {"request_id": "rid"},
    "glossary_review.pause_task": {"task_id": "missing"},
    "glossary_review.stop_task": {"task_id": "missing"},
    "glossary_review.continue_task": {"task_id": "missing"},
    "glossary_review.probe_continuable": {},
    "glossary_review.read_snapshot": {"task_id": "missing"},
    "glossary_review.list_recent_tasks": {},
    "glossary_review.read_artifacts": {"task_id": "missing"},
    "glossary_review.list_failed_subtasks": {"task_id": "missing"},
    "glossary_review.read_request_events": {"task_id": "missing"},
    "glossary_review.discover_inputs": {
        "input_folder": "/nonexistent-folder",
        "output_filename": "final.xlsx",
    },
    "glossary_review.read_report": {"task_id": "missing"},
    "glossary_review.read_final": {"task_id": "missing"},
    "glossary_review.update_final_row": {
        "task_id": "missing",
        "row_index": 2,
        "src": "",
        "dst": "",
        "info": "",
    },
    "glossary_review.delete_final_rows": {
        "task_id": "missing",
        "row_indices": [2],
    },
    "glossary_review.restore_deleted_report_row": {
        "task_id": "missing",
        "src": "x",
        "dst": "y",
        "info": "",
        "frequency": 0,
    },
    "rules.import_rules": {
        "kind": "text_preserve",
        "path": "/nonexistent-rules-file",
    },
    "rules.export_rules": {
        "kind": "text_preserve",
        "path": "/tmp/transoria-test-rules-export.json",
        "rules": [],
    },
    "tasks.summarize_caches": {},
    "tasks.purge_caches": {"scope": "older_than_days", "days": 365},
    "proofreading.list_tasks": {},
    "proofreading.load_snapshot": {"task_id": "translation-missing"},
    "proofreading.update_segment": {
        "task_id": "translation-missing",
        "segment_id": "0:0",
        "dst": "x",
    },
    "proofreading.regenerate_outputs": {"task_id": "translation-missing"},
    "proofreading.retranslate_segment": {
        "task_id": "translation-missing",
        "segment_id": "0:0",
    },
    "proofreading.retranslate_status": {"request_id": "retranslate-missing"},
    "proofreading.resume_retranslate": {"request_id": "retranslate-missing"},
    "replacement.import_rules": {"path": "/nonexistent-rule-file"},
    "replacement.validate_rules": {"rules": []},
    "replacement.start_task": {"request_id": "rid", "rules": []},
    "replacement.stop_task": {"task_id": "missing"},
    "replacement.pause_task": {"task_id": "missing"},
    "replacement.continue_task": {"task_id": "missing"},
    "replacement.probe_continuable": {},
    "replacement.read_snapshot": {"task_id": "missing"},
    "replacement.list_recent_tasks": {},
    "replacement.read_artifacts": {"task_id": "missing"},
    "replacement.read_replacement_report": {"task_id": "missing"},
    "replacement.list_failed_subtasks": {"task_id": "missing"},
    "epub_compress.preview": {
        "input_path": "/nonexistent-folder",
        "mode": "folder",
        "options": {},
    },
    "epub_compress.start_task": {
        "request_id": "rid",
        "input_path": "/nonexistent-folder",
        "mode": "folder",
        "options": {},
        "actions": [],
    },
    "epub_compress.stop_task": {"task_id": "missing"},
    "epub_compress.pause_task": {"task_id": "missing"},
    "epub_compress.continue_task": {"task_id": "missing"},
    "epub_compress.probe_continuable": {},
    "epub_compress.read_snapshot": {"task_id": "missing"},
    "epub_compress.list_recent_tasks": {},
    "epub_compress.read_artifacts": {"task_id": "missing"},
    "epub_compress.read_report": {"task_id": "missing"},
    "epub_compress.list_failed_subtasks": {"task_id": "missing"},
    "epub_merge.preview": {
        "input_dir": "/nonexistent-folder",
        "options": {},
    },
    "epub_merge.start_task": {
        "request_id": "rid",
        "input_dir": "/nonexistent-folder",
        "output_path": "/tmp/out.epub",
        "options": {},
        "actions": [],
    },
    "epub_merge.stop_task": {"task_id": "missing"},
    "epub_merge.pause_task": {"task_id": "missing"},
    "epub_merge.continue_task": {"task_id": "missing"},
    "epub_merge.probe_continuable": {},
    "epub_merge.read_snapshot": {"task_id": "missing"},
    "epub_merge.list_recent_tasks": {},
    "epub_merge.read_artifacts": {"task_id": "missing"},
    "epub_merge.read_report": {"task_id": "missing"},
    "epub_merge.list_failed_subtasks": {"task_id": "missing"},
    "epub_convert.preview": {
        "input_path": "/nonexistent-folder",
        "mode": "folder",
        "options": {},
    },
    "epub_convert.start_task": {
        "request_id": "rid",
        "input_path": "/nonexistent-folder",
        "mode": "folder",
        "options": {},
        "actions": [],
    },
    "epub_convert.stop_task": {"task_id": "missing"},
    "epub_convert.pause_task": {"task_id": "missing"},
    "epub_convert.continue_task": {"task_id": "missing"},
    "epub_convert.probe_continuable": {},
    "epub_convert.read_snapshot": {"task_id": "missing"},
    "epub_convert.list_recent_tasks": {},
    "epub_convert.read_artifacts": {"task_id": "missing"},
    "epub_convert.read_report": {"task_id": "missing"},
    "epub_convert.list_failed_subtasks": {"task_id": "missing"},
    "txt_to_epub.list_styles": {},
    "txt_to_epub.list_presets": {},
    "txt_to_epub.scan_toc": {
        "source_path": "/nonexistent-file.txt",
        "preset_id": "markdown",
    },
    "txt_to_epub.locate_toc_entry": {
        "source_path": "/nonexistent-file.txt",
        "query": "chapter",
    },
    "txt_to_epub.preview": {
        "options": {"source_path": "/nonexistent-file.txt"},
    },
    "txt_to_epub.start_task": {
        "request_id": "rid",
        "options": {"source_path": "/nonexistent-file.txt"},
    },
    "txt_to_epub.stop_task": {"task_id": "missing"},
    "txt_to_epub.probe_continuable": {},
    "txt_to_epub.read_snapshot": {"task_id": "missing"},
    "txt_to_epub.list_recent_tasks": {},
    "txt_to_epub.read_artifacts": {"task_id": "missing"},
    "txt_to_epub.read_report": {"task_id": "missing"},
    "txt_to_epub.list_failed_subtasks": {"task_id": "missing"},
    "epub_metadata.read": {"input_path": "/nonexistent.epub"},
    "epub_metadata.cover_preview": {"cover_path": "/nonexistent-cover.jpg"},
    "epub_metadata.apply": {
        "input_path": "/nonexistent.epub",
        "output_path": "/tmp/out.epub",
        "title": "",
        "author": "",
        "cover_path": "",
    },
    "epub_repair.apply": {
        "input_path": "/nonexistent.epub",
        "output_path": "",
        "overwrite": False,
    },
    "updates.check_latest": {"request_id": "rid"},
    "updates.open_release_page": {"url": "https://example.com"},
    "updates.download_asset": {
        "request_id": "rid",
        "asset_url": "https://example.com/x",
        "suggested_filename": "x.zip",
    },
    "updates.apply_update_windows": {
        "asset_url": "https://example.com/x",
        "suggested_filename": "x.zip",
        "target_version": "1.0.1",
    },
}


@pytest.mark.parametrize("method", EXPECTED_METHODS)
def test_method_returns_mapping_or_typed_error(router, method: str):
    payload = MIN_PAYLOADS[method]
    try:
        response = router.call(method, payload)
    except BridgeError as exc:
        assert isinstance(exc.payload.code, str) and exc.payload.code.strip()
        assert isinstance(exc.payload.message, str)
        return
    assert isinstance(response, dict), (
        f"{method} returned non-mapping response: {response!r}"
    )


def test_min_payloads_cover_every_method():
    """Guards against drift: if a new backend method is added, the test
    matrix must be extended too."""

    extra = set(MIN_PAYLOADS) - set(EXPECTED_METHODS)
    missing = set(EXPECTED_METHODS) - set(MIN_PAYLOADS)
    assert not extra, f"MIN_PAYLOADS has stale entries: {sorted(extra)}"
    assert not missing, f"MIN_PAYLOADS missing entries: {sorted(missing)}"
