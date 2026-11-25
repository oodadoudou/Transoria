import type { ReactNode } from "react";
import { HelpTip } from "./HelpTip";
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
        {typeof help === "string" ? <HelpTip>{help}</HelpTip> : help}
        {typeof hint === "string" ? <HelpTip>{hint}</HelpTip> : hint}
      </span>
      {children}
    </label>
  );
}

export const fieldStyles = styles;
