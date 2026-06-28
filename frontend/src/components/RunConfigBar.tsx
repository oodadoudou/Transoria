import styles from "./RunConfigBar.module.css";

export interface RunConfigBarItem {
  id: string;
  label: string;
  primary: string;
  secondary?: string;
  actionLabel: string;
  onClick: () => void;
}

interface RunConfigBarProps {
  items: RunConfigBarItem[];
}

export function RunConfigBar({ items }: RunConfigBarProps) {
  return (
    <div className={styles.bar}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          className={styles.item}
          onClick={item.onClick}
          title={[item.primary, item.secondary].filter(Boolean).join("\n")}
        >
          <span className={styles.meta}>
            <span className={styles.label}>{item.label}</span>
            <span className={styles.primary}>{item.primary}</span>
            {item.secondary ? (
              <span className={styles.secondary}>{item.secondary}</span>
            ) : null}
          </span>
          <span className={styles.action}>{item.actionLabel}</span>
        </button>
      ))}
    </div>
  );
}
