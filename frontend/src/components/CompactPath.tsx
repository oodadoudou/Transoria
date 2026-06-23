import styles from "./CompactPath.module.css";

interface CompactPathProps {
  value: string;
  copyLabel: string;
  onCopy?: (value: string) => void;
  className?: string;
  valueClassName?: string;
  asCode?: boolean;
  emptyLabel?: string;
}

export function CompactPath({
  value,
  copyLabel,
  onCopy,
  className = "",
  valueClassName = "",
  asCode = false,
  emptyLabel = "-",
}: CompactPathProps) {
  const display = value || emptyLabel;
  const valueClass = `${styles.value} ${asCode ? styles.code : ""} ${valueClassName}`.trim();
  const handleCopy = () => {
    if (onCopy) {
      onCopy(value);
      return;
    }
    void navigator.clipboard.writeText(value);
  };

  return (
    <span className={`${styles.root} ${className}`.trim()} title={value || display}>
      {asCode ? (
        <code className={valueClass}>{display}</code>
      ) : (
        <span className={valueClass}>{display}</span>
      )}
      {value ? (
        <button
          type="button"
          className={styles.copyButton}
          onClick={handleCopy}
        >
          {copyLabel}
        </button>
      ) : null}
    </span>
  );
}
