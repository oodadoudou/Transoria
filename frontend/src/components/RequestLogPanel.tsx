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

function mappedResponseRows(event: RequestLogEvent) {
  if (!event.segment_refs?.length) return [];
  const responseText = event.response_text || event.partial_response_text || "";
  const replies = new Map<string, string>();
  responseText.split(/\r?\n/).forEach((line) => {
    const candidate = line.trim();
    if (!candidate.startsWith("{") || !candidate.endsWith("}")) return;
    try {
      const decoded = JSON.parse(candidate) as Record<string, unknown>;
      Object.entries(decoded).forEach(([requestIndex, value]) => {
        replies.set(
          requestIndex,
          typeof value === "string" ? value : JSON.stringify(value),
        );
      });
    } catch {
      // Provider errors and diagnostic text remain available in the raw view.
    }
  });
  return event.segment_refs.flatMap((ref) => {
    const reply = replies.get(ref.request_index);
    return reply === undefined ? [] : [{ ...ref, reply }];
  });
}

export function RequestLogPanel({
  kind,
  taskId,
  taskStatus,
  launcherVariant = "default",
}: {
  kind: RequestLogKind;
  taskId: string | null;
  taskStatus?: TaskStatus | null;
  launcherVariant?: "default" | "bare";
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
  const [copyFeedback, setCopyFeedback] = useState<{
    requestId: string;
    message: string;
  } | null>(null);
  const eventCountRef = useRef(0);
  const loadSequenceRef = useRef(0);
  const copyFeedbackTimerRef = useRef<number | null>(null);

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
    const loadSequence = ++loadSequenceRef.current;
    setLoading(true);
    try {
      const result = await BRIDGES[kind].readRequestEvents(taskId, {
        limit: LIMIT,
        offset: append ? eventCountRef.current : 0,
        status: statusFilter,
      });
      if (loadSequence !== loadSequenceRef.current) return;
      setEvents((current) =>
        append ? [...current, ...result.events] : result.events,
      );
      setTotal(result.total);
      setTruncated(Boolean(result.truncated));
      setError("");
    } catch (err) {
      if (loadSequence !== loadSequenceRef.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (loadSequence === loadSequenceRef.current) setLoading(false);
    }
  }, [kind, statusFilter, taskId]);

  useEffect(() => {
    eventCountRef.current = events.length;
  }, [events.length]);

  useEffect(
    () => () => {
      loadSequenceRef.current += 1;
      if (copyFeedbackTimerRef.current !== null) {
        window.clearTimeout(copyFeedbackTimerRef.current);
      }
    },
    [],
  );

  useEffect(() => {
    if (visible) void loadEvents();
  }, [loadEvents, taskStatus, visible]);

  useEffect(() => {
    if (!visible || !taskId || isTerminalTaskStatus(taskStatus)) {
      return undefined;
    }
    const timer = window.setInterval(() => {
      void loadEvents();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadEvents, taskId, taskStatus, visible]);

  useEscapeKey(() => setVisible(false), visible);

  const handleToggle = (next: boolean) => {
    setVisible(next);
    if (!next) loadSequenceRef.current += 1;
  };

  const handleStatusFilter = (next: RequestLogStatusFilter) => {
    if (next === statusFilter) return;
    loadSequenceRef.current += 1;
    eventCountRef.current = 0;
    setEvents([]);
    setTotal(0);
    setTruncated(false);
    setError("");
    setLoading(true);
    setStatusFilter(next);
    setExpanded({});
  };

  const copyText = useCallback(
    async (requestId: string, text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopyFeedback({ requestId, message: copy.copyDone });
      } catch {
        setCopyFeedback({ requestId, message: copy.copyFailed });
      }
      if (copyFeedbackTimerRef.current !== null) {
        window.clearTimeout(copyFeedbackTimerRef.current);
      }
      copyFeedbackTimerRef.current = window.setTimeout(() => {
        setCopyFeedback(null);
        copyFeedbackTimerRef.current = null;
      }, 1400);
    },
    [copy.copyDone, copy.copyFailed],
  );

  return (
    <>
      <section
        className={`${styles.launcher} ${
          launcherVariant === "bare" ? styles.launcherBare : ""
        }`.trim()}
        aria-label={copy.title}
      >
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
                          const requestTitle = event.label || event.subtask_id;
                          const responseText =
                            event.response_text || event.partial_response_text || "";
                          const errorText = event.error || "";
                          const response =
                            responseText || errorText;
                          const mappedRows = mappedResponseRows(event);
                          const expandedText = event.response_text
                            ? event.response_text
                            : event.partial_response_text
                              ? `${copy.partialResponse}\n\n${event.partial_response_text}`
                              : errorText || copy.noResponse;
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
                                  <div
                                    className={styles.requestLabel}
                                    title={requestTitle}
                                  >
                                    {requestTitle}
                                  </div>
                                  <div
                                    className={styles.requestMeta}
                                    title={event.subtask_id}
                                  >
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
                                  <div
                                    className={styles.model}
                                    title={event.model_id || undefined}
                                  >
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
                                    <div className={styles.responseTools}>
                                      {event.subtask_id ? (
                                        <button
                                          type="button"
                                          className={styles.copyButton}
                                          onClick={() =>
                                            void copyText(
                                              event.request_id,
                                              event.subtask_id,
                                            )
                                          }
                                        >
                                          {copy.copySubtaskId}
                                        </button>
                                      ) : null}
                                      {responseText ? (
                                        <button
                                          type="button"
                                          className={styles.copyButton}
                                          onClick={() =>
                                            void copyText(
                                              event.request_id,
                                              responseText,
                                            )
                                          }
                                        >
                                          {copy.copyResponse}
                                        </button>
                                      ) : null}
                                      {errorText ? (
                                        <button
                                          type="button"
                                          className={styles.copyButton}
                                          onClick={() =>
                                            void copyText(
                                              event.request_id,
                                              errorText,
                                            )
                                          }
                                        >
                                          {copy.copyError}
                                        </button>
                                      ) : null}
                                      {copyFeedback?.requestId ===
                                      event.request_id ? (
                                        <span className={styles.copyFeedback}>
                                          {copyFeedback.message}
                                        </span>
                                      ) : null}
                                    </div>
                                    {mappedRows.length > 0 ? (
                                      <>
                                        <div className={styles.mappedTableWrap}>
                                          <table className={styles.mappedTable}>
                                            <thead>
                                              <tr>
                                                <th>{copy.segmentId}</th>
                                                <th>{copy.requestIndex}</th>
                                                <th>{copy.modelReply}</th>
                                                <th>{copy.currentCache}</th>
                                              </tr>
                                            </thead>
                                            <tbody>
                                              {mappedRows.map((row) => (
                                                <tr
                                                  key={`${event.request_id}:${row.request_index}`}
                                                >
                                                  <td>
                                                    <button
                                                      type="button"
                                                      className={styles.segmentIdButton}
                                                      title={copy.copySegmentId}
                                                      onClick={() =>
                                                        void copyText(
                                                          event.request_id,
                                                          row.segment_id,
                                                        )
                                                      }
                                                    >
                                                      {row.segment_id}
                                                    </button>
                                                  </td>
                                                  <td className={styles.requestIndex}>
                                                    {row.request_index}
                                                  </td>
                                                  <td className={styles.modelReply}>
                                                    {row.reply}
                                                  </td>
                                                  <td>
                                                    <span
                                                      className={`${styles.cacheStatus} ${
                                                        row.cache_status === "matched"
                                                          ? styles.cacheMatched
                                                          : row.cache_status === "different"
                                                            ? styles.cacheDifferent
                                                            : styles.cacheMissing
                                                      }`.trim()}
                                                    >
                                                      {row.cache_status === "matched"
                                                        ? copy.cacheMatched
                                                        : row.cache_status === "different"
                                                          ? copy.cacheDifferent
                                                          : copy.cacheMissing}
                                                    </span>
                                                  </td>
                                                </tr>
                                              ))}
                                            </tbody>
                                          </table>
                                        </div>
                                        <details className={styles.rawResponse}>
                                          <summary>{copy.rawResponse}</summary>
                                          <pre>{expandedText}</pre>
                                        </details>
                                      </>
                                    ) : (
                                      <pre>{expandedText}</pre>
                                    )}
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
