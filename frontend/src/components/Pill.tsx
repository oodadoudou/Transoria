import type { ButtonHTMLAttributes, ReactNode } from 'react';
import styles from './Pill.module.css';

type Variant = 'primary' | 'ghost';

interface PillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  icon?: ReactNode;
  children?: ReactNode;
}

export function Pill({ variant = 'primary', icon, children, className, ...rest }: PillProps) {
  const variantClass = variant === 'primary' ? styles.primary : styles.ghost;
  return (
    <button className={`${styles.pill} ${variantClass} ${className ?? ''}`.trim()} {...rest}>
      {icon ? <span className={styles.icon}>{icon}</span> : null}
      <span>{children}</span>
    </button>
  );
}
