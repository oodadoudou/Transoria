import { useMessages } from "@/locales";
import { useRuntimeStore, type RunKind } from "@/store/useRuntimeStore";
import styles from "./RunErrorBanner.module.css";

interface RunErrorBannerProps {
  kind: RunKind;
}

export function RunErrorBanner({ kind }: RunErrorBannerProps) {
  const messages = useMessages();
  const error = useRuntimeStore((state) => state[kind].lastError);
  const earlierCount = useRuntimeStore((state) =>
    Math.max(0, state[kind].errorHistory.length - 1),
  );
  const clearError = useRuntimeStore((state) => state.clearError);
  // Task-level failure reason recorded by ``_on_task_failure`` into
  // ``record.metadata.last_error``. Surfaces "empty input", "no
  // chunks built", etc. — the kinds of failures that don't raise a
  // bridge-level error but still produce a FAILED task status.
  const snapshot = useRuntimeStore((state) => state[kind].snapshot);
  const taskFailureReason =
    snapshot?.header.status === "failed"
      ? extractLastError(snapshot.metadata)
      : null;

  if (!error && !taskFailureReason) return null;

  return (
    <div className={styles.banner} role="alert">
      <div className={styles.body}>
        <span className={styles.title}>{messages.errors.runFailureTitle}</span>
        {error ? (
          <>
            <span className={styles.message}>{error.message}</span>
            <span className={styles.code}>{error.code}</span>
            {earlierCount > 0 ? (
              <span
                className={styles.code}
                title={`${earlierCount} earlier error(s) hidden`}
              >
                +{earlierCount} earlier
              </span>
            ) : null}
          </>
        ) : (
          <span className={styles.message}>{taskFailureReason}</span>
        )}
      </div>
      {error ? (
        <button
          type="button"
          className={styles.dismiss}
          onClick={() => clearError(kind)}
        >
          {messages.errors.dismiss}
        </button>
      ) : null}
    </div>
  );
}

function extractLastError(
  metadata: Record<string, unknown> | null | undefined,
): string | null {
  if (!metadata) return null;
  const raw = metadata["last_error"];
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : null;
}
