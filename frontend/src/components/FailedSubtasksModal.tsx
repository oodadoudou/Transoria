import { useMemo, useState } from "react";
import type { TaskFailure } from "@/bridge";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useMessages } from "@/locales";
import { Pill } from "./Pill";
import styles from "./FailedSubtasksModal.module.css";

interface FailedSubtasksModalProps {
  failures: TaskFailure[];
  runtimeConfig?: FailureRuntimeConfig;
  onClose: () => void;
}

interface FailureRuntimeConfig {
  concurrencyLimit: number;
  rpmLimit: number;
  timeoutSeconds: number;
  retryAttempts: number;
}

interface FailureGroup {
  code: string;
  message: string;
  type: FailureType;
  sourceFiles: string[];
  failures: TaskFailure[];
}

type FailureType =
  | "timeout"
  | "rateLimit"
  | "connection"
  | "format"
  | "lineCount"
  | "languageMismatch"
  | "emptyInput"
  | "unknown";

type FailureRecommendation =
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

interface FailureDiagnosis {
  dominantType: FailureType;
  dominantCount: number;
  recommendation: FailureRecommendation;
  stats: Array<{ type: FailureType; count: number }>;
}

export function FailedSubtasksModal({
  failures,
  runtimeConfig,
  onClose,
}: FailedSubtasksModalProps) {
  const messages = useMessages().failedSubtasksModal;
  const groups = useMemo(() => buildGroups(failures), [failures]);
  const diagnosis = useMemo(
    () => buildDiagnosis(groups, runtimeConfig),
    [groups, runtimeConfig],
  );
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  useEscapeKey(onClose);

  const toggle = (key: string): void => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      aria-labelledby="failed-subtasks-title"
      onClick={onClose}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title} id="failed-subtasks-title">
            {messages.title}
          </h2>
          <span className={styles.countBadge}>{failures.length}</span>
        </div>
        <div className={styles.body}>
          {groups.length === 0 ? (
            <p className={styles.empty}>{messages.empty}</p>
          ) : (
            <>
              {diagnosis ? (
                <div className={styles.diagnosisCard}>
                  <div>
                    <div className={styles.diagnosisTitle}>
                      {messages.diagnosis.title}
                    </div>
                    <div className={styles.diagnosisSubtitle}>
                      {messages.diagnosis.subtitle}
                    </div>
                  </div>
                  {runtimeConfig ? (
                    <div className={styles.configLine}>
                      <span>{messages.diagnosis.configLabel}</span>
                      <strong>
                        {formatMessage(messages.diagnosis.configValue, {
                          concurrency: runtimeConfig.concurrencyLimit,
                          rpm: runtimeConfig.rpmLimit,
                          timeout: runtimeConfig.timeoutSeconds,
                          retries: runtimeConfig.retryAttempts,
                        })}
                      </strong>
                    </div>
                  ) : null}
                  <div className={styles.dominantLine}>
                    {formatMessage(messages.diagnosis.dominant, {
                      type: messages.failureTypes[diagnosis.dominantType],
                      count: diagnosis.dominantCount,
                      total: failures.length,
                    })}
                  </div>
                  <div className={styles.recommendation}>
                    {messages.diagnosis.recommendations[diagnosis.recommendation]}
                  </div>
                  <div className={styles.statChips}>
                    {diagnosis.stats.map((stat) => (
                      <span key={stat.type} className={styles.statChip}>
                        {messages.failureTypes[stat.type]} · {stat.count}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
              <ul className={styles.groupList}>
                {groups.map((group) => {
                  const key = `${group.code}::${group.message}`;
                  const isOpen = expanded.has(key);
                  return (
                    <li key={key} className={styles.group}>
                      <button
                        type="button"
                        className={styles.groupHead}
                        onClick={() => toggle(key)}
                        aria-expanded={isOpen}
                      >
                        <code className={styles.groupCode}>{group.code}</code>
                        <span className={styles.groupType}>
                          {messages.failureTypes[group.type]}
                        </span>
                        <span className={styles.groupMessage}>
                          {group.message || messages.noMessage}
                        </span>
                        <span className={styles.groupCount}>
                          ×{group.failures.length}
                        </span>
                        <span className={styles.chevron} aria-hidden="true">
                          {isOpen ? "▾" : "▸"}
                        </span>
                      </button>
                      {isOpen ? (
                        <div className={styles.groupBody}>
                          {group.sourceFiles.length > 0 ? (
                            <div className={styles.fileList}>
                              <span className={styles.fileLabel}>
                                {messages.fileLabel}
                              </span>
                              {group.sourceFiles.map((path) => (
                                <code key={path} className={styles.filePath}>
                                  {path}
                                </code>
                              ))}
                            </div>
                          ) : null}
                          <div className={styles.affectedRow}>
                            <span className={styles.affectedLabel}>
                              {messages.affectedLabel}
                            </span>
                            <span className={styles.chunkIds}>
                              {group.failures
                                .map((failure) => failure.subtask_id)
                                .join(", ")}
                            </span>
                          </div>
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
        <div className={styles.footer}>
          <Pill variant="ghost" onClick={onClose}>
            {messages.close}
          </Pill>
        </div>
      </div>
    </div>
  );
}

function buildDiagnosis(
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

function recommendationFor(
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

function buildGroups(failures: TaskFailure[]): FailureGroup[] {
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

function classifyFailure(code: string, message: string): FailureType {
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

function formatMessage(
  template: string,
  values: Record<string, string | number>,
): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match,
  );
}
