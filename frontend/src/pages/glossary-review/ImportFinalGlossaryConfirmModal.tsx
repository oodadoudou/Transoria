import { useEffect } from "react";
import { format } from "@/locales";
import styles from "@/components/GlossaryExportModal.module.css";
import type { ImportFinalGlossaryMode } from "./importFinalGlossary";

interface ImportFinalGlossaryConfirmLabels {
  title: string;
  body: string;
  replaceBadge: string;
  replaceAction: string;
  replaceHint: string;
  appendBadge: string;
  appendAction: string;
  appendHint: string;
  cancelAction: string;
}

interface ImportFinalGlossaryConfirmModalProps {
  existingCount: number;
  labels: ImportFinalGlossaryConfirmLabels;
  onPick: (mode: ImportFinalGlossaryMode) => void;
  onCancel: () => void;
}

export function ImportFinalGlossaryConfirmModal({
  existingCount,
  labels,
  onPick,
  onCancel,
}: ImportFinalGlossaryConfirmModalProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div
      className={styles.overlay}
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="glossary-review-import-title"
      onClick={onCancel}
    >
      <div className={styles.modal} onClick={(event) => event.stopPropagation()}>
        <div className={styles.header}>
          <h2 id="glossary-review-import-title" className={styles.title}>
            {labels.title}
          </h2>
          <button
            type="button"
            className={styles.close}
            onClick={onCancel}
            aria-label={labels.cancelAction}
          >
            x
          </button>
        </div>
        <div className={styles.body}>
          <p className={styles.hint}>
            {format(labels.body, { n: existingCount })}
          </p>
          <div className={styles.choices}>
            <button
              type="button"
              className={styles.choice}
              onClick={() => onPick("replace")}
            >
              <span className={styles.choiceBadge}>{labels.replaceBadge}</span>
              <span className={styles.choiceText}>
                <span className={styles.choiceLabel}>
                  {labels.replaceAction}
                </span>
                <span className={styles.choiceHint}>{labels.replaceHint}</span>
              </span>
            </button>
            <button
              type="button"
              className={styles.choice}
              onClick={() => onPick("append")}
            >
              <span className={styles.choiceBadge}>{labels.appendBadge}</span>
              <span className={styles.choiceText}>
                <span className={styles.choiceLabel}>
                  {labels.appendAction}
                </span>
                <span className={styles.choiceHint}>{labels.appendHint}</span>
              </span>
            </button>
          </div>
        </div>
        <div className={styles.footer}>
          <button type="button" className={styles.cancel} onClick={onCancel}>
            {labels.cancelAction}
          </button>
        </div>
      </div>
    </div>
  );
}
