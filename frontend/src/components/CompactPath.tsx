import styles from "./CompactPath.module.css";

interface CompactPathProps {
  value: string;
  copyLabel: string;
  displayValue?: string;
  displayMode?: "path" | "filename";
  onCopy?: (value: string) => void;
  className?: string;
  valueClassName?: string;
  asCode?: boolean;
  emptyLabel?: string;
}

function fileNameFromPath(value: string): string {
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || value;
}

export function CompactPath({
  value,
  copyLabel,
  displayValue,
  displayMode = "path",
  onCopy,
  className = "",
  valueClassName = "",
  asCode = false,
  emptyLabel = "-",
}: CompactPathProps) {
  const resolvedDisplay =
    displayValue ??
    (displayMode === "filename" && value ? fileNameFromPath(value) : value);
  const display = resolvedDisplay || emptyLabel;
  const valueClass = `${styles.value} ${asCode ? styles.code : ""} ${valueClassName}`.trim();
  const handleCopy = () => {
    if (onCopy) {
      onCopy(value);
      return;
    }
    void navigator.clipboard.writeText(value);
  };

  if (!value) {
    return (
      <span className={`${styles.root} ${className}`.trim()}>
        {asCode ? (
          <code className={valueClass}>{display}</code>
        ) : (
          <span className={valueClass}>{display}</span>
        )}
      </span>
    );
  }

  return (
    <button
      type="button"
      className={`${styles.root} ${styles.copyTarget} ${className}`.trim()}
      title={`${copyLabel}: ${value}`}
      aria-label={`${copyLabel}: ${display}`}
      onClick={handleCopy}
    >
      {asCode ? (
        <code className={valueClass}>{display}</code>
      ) : (
        <span className={valueClass}>{display}</span>
      )}
    </button>
  );
}
