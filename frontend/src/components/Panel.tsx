import type { HTMLAttributes, ReactNode } from 'react';
import { HelpTip } from './HelpTip';
import styles from './Panel.module.css';

interface PanelProps extends HTMLAttributes<HTMLElement> {
  label?: string;
  title?: string;
  subtitle?: string;
  subtitleSingleLine?: boolean;
  labelExtra?: ReactNode;
  labelHelp?: string;
  children?: ReactNode;
}

export function Panel({
  label,
  title,
  subtitle,
  subtitleSingleLine = false,
  labelExtra,
  labelHelp,
  children,
  className,
  ...rest
}: PanelProps) {
  const composed = [
    styles.section,
    subtitleSingleLine ? styles.singleLineSubtitle : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <section className={composed} {...rest}>
      {label ? (
        <div className={styles.labelRow}>
          <div className={styles.labelGroup}>
            <h3 className={styles.label}>{label}</h3>
            {labelHelp ? <HelpTip>{labelHelp}</HelpTip> : null}
          </div>
          {labelExtra ? <div className={styles.labelExtra}>{labelExtra}</div> : null}
        </div>
      ) : null}
      {title ? (
        <div className={styles.titleRow}>
          <div className={styles.title}>{title}</div>
          {subtitle ? <HelpTip>{subtitle}</HelpTip> : null}
        </div>
      ) : null}
      {!title && subtitle ? <div className={styles.subtitle}>{subtitle}</div> : null}
      {children}
    </section>
  );
}
