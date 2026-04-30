import { useEffect, useMemo, useState } from "react";
import { useMessages } from "@/locales";
import type { ModelProfile } from "@/bridge";
import { Pill } from "./Pill";
import styles from "./QuickSwitchModal.module.css";
import pickerStyles from "./ModelListPicker.module.css";

interface ModelListPickerProps {
  title: string;
  available: ModelProfile[];
  selectedIds: string[];
  emptyMessage: string;
  onSubmit: (orderedIds: string[]) => Promise<void> | void;
  onClose: () => void;
  onManage?: () => void;
}

/**
 * Ordered multi-select for the per-module model rotation list.
 * Top-to-bottom = rotation order. Used on the Run pages to edit
 * which profiles the runtime cycles through. The catalog itself is
 * managed on the top-level Models page.
 */
export function ModelListPicker({
  title,
  available,
  selectedIds,
  emptyMessage,
  onSubmit,
  onClose,
  onManage,
}: ModelListPickerProps) {
  const messages = useMessages();
  const labels = messages.quickSwitch;
  const pickerLabels = messages.modelListPicker;

  const [order, setOrder] = useState<string[]>(selectedIds);

  useEffect(() => {
    setOrder(selectedIds);
  }, [selectedIds]);

  const byId = useMemo(() => {
    const map = new Map<string, ModelProfile>();
    for (const profile of available) map.set(profile.id, profile);
    return map;
  }, [available]);

  const selectedRows = useMemo(
    () =>
      order
        .map((id) => byId.get(id))
        .filter((profile): profile is ModelProfile => profile !== undefined),
    [order, byId],
  );

  const unselected = useMemo(() => {
    const taken = new Set(order);
    return available.filter((profile) => !taken.has(profile.id));
  }, [available, order]);

  const dirty = useMemo(
    () =>
      order.length !== selectedIds.length ||
      order.some((id, index) => id !== selectedIds[index]),
    [order, selectedIds],
  );

  const move = (index: number, delta: number) => {
    setOrder((prev) => {
      const target = index + delta;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const remove = (id: string) => {
    setOrder((prev) => prev.filter((entry) => entry !== id));
  };

  const append = (id: string) => {
    setOrder((prev) => (prev.includes(id) ? prev : [...prev, id]));
  };

  const submit = () => {
    void Promise.resolve(onSubmit(order)).then(() => onClose());
  };

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className={`${styles.modal} ${pickerStyles.modalWide}`}
        onClick={(e) => e.stopPropagation()}
      >
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
          <div className={pickerStyles.section}>
            <div className={pickerStyles.sectionLabel}>
              {pickerLabels.selectedTitle}
            </div>
            {selectedRows.length === 0 ? (
              <div className={styles.empty}>{pickerLabels.selectedEmpty}</div>
            ) : (
              <ol className={pickerStyles.orderedList}>
                {selectedRows.map((profile, index) => (
                  <li key={profile.id} className={pickerStyles.orderedRow}>
                    <span className={pickerStyles.rank}>{index + 1}</span>
                    <span className={pickerStyles.rowText}>
                      <span className={pickerStyles.rowName}>
                        {profile.display_name}
                      </span>
                      <span className={pickerStyles.rowMeta}>
                        {profile.model_id}
                      </span>
                    </span>
                    <span className={pickerStyles.actions}>
                      <button
                        type="button"
                        className={pickerStyles.iconButton}
                        onClick={() => move(index, -1)}
                        disabled={index === 0}
                        aria-label={pickerLabels.moveUp}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className={pickerStyles.iconButton}
                        onClick={() => move(index, 1)}
                        disabled={index === selectedRows.length - 1}
                        aria-label={pickerLabels.moveDown}
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        className={pickerStyles.iconButton}
                        onClick={() => remove(profile.id)}
                        aria-label={pickerLabels.removeAction}
                      >
                        ×
                      </button>
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </div>

          <div className={pickerStyles.section}>
            <div className={pickerStyles.sectionLabel}>
              {pickerLabels.availableTitle}
            </div>
            {unselected.length === 0 ? (
              <div className={styles.empty}>
                {available.length === 0
                  ? emptyMessage
                  : pickerLabels.availableEmpty}
              </div>
            ) : (
              <div className={pickerStyles.availableList}>
                {unselected.map((profile) => (
                  <button
                    key={profile.id}
                    type="button"
                    className={pickerStyles.availableRow}
                    onClick={() => append(profile.id)}
                  >
                    <span className={pickerStyles.rowText}>
                      <span className={pickerStyles.rowName}>
                        {profile.display_name}
                      </span>
                      <span className={pickerStyles.rowMeta}>
                        {profile.model_id}
                      </span>
                    </span>
                    <span className={pickerStyles.addBadge}>
                      + {pickerLabels.addAction}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className={styles.footer}>
          {onManage ? (
            <Pill
              variant="ghost"
              onClick={() => {
                onClose();
                onManage();
              }}
            >
              {labels.manageLink}
            </Pill>
          ) : null}
          <div className={pickerStyles.spacer} />
          <Pill variant="ghost" onClick={onClose}>
            {pickerLabels.cancelAction}
          </Pill>
          <Pill onClick={submit} disabled={!dirty}>
            {pickerLabels.applyAction}
          </Pill>
        </div>
      </div>
    </div>
  );
}
