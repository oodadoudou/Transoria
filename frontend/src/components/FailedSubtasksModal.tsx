import { useMemo, useState } from "react";
import type { TaskFailure } from "@/bridge";
import { useMessages } from "@/locales";
import { Pill } from "./Pill";
import styles from "./FailedSubtasksModal.module.css";

interface FailedSubtasksModalProps {
  failures: TaskFailure[];
  onClose: () => void;
}

interface FailureGroup {
  code: string;
  message: string;
  sourceFiles: string[];
  failures: TaskFailure[];
}

export function FailedSubtasksModal({
  failures,
  onClose,
}: FailedSubtasksModalProps) {
  const messages = useMessages().failedSubtasksModal;
  const groups = useMemo(() => buildGroups(failures), [failures]);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

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
        sourceFiles: failure.source_file ? [failure.source_file] : [],
        failures: [failure],
      });
    }
  }
  return Array.from(buckets.values()).sort(
    (a, b) => b.failures.length - a.failures.length,
  );
}
