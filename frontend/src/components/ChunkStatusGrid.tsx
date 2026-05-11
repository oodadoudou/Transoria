import { useEffect, useRef, useState } from "react";
import type { SubtaskMini } from "@/bridge";
import styles from "./ChunkStatusGrid.module.css";

interface ChunkStatusGridProps {
  subtasks: SubtaskMini[];
  itemLabel?: string;
}

// Above this count the wrapped grid becomes a wall of squares and DOM
// node count starts to hurt — switch to a single-row canvas heatmap.
const CANVAS_THRESHOLD = 500;
const CANVAS_HEIGHT = 16;

export function ChunkStatusGrid({
  subtasks,
  itemLabel = "Chunk",
}: ChunkStatusGridProps) {
  // SKIPPED subtasks are split-parent placeholders whose work is done
  // by their child subtasks; rendering them leaves a stray gray cell
  // in an otherwise-green grid even though every line is translated.
  const visible = subtasks.filter((s) => s.status !== "skipped");
  if (visible.length === 0) return null;
  if (visible.length > CANVAS_THRESHOLD) {
    return <CanvasGrid subtasks={visible} itemLabel={itemLabel} />;
  }
  return (
    <div className={styles.grid} role="list" aria-label={itemLabel}>
      {visible.map((subtask, index) => (
        <span
          key={subtask.id}
          role="listitem"
          className={`${styles.cell} ${styles[subtask.status] ?? ""}`.trim()}
          title={cellTooltip(subtask, index, itemLabel)}
        />
      ))}
    </div>
  );
}

function cellTooltip(
  subtask: SubtaskMini,
  index: number,
  itemLabel: string,
): string {
  const parts = [
    `${itemLabel} ${index + 1}`,
    subtask.id,
    subtask.status,
  ];
  if (subtask.attempts && subtask.attempts > 0) {
    parts.push(`attempt ${subtask.attempts}`);
  }
  if (subtask.status === "running" && subtask.started_at) {
    parts.push(`started ${subtask.started_at}`);
  }
  const head = parts.join(" · ");
  // Failed-chunk users want to know *why*. The wire shape only carries
  // ``last_error`` on FAILED subtasks (gated server-side) so the
  // tooltip stays compact for the common green-grid case.
  if (subtask.status === "failed" && subtask.last_error) {
    // Trim very long messages so the native title tooltip stays
    // readable on Linux/Windows (some platforms truncate around 1024
    // chars by default).
    const trimmed =
      subtask.last_error.length > 600
        ? `${subtask.last_error.slice(0, 600)}…`
        : subtask.last_error;
    return `${head}\n${trimmed}`;
  }
  return head;
}

function CanvasGrid({
  subtasks,
  itemLabel,
}: {
  subtasks: SubtaskMini[];
  itemLabel: string;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;
    setWidth(wrap.clientWidth);
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? 0;
      setWidth(next);
    });
    observer.observe(wrap);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0 || subtasks.length === 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(CANVAS_HEIGHT * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${CANVAS_HEIGHT}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, CANVAS_HEIGHT);

    const root = getComputedStyle(document.documentElement);
    const palette: Record<SubtaskMini["status"], string> = {
      pending: cssVar(root, "--hairline-strong", "#d8d2c7"),
      running: cssVar(root, "--accent", "#ad9d7a"),
      completed: cssVar(root, "--success", "#2f7d57"),
      failed: cssVar(root, "--warn", "#b6541b"),
      skipped: cssVar(root, "--muted", "#7a7166"),
    };

    const n = subtasks.length;
    // When cellWidth < 1 multiple subtasks fold into one pixel column;
    // running > failed > pending order so highlights win the merge.
    const cellWidth = width / n;
    if (cellWidth >= 1) {
      for (let i = 0; i < n; i++) {
        ctx.fillStyle = palette[subtasks[i].status] ?? palette.pending;
        const x = i * cellWidth;
        ctx.fillRect(x, 0, Math.ceil(cellWidth) + 0.5, CANVAS_HEIGHT);
      }
      return;
    }
    const columns = Math.floor(width);
    const perColumn = n / columns;
    for (let col = 0; col < columns; col++) {
      const start = Math.floor(col * perColumn);
      const end = Math.max(start + 1, Math.floor((col + 1) * perColumn));
      let dominant: SubtaskMini["status"] = "pending";
      let priority = 0;
      for (let i = start; i < end && i < n; i++) {
        const status = subtasks[i].status;
        const p = STATUS_PRIORITY[status] ?? 0;
        if (p > priority) {
          priority = p;
          dominant = status;
        }
      }
      ctx.fillStyle = palette[dominant];
      ctx.fillRect(col, 0, 1, CANVAS_HEIGHT);
    }
  }, [subtasks, width]);

  return (
    <div ref={wrapRef} className={styles.canvasWrap}>
      <canvas
        ref={canvasRef}
        className={styles.canvas}
        role="img"
        aria-label={`${itemLabel} status heatmap (${subtasks.length})`}
      />
    </div>
  );
}

const STATUS_PRIORITY: Record<SubtaskMini["status"], number> = {
  failed: 5,
  running: 4,
  completed: 3,
  skipped: 2,
  pending: 1,
};

function cssVar(
  style: CSSStyleDeclaration,
  name: string,
  fallback: string,
): string {
  const raw = style.getPropertyValue(name).trim();
  return raw.length > 0 ? raw : fallback;
}
