import { useMessages } from "@/locales";
import { Pill } from "./Pill";
import styles from "./CompletionWithFailuresDialog.module.css";

interface CompletionWithFailuresDialogProps {
  failedCount: number;
  rerunPending: boolean;
  onRerun: () => void;
  onAccept: () => void;
}

export function CompletionWithFailuresDialog({
  failedCount,
  rerunPending,
  onRerun,
  onAccept,
}: CompletionWithFailuresDialogProps) {
  const messages = useMessages().completionWithFailures;
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
          <Pill variant="ghost" onClick={onAccept} disabled={rerunPending}>
            {messages.acceptAction}
          </Pill>
          <Pill onClick={onRerun} disabled={rerunPending}>
            {rerunPending ? messages.rerunPending : messages.rerunAction}
          </Pill>
        </div>
      </div>
    </div>
  );
}
