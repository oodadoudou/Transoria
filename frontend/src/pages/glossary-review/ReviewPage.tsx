import { useEffect, useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";
import { BridgeError, glossaryReviewBridge, type GlossaryReviewFinalRow, type GlossaryReviewFinalSheet, type TaskHeader } from "@/bridge";
import { format, useMessages } from "@/locales";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import styles from "./ReviewPage.module.css";
import { ImportFinalGlossaryConfirmModal } from "./ImportFinalGlossaryConfirmModal";
import {
  importFinalGlossaryToTranslation,
  type ImportFinalGlossaryMode,
} from "./importFinalGlossary";

type FeedbackKind = "error" | "success";
type SortKey = "row_index" | "src" | "dst" | "info" | "frequency";
type SortDirection = "asc" | "desc";

interface SortState {
  key: SortKey;
  direction: SortDirection;
}

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
  const runLabels = messages.glossaryReview.run;
  const [tasks, setTasks] = useState<TaskHeader[] | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [sheet, setSheet] = useState<GlossaryReviewFinalSheet | null>(null);
  const [selectedRowIndex, setSelectedRowIndex] = useState<number | null>(null);
  const [selectedRowIndices, setSelectedRowIndices] = useState<ReadonlySet<number>>(
    () => new Set(),
  );
  const [selectionAnchor, setSelectionAnchor] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft>({ src: "", dst: "", info: "" });
  const [query, setQuery] = useState("");
  const [sortState, setSortState] = useState<SortState | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [importingFinal, setImportingFinal] = useState(false);
  const [importDecision, setImportDecision] = useState<{
    outputPath: string;
    existingCount: number;
  } | null>(null);
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
      setSelectedRowIndices(new Set());
      setSelectionAnchor(null);
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
        setSelectedRowIndices(first ? new Set([first.row_index]) : new Set());
        setSelectionAnchor(first?.row_index ?? null);
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
    const filtered = !q
      ? sheet.rows
      : sheet.rows.filter((row) =>
          [row.src, row.dst, row.info].join("\n").toLowerCase().includes(q),
        );
    if (!sortState) return filtered;
    const direction = sortState.direction === "asc" ? 1 : -1;
    const valueFor = (row: GlossaryReviewFinalRow): number | string => {
      switch (sortState.key) {
        case "row_index":
          return row.row_index;
        case "src":
          return row.src.toLowerCase();
        case "dst":
          return row.dst.toLowerCase();
        case "info":
          return row.info.toLowerCase();
        case "frequency":
          return row.frequency;
      }
    };
    return [...filtered].sort((a, b) => {
      const av = valueFor(a);
      const bv = valueFor(b);
      if (av < bv) return -direction;
      if (av > bv) return direction;
      return a.row_index - b.row_index;
    });
  }, [query, sheet, sortState]);

  const toggleSort = (key: SortKey) => {
    setSortState((current) => {
      if (current?.key !== key) return { key, direction: "asc" };
      if (current.direction === "asc") return { key, direction: "desc" };
      return null;
    });
  };

  const dirty =
    selected !== null &&
    (draft.src !== selected.src ||
      draft.dst !== selected.dst ||
      draft.info !== selected.info);

  const selectRow = (row: GlossaryReviewFinalRow) => {
    setSelectedRowIndex(row.row_index);
    setSelectedRowIndices(new Set([row.row_index]));
    setSelectionAnchor(row.row_index);
    setDraft(rowToDraft(row));
  };

  const selectPrimaryRow = (row: GlossaryReviewFinalRow | null) => {
    setSelectedRowIndex(row?.row_index ?? null);
    setDraft(row ? rowToDraft(row) : { src: "", dst: "", info: "" });
  };

  const handleRowClick = (
    event: MouseEvent<HTMLElement>,
    row: GlossaryReviewFinalRow,
    rowPosition: number,
  ) => {
    if (event.shiftKey && selectionAnchor !== null) {
      const anchorPosition = rows.findIndex(
        (item) => item.row_index === selectionAnchor,
      );
      const start = Math.min(anchorPosition >= 0 ? anchorPosition : rowPosition, rowPosition);
      const end = Math.max(anchorPosition >= 0 ? anchorPosition : rowPosition, rowPosition);
      const range = rows.slice(start, end + 1).map((item) => item.row_index);
      setSelectedRowIndices((current) => {
        const next = event.metaKey || event.ctrlKey ? new Set(current) : new Set<number>();
        range.forEach((rowIndex) => next.add(rowIndex));
        return next;
      });
      selectPrimaryRow(row);
      return;
    }
    if (event.metaKey || event.ctrlKey) {
      const next = new Set(selectedRowIndices);
      if (next.has(row.row_index)) next.delete(row.row_index);
      else next.add(row.row_index);
      const nextPrimary =
        next.has(row.row_index)
          ? row
          : rows.find((item) => next.has(item.row_index)) ?? null;
      setSelectedRowIndices(next);
      selectPrimaryRow(nextPrimary);
      setSelectionAnchor(row.row_index);
      return;
    }
    selectRow(row);
  };

  const handleSelectAllRows = () => {
    if (rows.length === 0) return;
    const allSelected = rows.every((row) => selectedRowIndices.has(row.row_index));
    if (allSelected) {
      setSelectedRowIndices(new Set());
      setSelectedRowIndex(null);
      setSelectionAnchor(null);
      setDraft({ src: "", dst: "", info: "" });
      return;
    }
    setSelectedRowIndices(new Set(rows.map((row) => row.row_index)));
    setSelectionAnchor(rows[0].row_index);
    selectPrimaryRow(selected ?? rows[0]);
  };

  const handleTableKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "a") {
      event.preventDefault();
      if (rows.length === 0) return;
      setSelectedRowIndices(new Set(rows.map((row) => row.row_index)));
      setSelectionAnchor(rows[0].row_index);
      selectPrimaryRow(selected ?? rows[0]);
    }
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

  const deleteSelectedRows = async () => {
    if (!activeTaskId || selectedRowIndices.size === 0) return;
    setSaving(true);
    setFeedback(null);
    try {
      const deletedCount = selectedRowIndices.size;
      const next = await glossaryReviewBridge.deleteFinalRows(
        activeTaskId,
        [...selectedRowIndices],
      );
      setSheet(next);
      const nextSelected =
        next.rows.find((row) => row.row_index === selectedRowIndex) ??
        next.rows[0] ??
        null;
      selectPrimaryRow(nextSelected);
      setSelectedRowIndices(nextSelected ? new Set([nextSelected.row_index]) : new Set());
      setSelectionAnchor(nextSelected?.row_index ?? null);
      setFeedback({
        kind: "success",
        text:
          deletedCount === 1
            ? labels.deleted
            : format(labels.deletedMany, { n: deletedCount }),
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

  const importFinalFromPath = async (
    outputPath: string,
    mode?: ImportFinalGlossaryMode,
  ) => {
    setImportingFinal(true);
    setFeedback(null);
    try {
      const result = await importFinalGlossaryToTranslation(outputPath, {
        empty: runLabels.importFinalEmpty,
      }, mode);
      if (result.status === "needs_decision") {
        setImportDecision({
          outputPath,
          existingCount: result.existingCount,
        });
        return;
      }
      setImportDecision(null);
      setFeedback({
        kind: "success",
        text: format(runLabels.importFinalSuccess, { n: result.count }),
      });
    } catch (error) {
      setFeedback({
        kind: "error",
        text: format(runLabels.importFinalFailed, {
          reason: errorText(error),
        }),
      });
    } finally {
      setImportingFinal(false);
    }
  };

  const handleImportFinal = async () => {
    if (!activeTaskId || !sheet || importingFinal) return;
    setImportingFinal(true);
    setFeedback(null);
    try {
      let outputPath = sheet.path;
      if (dirty && selected) {
        const next = await glossaryReviewBridge.updateFinalRow(activeTaskId, {
          row_index: selected.row_index,
          src: draft.src,
          dst: draft.dst,
          info: draft.info,
          delete: false,
        });
        setSheet(next);
        outputPath = next.path;
        const nextSelected =
          next.rows.find((row) => row.row_index === selected.row_index) ??
          next.rows[0] ??
          null;
        setSelectedRowIndex(nextSelected?.row_index ?? null);
        setDraft(
          nextSelected ? rowToDraft(nextSelected) : { src: "", dst: "", info: "" },
        );
      }
      await importFinalFromPath(outputPath);
    } catch (error) {
      setFeedback({
        kind: "error",
        text: format(runLabels.importFinalFailed, {
          reason: errorText(error),
        }),
      });
    } finally {
      setImportingFinal(false);
    }
  };

  const selectedCount = selectedRowIndices.size;
  const allVisibleSelected =
    rows.length > 0 && rows.every((row) => selectedRowIndices.has(row.row_index));

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
        <Pill
          disabled={!sheet || importingFinal || saving}
          onClick={() => void handleImportFinal()}
        >
          {importingFinal
            ? runLabels.importingFinal
            : runLabels.importFinalToTranslation}
        </Pill>
      </div>

      <input
        className={styles.searchInput}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={labels.searchPlaceholder}
      />

      <div className={styles.layout}>
        <div
          className={styles.tableWrap}
          tabIndex={0}
          onKeyDown={handleTableKeyDown}
        >
          {loading ? (
            <div className={styles.empty}>{labels.loading}</div>
          ) : rows.length === 0 ? (
            <div className={styles.empty}>{labels.empty}</div>
          ) : (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.selectColumn}>
                    <input
                      type="checkbox"
                      checked={allVisibleSelected}
                      aria-label={labels.deleteSelected}
                      onChange={handleSelectAllRows}
                    />
                  </th>
                  <SortableTh
                    label={labels.columns.index}
                    sortKey="row_index"
                    sortState={sortState}
                    onSort={toggleSort}
                  />
                  <SortableTh
                    label={labels.columns.src}
                    sortKey="src"
                    sortState={sortState}
                    onSort={toggleSort}
                  />
                  <SortableTh
                    label={labels.columns.dst}
                    sortKey="dst"
                    sortState={sortState}
                    onSort={toggleSort}
                  />
                  <SortableTh
                    label={labels.columns.info}
                    sortKey="info"
                    sortState={sortState}
                    onSort={toggleSort}
                  />
                  <SortableTh
                    label={labels.columns.frequency}
                    sortKey="frequency"
                    sortState={sortState}
                    onSort={toggleSort}
                  />
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr
                    key={row.row_index}
                    className={`${selectedRowIndices.has(row.row_index) ? styles.selectedRow : ""} ${
                      row.row_index === selectedRowIndex ? styles.activeRow : ""
                    }`.trim()}
                    onClick={(event) => handleRowClick(event, row, index)}
                  >
                    <td className={styles.selectColumn}>
                      <input
                        type="checkbox"
                        checked={selectedRowIndices.has(row.row_index)}
                        readOnly
                        onClick={(event) => {
                          event.stopPropagation();
                          handleRowClick(event, row, index);
                        }}
                      />
                    </td>
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
              <span className={styles.dirty}>
                {dirty
                  ? labels.dirty
                  : selectedCount > 1
                    ? format(labels.selectedCount, { n: selectedCount })
                    : ""}
              </span>
              <span style={{ display: "flex", gap: 8 }}>
                <Pill
                  variant="ghost"
                  disabled={saving || selectedCount === 0}
                  onClick={() => void deleteSelectedRows()}
                >
                  {selectedCount > 1 ? labels.deleteSelected : labels.delete}
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
      {importDecision ? (
        <ImportFinalGlossaryConfirmModal
          existingCount={importDecision.existingCount}
          labels={{
            title: runLabels.importFinalDecisionTitle,
            body: runLabels.importFinalDecisionBody,
            replaceBadge: runLabels.importFinalReplaceBadge,
            replaceAction: runLabels.importFinalReplaceAction,
            replaceHint: runLabels.importFinalReplaceHint,
            appendBadge: runLabels.importFinalAppendBadge,
            appendAction: runLabels.importFinalAppendAction,
            appendHint: runLabels.importFinalAppendHint,
            cancelAction: runLabels.importFinalCancelAction,
          }}
          onPick={(mode) => {
            const { outputPath } = importDecision;
            setImportDecision(null);
            void importFinalFromPath(outputPath, mode);
          }}
          onCancel={() => setImportDecision(null)}
        />
      ) : null}
    </Panel>
  );
}

function errorText(error: unknown): string {
  if (BridgeError.isBridgeError(error)) return `${error.code}: ${error.message}`;
  return error instanceof Error ? error.message : String(error);
}

interface SortableThProps {
  label: string;
  sortKey: SortKey;
  sortState: SortState | null;
  onSort: (key: SortKey) => void;
}

function SortableTh({ label, sortKey, sortState, onSort }: SortableThProps) {
  const active = sortState?.key === sortKey;
  const indicator = !active ? "" : sortState.direction === "asc" ? " ↑" : " ↓";
  return (
    <th>
      <button
        type="button"
        className={`${styles.sortButton} ${active ? styles.sortActive : ""}`.trim()}
        onClick={() => onSort(sortKey)}
      >
        {label}
        {indicator}
      </button>
    </th>
  );
}
