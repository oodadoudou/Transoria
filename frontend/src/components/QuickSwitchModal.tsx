import { ModalOverlay } from "@/components/ModalOverlay";
import { useMessages } from "@/locales";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { Button } from "./Button";
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
  onManage?: () => void;
  allowReselect?: boolean;
  /** Multi-select: rows toggle in place and the modal stays open. */
  selectedIds?: string[];
}

export function QuickSwitchModal({
  title,
  items,
  activeId,
  emptyMessage,
  onSelect,
  onClose,
  onManage,
  allowReselect = false,
  selectedIds,
}: QuickSwitchModalProps) {
  const messages = useMessages();
  const labels = messages.quickSwitch;
  useEscapeKey(onClose);

  const isActive = (id: string) => (selectedIds ? selectedIds.includes(id) : id === activeId);

  const handlePick = (id: string) => {
    if (selectedIds) {
      void onSelect(id);
      return;
    }
    if (id === activeId && !allowReselect) {
      onClose();
      return;
    }
    void Promise.resolve(onSelect(id)).then(() => onClose());
  };

  return (
    <ModalOverlay
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
                  className={`${styles.row} ${isActive(item.id) ? styles.rowActive : ""}`.trim()}
                  onClick={() => handlePick(item.id)}
                >
                  <span className={styles.rowText}>
                    <span className={styles.rowName} title={item.name}>
                      {item.name}
                    </span>
                    {item.description ? (
                      <span
                        className={styles.rowMeta}
                        title={item.description}
                      >
                        {item.description}
                      </span>
                    ) : null}
                  </span>
                  {isActive(item.id) ? (
                    <span className={styles.rowBadge}>{selectedIds ? "✓" : labels.activeBadge}</span>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </div>
        {onManage ? (
          <div className={styles.footer}>
            <Button
              variant="ghost"
              onClick={() => {
                onClose();
                onManage();
              }}
            >
              {labels.manageLink}
            </Button>
          </div>
        ) : null}
      </div>
    </ModalOverlay>
  );
}
