import type { TaskFailure } from "@/bridge";

export interface FailureRuntimeConfig {
  concurrencyLimit: number;
  rpmLimit: number;
  timeoutSeconds: number;
  retryAttempts: number;
}

export interface FailureGroup {
  code: string;
  message: string;
  type: FailureType;
  sourceFiles: string[];
  failures: TaskFailure[];
}

export type FailureType =
  | "timeout"
  | "rateLimit"
  | "connection"
  | "format"
  | "lineCount"
  | "languageMismatch"
  | "emptyInput"
  | "unknown";

export type FailureRecommendation =
  | "rateLimitHighConcurrency"
  | "rateLimit"
  | "timeoutHighConcurrency"
  | "timeout"
  | "connection"
  | "format"
  | "lineCount"
  | "languageMismatch"
  | "emptyInput"
  | "unknown";

export interface FailureDiagnosis {
  dominantType: FailureType;
  dominantCount: number;
  recommendation: FailureRecommendation;
  stats: Array<{ type: FailureType; count: number }>;
}

export function buildDiagnosis(
  groups: FailureGroup[],
  runtimeConfig?: FailureRuntimeConfig,
): FailureDiagnosis | null {
  if (groups.length === 0) return null;

  const counts = new Map<FailureType, number>();
  for (const group of groups) {
    counts.set(group.type, (counts.get(group.type) ?? 0) + group.failures.length);
  }

  const stats = Array.from(counts.entries())
    .map(([type, count]) => ({ type, count }))
    .sort((a, b) => b.count - a.count);
  const dominant = stats[0];
  return {
    dominantType: dominant.type,
    dominantCount: dominant.count,
    recommendation: recommendationFor(dominant.type, runtimeConfig),
    stats,
  };
}

export function recommendationFor(
  type: FailureType,
  runtimeConfig?: FailureRuntimeConfig,
): FailureRecommendation {
  const highConcurrencySignal =
    (runtimeConfig?.concurrencyLimit ?? 0) >= 20 ||
    (runtimeConfig?.rpmLimit ?? 0) >= 120;

  if (type === "rateLimit") {
    return highConcurrencySignal ? "rateLimitHighConcurrency" : "rateLimit";
  }
  if (type === "timeout") {
    return highConcurrencySignal ? "timeoutHighConcurrency" : "timeout";
  }
  return type;
}

export function buildGroups(failures: TaskFailure[]): FailureGroup[] {
  const buckets = new Map<string, FailureGroup>();
  for (const failure of failures) {
    const code = failure.last_error_code || "unknown";
    const message = failure.message || "";
    const key = `${code}::${message}`;
    const existing = buckets.get(key);
    if (existing) {
      existing.failures.push(failure);
      if (
        failure.source_file &&
        !existing.sourceFiles.includes(failure.source_file)
      ) {
        existing.sourceFiles.push(failure.source_file);
      }
    } else {
      buckets.set(key, {
        code,
        message,
        type: classifyFailure(code, message),
        sourceFiles: failure.source_file ? [failure.source_file] : [],
        failures: [failure],
      });
    }
  }
  return Array.from(buckets.values()).sort(
    (a, b) => b.failures.length - a.failures.length,
  );
}

export function classifyFailure(code: string, message = ""): FailureType {
  const normalizedCode = code.toLowerCase();
  if (normalizedCode.includes("line_count")) return "lineCount";
  if (
    normalizedCode.includes("rate_limit") ||
    normalizedCode.includes("ratelimit") ||
    normalizedCode.includes("429")
  ) {
    return "rateLimit";
  }
  if (normalizedCode.includes("timeout")) return "timeout";
  if (
    normalizedCode.includes("malformed") ||
    normalizedCode.includes("decode") ||
    normalizedCode.includes("format") ||
    normalizedCode.includes("json")
  ) {
    return "format";
  }
  if (normalizedCode.includes("language")) return "languageMismatch";
  if (
    normalizedCode.includes("empty") ||
    normalizedCode.includes("no_chunks") ||
    normalizedCode.includes("no_usable")
  ) {
    return "emptyInput";
  }
  if (
    normalizedCode.includes("connect") ||
    normalizedCode.includes("network") ||
    normalizedCode.includes("transport")
  ) {
    return "connection";
  }

  const haystack = `${code} ${message}`.toLowerCase();
  if (
    haystack.includes("429") ||
    haystack.includes("rate") ||
    haystack.includes("限流") ||
    haystack.includes("too many requests")
  ) {
    return "rateLimit";
  }
  if (haystack.includes("timeout") || haystack.includes("timed out")) {
    return "timeout";
  }
  if (
    haystack.includes("connect") ||
    haystack.includes("network") ||
    haystack.includes("readerror") ||
    haystack.includes("transport")
  ) {
    return "connection";
  }
  if (
    haystack.includes("line_count") ||
    haystack.includes("line count") ||
    haystack.includes("行数")
  ) {
    return "lineCount";
  }
  if (
    haystack.includes("json") ||
    haystack.includes("format") ||
    haystack.includes("decode") ||
    haystack.includes("格式")
  ) {
    return "format";
  }
  if (
    haystack.includes("source language") ||
    haystack.includes("configured source") ||
    haystack.includes("源语言") ||
    haystack.includes("未检测到")
  ) {
    return "languageMismatch";
  }
  if (
    haystack.includes("empty") ||
    haystack.includes("no chunks") ||
    haystack.includes("no usable") ||
    haystack.includes("空")
  ) {
    return "emptyInput";
  }
  return "unknown";
}
