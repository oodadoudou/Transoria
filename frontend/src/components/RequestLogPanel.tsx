import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import {
  glossaryBridge,
  glossaryReviewBridge,
  translationBridge,
} from "@/bridge";
import type { RequestLogEvent, TaskStatus } from "@/bridge/types";
import { useMessages } from "@/locales";
import { ToggleSwitch } from "@/components/ToggleSwitch";
import styles from "./RequestLogPanel.module.css";

type RequestLogKind = "translation" | "glossary" | "glossary_review";

const LIMIT = 200;

const BRIDGES = {
  translation: translationBridge,
  glossary: glossaryBridge,
  glossary_review: glossaryReviewBridge,
} as const;

function storageKey(kind: RequestLogKind): string {
  return `transoria.request-log.visible.${kind}`;
}

function loadVisible(kind: RequestLogKind): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(storageKey(kind)) === "1";
  } catch {
    return false;
  }
}

function saveVisible(kind: RequestLogKind, visible: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(storageKey(kind), visible ? "1" : "0");
  } catch {
    // Diagnostic UI only; storage failure should not affect task pages.
  }
}

function isTerminalTaskStatus(status: TaskStatus | null | undefined): boolean {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "stopped" ||
    status === "paused"
  );
}

function formatNumber(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "0";
  return new Intl.NumberFormat("en").format(value);
}

function formatDuration(value: number | undefined, suffix: string): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${value.toFixed(value >= 10 ? 1 : 2)}${suffix}`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function RequestLogPanel({
  kind,
  taskId,
  taskStatus,
}: {
  kind: RequestLogKind;
  taskId: string | null;
  taskStatus?: TaskStatus | null;
}) {
  const messages = useMessages();
  const copy = messages.requestLog;
  const [visible, setVisible] = useState(() => loadVisible(kind));
  const [events, setEvents] = useState<RequestLogEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const statusLabels = useMemo(
    () => ({
      running: copy.statusRunning,
      completed: copy.statusCompleted,
      failed: copy.statusFailed,
      cancelled: copy.statusCancelled,
    }),
    [copy],
  );
  const statusClasses = useMemo(
    () => ({
      running: styles.running,
      completed: styles.completed,
      failed: styles.failed,
      cancelled: styles.cancelled,
    }),
    [],
  );

  const loadEvents = useCallback(async () => {
    if (!taskId) {
      setEvents([]);
      setTotal(0);
      setError("");
      return;
    }
    setLoading(true);
    try {
      const result = await BRIDGES[kind].readRequestEvents(taskId, LIMIT);
      setEvents(result.events);
      setTotal(result.total);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [kind, taskId]);

  useEffect(() => {
    saveVisible(kind, visible);
    if (visible) void loadEvents();
  }, [kind, loadEvents, taskStatus, visible]);

  useEffect(() => {
    if (!visible || !taskId || isTerminalTaskStatus(taskStatus)) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void loadEvents();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [loadEvents, taskId, taskStatus, visible]);

  const handleToggle = (next: boolean) => {
    setVisible(next);
    if (next) void loadEvents();
  };

  return (
    <section className={styles.panel} aria-label={copy.title}>
      <div className={styles.header}>
        <div className={styles.heading}>
          <h2>{copy.title}</h2>
          <p>{copy.subtitle}</p>
        </div>
        <div className={styles.actions}>
          {visible ? (
            <button
              type="button"
              className={styles.refresh}
              onClick={() => void loadEvents()}
              disabled={loading}
            >
              {loading ? copy.loading : copy.refresh}
            </button>
          ) : null}
          <ToggleSwitch
            label={copy.toggle}
            checked={visible}
            onChange={handleToggle}
          />
        </div>
      </div>

      {visible ? (
        <div className={styles.body}>
          {!taskId ? <div className={styles.empty}>{copy.emptyNoTask}</div> : null}
          {taskId && error ? (
            <div className={styles.error}>
              {copy.errorPrefix} {error}
            </div>
          ) : null}
          {taskId && !error && events.length === 0 ? (
            <div className={styles.empty}>
              {loading ? copy.loading : copy.empty}
            </div>
          ) : null}
          {events.length > 0 ? (
            <>
              <div className={styles.meta}>
                {copy.showing.replace("{shown}", String(events.length)).replace(
                  "{total}",
                  String(total),
                )}
              </div>
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>{copy.columnTime}</th>
                      <th>{copy.columnStatus}</th>
                      <th>{copy.columnRequest}</th>
                      <th>{copy.columnModel}</th>
                      <th>{copy.columnDuration}</th>
                      <th>{copy.columnTokens}</th>
                      <th>{copy.columnResponse}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {events.map((event) => {
                      const isExpanded = Boolean(expanded[event.request_id]);
                      const response = event.response_text || event.error || "";
                      const statusClass = statusClasses[event.status] ?? "";
                      return (
                        <Fragment key={event.request_id}>
                          <tr>
                            <td>{formatTime(event.timestamp)}</td>
                            <td>
                              <span
                                className={`${styles.status} ${statusClass}`.trim()}
                              >
                                {statusLabels[event.status] ?? event.status}
                              </span>
                            </td>
                            <td>
                              <div className={styles.requestLabel}>
                                {event.label || event.subtask_id}
                              </div>
                              <div className={styles.requestMeta}>
                                {event.subtask_id} · {copy.attempt.replace(
                                  "{n}",
                                  String(event.subtask_attempt || 1),
                                )}
                              </div>
                            </td>
                            <td>
                              <div className={styles.model}>{event.model_id || "-"}</div>
                              <div className={styles.provider}>
                                {event.provider_format || "-"}
                              </div>
                            </td>
                            <td>
                              {formatDuration(
                                event.duration_seconds,
                                copy.secondsSuffix,
                              )}
                            </td>
                            <td>
                              <div className={styles.tokens}>
                                {copy.inputTokens.replace(
                                  "{n}",
                                  formatNumber(event.input_tokens),
                                )}
                              </div>
                              <div className={styles.tokens}>
                                {copy.outputTokens.replace(
                                  "{n}",
                                  formatNumber(event.output_tokens),
                                )}
                              </div>
                              {event.cached_input_tokens ? (
                                <div className={styles.cached}>
                                  {copy.cachedTokens.replace(
                                    "{n}",
                                    formatNumber(event.cached_input_tokens),
                                  )}
                                </div>
                              ) : null}
                            </td>
                            <td>
                              <button
                                type="button"
                                className={styles.responseButton}
                                onClick={() =>
                                  setExpanded((current) => ({
                                    ...current,
                                    [event.request_id]: !current[event.request_id],
                                  }))
                                }
                                disabled={!response}
                              >
                                {isExpanded ? copy.hideResponse : copy.showResponse}
                              </button>
                            </td>
                          </tr>
                          {isExpanded ? (
                            <tr className={styles.responseRow}>
                              <td colSpan={7}>
                                <pre>{response || copy.noResponse}</pre>
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
