import type { SubtaskMini } from "@/bridge";
import styles from "./ChunkStatusGrid.module.css";

interface ChunkStatusGridProps {
  subtasks: SubtaskMini[];
  /** Hover tooltip prefix, e.g. ``"Chunk"`` → ``"Chunk 12 · running"``. */
  itemLabel?: string;
}

/**
 * Horizontal row of small status dots — one per subtask. Pending = idle
 * grey, running = warm beige with a pulsing glow, completed = green,
 * failed = warn red. Each cell shows ``id · status`` on hover.
 */
export function ChunkStatusGrid({
  subtasks,
  itemLabel = "Chunk",
}: ChunkStatusGridProps) {
  if (subtasks.length === 0) return null;
  return (
    <div className={styles.grid} role="list" aria-label={itemLabel}>
      {subtasks.map((subtask, index) => (
        <span
          key={subtask.id}
          role="listitem"
          className={`${styles.cell} ${styles[subtask.status] ?? ""}`.trim()}
          title={`${itemLabel} ${index + 1} · ${subtask.id} · ${subtask.status}`}
        />
      ))}
    </div>
  );
}
