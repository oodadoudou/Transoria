"""End-to-end translation smoke test against the live API.

Reads the configured ``deepseek-14ee52`` profile from the local cache,
copies a single fixture slice into a temp input dir, runs the
TranslationOrchestrator, then prints input/output snippets so the
quality of the live model is visible without standing up the
frontend.

Usage::

    .venv/bin/python tests/smoke/live/translate_slice.py txt
    .venv/bin/python tests/smoke/live/translate_slice.py epub
    .venv/bin/python tests/smoke/live/translate_slice.py both

Optional flags:

    --concurrency N      Override the profile's concurrency_limit
                         (default: profile value, currently 10).
    --prompt-file PATH   Replace the default translation system_prompt
                         with the contents of this file. The
                         JSONLINE suffix + thinking blocks from the
                         seeded preset are preserved so output decoding
                         still works.

This makes real API calls against the configured key and will incur
cost. Outputs go to ``tests/private/smoke_out/translation/``.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import shutil
import sys
import time
from pathlib import Path

from transoria.domain import Language
from transoria.llm.client import HttpxChatTransport, LlmClient
from transoria.model_profiles import ModelProfileStore
from transoria.prompts import PromptKind, PromptPreset, default_preset
from transoria.runtime import TaskCache
from transoria.workflows.translation import TranslationConfig
from transoria.workflows.translation.orchestrator import TranslationOrchestrator


ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "tests" / "private" / "fixtures" / "slices"
OUT_ROOT = ROOT / "tests" / "private" / "smoke_out" / "translation"
CACHE_DIR = ROOT / ".transoria-cache"

TXT_SLICE = "블랙 앤 그레이(BLACK ＆ GREY) 1권 - slice 1of4.txt"
EPUB_SLICE = "[몽년] 스노우 화이트 1권 @공이 - slice 1of4.epub"

PROFILE_ID = "deepseek-14ee52"


def load_profile():
    store = ModelProfileStore(
        profiles_path=CACHE_DIR / "model_profiles.json",
        keys_path=CACHE_DIR / "model_profile_keys.json",
    )
    profile = store.get(PROFILE_ID)
    if profile is None:
        sys.exit(f"profile {PROFILE_ID!r} not found in cache")
    if not profile.api_keys:
        sys.exit(f"profile {PROFILE_ID!r} has no API key configured")
    return profile


def stage_input(slice_name: str, kind: str) -> tuple[Path, Path]:
    src = SRC_DIR / slice_name
    if not src.exists():
        sys.exit(f"slice not found: {src}")
    work_root = OUT_ROOT / kind
    if work_root.exists():
        shutil.rmtree(work_root)
    input_dir = work_root / "input"
    output_dir = work_root / "output"
    cache_root = work_root / "cache"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    cache_root.mkdir(parents=True)
    target = input_dir / src.name
    shutil.copy2(src, target)
    return input_dir, output_dir


def build_preset(prompt_file: Path | None) -> PromptPreset:
    base = default_preset(PromptKind.TRANSLATION)
    if prompt_file is None:
        return base
    custom_system = prompt_file.read_text(encoding="utf-8").rstrip()
    return dataclasses.replace(
        base,
        id=f"{base.id}-custom",
        name=f"{base.name} (custom system prompt)",
        system_prompt=custom_system,
    )


def build_config(
    profile,
    input_dir: Path,
    output_dir: Path,
    *,
    preset: PromptPreset,
) -> TranslationConfig:
    return TranslationConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=profile,
        prompt_preset=preset,
        # Modest chunking — keep per-call payloads small so the smoke
        # test surfaces issues quickly.
        chunk_size=8,
        context_line_count=4,
        # Confidence retry off in smoke mode: we want the raw first-
        # pass quality, not retried output.
        enable_confidence_check=False,
        low_confidence_max_retries=0,
    )


async def translate(
    profile,
    kind: str,
    *,
    preset: PromptPreset,
) -> tuple[Path, dict]:
    slice_name = TXT_SLICE if kind == "txt" else EPUB_SLICE
    input_dir, output_dir = stage_input(slice_name, kind)
    work_root = output_dir.parent
    cache_root = work_root / "cache"

    client = LlmClient(transport=HttpxChatTransport())
    orchestrator = TranslationOrchestrator(
        cache=TaskCache(root=cache_root),
        client=client,
    )
    config = build_config(profile, input_dir, output_dir, preset=preset)

    started = time.monotonic()
    result = await orchestrator.run(config)
    elapsed = time.monotonic() - started

    stats = result.statistics
    summary = {
        "kind": kind,
        "elapsed_seconds": round(elapsed, 1),
        "task_id": result.task_id,
        "status": result.final_status.value,
        "translated_outputs": [str(p) for p in result.translated_outputs],
        "completed_segments": stats.completed_segments,
        "failed_subtasks": stats.failed_subtasks,
        "total_segments": stats.total_segments,
        "input_tokens": stats.usage.input_tokens,
        "output_tokens": stats.usage.output_tokens,
        "total_tokens": stats.usage.total_tokens,
    }
    return output_dir, summary


def show_diff_snippet(kind: str, input_dir: Path, output_dir: Path) -> None:
    print(f"\n=== {kind.upper()} snippets ===")
    if kind == "txt":
        src_files = list(input_dir.glob("*.txt"))
        out_files = list(output_dir.glob("*.txt"))
        if not src_files or not out_files:
            print("(no files)")
            return
        src_lines = src_files[0].read_text(encoding="utf-8").splitlines()
        out_lines = out_files[0].read_text(encoding="utf-8").splitlines()
        # Show first 12 non-empty source lines paired with output.
        ko_idx = [i for i, ln in enumerate(src_lines) if ln.strip()][:12]
        for i in ko_idx:
            ko = src_lines[i].strip()
            zh = out_lines[i].strip() if i < len(out_lines) else ""
            print(f"  KO  {ko[:80]}")
            print(f"  ZH  {zh[:80]}")
            print()
    else:
        out_files = sorted(output_dir.rglob("*.epub"))
        if not out_files:
            print("(no output epub)")
            return
        from transoria.formats.epub_parser import parse_epub_file

        in_doc = parse_epub_file(next(input_dir.glob("*.epub")))
        out_doc = parse_epub_file(out_files[0])
        in_segs = [s for s in in_doc.segments if s.text.strip()][:10]
        out_by_index = {s.index: s.text for s in out_doc.segments}
        for seg in in_segs:
            print(f"  KO  {seg.text.strip()[:80]}")
            zh = out_by_index.get(seg.index, "").strip()
            print(f"  ZH  {zh[:80]}")
            print()


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["txt", "epub", "both"])
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--prompt-file", type=Path, default=None)
    args = parser.parse_args(argv[1:])

    profile = load_profile()
    if args.concurrency is not None and args.concurrency > 0:
        profile = dataclasses.replace(profile, concurrency_limit=args.concurrency)

    preset = build_preset(args.prompt_file)

    print(
        f"profile: {profile.id}  model={profile.model_id}  "
        f"thinking={profile.thinking_level.value}  "
        f"concurrency={profile.concurrency_limit}  rpm={profile.rpm_limit}"
    )
    print(f"prompt:  {preset.name}  ({len(preset.system_prompt)} chars)")

    targets = ["txt", "epub"] if args.mode == "both" else [args.mode]
    summaries = []
    for kind in targets:
        print(f"\n--- translating {kind} slice ---")
        output_dir, summary = asyncio.run(translate(profile, kind, preset=preset))
        summaries.append((kind, output_dir, summary))
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    for kind, output_dir, _ in summaries:
        input_dir = output_dir.parent / "input"
        show_diff_snippet(kind, input_dir, output_dir)


if __name__ == "__main__":
    main(sys.argv)
