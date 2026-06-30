import { useMemo, useState } from "react";
import type { TaskFailure } from "@/bridge";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useMessages } from "@/locales";
import {
  buildDiagnosis,
  buildGroups,
  type FailureGroup,
  type FailureRuntimeConfig,
} from "./FailedSubtasksModal.logic";
import { Pill } from "./Pill";
import styles from "./FailedSubtasksModal.module.css";

interface FailedSubtasksModalProps {
  failures: TaskFailure[];
  runtimeConfig?: FailureRuntimeConfig;
  onClose: () => void;
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
  const [copyFeedback, setCopyFeedback] = useState("");
  useEscapeKey(onClose);

  const toggle = (key: string): void => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleCopySummary = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(
        buildFailureSummary(groups, failures.length, messages),
      );
      setCopyFeedback(messages.copySummaryDone);
    } catch {
      setCopyFeedback(messages.copySummaryFailed);
    }
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
          <div className={styles.headerActions}>
            {copyFeedback ? (
              <span className={styles.copyFeedback}>{copyFeedback}</span>
            ) : null}
            <button
              type="button"
              className={styles.copyButton}
              onClick={() => void handleCopySummary()}
              disabled={failures.length === 0}
            >
              {messages.copySummary}
            </button>
            <span className={styles.countBadge}>{failures.length}</span>
          </div>
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
                        <span
                          className={styles.groupMessage}
                          title={group.message || messages.noMessage}
                        >
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
                                <code
                                  key={path}
                                  className={styles.filePath}
                                  title={path}
                                >
                                  {path}
                                </code>
                              ))}
                            </div>
                          ) : null}
                          <div className={styles.affectedRow}>
                            <span className={styles.affectedLabel}>
                              {messages.affectedLabel}
                            </span>
                            <span
                              className={styles.chunkIds}
                              title={group.failures
                                .map((failure) => failure.subtask_id)
                                .join(", ")}
                            >
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

function buildFailureSummary(
  groups: FailureGroup[],
  total: number,
  messages: ReturnType<typeof useMessages>["failedSubtasksModal"],
): string {
  const lines = [`${messages.title}: ${total}`];
  for (const group of groups) {
    lines.push("");
    lines.push(
      `[${group.code}] ${messages.failureTypes[group.type]} x${group.failures.length}`,
    );
    lines.push(group.message || messages.noMessage);
    if (group.sourceFiles.length > 0) {
      lines.push(`${messages.fileLabel}: ${group.sourceFiles.join(", ")}`);
    }
    lines.push(
      `${messages.affectedLabel}: ${group.failures
        .map((failure) => failure.subtask_id)
        .join(", ")}`,
    );
  }
  return lines.join("\n");
}

function formatMessage(
  template: string,
  values: Record<string, string | number>,
): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match,
  );
}
