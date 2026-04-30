import { format, useMessages } from "@/locales";
import { useTaskStore } from "@/store/useTaskStore";
import { useRunSnapshot, type RunKind } from "@/store/useRuntimeStore";
import styles from "./StatusBar.module.css";

const TOKEN_FORMATTER = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function routeToRunKind(module: string): RunKind {
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
        <span className={styles.pillMini}>
          {format(messages.status.tokens, {
            n: TOKEN_FORMATTER.format(snapshot.usage.total_tokens),
          })}
        </span>
      </div>
    </footer>
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
