import { useEffect, useRef, useState } from "react";
import type { TaskProgress } from "@/bridge";
import styles from "./LiveRequestCounter.module.css";

interface LiveRequestCounterProps {
  progress: TaskProgress;
  /** Localized template, e.g. ``"已完成 {done} / 共 {total}"``. */
  label: string;
  /** Localized template for the in-flight chip. */
  inflightLabel: string;
  longestLabel?: string;
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
  longestLabel,
}: LiveRequestCounterProps) {
  const done = progress.completed + progress.failed;
  const inflight = progress.running;
  const total = progress.total;
  const longest = Math.floor(progress.longest_running_seconds ?? 0);
  const inflightText =
    inflight > 0 && longest > 0 && longestLabel
      ? `${inflightLabel.replace("{n}", String(inflight))} · ${longestLabel.replace("{time}", formatDuration(longest))}`
      : inflightLabel.replace("{n}", String(inflight));

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
        {inflightText}
      </span>
    </div>
  );
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
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
