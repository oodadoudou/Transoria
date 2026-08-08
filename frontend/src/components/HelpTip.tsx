import { useCallback, useId, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";

import { useEscapeKey } from "@/hooks/useEscapeKey";
import styles from "./HelpTip.module.css";

interface HelpTipProps {
  children: string;
  ariaLabel?: string;
}

export function HelpTip({ children, ariaLabel = "Help" }: HelpTipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [popoverStyle, setPopoverStyle] = useState<CSSProperties | null>(null);
  const updatePosition = useCallback((node: HTMLElement) => {
    if (typeof window === "undefined") return;
    const rect = node.getBoundingClientRect();
    const gap = 8;
    const margin = 12;
    const width = Math.min(360, Math.max(240, window.innerWidth - margin * 2));
    const rightX = rect.right + gap;
    const leftX = rect.left - gap - width;
    const left =
      rightX + width <= window.innerWidth - margin
        ? rightX
        : Math.max(margin, leftX);
    const centerY = rect.top + rect.height / 2;
    const top = Math.min(
      Math.max(centerY, margin + 28),
      window.innerHeight - margin - 28,
    );
    setPopoverStyle({ left, top, width });
  }, []);
  useEscapeKey(() => setOpen(false), open);

  if (!children.trim()) return null;
  return (
    <span
      className={styles.wrap}
      onMouseLeave={() => setOpen(false)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) {
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        className={styles.trigger}
        aria-label={ariaLabel}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onClick={(event) => {
          updatePosition(event.currentTarget);
          setOpen(true);
        }}
        onFocus={(event) => {
          updatePosition(event.currentTarget);
          setOpen(true);
        }}
        onMouseEnter={(event) => {
          updatePosition(event.currentTarget);
          setOpen(true);
        }}
      >
        ?
      </button>
      {open && popoverStyle && typeof document !== "undefined"
        ? createPortal(
            <span
              id={id}
              className={styles.popover}
              role="tooltip"
              style={popoverStyle}
            >
              {children}
            </span>,
            document.body,
          )
        : null}
    </span>
  );
}
