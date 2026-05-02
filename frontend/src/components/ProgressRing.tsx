import styles from "./ProgressRing.module.css";

interface ProgressRingProps {
  /** 0–100 */
  percent: number;
  completed: number;
  total: number;
}

const RADIUS = 78;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS; // ≈ 490

const NUM = new Intl.NumberFormat("en");

export function ProgressRing({ percent, completed, total }: ProgressRingProps) {
  const clamped = Math.max(0, Math.min(100, percent));
  const offset = CIRCUMFERENCE * (1 - clamped / 100);
  // Floor (not round) so 400/402 = 99.5% renders as "99%", not "100%".
  // Showing 100% before the task actually finishes makes users think
  // the run is done when subtasks are still in flight.
  const display = Math.floor(clamped);

  return (
    <div className={styles.ring}>
      <svg width="160" height="160" viewBox="0 0 180 180" aria-hidden>
        <circle
          cx="90"
          cy="90"
          r={RADIUS}
          fill="none"
          stroke="#ebe9e2"
          strokeWidth="9"
        />
        <circle
          cx="90"
          cy="90"
          r={RADIUS}
          fill="none"
          stroke="var(--action)"
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 280ms ease" }}
        />
      </svg>
      <div className={styles.num}>
        <b className="tnum">{display}%</b>
        <span className="tnum">
          {NUM.format(completed)} / {NUM.format(total)}
        </span>
      </div>
    </div>
  );
}
