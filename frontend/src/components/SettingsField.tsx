import type { ReactNode } from "react";
import styles from "./SettingsField.module.css";

interface SettingsFieldStackProps {
  children: ReactNode;
}

interface SettingsFieldProps {
  label: string;
  hint?: string;
  children: ReactNode;
}

interface SettingsFieldFrameProps {
  children: ReactNode;
}

export function SettingsFieldStack({ children }: SettingsFieldStackProps) {
  return <div className={styles.stack}>{children}</div>;
}

export function SettingsField({ label, hint, children }: SettingsFieldProps) {
  return (
    <div className={styles.item}>
      <div className={styles.row}>
        <div className={styles.text}>
          <div className={styles.label}>{label}</div>
          {hint ? <div className={styles.hint}>{hint}</div> : null}
        </div>
        <div className={styles.control}>{children}</div>
      </div>
    </div>
  );
}

export function SettingsFieldFrame({ children }: SettingsFieldFrameProps) {
  return <div className={styles.item}>{children}</div>;
}
