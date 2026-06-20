import { useMessages } from "@/locales";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import styles from "./GlossaryExportModal.module.css";

export type RuleExportFormat = "json" | "xlsx";

interface RuleExportModalProps {
  onPick: (format: RuleExportFormat) => void;
  onClose: () => void;
}

export function RuleExportModal({ onPick, onClose }: RuleExportModalProps) {
  const labels = useMessages().ruleExport;
  useEscapeKey(onClose);

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>{labels.title}</h2>
          <button
            type="button"
            className={styles.close}
            onClick={onClose}
            aria-label={labels.cancel}
          >
            ×
          </button>
        </div>
        <div className={styles.body}>
          <p className={styles.hint}>{labels.hint}</p>
          <div className={styles.choices}>
            <button
              type="button"
              className={styles.choice}
              onClick={() => onPick("json")}
            >
              <span className={styles.choiceBadge}>JSON</span>
              <span className={styles.choiceText}>
                <span className={styles.choiceLabel}>{labels.formatJson}</span>
                <span className={styles.choiceHint}>
                  {labels.formatJsonHint}
                </span>
              </span>
            </button>
            <button
              type="button"
              className={styles.choice}
              onClick={() => onPick("xlsx")}
            >
              <span className={styles.choiceBadge}>XLSX</span>
              <span className={styles.choiceText}>
                <span className={styles.choiceLabel}>{labels.formatXlsx}</span>
                <span className={styles.choiceHint}>
                  {labels.formatXlsxHint}
                </span>
              </span>
            </button>
          </div>
        </div>
        <div className={styles.footer}>
          <button type="button" className={styles.cancel} onClick={onClose}>
            {labels.cancel}
          </button>
        </div>
      </div>
    </div>
  );
}
