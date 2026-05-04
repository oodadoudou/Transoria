import { useEffect, useMemo, useState } from "react";
import { BridgeError, glossaryReviewBridge, type GlossaryReviewFinalRow, type GlossaryReviewFinalSheet, type TaskHeader } from "@/bridge";
import { format, useMessages } from "@/locales";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import styles from "./ReviewPage.module.css";

type FeedbackKind = "error" | "success";

interface Feedback {
  kind: FeedbackKind;
  text: string;
}

interface Draft {
  src: string;
  dst: string;
  info: string;
}

function rowToDraft(row: GlossaryReviewFinalRow): Draft {
  return { src: row.src, dst: row.dst, info: row.info };
}

export function ReviewPage() {
  const messages = useMessages();
  const labels = messages.glossaryReview.review;
  const [tasks, setTasks] = useState<TaskHeader[] | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [sheet, setSheet] = useState<GlossaryReviewFinalSheet | null>(null);
  const [selectedRowIndex, setSelectedRowIndex] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>({ src: "", dst: "", info: "" });
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    glossaryReviewBridge
      .listRecentTasks(20)
      .then((res) => {
        if (cancelled) return;
        const completed = res.tasks.filter((task) => task.status === "completed");
        setTasks(completed);
        setActiveTaskId(completed[0]?.id ?? null);
      })
      .catch((error) => {
        if (!cancelled) setFeedback({ kind: "error", text: String(error) });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!activeTaskId) {
      setSheet(null);
      setSelectedRowIndex(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setFeedback(null);
    glossaryReviewBridge
      .readFinal(activeTaskId)
      .then((next) => {
        if (cancelled) return;
        setSheet(next);
        const first = next.rows[0] ?? null;
        setSelectedRowIndex(first?.row_index ?? null);
        setDraft(first ? rowToDraft(first) : { src: "", dst: "", info: "" });
      })
      .catch((error) => {
        if (!cancelled) setFeedback({ kind: "error", text: errorText(error) });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTaskId]);

  const selected = useMemo(() => {
    if (!sheet || selectedRowIndex === null) return null;
    return sheet.rows.find((row) => row.row_index === selectedRowIndex) ?? null;
  }, [selectedRowIndex, sheet]);

  const rows = useMemo(() => {
    if (!sheet) return [] as GlossaryReviewFinalRow[];
    const q = query.trim().toLowerCase();
    if (!q) return sheet.rows;
    return sheet.rows.filter((row) =>
      [row.src, row.dst, row.info].join("\n").toLowerCase().includes(q),
    );
  }, [query, sheet]);

  const dirty =
    selected !== null &&
    (draft.src !== selected.src ||
      draft.dst !== selected.dst ||
      draft.info !== selected.info);

  const selectRow = (row: GlossaryReviewFinalRow) => {
    setSelectedRowIndex(row.row_index);
    setDraft(rowToDraft(row));
  };

  const save = async (deleteRow: boolean) => {
    if (!activeTaskId || !selected) return;
    setSaving(true);
    setFeedback(null);
    try {
      const next = await glossaryReviewBridge.updateFinalRow(activeTaskId, {
        row_index: selected.row_index,
        src: draft.src,
        dst: draft.dst,
        info: draft.info,
        delete: deleteRow,
      });
      setSheet(next);
      const nextSelected =
        next.rows.find((row) => row.row_index === selected.row_index) ??
        next.rows[0] ??
        null;
      setSelectedRowIndex(nextSelected?.row_index ?? null);
      setDraft(nextSelected ? rowToDraft(nextSelected) : { src: "", dst: "", info: "" });
      setFeedback({
        kind: "success",
        text: deleteRow ? labels.deleted : labels.saved,
      });
    } catch (error) {
      setFeedback({
        kind: "error",
        text: format(labels.failed, { reason: errorText(error) }),
      });
    } finally {
      setSaving(false);
    }
  };

  if (tasks !== null && tasks.length === 0) {
    return (
      <Panel title={labels.title} subtitle={labels.sub}>
        <div className={styles.empty}>{labels.noTasks}</div>
      </Panel>
    );
  }

  return (
    <Panel title={labels.title} subtitle={labels.sub}>
      <div className={styles.headerRow}>
        <select
          className={styles.taskSelect}
          value={activeTaskId ?? ""}
          onChange={(event) => setActiveTaskId(event.target.value || null)}
          aria-label={labels.taskPicker}
        >
          {(tasks ?? []).map((task) => (
            <option key={task.id} value={task.id}>
              {task.id} · {task.status}
            </option>
          ))}
        </select>
        <span className={styles.hint}>
          {sheet ? format(labels.pathHint, { path: sheet.path }) : null}
        </span>
      </div>

      <input
        className={styles.searchInput}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={labels.searchPlaceholder}
      />

      <div className={styles.layout}>
        <div className={styles.tableWrap}>
          {loading ? (
            <div className={styles.empty}>{labels.loading}</div>
          ) : rows.length === 0 ? (
            <div className={styles.empty}>{labels.empty}</div>
          ) : (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>{labels.columns.index}</th>
                  <th>{labels.columns.src}</th>
                  <th>{labels.columns.dst}</th>
                  <th>{labels.columns.info}</th>
                  <th>{labels.columns.frequency}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.row_index}
                    className={
                      row.row_index === selectedRowIndex ? styles.activeRow : ""
                    }
                    onClick={() => selectRow(row)}
                  >
                    <td className={styles.mono}>{row.row_index}</td>
                    <td className={styles.truncate}>{row.src}</td>
                    <td className={styles.truncate}>{row.dst}</td>
                    <td className={styles.truncate}>{row.info}</td>
                    <td className={styles.mono}>{row.frequency || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {selected ? (
          <div className={styles.editor}>
            <div>
              <div className={styles.label}>{labels.srcLabel}</div>
              <textarea
                className={styles.textarea}
                value={draft.src}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, src: event.target.value }))
                }
              />
            </div>
            <div>
              <div className={styles.label}>{labels.dstLabel}</div>
              <textarea
                className={styles.textarea}
                value={draft.dst}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, dst: event.target.value }))
                }
              />
            </div>
            <div>
              <div className={styles.label}>{labels.infoLabel}</div>
              <input
                className={styles.field}
                value={draft.info}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, info: event.target.value }))
                }
              />
            </div>
            <div className={styles.actions}>
              <span className={styles.dirty}>{dirty ? labels.dirty : ""}</span>
              <span style={{ display: "flex", gap: 8 }}>
                <Pill
                  variant="ghost"
                  disabled={saving}
                  onClick={() => void save(true)}
                >
                  {labels.delete}
                </Pill>
                <Pill
                  disabled={saving || !dirty}
                  onClick={() => void save(false)}
                >
                  {labels.save}
                </Pill>
              </span>
            </div>
          </div>
        ) : (
          <div className={styles.empty}>{labels.editorEmpty}</div>
        )}
      </div>

      {feedback ? (
        <div
          className={`${styles.banner} ${
            feedback.kind === "error" ? styles.error : styles.success
          }`}
        >
          {feedback.text}
        </div>
      ) : null}
    </Panel>
  );
}

function errorText(error: unknown): string {
  if (BridgeError.isBridgeError(error)) return `${error.code}: ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}
