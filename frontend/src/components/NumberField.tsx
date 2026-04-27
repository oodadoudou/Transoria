import { type CSSProperties, useId, useState } from 'react';
import styles from './NumberField.module.css';

const NUM = new Intl.NumberFormat('en');

interface NumberFieldProps {
  label: string;
  value: number;
  onChange?: (v: number) => void;
  /** When set, a `?` button toggles this hint inline below the row. */
  help?: string;
  /** Optional trailing unit (e.g. "s", "/min"). */
  unit?: string;
  min?: number;
  max?: number;
  /** Width of the input; defaults to 96 px so 6-digit numbers fit cleanly. */
  inputWidth?: string;
}

/**
 * Label · ? · numeric input. Replaces the previous slider grammar — desktop
 * users want to type exact values, and the slider's pixel-precision rarely
 * matched the values they had in mind. The `?` toggle reveals a short hint
 * paragraph explaining what the parameter does.
 */
export function NumberField({
  label,
  value,
  onChange,
  help,
  unit,
  min,
  max,
  inputWidth = '96px',
}: NumberFieldProps) {
  const [helpOpen, setHelpOpen] = useState(false);
  const helpId = useId();

  const handleChange = (raw: string) => {
    if (!onChange) return;
    const stripped = raw.replace(/[^\d.-]/g, '');
    if (stripped === '' || stripped === '-' || stripped === '.') return;
    const num = Number(stripped);
    if (Number.isNaN(num)) return;
    let clamped = num;
    if (min !== undefined) clamped = Math.max(min, clamped);
    if (max !== undefined) clamped = Math.min(max, clamped);
    onChange(clamped);
  };

  return (
    <div className={styles.field}>
      <div className={styles.row}>
        <div className={styles.labelWrap}>
          <span className={styles.label}>{label}</span>
          {help ? (
            <button
              type="button"
              className={styles.help}
              aria-expanded={helpOpen}
              aria-controls={helpId}
              onClick={() => setHelpOpen(!helpOpen)}
              title={help}
            >
              ?
            </button>
          ) : null}
        </div>
        <div className={styles.inputWrap}>
          <input
            type="text"
            inputMode="numeric"
            className={`${styles.input} tnum`}
            value={NUM.format(value)}
            style={{ width: inputWidth } as CSSProperties}
            onChange={(e) => handleChange(e.target.value)}
            spellCheck={false}
            readOnly={!onChange}
          />
          {unit ? <span className={styles.unit}>{unit}</span> : null}
        </div>
      </div>
      {help && helpOpen ? (
        <div id={helpId} className={styles.hint}>
          {help}
        </div>
      ) : null}
    </div>
  );
}
