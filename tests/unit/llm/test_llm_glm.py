from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import Mapping

import pytest

from transoria.llm import ChatRequest, LlmClient, LlmRequestError, ModelConfig, ProviderFormat
from transoria.llm.client import LlmFilteredResponseError, LlmTruncatedResponseError, TransportResult
from transoria.llm.config import ThinkingLevel
from transoria.llm.glm import (
    glm_anthropic_fields,
    glm_requires_thinking,
    glm_thinking_fields,
    glm_version,
)

ZHIPU = "https://open.bigmodel.cn/api/paas/v4"
OPENROUTER = "https://openrouter.ai/api/v1"
DASHSCOPE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
TOGETHER = "https://api.together.xyz/v1"
FRIENDLI = "https://api.friendli.ai/serverless/v1"
SELF_HOSTED = "http://127.0.0.1:8000/v1"


@dataclass
class _Transport:
    responses: list[TransportResult]
    calls: list[dict[str, object]] = field(default_factory=list)

    async def execute(
        self, url: str, headers: Mapping[str, str], payload: Mapping[str, object], timeout: float
    ) -> TransportResult:
        self.calls.append(deepcopy(dict(payload)))
        return self.responses.pop(0)


def _model(model_id: str, level: ThinkingLevel, base_url: str = ZHIPU) -> ModelConfig:
    return ModelConfig(
        id="glm",
        display_name="glm",
        provider_format=ProviderFormat.OPENAI,
        base_url=base_url,
        model_id=model_id,
        api_keys=("k",),
        thinking_level=level,
        rotate_keys=False,
    )


def _ok(content: object = "ok", finish_reason: str = "stop") -> TransportResult:
    return TransportResult(
        200,
        {
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )


def _send(model: ModelConfig, *responses: TransportResult):
    transport = _Transport(list(responses))
    response = asyncio.run(
        LlmClient(transport=transport).chat(ChatRequest(model=model, system_prompt="", user_prompt="x"))
    )
    return response, transport


@pytest.mark.parametrize(
    ("model_id", "version"),
    [
        ("glm-5.3", (5, 3)),
        ("GLM-5.3-FLASH", (5, 3)),
        ("glm-5.3-flashx", (5, 3)),
        ("z-ai/glm-5.3", (5, 3)),
        ("zai-org/GLM-5.2", (5, 2)),
        ("glm-5-turbo", (5, 0)),
        ("glm-5v-turbo", (5, 0)),
        ("glm-4.7-flash", (4, 7)),
        ("glm-4-7-251222", (4, 7)),
        ("glm-4-flash-250414", (4, 0)),
        ("~z-ai/glm-latest", (99, 0)),
        ("~z-ai/glm-flash-latest", (99, 0)),
        ("chatglm3-6b", None),
        ("gpt-4o-mini", None),
    ],
)
def test_glm_version_reads_common_model_ids(model_id, version) -> None:
    assert glm_version(model_id) == version


@pytest.mark.parametrize("model_id", ["glm-5.3", "glm-5.3-flash", "~z-ai/glm-latest", "~z-ai/glm-flash-latest"])
def test_current_glm_generation_requires_thinking(model_id) -> None:
    assert glm_requires_thinking(model_id)
    assert not glm_requires_thinking("glm-5.2")


@pytest.mark.parametrize(
    ("level", "effort"),
    [
        (ThinkingLevel.LOW, "low"),
        (ThinkingLevel.MEDIUM, "high"),
        (ThinkingLevel.HIGH, "high"),
    ],
)
def test_forced_thinking_models_only_receive_supported_efforts(level, effort) -> None:
    assert glm_thinking_fields(_model("glm-5.3-flash", level)) == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": effort,
    }


def test_older_glm_models_do_not_receive_reasoning_effort() -> None:
    assert glm_thinking_fields(_model("glm-4.7-flash", ThinkingLevel.LOW)) == {
        "thinking": {"type": "enabled"}
    }
    assert glm_thinking_fields(_model("glm-5.2", ThinkingLevel.MEDIUM)) == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "medium",
    }


@pytest.mark.parametrize(
    ("model_id", "level", "expected"),
    [
        ("~z-ai/glm-latest", ThinkingLevel.LOW, {"effort": "low"}),
        ("z-ai/glm-5.3-flash", ThinkingLevel.MEDIUM, {"effort": "high"}),
        ("z-ai/glm-5.2", ThinkingLevel.LOW, {"effort": "high"}),
        ("z-ai/glm-5.1", ThinkingLevel.LOW, {"enabled": True}),
        ("z-ai/glm-5.2", ThinkingLevel.OFF, {"enabled": False}),
    ],
)
def test_openrouter_uses_its_reasoning_object(model_id, level, expected) -> None:
    assert glm_thinking_fields(_model(model_id, level, OPENROUTER)) == {"reasoning": expected}


def test_dashscope_uses_enable_thinking() -> None:
    assert glm_thinking_fields(_model("glm-5.3", ThinkingLevel.MEDIUM, DASHSCOPE)) == {
        "enable_thinking": True,
        "reasoning_effort": "high",
    }
    assert glm_thinking_fields(_model("glm-5.2", ThinkingLevel.OFF, DASHSCOPE)) == {
        "enable_thinking": False
    }
    assert glm_thinking_fields(_model("glm-4.7", ThinkingLevel.LOW, DASHSCOPE)) == {
        "enable_thinking": True
    }


def test_non_glm_models_keep_the_generic_fields() -> None:
    assert glm_thinking_fields(_model("deepseek-chat", ThinkingLevel.LOW)) is None
    _response, transport = _send(_model("deepseek-chat", ThinkingLevel.MEDIUM), _ok())
    assert transport.calls[0]["thinking"] == {"type": "enabled"}
    assert transport.calls[0]["reasoning_effort"] == "medium"


def test_low_on_forced_thinking_model_sends_enabled_thinking() -> None:
    response, transport = _send(_model("glm-5.3-flash", ThinkingLevel.LOW), _ok())
    assert response.content == "ok"
    assert transport.calls[0]["thinking"] == {"type": "enabled"}
    assert transport.calls[0]["reasoning_effort"] == "low"


def test_off_on_forced_thinking_model_reports_a_clear_error_without_retrying() -> None:
    rejected = TransportResult(
        400, {"error": {"code": "1210", "message": "API 调用参数有误，请检查文档"}}
    )
    transport = _Transport([rejected])
    with pytest.raises(LlmRequestError) as caught:
        asyncio.run(
            LlmClient(transport=transport).chat(
                ChatRequest(model=_model("glm-5.3", ThinkingLevel.OFF), system_prompt="", user_prompt="x")
            )
        )
    assert caught.value.code == "llm.thinking_off_unsupported"
    assert len(transport.calls) == 1
    assert transport.calls[0]["thinking"] == {"type": "disabled"}


def test_off_on_forced_thinking_model_still_works_when_a_relay_accepts_it() -> None:
    response, _transport = _send(_model("glm-5.3", ThinkingLevel.OFF), _ok())
    assert response.content == "ok"


def test_prompt_too_long_is_not_reported_as_a_thinking_problem() -> None:
    transport = _Transport([TransportResult(400, {"error": {"code": "1261", "message": "Prompt 超长"}})])
    with pytest.raises(LlmRequestError) as caught:
        asyncio.run(
            LlmClient(transport=transport).chat(
                ChatRequest(model=_model("glm-5.3", ThinkingLevel.OFF), system_prompt="", user_prompt="x")
            )
        )
    assert caught.value.code == "llm.http_error"


@pytest.mark.parametrize(
    "message", ["Parameter `reasoning_effort` is invalid. Please check", "`reasoning_effort` 参数非法。请检查文档"]
)
def test_rejected_field_named_by_zhipu_is_dropped_and_retried(message) -> None:
    response, transport = _send(
        _model("glm-5.2", ThinkingLevel.LOW),
        TransportResult(400, {"error": {"code": "1214", "message": message}}),
        _ok(),
    )
    assert response.content == "ok"
    assert "reasoning_effort" not in transport.calls[1]
    assert transport.calls[1]["thinking"] == {"type": "enabled"}


def test_safety_block_returned_as_http_400_uses_the_content_filter_code() -> None:
    transport = _Transport([TransportResult(400, {"error": {"code": "1301", "message": "blocked"}})])
    with pytest.raises(LlmRequestError) as caught:
        asyncio.run(
            LlmClient(transport=transport).chat(
                ChatRequest(model=_model("glm-5.3", ThinkingLevel.LOW), system_prompt="", user_prompt="x")
            )
        )
    assert caught.value.code == "llm.content_filter"


@pytest.mark.parametrize(
    ("finish_reason", "error_type", "code"),
    [
        ("sensitive", LlmFilteredResponseError, "llm.content_filter"),
        ("model_context_window_exceeded", LlmTruncatedResponseError, "llm.length_truncated"),
        ("network_error", LlmRequestError, "llm.transport_error"),
    ],
)
def test_glm_finish_reasons_are_not_treated_as_success(finish_reason, error_type, code) -> None:
    transport = _Transport([_ok("partial", finish_reason)])
    with pytest.raises(error_type) as caught:
        asyncio.run(
            LlmClient(transport=transport).chat(
                ChatRequest(model=_model("glm-5.3", ThinkingLevel.LOW), system_prompt="", user_prompt="x")
            )
        )
    assert caught.value.code == code


def test_null_content_is_empty_text() -> None:
    response, _transport = _send(_model("glm-5.3", ThinkingLevel.LOW), _ok(None))
    assert response.content == ""


@pytest.mark.parametrize(
    ("model_id", "level", "expected"),
    [
        ("zai-org/GLM-5.3-Flash", ThinkingLevel.MEDIUM, {"reasoning_effort": "high"}),
        ("zai-org/GLM-5.3", ThinkingLevel.OFF, {"reasoning": {"enabled": False}}),
        ("zai-org/GLM-5.2", ThinkingLevel.LOW, {"reasoning": {"enabled": True}}),
        ("zai-org/GLM-5.2", ThinkingLevel.OFF, {"reasoning": {"enabled": False}}),
    ],
)
def test_together_fields(model_id, level, expected) -> None:
    assert glm_thinking_fields(_model(model_id, level, TOGETHER)) == expected


@pytest.mark.parametrize(
    ("model_id", "level", "expected"),
    [
        ("zai-org/GLM-5.3", ThinkingLevel.LOW, {"reasoning_effort": "low"}),
        ("zai-org/GLM-5.3", ThinkingLevel.OFF, {}),
        (
            "zai-org/GLM-5.2",
            ThinkingLevel.LOW,
            {"chat_template_kwargs": {"enable_thinking": True}, "reasoning_effort": "high"},
        ),
        ("zai-org/GLM-5.2", ThinkingLevel.OFF, {"chat_template_kwargs": {"enable_thinking": False}}),
        ("zai-org/GLM-5.1", ThinkingLevel.HIGH, {"chat_template_kwargs": {"enable_thinking": True}}),
    ],
)
def test_friendli_fields(model_id, level, expected) -> None:
    assert glm_thinking_fields(_model(model_id, level, FRIENDLI)) == expected


def test_self_hosted_off_also_uses_the_chat_template_switch() -> None:
    assert glm_thinking_fields(_model("zai-org/GLM-5.1", ThinkingLevel.OFF, SELF_HOSTED)) == {
        "thinking": {"type": "disabled"},
        "chat_template_kwargs": {"enable_thinking": False},
    }
    assert "chat_template_kwargs" not in glm_thinking_fields(
        _model("zai-org/GLM-5.1", ThinkingLevel.LOW, SELF_HOSTED)
    )
    assert "chat_template_kwargs" not in glm_thinking_fields(_model("glm-5.2", ThinkingLevel.OFF))
    assert "chat_template_kwargs" not in glm_thinking_fields(
        _model("zai-org/GLM-5.3", ThinkingLevel.OFF, SELF_HOSTED)
    )


def test_relay_rejecting_the_chat_template_switch_keeps_off_working() -> None:
    rejected = TransportResult(
        400, {"error": {"message": "Unknown parameter: chat_template_kwargs"}}
    )
    response, transport = _send(
        _model("glm-5.1", ThinkingLevel.OFF, "https://relay.example/v1"), rejected, _ok()
    )
    assert response.content == "ok"
    assert "chat_template_kwargs" not in transport.calls[1]
    assert transport.calls[1]["thinking"] == {"type": "disabled"}


def _anthropic_ok() -> TransportResult:
    return TransportResult(
        200,
        {
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


def test_anthropic_format_sends_a_valid_budget_and_the_mapped_effort() -> None:
    model = replace(
        _model("glm-5.3-flash", ThinkingLevel.MEDIUM, "https://api.z.ai/api/anthropic"),
        provider_format=ProviderFormat.ANTHROPIC,
    )
    _response, transport = _send(model, _anthropic_ok())
    assert transport.calls[0]["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert transport.calls[0]["reasoning_effort"] == "high"


def test_anthropic_fields_respect_output_limit_and_host_rules() -> None:
    model = _model("glm-5.2", ThinkingLevel.LOW, "https://api.z.ai/api/anthropic")
    assert glm_anthropic_fields(model, 1024)["thinking"] == {"type": "enabled"}
    assert glm_anthropic_fields(_model("glm-4.7", ThinkingLevel.LOW), 8192) == {
        "thinking": {"type": "enabled", "budget_tokens": 1024}
    }
    assert "reasoning_effort" not in glm_anthropic_fields(
        _model("glm-5.3", ThinkingLevel.LOW, "https://dashscope.aliyuncs.com/apps/anthropic"), 8192
    )
    assert glm_anthropic_fields(_model("glm-5.2", ThinkingLevel.OFF), 8192) == {
        "thinking": {"type": "disabled"}
    }
    assert glm_anthropic_fields(_model("claude-sonnet-4-6", ThinkingLevel.LOW), 8192) is None


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (ThinkingLevel.OFF, {"enabled": False}),
        (ThinkingLevel.LOW, {"effort": "low"}),
        (ThinkingLevel.HIGH, {"effort": "high"}),
    ],
)
def test_openrouter_non_glm_models_use_reasoning_object(level, expected) -> None:
    _response, transport = _send(_model("deepseek/deepseek-v3.2", level, OPENROUTER), _ok())
    payload = transport.calls[0]
    assert payload["reasoning"] == expected
    assert "thinking" not in payload and "reasoning_effort" not in payload
