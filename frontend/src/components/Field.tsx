import type { ReactNode } from "react";
import styles from "./Field.module.css";

interface FieldProps {
  label: ReactNode;
  help?: ReactNode;
  hint?: ReactNode;
  className?: string;
  children: ReactNode;
}

export function Field({ label, help, hint, className, children }: FieldProps) {
  return (
    <label className={`${styles.field} ${className ?? ""}`.trim()}>
      <span className={styles.label}>
        {label}
        {help}
      </span>
      {children}
      {hint ? <span className={styles.hint}>{hint}</span> : null}
    </label>
  );
}

export const fieldStyles = styles;
