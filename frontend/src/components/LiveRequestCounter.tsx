import { useEffect, useRef, useState } from "react";
import type { TaskProgress } from "@/bridge";
import styles from "./LiveRequestCounter.module.css";

interface LiveRequestCounterProps {
  progress: TaskProgress;
  /** Localized template, e.g. ``"已完成 {done} / 共 {total}"``. */
  label: string;
  /** Localized template for the in-flight chip. */
  inflightLabel: string;
}

/**
 * Pair of compact counters that flash briefly when the underlying
 * count moves. Done count and in-flight count animate independently
 * so the user gets a visual heartbeat on each new RECV / SEND.
 */
export function LiveRequestCounter({
  progress,
  label,
  inflightLabel,
}: LiveRequestCounterProps) {
  const done = progress.completed + progress.failed + progress.skipped;
  const inflight = progress.running;
  const total = progress.total;

  const doneFlash = useFlashOnChange(done);
  const inflightFlash = useFlashOnChange(inflight);

  return (
    <div className={styles.row}>
      <span className={`${styles.chip} ${doneFlash ? styles.flash : ""}`.trim()}>
        {label
          .replace("{done}", String(done))
          .replace("{total}", String(total))}
      </span>
      <span
        className={`${styles.chip} ${styles.inflight} ${
          inflightFlash ? styles.flash : ""
        }`.trim()}
      >
        {inflightLabel.replace("{n}", String(inflight))}
      </span>
    </div>
  );
}

/** Returns ``true`` for ~600ms after ``value`` changes, then resets. */
function useFlashOnChange(value: number): boolean {
  const [flash, setFlash] = useState(false);
  const previous = useRef(value);
  useEffect(() => {
    if (previous.current === value) return;
    previous.current = value;
    setFlash(true);
    const handle = window.setTimeout(() => setFlash(false), 600);
    return () => window.clearTimeout(handle);
  }, [value]);
  return flash;
}
