import type { ReactNode } from "react";
import { Pill } from "./Pill";
import styles from "./GuidedEmptyState.module.css";

interface GuidedEmptyStateProps {
  label?: string;
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
  children?: ReactNode;
}

export function GuidedEmptyState({
  label,
  title,
  body,
  actionLabel,
  onAction,
  children,
}: GuidedEmptyStateProps) {
  return (
    <section className={styles.root}>
      <div className={styles.copy}>
        {label ? <div className={styles.label}>{label}</div> : null}
        <h3>{title}</h3>
        <p>{body}</p>
      </div>
      {actionLabel && onAction ? (
        <Pill variant="primary" onClick={onAction}>
          {actionLabel}
        </Pill>
      ) : null}
      {children ? <div className={styles.extra}>{children}</div> : null}
    </section>
  );
}
