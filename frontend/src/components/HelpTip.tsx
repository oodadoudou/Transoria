import styles from "./HelpTip.module.css";

interface HelpTipProps {
  children: string;
  ariaLabel?: string;
}

export function HelpTip({ children, ariaLabel = "Help" }: HelpTipProps) {
  if (!children.trim()) return null;
  return (
    <span className={styles.wrap}>
      <button type="button" className={styles.trigger} aria-label={ariaLabel}>
        ?
      </button>
      <span className={styles.popover} role="tooltip">
        {children}
      </span>
    </span>
  );
}
