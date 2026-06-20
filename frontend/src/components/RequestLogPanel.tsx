import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  glossaryBridge,
  glossaryReviewBridge,
  translationBridge,
} from "@/bridge";
import type {
  RequestLogEvent,
  RequestLogPhase,
  RequestLogStatusFilter,
  TaskStatus,
} from "@/bridge/types";
import { useEscapeKey } from "@/hooks/useEscapeKey";
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
  const [visible, setVisible] = useState(false);
  const [events, setEvents] = useState<RequestLogEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [statusFilter, setStatusFilter] =
    useState<RequestLogStatusFilter>("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const eventCountRef = useRef(0);

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
  const phaseLabels = useMemo<Record<RequestLogPhase, string>>(
    () => ({
      sent: copy.phaseSent,
      headers_received: copy.phaseHeadersReceived,
      first_token: copy.phaseFirstToken,
      streaming: copy.phaseStreaming,
      validation: copy.phaseValidation,
      completed: copy.phaseCompleted,
      failed: copy.phaseFailed,
      cancelled: copy.phaseCancelled,
    }),
    [copy],
  );

  const loadEvents = useCallback(async (append = false) => {
    if (!taskId) {
      setEvents([]);
      setTotal(0);
      setTruncated(false);
      setError("");
      return;
    }
    setLoading(true);
    try {
      const result = await BRIDGES[kind].readRequestEvents(taskId, {
        limit: LIMIT,
        offset: append ? eventCountRef.current : 0,
        status: statusFilter,
      });
      setEvents((current) =>
        append ? [...current, ...result.events] : result.events,
      );
      setTotal(result.total);
      setTruncated(Boolean(result.truncated));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [kind, statusFilter, taskId]);

  useEffect(() => {
    eventCountRef.current = events.length;
  }, [events.length]);

  useEffect(() => {
    if (visible) void loadEvents();
  }, [loadEvents, taskStatus, visible]);

  useEffect(() => {
    if (!visible || !taskId || isTerminalTaskStatus(taskStatus)) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void loadEvents();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [loadEvents, taskId, taskStatus, visible]);

  useEscapeKey(() => setVisible(false), visible);

  const handleToggle = (next: boolean) => {
    setVisible(next);
    if (next) void loadEvents();
  };

  const handleStatusFilter = (next: RequestLogStatusFilter) => {
    setStatusFilter(next);
    setExpanded({});
  };

  return (
    <>
      <section className={styles.launcher} aria-label={copy.title}>
        <ToggleSwitch
          label={copy.toggle}
          checked={visible}
          onChange={handleToggle}
        />
      </section>

      {visible ? (
        <div
          className={styles.overlay}
          role="dialog"
          aria-modal="true"
          aria-label={copy.title}
          onClick={() => setVisible(false)}
        >
          <div className={styles.modal} onClick={(event) => event.stopPropagation()}>
            <div className={styles.header}>
              <div className={styles.heading}>
                <h2>{copy.title}</h2>
                <p>{copy.subtitle}</p>
              </div>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.refresh}
                  onClick={() => void loadEvents()}
                  disabled={loading}
                >
                  {loading ? copy.loading : copy.refresh}
                </button>
                <button
                  type="button"
                  className={styles.closeButton}
                  onClick={() => setVisible(false)}
                >
                  {copy.close}
                </button>
              </div>
            </div>

            <div className={styles.body}>
              {!taskId ? <div className={styles.empty}>{copy.emptyNoTask}</div> : null}
              {taskId && error ? (
                <div className={styles.error}>
                  {copy.errorPrefix} {error}
                </div>
              ) : null}
              {taskId && !error ? (
                <div className={styles.toolbar}>
                  <div className={styles.filters} aria-label={copy.filterLabel}>
                    {(
                      [
                        ["all", copy.filterAll],
                        ["failed", copy.filterFailed],
                        ["completed", copy.filterCompleted],
                        ["running", copy.filterRunning],
                        ["cancelled", copy.filterCancelled],
                      ] as const
                    ).map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        className={`${styles.filterButton} ${
                          statusFilter === value ? styles.filterButtonActive : ""
                        }`.trim()}
                        onClick={() => handleStatusFilter(value)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                  <div className={styles.meta}>
                    {(truncated ? copy.showingRecent : copy.showing)
                      .replace("{shown}", String(events.length))
                      .replace("{total}", String(total))}
                    {truncated ? ` · ${copy.truncated}` : ""}
                  </div>
                </div>
              ) : null}
              {taskId && !error && events.length === 0 ? (
                <div className={styles.empty}>
                  {loading ? copy.loading : copy.empty}
                </div>
              ) : null}
              {events.length > 0 ? (
                <>
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
                          const response =
                            event.response_text ||
                            event.partial_response_text ||
                            event.error ||
                            "";
                          const statusClass = statusClasses[event.status] ?? "";
                          const phaseLabel = event.phase
                            ? phaseLabels[event.phase] ?? event.phase
                            : "";
                          const lastActivity = event.last_activity_at
                            ? copy.lastActivity.replace(
                                "{time}",
                                formatTime(event.last_activity_at),
                              )
                            : "";
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
                                  {phaseLabel ? (
                                    <div className={styles.phase}>{phaseLabel}</div>
                                  ) : null}
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
                                  {lastActivity ? (
                                    <div className={styles.requestMeta}>
                                      {lastActivity}
                                    </div>
                                  ) : null}
                                </td>
                                <td>
                                  <div className={styles.model}>
                                    {event.model_id || "-"}
                                  </div>
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
                                  {event.usage_estimated ? (
                                    <div className={styles.estimated}>
                                      {copy.estimatedTokens}
                                    </div>
                                  ) : null}
                                  {event.response_chars ? (
                                    <div className={styles.tokens}>
                                      {copy.responseChars.replace(
                                        "{n}",
                                        formatNumber(event.response_chars),
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
                                        [event.request_id]:
                                          !current[event.request_id],
                                      }))
                                    }
                                    disabled={!response}
                                  >
                                    {isExpanded
                                      ? copy.hideResponse
                                      : copy.showResponse}
                                  </button>
                                </td>
                              </tr>
                              {isExpanded ? (
                                <tr className={styles.responseRow}>
                                  <td colSpan={7}>
                                    <pre>
                                      {event.response_text
                                        ? response
                                        : event.partial_response_text
                                          ? `${copy.partialResponse}\n\n${response}`
                                          : response || copy.noResponse}
                                    </pre>
                                  </td>
                                </tr>
                              ) : null}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  {events.length < total || truncated ? (
                    <button
                      type="button"
                      className={styles.loadMore}
                      onClick={() => void loadEvents(true)}
                      disabled={loading}
                    >
                      {loading ? copy.loading : copy.loadOlder}
                    </button>
                  ) : null}
                </>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
