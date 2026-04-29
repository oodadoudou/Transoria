import { useMessages } from "@/locales";
import { Pill } from "./Pill";
import styles from "./QuickSwitchModal.module.css";

export interface QuickSwitchItem {
  id: string;
  name: string;
  description?: string;
}

interface QuickSwitchModalProps {
  title: string;
  items: QuickSwitchItem[];
  activeId: string | null;
  emptyMessage: string;
  onSelect: (itemId: string) => Promise<void> | void;
  onClose: () => void;
  /** Optional "Manage…" link that takes the user to the full
   *  configuration page (Model / Prompt). Closes the modal first. */
  onManage?: () => void;
}

/**
 * Compact modal that lists profiles or prompt presets and activates
 * one on click. Architecture § 3.4 G.5 — used by Run pages to switch
 * the active model / prompt without leaving the Run page. The modal
 * does not edit anything; full management still lives on the
 * dedicated config pages.
 */
export function QuickSwitchModal({
  title,
  items,
  activeId,
  emptyMessage,
  onSelect,
  onClose,
  onManage,
}: QuickSwitchModalProps) {
  const messages = useMessages();
  const labels = messages.quickSwitch;

  const handlePick = (id: string) => {
    if (id === activeId) {
      onClose();
      return;
    }
    void Promise.resolve(onSelect(id)).then(() => onClose());
  };

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>{title}</h2>
          <button
            type="button"
            className={styles.closeButton}
            onClick={onClose}
            aria-label={labels.closeAction}
          >
            ×
          </button>
        </div>
        <div className={styles.body}>
          {items.length === 0 ? (
            <div className={styles.empty}>{emptyMessage}</div>
          ) : (
            <div className={styles.list}>
              {items.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`${styles.row} ${item.id === activeId ? styles.rowActive : ""}`.trim()}
                  onClick={() => handlePick(item.id)}
                >
                  <span className={styles.rowText}>
                    <span className={styles.rowName}>{item.name}</span>
                    {item.description ? (
                      <span className={styles.rowMeta}>{item.description}</span>
                    ) : null}
                  </span>
                  {item.id === activeId ? (
                    <span className={styles.rowBadge}>{labels.activeBadge}</span>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </div>
        {onManage ? (
          <div className={styles.footer}>
            <Pill
              variant="ghost"
              onClick={() => {
                onClose();
                onManage();
              }}
            >
              {labels.manageLink}
            </Pill>
          </div>
        ) : null}
      </div>
    </div>
  );
}
