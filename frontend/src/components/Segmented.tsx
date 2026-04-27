import styles from './Segmented.module.css';

export interface SegmentedOption<T extends string> {
  id: T;
  label: string;
}

interface SegmentedProps<T extends string> {
  options: ReadonlyArray<SegmentedOption<T>>;
  value: T;
  onChange: (value: T) => void;
  ariaLabel?: string;
}

/**
 * Pill segmented control. Active option fills warm graphite (`--action`);
 * inactives are quiet on a soft cream surface. Used wherever a choice has
 * 2-N short options (UI language, thinking level, provider format).
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: SegmentedProps<T>) {
  return (
    <div className={styles.segment} role="radiogroup" aria-label={ariaLabel}>
      {options.map((opt) => {
        const active = value === opt.id;
        return (
          <button
            key={opt.id}
            type="button"
            role="radio"
            aria-checked={active}
            className={`${styles.item} ${active ? styles.itemActive : ''}`.trim()}
            onClick={() => onChange(opt.id)}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
