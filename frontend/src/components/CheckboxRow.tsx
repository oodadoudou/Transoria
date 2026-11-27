import styles from "./CheckboxRow.module.css";

interface CheckboxRowProps {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}

export function CheckboxRow({ label, checked, disabled, onChange }: CheckboxRowProps) {
  return (
    <label className={styles.row}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
