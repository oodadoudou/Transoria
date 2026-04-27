import { useId, useState } from 'react';
import styles from './ToggleSwitch.module.css';

interface ToggleSwitchProps {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  help?: string;
  /** Optional inline control rendered next to the toggle when checked (e.g., a value input). */
  trailing?: React.ReactNode;
}

export function ToggleSwitch({
  label,
  checked,
  onChange,
  help,
  trailing,
}: ToggleSwitchProps) {
  const [helpOpen, setHelpOpen] = useState(false);
  const helpId = useId();

  return (
    <div className={styles.wrap}>
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
        <div className={styles.controls}>
          {checked && trailing ? <div className={styles.trailing}>{trailing}</div> : null}
          <button
            type="button"
            role="switch"
            aria-checked={checked}
            className={`${styles.switch} ${checked ? styles.on : ''}`.trim()}
            onClick={() => onChange(!checked)}
          >
            <span className={styles.thumb} aria-hidden />
          </button>
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
