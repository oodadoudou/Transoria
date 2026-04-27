import type { HTMLAttributes, ReactNode } from 'react';
import styles from './Panel.module.css';

interface PanelProps extends HTMLAttributes<HTMLElement> {
  /** Optional uppercase section label that sits above panel content. */
  label?: string;
  /** Display title (sits above subtitle and children). */
  title?: string;
  /** Body subtitle under the title. */
  subtitle?: string;
  /** Optional inline-end slot rendered to the right of the section label. */
  labelExtra?: ReactNode;
  children?: ReactNode;
}

/**
 * Flat section. No card wrapper, no border, no fill — just a tracked label
 * (or display title) followed by the children, separated by spacing. Visual
 * structure across multiple Panels comes from the parent flex `gap`.
 */
export function Panel({
  label,
  title,
  subtitle,
  labelExtra,
  children,
  className,
  ...rest
}: PanelProps) {
  const composed = `${styles.section} ${className ?? ''}`.trim();
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
