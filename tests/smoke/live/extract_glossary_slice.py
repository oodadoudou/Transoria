"""End-to-end glossary-extraction smoke test against the live API.

Mirrors ``translate_slice.py`` but for the glossary workflow. Reads
the configured ``deepseek-14ee52`` profile, copies the EPUB slice 1
into a temp input dir, runs ``GlossaryOrchestrator``, then prints the
top extracted entries so the new default prompt's quality is visible
without launching the frontend.

Usage::

    .venv/bin/python tests/smoke/live/extract_glossary_slice.py
    .venv/bin/python tests/smoke/live/extract_glossary_slice.py --concurrency 20

This makes real API calls and incurs cost. Outputs go to
``tests/private/smoke_out/glossary/``.
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
from transoria.workflows.glossary.config import GlossaryConfig
from transoria.workflows.glossary.orchestrator import GlossaryOrchestrator


ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = ROOT / "tests" / "private" / "fixtures" / "slices"
OUT_ROOT = ROOT / "tests" / "private" / "smoke_out"
CACHE_DIR = ROOT / ".transoria-cache"

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


def build_preset(prompt_file: Path | None) -> PromptPreset:
    base = default_preset(PromptKind.GLOSSARY)
    if prompt_file is None:
        return base
    custom_system = prompt_file.read_text(encoding="utf-8").rstrip()
    return dataclasses.replace(
        base,
        id=f"{base.id}-custom",
        name=f"{base.name} (custom system prompt)",
        system_prompt=custom_system,
    )


def stage_input() -> tuple[Path, Path, Path]:
    src = SRC_DIR / EPUB_SLICE
    if not src.exists():
        sys.exit(f"slice not found: {src}")
    work_root = OUT_ROOT / "glossary"
    if work_root.exists():
        shutil.rmtree(work_root)
    input_dir = work_root / "input"
    output_dir = work_root / "output"
    cache_root = work_root / "cache"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    cache_root.mkdir(parents=True)
    shutil.copy2(src, input_dir / src.name)
    return input_dir, output_dir, cache_root


async def extract(profile, preset: PromptPreset) -> dict:
    input_dir, output_dir, cache_root = stage_input()
    client = LlmClient(transport=HttpxChatTransport())
    orchestrator = GlossaryOrchestrator(
        cache=TaskCache(root=cache_root),
        client=client,
    )
    config = GlossaryConfig(
        input_dir=input_dir,
        output_dir=output_dir,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        model=profile,
        prompt_preset=preset,
        chunk_char_limit=4000,
    )

    started = time.monotonic()
    result = await orchestrator.run(config)
    elapsed = time.monotonic() - started

    stats = result.statistics
    summary = {
        "elapsed_seconds": round(elapsed, 1),
        "task_id": result.task_id,
        "status": result.final_status.value,
        "input_tokens": stats.usage.input_tokens,
        "output_tokens": stats.usage.output_tokens,
        "total_tokens": stats.usage.total_tokens,
        "per_file_artifacts": [
            str(artifact.json_path)
            for artifact in result.glossary_outputs_per_file
        ],
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    # Show first 25 entries from the first artifact's JSON.
    if result.glossary_outputs_per_file:
        first = result.glossary_outputs_per_file[0]
        try:
            data = json.loads(first.json_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print("\n(no entries written)")
            return summary
        print(f"\n=== {first.novel_name} — first 25 entries ===")
        entries = data if isinstance(data, list) else data.get("entries", [])
        for entry in entries[:25]:
            src = entry.get("src", "")
            dst = entry.get("dst", "")
            info = entry.get("info") or entry.get("type", "")
            print(f"  {src:<24}  →  {dst:<24}  [{info}]")
        print(f"\n  ... total {len(entries)} entries")
    return summary


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser()
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
        f"concurrency={profile.concurrency_limit}  "
        f"keys={len(profile.api_keys)}"
    )
    print(f"prompt:  {preset.name}  ({len(preset.system_prompt)} chars)")
    print()
    asyncio.run(extract(profile, preset))


if __name__ == "__main__":
    main(sys.argv)
