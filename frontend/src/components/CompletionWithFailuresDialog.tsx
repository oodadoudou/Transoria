import { useMessages } from "@/locales";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { Pill } from "./Pill";
import styles from "./CompletionWithFailuresDialog.module.css";

interface CompletionWithFailuresDialogProps {
  failedCount: number;
  onAccept: () => void;
}

export function CompletionWithFailuresDialog({
  failedCount,
  onAccept,
}: CompletionWithFailuresDialogProps) {
  const messages = useMessages().completionWithFailures;
  useEscapeKey(onAccept);

  return (
    <div
      className={styles.overlay}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="completion-with-failures-title"
      onClick={onAccept}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <h2 className={styles.title} id="completion-with-failures-title">
          {messages.title}
        </h2>
        <p className={styles.body}>
          {messages.bodyPrefix}
          <strong>{failedCount}</strong>
          {messages.bodySuffix}
        </p>
        <div className={styles.footer}>
          <Pill variant="ghost" onClick={onAccept}>
            {messages.acceptAction}
          </Pill>
        </div>
      </div>
    </div>
  );
}
