import { useEffect, useRef, useState } from "react";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { format, useMessages } from "@/locales";
import { useTaskStore } from "@/store/useTaskStore";
import { useRunSnapshot, type RunKind } from "@/store/useRuntimeStore";
import styles from "./StatusBar.module.css";

const TOKEN_FORMATTER = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

const FULL_FORMATTER = new Intl.NumberFormat("en");

function routeToRunKind(module: string): RunKind {
  if (module === "glossary-review") return "glossary_review";
  return module === "glossary" ? "glossary" : "translation";
}

export function StatusBar() {
  const messages = useMessages();
  const route = useTaskStore((state) => state.route);
  const snapshot = useRunSnapshot(routeToRunKind(route.module));

  const status = snapshot.status;
  const dotKind = statusDotKind(status);
  const label = statusLabel(status, messages);
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const activeRequests = snapshot.progress.running;

  return (
    <footer className={styles.status}>
      <div className={styles.group}>
        <span className={`${styles.dot} ${styles[dotKind]}`} />
        <b>{label}</b>
      </div>
      <div className={styles.group}>
        <span>
          {format(messages.status.activeRequests, { n: activeRequests })}
        </span>
        <span>·</span>
        <span>{format(messages.status.perMinute, { n: ratePerMinute })}</span>
      </div>
      <div className={styles.group}>
        <TokenPill snapshot={snapshot} messages={messages} />
      </div>
    </footer>
  );
}

interface TokenPillProps {
  snapshot: ReturnType<typeof useRunSnapshot>;
  messages: ReturnType<typeof useMessages>;
}

function TokenPill({ snapshot, messages }: TokenPillProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  useEscapeKey(() => setOpen(false), open);

  // Dismiss on outside click.
  useEffect(() => {
    if (!open) return;
    function onDown(event: MouseEvent) {
      if (!wrapRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  const usage = snapshot.usage;
  const completedSegments = snapshot.progress.completed;
  const elapsedSeconds = snapshot.progress.elapsed_seconds || 0;
  const tokensPerMinute =
    elapsedSeconds > 0
      ? Math.round((usage.total_tokens / elapsedSeconds) * 60)
      : 0;
  const avgPerSegment =
    completedSegments > 0
      ? Math.round(usage.total_tokens / completedSegments)
      : 0;
  const labels = messages.status.tokenDetail;

  return (
    <div className={styles.tokenWrap} ref={wrapRef}>
      <button
        type="button"
        className={styles.pillMini}
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-label={labels.title}
      >
        {format(messages.status.tokens, {
          n: TOKEN_FORMATTER.format(usage.total_tokens),
        })}
      </button>
      {open ? (
        <div className={styles.tokenPanel} role="dialog" aria-label={labels.title}>
          <div className={styles.tokenPanelTitle}>{labels.title}</div>
          <div className={styles.tokenRow}>
            <span className={styles.tokenRowLabel}>{labels.input}</span>
            <span className={styles.tokenRowValue}>
              {FULL_FORMATTER.format(usage.input_tokens)}
            </span>
          </div>
          <div className={styles.tokenRow}>
            <span className={styles.tokenRowLabel}>{labels.output}</span>
            <span className={styles.tokenRowValue}>
              {FULL_FORMATTER.format(usage.output_tokens)}
            </span>
          </div>
          <div className={styles.tokenRow}>
            <span className={styles.tokenRowLabel}>{labels.cachedInput}</span>
            <span className={styles.tokenRowValue}>
              {FULL_FORMATTER.format(usage.cached_input_tokens)}
            </span>
          </div>
          <div className={styles.tokenDivider} />
          <div className={styles.tokenRow}>
            <span className={styles.tokenRowLabel}>{labels.total}</span>
            <span className={styles.tokenRowValue}>
              {FULL_FORMATTER.format(usage.total_tokens)}
            </span>
          </div>
          <div className={styles.tokenDivider} />
          <div className={styles.tokenRow}>
            <span className={styles.tokenRowLabel}>{labels.perMinute}</span>
            <span className={styles.tokenRowValue}>
              {FULL_FORMATTER.format(tokensPerMinute)}
            </span>
          </div>
          <div className={styles.tokenRow}>
            <span className={styles.tokenRowLabel}>{labels.perSegment}</span>
            <span className={styles.tokenRowValue}>
              {avgPerSegment > 0 ? FULL_FORMATTER.format(avgPerSegment) : "—"}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type SnapshotStatus = ReturnType<typeof useRunSnapshot>["status"];

function statusDotKind(status: SnapshotStatus) {
  if (status === "running") return "success";
  if (status === "failed") return "warn";
  return "muted";
}

function statusLabel(
  status: SnapshotStatus,
  messages: ReturnType<typeof useMessages>,
): string {
  switch (status) {
    case "running":
      return messages.status.running;
    case "stopping":
    case "pausing":
      return messages.status.stopping;
    case "stopped":
    case "pending":
    case "paused":
      return messages.status.stopped;
    case "failed":
      return messages.status.failed;
    case "completed":
      return messages.status.completed;
  }
}
