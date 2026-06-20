import { useMessages } from "@/locales";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useTaskStore } from "@/store/useTaskStore";
import { useRuntimeStore, type RunKind } from "@/store/useRuntimeStore";
import { Pill } from "./Pill";
import styles from "./QuickSwitchModal.module.css";

const ERROR_CODE = "llm.all_keys_failed";

/**
 * Modal alert that fires when every API key in the active profile's
 * pool has been evicted by the LLM client. The task is already
 * stopped at this point; the dialog tells the user what happened and
 * jumps them to the Model config page to refresh credentials.
 */
export function AllKeysFailedDialog() {
  const messages = useMessages();
  const navigate = useTaskStore((state) => state.navigate);
  const translationError = useRuntimeStore(
    (state) => state.translation.lastError,
  );
  const glossaryError = useRuntimeStore((state) => state.glossary.lastError);
  const clearError = useRuntimeStore((state) => state.clearError);

  const kind: RunKind | null =
    translationError?.code === ERROR_CODE
      ? "translation"
      : glossaryError?.code === ERROR_CODE
        ? "glossary"
        : null;

  useEscapeKey(() => {
    if (kind !== null) clearError(kind);
  }, kind !== null);

  if (kind === null) return null;

  const error = kind === "translation" ? translationError : glossaryError;
  const labels = messages.allKeysFailed;

  const dismiss = () => clearError(kind);
  const goToModelConfig = () => {
    clearError(kind);
    navigate({ module: "model", page: "general" });
  };

  return (
    <div
      className={styles.overlay}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="all-keys-failed-title"
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title} id="all-keys-failed-title">
            {labels.title}
          </h2>
        </div>
        <div className={styles.body}>
          <p>{labels.body}</p>
          {error?.message ? (
            <pre
              style={{
                background: "var(--panel-soft, #fbf8f3)",
                border: "1px solid var(--hairline, #ece8e2)",
                borderRadius: 8,
                padding: "10px 12px",
                margin: "12px 18px 4px",
                fontSize: 12,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {error.message}
            </pre>
          ) : null}
        </div>
        <div className={styles.footer}>
          <Pill variant="ghost" onClick={dismiss}>
            {labels.dismiss}
          </Pill>
          <Pill onClick={goToModelConfig}>{labels.openModelConfig}</Pill>
        </div>
      </div>
    </div>
  );
}
