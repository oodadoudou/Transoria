import type { ReactNode } from "react";
import { ChevronDownIcon, FolderIcon } from "./Icon";
import styles from "./FieldCard.module.css";

interface FieldCardProps {
  label: string;
  value: string;
  trailing?: "folder" | "chevron" | ReactNode;
  truncate?: boolean;
  onClick?: () => void;
}

export function FieldCard({
  label,
  value,
  trailing = "chevron",
  truncate,
  onClick,
}: FieldCardProps) {
  const trailingNode =
    trailing === "folder" ? (
      <FolderIcon size={14} />
    ) : trailing === "chevron" ? (
      <ChevronDownIcon size={14} />
    ) : (
      trailing
    );

  return (
    <button type="button" className={styles.field} onClick={onClick}>
      <span className={styles.label}>{label}</span>
      <span className={styles.row}>
        <span className={truncate ? styles.truncate : undefined}>{value}</span>
        {trailingNode ? (
          <span className={styles.trailing}>{trailingNode}</span>
        ) : null}
      </span>
    </button>
  );
}
