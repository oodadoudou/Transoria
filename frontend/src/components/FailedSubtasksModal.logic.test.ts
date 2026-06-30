import { describe, expect, it } from "vitest";

import {
  buildDiagnosis,
  buildGroups,
  classifyFailure,
  recommendationFor,
} from "./FailedSubtasksModal.logic";
import type { TaskFailure } from "@/bridge";

function failure(
  subtaskId: string,
  code: string,
  message: string,
  sourceFile = "novel.txt",
): TaskFailure {
  return {
    subtask_id: subtaskId,
    source_file: sourceFile,
    message,
    attempts: 1,
    last_error_code: code,
    last_error_at: "2026-06-30T00:00:00Z",
  };
}

describe("classifyFailure", () => {
  it.each([
    ["llm.line_count_mismatch", "", "lineCount"],
    ["llm.rate_limit", "", "rateLimit"],
    ["llm.http_429", "", "rateLimit"],
    ["llm.timeout", "", "timeout"],
    ["llm.malformed_response", "", "format"],
    ["llm.decode_error", "", "format"],
    ["input.source_language_mismatch", "", "languageMismatch"],
    ["input.no_usable_text", "", "emptyInput"],
    ["llm.transport_error", "", "connection"],
  ] as const)("classifies structured code %s as %s", (code, message, expected) => {
    expect(classifyFailure(code, message)).toBe(expected);
  });

  it.each([
    ["unknown", "HTTP 429 Too Many Requests", "rateLimit"],
    ["unknown", "请求被限流", "rateLimit"],
    ["unknown", "operation timed out", "timeout"],
    ["unknown", "ReadError while streaming", "connection"],
    ["unknown", "输出行数不匹配", "lineCount"],
    ["unknown", "invalid JSON decode", "format"],
    ["unknown", "未检测到配置的源语言", "languageMismatch"],
    ["unknown", "解析后内容为空", "emptyInput"],
  ] as const)("keeps message fallback for %s", (code, message, expected) => {
    expect(classifyFailure(code, message)).toBe(expected);
  });

  it("returns unknown when neither code nor message is recognizable", () => {
    expect(classifyFailure("external.provider_error", "something changed")).toBe(
      "unknown",
    );
  });
});

describe("failure grouping and diagnosis", () => {
  it("groups matching code/message pairs and preserves source files", () => {
    const groups = buildGroups([
      failure("chunk-1", "llm.line_count_mismatch", "bad", "a.txt"),
      failure("chunk-2", "llm.line_count_mismatch", "bad", "b.txt"),
      failure("chunk-3", "llm.timeout", "slow", "a.txt"),
    ]);

    expect(groups).toHaveLength(2);
    expect(groups[0]).toMatchObject({
      code: "llm.line_count_mismatch",
      message: "bad",
      type: "lineCount",
      sourceFiles: ["a.txt", "b.txt"],
    });
    expect(groups[0].failures).toHaveLength(2);
  });

  it("uses high-concurrency recommendations for rate limit and timeout", () => {
    expect(
      recommendationFor("rateLimit", {
        concurrencyLimit: 20,
        rpmLimit: 60,
        timeoutSeconds: 120,
        retryAttempts: 2,
      }),
    ).toBe("rateLimitHighConcurrency");
    expect(
      recommendationFor("timeout", {
        concurrencyLimit: 4,
        rpmLimit: 120,
        timeoutSeconds: 120,
        retryAttempts: 2,
      }),
    ).toBe("timeoutHighConcurrency");
  });

  it("builds a dominant failure diagnosis by affected subtask count", () => {
    const groups = buildGroups([
      failure("chunk-1", "llm.timeout", "slow"),
      failure("chunk-2", "llm.timeout", "slow"),
      failure("chunk-3", "llm.line_count_mismatch", "bad"),
    ]);

    expect(buildDiagnosis(groups)?.dominantType).toBe("timeout");
    expect(buildDiagnosis(groups)?.dominantCount).toBe(2);
  });
});
