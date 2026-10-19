from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from openpyxl import Workbook, load_workbook

from transoria.llm.config import ModelConfig
from transoria.prompts import PromptPreset
from transoria.domain import Language, SubtaskStatus, TaskKind, TaskStatus
from transoria.runtime import Subtask, SubtaskResult, TaskCache
from transoria.runtime.task_record import TaskRecord
from transoria.workflows.glossary_review.config import GlossaryReviewConfig
from transoria.workflows.glossary_review.loader import GlossaryReviewRow
from transoria.workflows.glossary_review.orchestrator import GlossaryReviewOrchestrator
from transoria.workflows.glossary_review.orchestrator import _default_runner_factory
from transoria.workflows.glossary_review.runner import decode_review_response


def test_decode_review_response_ignores_keep_rows() -> None:
    decoded = decode_review_response(
        '{"decisions":[{"row_index":2,"action":"keep"},{"row_index":3,"action":"modify","suggested_dst":"甲","reason":"better"}]}'
    )

    assert len(decoded) == 2
    assert decoded[0].action == "keep"
    assert decoded[1].row_index == 3
    assert decoded[1].suggested_dst == "甲"


def test_round_payload_includes_tier_and_history(tmp_path) -> None:
    config = _config(tmp_path, novel_background="신해범 是核心人物")
    row = GlossaryReviewRow(
        row_index=2,
        src="신해범",
        dst="申海凡",
        info="男性角色",
        frequency=8,
        context="신해범 出现在正文。",
    )
    orchestrator = GlossaryReviewOrchestrator(cache=None, client=None)  # type: ignore[arg-type]

    subtasks = orchestrator._build_round_subtasks(
        "task",
        round_index=2,
        config=config,
        rows=(row,),
        history={row.src: ()},
    )

    payload_row = subtasks[0].request_payload["rows"][0]
    assert payload_row["tier"] == "S"
    assert "核心设定词" in payload_row["instruction"]
    assert payload_row["is_character"] is True
    assert payload_row["current_category"] == "男性角色"


def test_consensus_terms_are_skipped_from_third_round(tmp_path) -> None:
    config = _config(tmp_path)
    row = GlossaryReviewRow(
        row_index=2,
        src="응",
        dst="嗯",
        info="其它",
        frequency=20,
    )
    orchestrator = GlossaryReviewOrchestrator(cache=None, client=None)  # type: ignore[arg-type]

    subtasks = orchestrator._build_round_subtasks(
        "task",
        round_index=3,
        config=config,
        rows=(row,),
        history={
            row.src: (
                type("History", (), {"dst": "嗯", "info": "其它", "deleted": True})(),
                type("History", (), {"dst": "嗯", "info": "其它", "deleted": True})(),
            )
        },
    )

    assert subtasks == []


def test_review_finalizes_output_when_stop_hits_after_all_batches(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["src", "dst", "info", "frequency"])
    sheet.append(["신해범", "申海凡", "男性角色", 5])
    workbook.save(tmp_path / "terms.xlsx")
    (tmp_path / "terms-references.txt").write_text("신해범 出现在正文。", encoding="utf-8")
    holder: dict[str, object] = {}

    class CompleteRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            row = subtask.request_payload["rows"][0]
            return SubtaskResult(
                response_content=json.dumps(
                    {
                        "decisions": [
                            {
                                "row_index": row["row_index"],
                                "action": "modify",
                                "suggested_dst": "申海范",
                                "suggested_info": "男性角色",
                                "reason": "统一人名",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    def request_stop(_event: object) -> None:
        executor = holder.get("executor")
        if executor is not None:
            executor.request_stop()  # type: ignore[attr-defined]

    orchestrator = GlossaryReviewOrchestrator(
        cache=TaskCache(root=tmp_path / "cache"),
        client=None,  # type: ignore[arg-type]
        runner_factory=lambda _client, _config: CompleteRunner(),
        progress=request_stop,
        on_executor_created=lambda executor: holder.__setitem__("executor", executor),
        id_factory=lambda: "review-stop-after-complete",
    )

    result = asyncio.run(
        orchestrator.run(
            GlossaryReviewConfig(
                **{
                    **_config(tmp_path).__dict__,
                    "review_rounds": 1,
                }
            )
        )
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert result.output_path is not None
    assert result.report_path is not None
    assert result.output_path.exists()
    assert result.report_path.exists()
    rows = [tuple(row) for row in load_workbook(result.output_path).active.iter_rows(values_only=True)]
    assert rows[1][1] == "申海范"
    assert orchestrator.cache.load(result.task_id).record.status is TaskStatus.COMPLETED
    assert getattr(holder["executor"], "subtask_timeout_seconds") == 0.0


def test_review_marks_completed_when_all_rounds_were_already_done(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["src", "dst", "info", "frequency"])
    sheet.append(["신해범", "申海凡", "男性角色", 5])
    workbook.save(tmp_path / "terms.xlsx")
    (tmp_path / "terms-references.txt").write_text("신해범 出现在正文。", encoding="utf-8")
    cache = TaskCache(root=tmp_path / "cache")
    task_id = "review-already-done"
    created_at = "2026-05-01T00:00:00+00:00"
    cache.write_seed(
        TaskRecord(
            id=task_id,
            kind=TaskKind.GLOSSARY_REVIEW,
            status=TaskStatus.RUNNING,
            created_at=created_at,
            updated_at=created_at,
            metadata={
                "input_dir": str(tmp_path),
                "output_dir": str(tmp_path),
                "output_filename": "reviewed.xlsx",
                "input_xlsx": str(tmp_path / "terms.xlsx"),
                "reference_files": [str(tmp_path / "terms-references.txt")],
                "review_rounds_total": 1,
                "review_round_current": 1,
                "review_round_completed": 1,
                "review_round_total_batches": 1,
                "review_round_completed_batches": 1,
            },
        ),
        (
            Subtask(
                id="round-01-batch-0000",
                task_id=task_id,
                status=SubtaskStatus.COMPLETED,
                request_payload={
                    "round": 1,
                    "batch": 0,
                    "rows": [
                        {
                            "row_index": 2,
                            "src": "신해범",
                            "dst": "申海凡",
                            "info": "男性角色",
                            "frequency": 5,
                        }
                    ],
                },
                response_content=json.dumps(
                    {
                        "decisions": [
                            {
                                "row_index": 2,
                                "action": "modify",
                                "suggested_dst": "申海范",
                                "suggested_info": "男性角色",
                                "reason": "统一人名",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
            ),
        ),
    )

    class KeepRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            return SubtaskResult(
                response_content=json.dumps(
                    {
                        "decisions": [
                            {"row_index": row["row_index"], "action": "keep"}
                            for row in subtask.request_payload["rows"]
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    orchestrator = GlossaryReviewOrchestrator(
        cache=cache,
        client=None,  # type: ignore[arg-type]
        runner_factory=lambda _client, _config: KeepRunner(),
        id_factory=lambda: task_id,
    )

    result = asyncio.run(
        orchestrator.run(
            GlossaryReviewConfig(
                **{
                    **_config(tmp_path).__dict__,
                    "review_rounds": 1,
                }
            )
        )
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert result.output_path is not None
    assert result.report_path is not None
    assert result.output_path.exists()
    assert result.report_path.exists()
    assert cache.load(task_id).record.status is TaskStatus.COMPLETED


def test_final_character_consistency_round_updates_name_and_gender_category(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["src", "dst", "info", "frequency"])
    sheet.append(["하얀이", "李夏彦", "女性角色", 12])
    sheet.append(["하얀", "李夏言", "男性角色", 9])
    sheet.append(["센터", "中心", "命名组织", 3])
    workbook.save(tmp_path / "terms.xlsx")
    (tmp_path / "terms-references.txt").write_text(
        "하얀이 和 하얀 是同一角色的称呼。", encoding="utf-8"
    )

    class ConsistencyRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            if subtask.request_payload.get("mode") == "character_consistency":
                return SubtaskResult(
                    response_content=json.dumps(
                        {
                            "decisions": [
                                {
                                    "row_index": 2,
                                    "action": "modify_category",
                                    "suggested_dst": "李夏言",
                                    "suggested_info": "男性角色",
                                    "reason": "同一角色译名和性别分类统一",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
            return SubtaskResult(
                response_content=json.dumps(
                    {
                        "decisions": [
                            {"row_index": row["row_index"], "action": "keep"}
                            for row in subtask.request_payload["rows"]
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    cache = TaskCache(root=tmp_path / "cache")
    orchestrator = GlossaryReviewOrchestrator(
        cache=cache,
        client=None,  # type: ignore[arg-type]
        runner_factory=lambda _client, _config: ConsistencyRunner(),
        id_factory=lambda: "review-character-consistency",
    )

    result = asyncio.run(
        orchestrator.run(
            GlossaryReviewConfig(
                **{
                    **_config(tmp_path).__dict__,
                    "review_rounds": 1,
                }
            )
        )
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert result.output_path is not None
    assert result.report_path is not None
    rows = [
        tuple(row)
        for row in load_workbook(result.output_path).active.iter_rows(values_only=True)
    ]
    assert rows[1][1] == "李夏言"
    assert rows[1][2] == "男性角色"
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["rows"][0]["action"] == "name_consistency"
    assert report["rows"][0]["suggested_info"] == "男性角色"
    metadata = cache.load(result.task_id).record.metadata
    assert metadata["review_rounds_total"] == 2
    consistency_subtasks = [
        subtask
        for subtask in cache.load_subtasks(result.task_id)
        if subtask.request_payload.get("mode") == "character_consistency"
    ]
    assert len(consistency_subtasks) == 1
    assert [
        row["src"] for row in consistency_subtasks[0].request_payload["rows"]
    ] == ["하얀이", "하얀"]


def test_final_character_consistency_round_accepts_category_only_fix(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["src", "dst", "info", "frequency"])
    sheet.append(["선우", "善宇", "女性角色", 155])
    sheet.append(["남선우", "南善宇", "男性角色", 5])
    workbook.save(tmp_path / "terms.xlsx")
    (tmp_path / "terms-references.txt").write_text(
        "선우 是 남선우 的简称。", encoding="utf-8"
    )

    class ConsistencyRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            if subtask.request_payload.get("mode") == "character_consistency":
                return SubtaskResult(
                    response_content=json.dumps(
                        {
                            "decisions": [
                                {
                                    "row_index": 2,
                                    "action": "category",
                                    "suggested_info": "男性角色",
                                    "reason": "简称与全名指向同一男性角色",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                )
            return SubtaskResult(
                response_content=json.dumps(
                    {
                        "decisions": [
                            {"row_index": row["row_index"], "action": "keep"}
                            for row in subtask.request_payload["rows"]
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    cache = TaskCache(root=tmp_path / "cache")
    orchestrator = GlossaryReviewOrchestrator(
        cache=cache,
        client=None,  # type: ignore[arg-type]
        runner_factory=lambda _client, _config: ConsistencyRunner(),
        id_factory=lambda: "review-character-gender-consistency",
    )

    result = asyncio.run(
        orchestrator.run(
            GlossaryReviewConfig(
                **{
                    **_config(tmp_path).__dict__,
                    "review_rounds": 1,
                }
            )
        )
    )

    assert result.final_status is TaskStatus.COMPLETED
    assert result.output_path is not None
    rows = [
        tuple(row)
        for row in load_workbook(result.output_path).active.iter_rows(values_only=True)
    ]
    assert rows[1][1] == "善宇"
    assert rows[1][2] == "男性角色"
    assert result.report_path is not None
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["rows"][0]["action"] == "name_consistency"
    assert report["rows"][0]["suggested_info"] == "男性角色"


def test_default_review_runner_uses_config_retry_attempts(tmp_path) -> None:
    config = replace(_config(tmp_path), retry_attempts=4)

    runner = _default_runner_factory(None, config)  # type: ignore[arg-type]

    assert runner.transport_retry_attempts == 4  # type: ignore[attr-defined]


def _config(tmp_path, *, novel_background: str = "") -> GlossaryReviewConfig:
    return GlossaryReviewConfig(
        input_dir=tmp_path,
        selected_xlsx_path=None,
        selected_reference_paths=(),
        source_language=Language.KOREAN,
        target_language=Language.CHINESE_SIMPLIFIED,
        output_filename="reviewed.xlsx",
        novel_background=novel_background,
        review_rounds=3,
        batch_size=20,
        retry_attempts=3,
        model=ModelConfig(
            id="m",
            display_name="m",
            provider_format="custom",
            base_url="http://localhost/v1",
            model_id="model",
            api_keys=("key",),
        ),
        prompt_preset=PromptPreset(
            id="p",
            kind="glossary_review",
            name="p",
            description="",
            enabled=True,
            is_system=False,
            system_prompt="review",
        ),
    )


class _RecordingClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.prompts: list[str] = []

    async def chat(self, request):
        from transoria.llm.client import ChatResponse
        from transoria.llm.usage import TokenUsage

        self.prompts.append(request.user_prompt)
        return ChatResponse(content=self.content, usage=TokenUsage(), raw={})


def _consistency_subtask(rows: list[dict[str, object]]) -> Subtask:
    from transoria.workflows.glossary_review.runner import encode_review_payload

    return Subtask(
        id="round-04-character-consistency",
        task_id="t",
        request_payload=encode_review_payload(
            round_index=4,
            batch_index=0,
            rows=tuple(rows),
            novel_background="",
            mode="character_consistency",
        ),
    )


def _consistency_runner(tmp_path, client: _RecordingClient):
    from transoria.workflows.glossary_review.runner import GlossaryReviewSubtaskRunner

    config = _config(tmp_path)
    return GlossaryReviewSubtaskRunner(
        client=client,  # type: ignore[arg-type]
        model=config.model,
        prompt_preset=config.prompt_preset,
    )


def test_consistency_prompt_asks_only_for_changed_rows(tmp_path) -> None:
    client = _RecordingClient("[]")
    runner = _consistency_runner(tmp_path, client)
    rows = [
        {"row_index": 2, "src": "하얀", "dst": "夏言", "info": "男性角色", "context": ""},
        {"row_index": 3, "src": "하얀이", "dst": "夏言", "info": "男性角色", "context": ""},
    ]

    result = asyncio.run(runner.run(_consistency_subtask(rows)))

    prompt = client.prompts[0]
    assert "omit unchanged rows" in prompt
    assert "Use keep for unchanged rows" not in prompt
    assert '"하얀이"' in prompt
    assert json.loads(result.response_content)["decisions"] == []
    assert decode_review_response(result.response_content) == ()


def test_consistency_runner_keeps_only_returned_changes(tmp_path) -> None:
    client = _RecordingClient(
        json.dumps(
            [
                {
                    "row_index": 3,
                    "action": "modify",
                    "suggested_dst": "夏言",
                    "reason": "同一角色",
                }
            ],
            ensure_ascii=False,
        )
    )
    runner = _consistency_runner(tmp_path, client)
    rows = [
        {"row_index": 2, "src": "하얀", "dst": "夏言", "info": "男性角色", "context": ""},
        {"row_index": 3, "src": "하얀이", "dst": "夏彦", "info": "男性角色", "context": ""},
    ]

    result = asyncio.run(runner.run(_consistency_subtask(rows)))

    decisions = decode_review_response(result.response_content)
    assert [(d.row_index, d.action, d.suggested_dst) for d in decisions] == [
        (3, "modify", "夏言")
    ]


class _TruncatingClient:
    """Serves ``pieces`` in order; every piece but the last is cut off by the length cap."""

    def __init__(self, pieces: list[str]) -> None:
        self.pieces = pieces
        self.requests = []

    async def chat(self, request):
        from transoria.llm.client import ChatResponse, LlmTruncatedResponseError
        from transoria.llm.usage import TokenUsage

        self.requests.append(request)
        piece = self.pieces[len(self.requests) - 1]
        usage = TokenUsage(input_tokens=100, output_tokens=10)
        if len(self.requests) < len(self.pieces):
            raise LlmTruncatedResponseError(
                piece, usage, finish_reason="length", model_id="m"
            )
        return ChatResponse(content=piece, usage=usage, raw={})


def _keep_all_reply(rows: list[dict[str, object]]) -> str:
    return json.dumps(
        [
            {
                "row_index": row["row_index"],
                "action": "modify" if row["row_index"] == 3 else "keep",
                "suggested_dst": "夏言" if row["row_index"] == 3 else "",
                "reason": "一致",
            }
            for row in rows
        ],
        ensure_ascii=False,
    )


def test_truncated_review_reply_continues_until_complete(tmp_path) -> None:
    from transoria.workflows.glossary_review.runner import CONTINUE_PROMPT

    rows = [
        {"row_index": index, "src": f"이름{index}", "dst": "名", "info": "男性角色", "context": ""}
        for index in range(2, 12)
    ]
    full = _keep_all_reply(rows)
    cuts = [len(full) // 3 + 7, 2 * len(full) // 3 + 3]
    pieces = [full[: cuts[0]], full[cuts[0] : cuts[1]], full[cuts[1] :]]
    client = _TruncatingClient(pieces)
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_consistency_subtask(rows)))

    decisions = decode_review_response(result.response_content)
    assert [d.row_index for d in decisions] == list(range(2, 12))
    assert [(d.row_index, d.suggested_dst) for d in decisions if d.action == "modify"] == [
        (3, "夏言")
    ]
    assert len(client.requests) == 3
    first, second, third = client.requests
    assert first.history == ()
    assert second.user_prompt == CONTINUE_PROMPT
    assert [m.role for m in second.history] == ["user", "assistant"]
    assert second.history[0].content == first.user_prompt
    assert third.history[1].content == pieces[0] + pieces[1]
    assert result.input_tokens == 300
    assert result.output_tokens == 30


def test_truncated_review_reply_stops_when_continuation_adds_nothing(tmp_path) -> None:
    import pytest

    from transoria.llm.client import LlmTruncatedResponseError

    rows = [{"row_index": 2, "src": "하얀", "dst": "夏言", "info": "男性角色", "context": ""}]
    client = _TruncatingClient(['[{"row_index": 2, "action": "keep"}, {"row', " ", " ", "]"])
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    with pytest.raises(LlmTruncatedResponseError):
        asyncio.run(runner.run(_consistency_subtask(rows)))
    assert len(client.requests) == 2


class _ScriptedClient:
    """Replies in order; an Exception entry is raised, a str is returned as content."""

    def __init__(self, replies: list[object]) -> None:
        self.replies = list(replies)
        self.requests = []

    async def chat(self, request):
        from transoria.llm.client import ChatResponse
        from transoria.llm.usage import TokenUsage

        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return ChatResponse(
            content=str(reply), usage=TokenUsage(input_tokens=10, output_tokens=5), raw={}
        )


def _review_subtask(rows: list[dict[str, object]]) -> Subtask:
    from transoria.workflows.glossary_review.runner import encode_review_payload

    return Subtask(
        id="round-02-batch-0005",
        task_id="t",
        request_payload=encode_review_payload(
            round_index=2, batch_index=5, rows=tuple(rows), novel_background=""
        ),
    )


def _review_rows(*indices: int) -> list[dict[str, object]]:
    return [
        {"row_index": index, "src": f"단어{index}", "dst": f"词{index}", "info": "物品", "frequency": 3}
        for index in indices
    ]


def _decisions_json(*items: tuple[int, str]) -> str:
    return json.dumps(
        [{"row_index": index, "action": action, "reason": "r"} for index, action in items]
    )


def test_review_ignores_stray_row_numbers_and_reasks_missing_rows(tmp_path) -> None:
    client = _ScriptedClient(
        [
            _decisions_json((171, "keep"), (172, "delete")),
            json.dumps([{"row_index": 173, "action": "modify", "suggested_dst": "新词", "reason": "r"}]),
        ]
    )
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_review_subtask(_review_rows(171, 173))))

    decisions = decode_review_response(result.response_content)
    assert [(d.row_index, d.action) for d in decisions] == [(171, "keep"), (173, "modify")]
    assert len(client.requests) == 2
    followup = client.requests[1].user_prompt
    assert '"row_index": 173' in followup
    assert '"row_index": 171' not in followup
    assert result.input_tokens == 20


def test_review_changes_only_reply_does_not_reask(tmp_path) -> None:
    client = _ScriptedClient([_decisions_json((173, "delete"))])
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_review_subtask(_review_rows(171, 173))))

    assert [(d.row_index, d.action) for d in decode_review_response(result.response_content)] == [
        (173, "delete")
    ]
    assert len(client.requests) == 1


def test_review_explicit_empty_list_is_accepted(tmp_path) -> None:
    client = _ScriptedClient(['{"decisions": []}'])
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_review_subtask(_review_rows(171, 173))))

    assert decode_review_response(result.response_content) == ()
    assert len(client.requests) == 1


def test_review_blank_or_prose_reply_is_retried_then_reported(tmp_path) -> None:
    import pytest

    from transoria.workflows.glossary_review.runner import ReviewReplyUnreadableError

    client = _ScriptedClient(["", "I could not review these terms."])
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    with pytest.raises(ReviewReplyUnreadableError):
        asyncio.run(runner.run(_review_subtask(_review_rows(171, 173))))
    assert len(client.requests) == 2


def test_review_unreadable_first_reply_recovers_on_retry(tmp_path) -> None:
    client = _ScriptedClient(["", _decisions_json((171, "keep"), (173, "keep"))])
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_review_subtask(_review_rows(171, 173))))

    assert len(decode_review_response(result.response_content)) == 2


def test_review_failed_missing_row_followup_keeps_main_reply(tmp_path) -> None:
    from transoria.llm.client import LlmRequestError

    client = _ScriptedClient(
        [_decisions_json((171, "keep")), LlmRequestError("HTTP 400 from x: bad", code="llm.http_error")]
    )
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_review_subtask(_review_rows(171, 173))))

    assert [(d.row_index, d.action) for d in decode_review_response(result.response_content)] == [
        (171, "keep")
    ]


def test_consistency_context_is_trimmed_only_for_large_name_sets(tmp_path) -> None:
    from transoria.workflows.glossary_review.runner import (
        CONSISTENCY_CONTEXT_BUDGET_CHARS,
        _build_character_consistency_prompt,
    )

    excerpt = "가" * 150
    context = "\n".join([excerpt + "1", excerpt + "2", excerpt + "3"])

    def rows(count: int):
        return tuple(
            {"row_index": i + 2, "src": f"이름{i}", "dst": "名", "info": "男性角色", "context": context}
            for i in range(count)
        )

    small = _build_character_consistency_prompt(instruction_prompt="", novel_background="", rows=rows(100))
    assert small.count(excerpt + "3") == 100

    large = _build_character_consistency_prompt(instruction_prompt="", novel_background="", rows=rows(400))
    assert large.count(excerpt + "1") == 400
    assert large.count(excerpt + "2") == 0

    huge_count = 1000
    huge = _build_character_consistency_prompt(instruction_prompt="", novel_background="", rows=rows(huge_count))
    per_row = CONSISTENCY_CONTEXT_BUDGET_CHARS // huge_count
    assert huge.count('"context": "' + "가" * per_row + '"') == huge_count


def _truncated(text: str, output_tokens: int):
    from transoria.llm.client import LlmTruncatedResponseError
    from transoria.llm.usage import TokenUsage

    return LlmTruncatedResponseError(
        text, TokenUsage(input_tokens=10, output_tokens=output_tokens), finish_reason="length", model_id="m"
    )


def test_continuation_raises_output_cap_only_above_where_provider_cut(tmp_path) -> None:
    rows = _review_rows(2, 3)
    full = _decisions_json((2, "keep"), (3, "keep"))
    client = _ScriptedClient([_truncated(full[:20], 4096), full[20:]])
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    asyncio.run(runner.run(_consistency_subtask(rows)))

    assert client.requests[0].max_output_tokens is None
    assert client.requests[1].max_output_tokens == runner.model.max_output_tokens == 8192


def test_continuation_keeps_provider_default_when_cut_at_profile_cap(tmp_path) -> None:
    rows = _review_rows(2, 3)
    full = _decisions_json((2, "keep"), (3, "keep"))
    client = _ScriptedClient([_truncated(full[:20], 8192), full[20:]])
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    asyncio.run(runner.run(_consistency_subtask(rows)))

    assert client.requests[1].max_output_tokens is None


def test_continuation_retries_without_cap_when_provider_rejects_it(tmp_path) -> None:
    from transoria.llm.client import LlmRequestError

    rows = _review_rows(2, 3)
    full = _decisions_json((2, "keep"), (3, "modify"))
    client = _ScriptedClient(
        [
            _truncated(full[:20], 4096),
            LlmRequestError("HTTP 400 from x: max_tokens out of range", code="llm.http_error"),
            full[20:],
        ]
    )
    runner = _consistency_runner(tmp_path, client)  # type: ignore[arg-type]

    result = asyncio.run(runner.run(_consistency_subtask(rows)))

    assert [r.max_output_tokens for r in client.requests] == [None, 8192, None]
    assert [d.row_index for d in decode_review_response(result.response_content)] == [2, 3]


def test_replay_ignores_decisions_for_rows_outside_the_batch(tmp_path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["src", "dst", "info", "frequency"])
    sheet.append(["하나", "一", "物品", 5])
    sheet.append(["둘", "二", "物品", 5])
    workbook.save(tmp_path / "terms.xlsx")
    (tmp_path / "terms-references.txt").write_text("하나 둘", encoding="utf-8")

    class StrayRunner:
        async def run(self, subtask: Subtask) -> SubtaskResult:
            rows = subtask.request_payload["rows"]
            decisions = [{"row_index": row["row_index"], "action": "keep"} for row in rows]
            if subtask.request_payload.get("batch") == 0 and subtask.request_payload.get("round") == 1:
                decisions.append({"row_index": 3, "action": "delete", "reason": "stray"})
            return SubtaskResult(response_content=json.dumps({"decisions": decisions}))

    cache = TaskCache(root=tmp_path / "cache")
    orchestrator = GlossaryReviewOrchestrator(
        cache=cache,
        client=None,  # type: ignore[arg-type]
        runner_factory=lambda _client, _config: StrayRunner(),
        id_factory=lambda: "review-stray",
    )
    config = GlossaryReviewConfig(**{**_config(tmp_path).__dict__, "review_rounds": 1, "batch_size": 1})

    result = asyncio.run(orchestrator.run(config))

    rows = [row for row in load_workbook(result.output_path).active.iter_rows(min_row=2, values_only=True)]
    assert [row[0] for row in rows] == ["하나", "둘"]
