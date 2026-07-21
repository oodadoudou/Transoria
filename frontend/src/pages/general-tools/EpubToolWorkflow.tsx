import type { ReactNode } from "react";

import styles from "./EpubToolWorkflow.module.css";

export function EpubToolWorkspace({ children }: { children: ReactNode }) {
  return <div className={styles.workspace}>{children}</div>;
}

export function EpubToolStage({ children }: { children: ReactNode }) {
  return <div className={styles.stage}>{children}</div>;
}

export function EpubToolAdvancedSettings({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.advanced} aria-label={label}>
      <div className={styles.advancedTitle}>{label}</div>
      <div className={styles.advancedBody}>{children}</div>
    </section>
  );
}

export function EpubToolActionDock({
  statusLabel,
  status,
  actions,
  error,
}: {
  statusLabel: string;
  status: ReactNode;
  actions: ReactNode;
  error?: ReactNode;
}) {
  return (
    <div className={styles.actionDock} role="group" aria-label={statusLabel}>
      <div className={styles.actionStatus}>
        <span>{statusLabel}</span>
        <strong>{status}</strong>
      </div>
      <div className={styles.actionButtons}>{actions}</div>
      {error ? <div className={styles.actionError}>{error}</div> : null}
    </div>
  );
}
