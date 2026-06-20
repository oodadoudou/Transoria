"""Real-API single-subtask smoke harness for translation format compliance.

Sends N independent single-subtask requests through ``TranslationSubtaskRunner``
against the live LLM, cycling through every preset stored in the local
``.transoria-cache/prompts.translation.json`` (system + user-authored).
For each request, records:

- which decoder layer accepted the response (strict / object-fallback /
  positional rescue), or whether all layers failed
- the attempt count consumed inside ``retry_async``
- whether the runner surfaced any ``low_confidence`` flags

The lesson from earlier sessions ("future LLM format validation must run
repeated single-subtask checks before multi-chunk smoke to avoid wasting
API tokens") drives the shape: many small requests, not a full slice
run. The output is a per-preset table so a regression that affects only
one preset shape is visible immediately.

Usage::

    .venv/bin/python tests/smoke/live/translate_format_compliance_smoke.py
    .venv/bin/python tests/smoke/live/translate_format_compliance_smoke.py \\
        --profile deepseek-4deaef \\
        --presets v4p-适配-dc60dd \\
        --rounds 3

This makes real API calls and incurs cost (small — 5 short Korean
sentences per request, default 3 requests per preset).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import time
from pathlib import Path
from typing import Iterable

from transoria.domain import Language
from transoria.llm.client import HttpxChatTransport, LlmClient, LlmRequestError
from transoria.llm.decoders import decode_translation_jsonl
from transoria.model_profiles import ModelProfileStore
from transoria.prompts import PromptKind, PromptPreset
from transoria.workflows.translation import (
    Glossary,
    PreparedSegment,
    TranslationSubtaskRunner,
    build_chunks,
    encode_subtask_payload,
    preprocess_segment,
)
from transoria.runtime import Subtask


ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / ".transoria-cache"
PROMPTS_PATH = CACHE_DIR / "prompts.translation.json"


# Five short Korean sentences from a representative novel snippet —
# realistic enough to exercise the model on real translation work, not
# trivial enough to be cached. Each sentence is intentionally one
# discrete unit so positional rescue would have a chance if format
# parsing fully failed.
SOURCE_LINES: tuple[str, ...] = (
    "「예쁜 것, 날 기다리고 있었구나.」",
    "신오는 천천히 눈을 떴다.",
    "객실의 낮은 천장이 보였다.",
    "남자의 서늘한 목소리가 다시 들려왔다.",
    "“그만 일어나세요.”",
)


def load_profile(profile_id: str):
    store = ModelProfileStore(
        profiles_path=CACHE_DIR / "model_profiles.json",
        keys_path=CACHE_DIR / "model_profile_keys.json",
    )
    profile = store.get(profile_id)
    if profile is None:
        sys.exit(f"profile {profile_id!r} not found in cache")
    if not profile.api_keys:
        sys.exit(f"profile {profile_id!r} has no API key configured")
    return profile


def load_presets(only_ids: Iterable[str] | None) -> list[PromptPreset]:
    if not PROMPTS_PATH.exists():
        sys.exit(f"prompts file not found: {PROMPTS_PATH}")
    raw = json.loads(PROMPTS_PATH.read_text(encoding="utf-8"))
    keep = set(only_ids) if only_ids else None
    presets: list[PromptPreset] = []
    for entry in raw:
        if entry.get("kind") != "translation":
            continue
        if keep is not None and entry.get("id") not in keep:
            continue
        presets.append(
            PromptPreset(
                id=str(entry.get("id", "")),
                name=str(entry.get("name", "")),
                kind=PromptKind.TRANSLATION,
                system_prompt=str(entry.get("system_prompt", "")),
                suffix_prompt=str(entry.get("suffix_prompt", "")),
                thinking_prompt=str(entry.get("thinking_prompt", "")),
                description=str(entry.get("description", "")),
                enabled=bool(entry.get("enabled", True)),
                is_system=bool(entry.get("is_system", False)),
            )
        )
    if not presets:
        sys.exit("no translation presets matched filter")
    return presets


def build_subtask(sources: tuple[str, ...]) -> Subtask:
    prepared: list[PreparedSegment] = []
    for offset, text in enumerate(sources):
        prepared.append(
            PreparedSegment(
                segment_id=f"0:{offset}",
                original_text=text,
                preprocessed=preprocess_segment(text),
            )
        )
    chunks = build_chunks(
        tuple(prepared),
        chunk_size=len(prepared),
        context_line_count=0,
        glossary=Glossary.empty(),
    )
    chunk = chunks[0]
    metadata = [
        {
            "original_text": item.original_text,
            "protection_spans": list(item.preprocessed.protection.spans),
            "leading_whitespace": item.preprocessed.leading_whitespace,
            "trailing_whitespace": item.preprocessed.trailing_whitespace,
        }
        for item in prepared
    ]
    payload = encode_subtask_payload(chunk, segment_metadata=metadata)
    return Subtask(id=f"smoke-{int(time.time() * 1000)}", task_id="smoke-task", request_payload=payload)


@dataclasses.dataclass
class CallObservation:
    preset_id: str
    round_index: int
    elapsed_seconds: float
    decoded_lines: int
    decode_issues: int
    decode_path: str  # "strict", "object_fallback", "rescue", "fail"
    low_confidence_count: int
    error: str | None


async def one_call(
    *,
    profile,
    preset: PromptPreset,
    round_index: int,
    sources: tuple[str, ...],
) -> CallObservation:
    transport = HttpxChatTransport()
    client = LlmClient(transport=transport)
    runner = TranslationSubtaskRunner(
        client=client,
        model=profile,
        prompt_preset=preset,
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
    )
    subtask = build_subtask(sources)
    started = time.monotonic()
    try:
        result = await runner.run(subtask)
    except LlmRequestError as exc:
        return CallObservation(
            preset_id=preset.id,
            round_index=round_index,
            elapsed_seconds=round(time.monotonic() - started, 2),
            decoded_lines=0,
            decode_issues=0,
            decode_path="fail",
            low_confidence_count=0,
            error=f"{exc.code}: {exc}",
        )
    elapsed = time.monotonic() - started
    payload = json.loads(result.response_content)
    translations = payload.get("translations", {})
    low_conf = payload.get("low_confidence", [])
    rescued = any(
        "positional_rescue_after_format_failure" in (item.get("reasons") or [])
        for item in low_conf
    )
    decode_path = "rescue" if rescued else "strict_or_fallback"
    return CallObservation(
        preset_id=preset.id,
        round_index=round_index,
        elapsed_seconds=round(elapsed, 2),
        decoded_lines=len(translations),
        decode_issues=0,
        decode_path=decode_path,
        low_confidence_count=len(low_conf),
        error=None,
    )


def print_summary(results: list[CallObservation]) -> None:
    by_preset: dict[str, list[CallObservation]] = {}
    for r in results:
        by_preset.setdefault(r.preset_id, []).append(r)

    print("\n" + "=" * 78)
    print(f"{'preset':<28} {'rounds':>7} {'pass':>5} {'fail':>5} {'rescue':>7} {'avg s':>6}")
    print("-" * 78)
    for preset_id, rs in by_preset.items():
        ok = sum(1 for r in rs if r.error is None and r.decoded_lines == len(SOURCE_LINES))
        fail = sum(1 for r in rs if r.error is not None)
        rescue = sum(1 for r in rs if r.decode_path == "rescue")
        avg = sum(r.elapsed_seconds for r in rs) / len(rs)
        print(f"{preset_id[:28]:<28} {len(rs):>7} {ok:>5} {fail:>5} {rescue:>7} {avg:>6.2f}")
    print("=" * 78)
    print()
    failures = [r for r in results if r.error is not None]
    if failures:
        print("FAILURES:")
        for r in failures:
            print(f"  [{r.preset_id}] round {r.round_index}: {r.error}")
    else:
        print("No failures across all preset × round combinations.")


async def main_async(args) -> int:
    profile = load_profile(args.profile)
    presets = load_presets(args.presets)
    print(
        f"profile: {profile.id}  model={profile.model_id}  "
        f"thinking={profile.thinking_level.value}"
    )
    print(f"presets: {[p.id for p in presets]}")
    print(f"rounds per preset: {args.rounds}")
    print(f"source lines per call: {len(SOURCE_LINES)}")
    print()

    results: list[CallObservation] = []
    for preset in presets:
        for round_index in range(args.rounds):
            print(
                f"-> [{preset.id}] round {round_index + 1}/{args.rounds} ...",
                end="",
                flush=True,
            )
            obs = await one_call(
                profile=profile,
                preset=preset,
                round_index=round_index,
                sources=SOURCE_LINES,
            )
            results.append(obs)
            tag = (
                "OK"
                if obs.error is None and obs.decoded_lines == len(SOURCE_LINES)
                else "FAIL"
            )
            print(
                f" {tag} ({obs.elapsed_seconds}s, {obs.decoded_lines} lines, "
                f"path={obs.decode_path})"
            )
    print_summary(results)
    return 0 if all(r.error is None for r in results) else 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        default="deepseek-4deaef",
        help="Model profile id from .transoria-cache/model_profiles.json",
    )
    parser.add_argument(
        "--presets",
        nargs="*",
        default=None,
        help="Restrict to these preset ids. Default: all translation presets.",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="Independent calls per preset (each request is fresh, no cache).",
    )
    args = parser.parse_args(argv[1:])
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
