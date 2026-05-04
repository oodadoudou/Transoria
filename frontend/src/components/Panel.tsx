import type { HTMLAttributes, ReactNode } from 'react';
import styles from './Panel.module.css';

interface PanelProps extends HTMLAttributes<HTMLElement> {
  label?: string;
  title?: string;
  subtitle?: string;
  subtitleSingleLine?: boolean;
  labelExtra?: ReactNode;
  children?: ReactNode;
}

export function Panel({
  label,
  title,
  subtitle,
  subtitleSingleLine = false,
  labelExtra,
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
          <h3 className={styles.label}>{label}</h3>
          {labelExtra ? <div className={styles.labelExtra}>{labelExtra}</div> : null}
        </div>
      ) : null}
      {title ? <div className={styles.title}>{title}</div> : null}
      {subtitle ? <div className={styles.subtitle}>{subtitle}</div> : null}
      {children}
    </section>
  );
}
