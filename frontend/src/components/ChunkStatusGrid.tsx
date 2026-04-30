import type { SubtaskMini } from "@/bridge";
import styles from "./ChunkStatusGrid.module.css";

interface ChunkStatusGridProps {
  subtasks: SubtaskMini[];
  /** Hover tooltip prefix, e.g. ``"Chunk"`` → ``"Chunk 12 · running"``. */
  itemLabel?: string;
}

const CHAR_BY_STATUS: Record<SubtaskMini["status"], string> = {
  pending: "░",
  running: "▓",
  completed: "█",
  failed: "✗",
  skipped: "─",
};

/**
 * ASCII-style progress bar — one monospace glyph per chunk, colored
 * by status. Running cells get a horizontal shimmer that sweeps
 * across the whole bar (so the "scrolling" feel reads even when only
 * 1-2 chunks are in flight against many done/pending). Pending /
 * completed / failed cells stay static.
 */
export function ChunkStatusGrid({
  subtasks,
  itemLabel = "Chunk",
}: ChunkStatusGridProps) {
  if (subtasks.length === 0) return null;
  const hasRunning = subtasks.some((s) => s.status === "running");
  return (
    <div
      className={`${styles.bar} ${hasRunning ? styles.barShimmer : ""}`.trim()}
      role="list"
      aria-label={itemLabel}
    >
      {subtasks.map((subtask, index) => (
        <span
          key={subtask.id}
          role="listitem"
          className={`${styles.cell} ${styles[subtask.status] ?? ""}`.trim()}
          title={`${itemLabel} ${index + 1} · ${subtask.id} · ${subtask.status}`}
        >
          {CHAR_BY_STATUS[subtask.status]}
        </span>
      ))}
    </div>
  );
}
