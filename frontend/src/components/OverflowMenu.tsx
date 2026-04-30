import { useEffect, useRef, useState } from "react";
import styles from "./OverflowMenu.module.css";

export interface OverflowMenuItem {
  key: string;
  label: string;
  onSelect: () => void;
  /** ``"danger"`` styles the item with the destructive accent (red).
   *  Default is neutral. */
  variant?: "default" | "danger";
  disabled?: boolean;
}

interface OverflowMenuProps {
  items: OverflowMenuItem[];
  ariaLabel: string;
}

/**
 * Vertical-three-dots menu used on cards / rows that previously
 * exposed inline edit / delete pills. Closes on outside click and
 * on ``Escape``. Designed for short menus (3-5 items).
 */
export function OverflowMenu({ items, ariaLabel }: OverflowMenuProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (
        wrapRef.current &&
        e.target instanceof Node &&
        !wrapRef.current.contains(e.target)
      ) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <button
        type="button"
        className={styles.trigger}
        aria-label={ariaLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
          <circle cx="3" cy="8" r="1.5" fill="currentColor" />
          <circle cx="8" cy="8" r="1.5" fill="currentColor" />
          <circle cx="13" cy="8" r="1.5" fill="currentColor" />
        </svg>
      </button>
      {open ? (
        <div className={styles.menu} role="menu">
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              className={[
                styles.item,
                item.variant === "danger" ? styles.itemDanger : "",
              ]
                .filter(Boolean)
                .join(" ")}
              disabled={item.disabled}
              onClick={() => {
                if (item.disabled) return;
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
