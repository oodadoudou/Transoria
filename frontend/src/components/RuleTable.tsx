import type { ReactNode } from "react";
import styles from "./RuleTable.module.css";

export interface RuleTableColumn<T> {
  key: string;
  label: string;
  width: string;
  align?: "left" | "right" | "center";
  render: (item: T, index: number) => ReactNode;
}

export interface RuleTableAction {
  label: string;
  onClick: () => void;
  primary?: boolean;
  disabled?: boolean;
}

interface RuleTableProps<T> {
  rules: T[];
  selectedIndex: number | null;
  onSelectIndex: (index: number | null) => void;
  isEnabled: (rule: T) => boolean;
  columns: RuleTableColumn<T>[];
  emptyMessage: string;
  editor: ReactNode;
  toolbar: RuleTableAction[];
}

export function RuleTable<T>({
  rules,
  selectedIndex,
  onSelectIndex,
  isEnabled,
  columns,
  emptyMessage,
  editor,
  toolbar,
}: RuleTableProps<T>) {
  const gridTemplate = ["36px", ...columns.map((c) => c.width)].join(" ");

  return (
    <div className={styles.editorGrid}>
      <div className={styles.tableWrap}>
        <div
          className={styles.tableHeader}
          style={{ gridTemplateColumns: gridTemplate }}
        >
          <span className={styles.colIndex}>#</span>
          {columns.map((col) => (
            <span
              key={col.key}
              className={col.align === "right" ? styles.colRight : ""}
            >
              {col.label}
            </span>
          ))}
        </div>
        {rules.length === 0 ? (
          <div className={styles.empty}>{emptyMessage}</div>
        ) : (
          rules.map((rule, index) => {
            const active = selectedIndex === index;
            const enabled = isEnabled(rule);
            const rowClass = [
              styles.row,
              active ? styles.rowActive : "",
              enabled ? "" : styles.rowDisabled,
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <button
                key={index}
                type="button"
                className={rowClass}
                style={{ gridTemplateColumns: gridTemplate }}
                onClick={() => onSelectIndex(active ? null : index)}
              >
                <span className={`${styles.colIndex} tnum`}>{index + 1}</span>
                {columns.map((col) => (
                  <span
                    key={col.key}
                    className={`${styles.cell} ${col.align === "right" ? styles.colRight : ""}`.trim()}
                  >
                    {col.render(rule, index)}
                  </span>
                ))}
              </button>
            );
          })
        )}
      </div>

      <aside className={styles.sidebar}>
        <div className={styles.toolbar}>
          {toolbar.map((action) => (
            <button
              key={action.label}
              type="button"
              className={`${styles.toolbarBtn} ${action.primary ? styles.toolbarBtnPrimary : ""}`.trim()}
              onClick={action.onClick}
              disabled={action.disabled}
            >
              {action.primary ? "+ " : ""}
              {action.label}
            </button>
          ))}
        </div>
        <div className={styles.editorBlock}>{editor}</div>
      </aside>
    </div>
  );
}

export const ruleTableStyles = styles;
