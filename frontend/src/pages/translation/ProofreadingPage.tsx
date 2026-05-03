import { useEffect, useMemo, useRef, useState } from "react";
import { format, useMessages } from "@/locales";
import {
  BridgeError,
  proofreadingBridge,
  type ProofreadingItem,
  type ProofreadingSnapshot,
  type TaskHeader,
} from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import styles from "./ProofreadingPage.module.css";

type FeedbackKind = "info" | "error" | "success";
interface Feedback {
  kind: FeedbackKind;
  text: string;
}

export function ProofreadingPage() {
  const messages = useMessages();
  const m = messages.translation.proofreadingPage;

  const [tasks, setTasks] = useState<TaskHeader[] | null>(null);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<ProofreadingSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(
    null,
  );
  const [draftDst, setDraftDst] = useState<string>("");
  const [search, setSearch] = useState("");
  const [onlyLowConf, setOnlyLowConf] = useState(false);
  const [savedTick, setSavedTick] = useState(0);
  const [regenerating, setRegenerating] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [inflightRetranslates, setInflightRetranslates] = useState<
    Record<string, string>
  >({});
  const tableBodyRef = useRef<HTMLDivElement | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(600);

  // Initial: load task list.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    proofreadingBridge
      .listTasks()
      .then((res) => {
        if (cancelled) return;
        setTasks(res.tasks);
        if (res.tasks.length > 0) {
          setActiveTaskId(res.tasks[0].id);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setFeedback({
          kind: "error",
          text: BridgeError.isBridgeError(err)
            ? `${err.code}: ${err.message}`
            : String(err),
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Load snapshot when active task changes.
  useEffect(() => {
    if (!activeTaskId) {
      setSnapshot(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setSelectedSegmentId(null);
    proofreadingBridge
      .loadSnapshot(activeTaskId)
      .then((next) => {
        if (cancelled) return;
        setSnapshot(next);
        setSelectedSegmentId(next.items[0]?.segment_id ?? null);
      })
      .catch((err) => {
        if (cancelled) return;
        setFeedback({
          kind: "error",
          text: BridgeError.isBridgeError(err)
            ? `${err.code}: ${err.message}`
            : String(err),
        });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTaskId]);

  // Sync draft when selection changes.
  const selectedItem = useMemo<ProofreadingItem | null>(() => {
    if (!snapshot || !selectedSegmentId) return null;
    return (
      snapshot.items.find((item) => item.segment_id === selectedSegmentId) ??
      null
    );
  }, [snapshot, selectedSegmentId]);

  useEffect(() => {
    setDraftDst(selectedItem?.dst ?? "");
  }, [selectedItem]);

  const filteredItems = useMemo(() => {
    if (!snapshot) return [] as ProofreadingItem[];
    const q = search.trim().toLowerCase();
    return snapshot.items.filter((item) => {
      if (onlyLowConf && !item.low_confidence) return false;
      if (!q) return true;
      return (
        item.src.toLowerCase().includes(q) || item.dst.toLowerCase().includes(q)
      );
    });
  }, [snapshot, search, onlyLowConf]);

  const dirty = selectedItem !== null && draftDst !== (selectedItem?.dst ?? "");

  const ROW_HEIGHT = 48;
  const OVERSCAN = 8;
  const startIndex = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const endIndex = Math.min(
    filteredItems.length,
    Math.ceil((scrollTop + viewportHeight) / ROW_HEIGHT) + OVERSCAN,
  );
  const visibleItems = filteredItems.slice(startIndex, endIndex);

  useEffect(() => {
    const el = tableBodyRef.current;
    if (!el) return;
    const update = () => setViewportHeight(el.clientHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const handleSave = async () => {
    if (!activeTaskId || !selectedItem || !dirty) return;
    setFeedback(null);
    try {
      await proofreadingBridge.updateSegment(
        activeTaskId,
        selectedItem.segment_id,
        draftDst,
      );
      // Patch the in-memory snapshot so the table reflects the edit
      // immediately without re-loading the entire task.
      setSnapshot((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.map((item) =>
            item.segment_id === selectedItem.segment_id
              ? { ...item, dst: draftDst }
              : item,
          ),
        };
      });
      setSavedTick((t) => t + 1);
    } catch (err) {
      setFeedback({
        kind: "error",
        text: BridgeError.isBridgeError(err)
          ? `${err.code}: ${err.message}`
          : String(err),
      });
    }
  };

  const handleRegenerate = async () => {
    if (!activeTaskId || regenerating) return;
    setRegenerating(true);
    setFeedback(null);
    try {
      const result = await proofreadingBridge.regenerateOutputs(activeTaskId);
      const total =
        result.translated_files.length + result.bilingual_files.length;
      setFeedback({
        kind: "success",
        text: format(m.regenerateSuccess, { n: total }),
      });
    } catch (err) {
      setFeedback({
        kind: "error",
        text: format(m.regenerateFailed, {
          reason: BridgeError.isBridgeError(err)
            ? `${err.code}: ${err.message}`
            : String(err),
        }),
      });
    } finally {
      setRegenerating(false);
    }
  };

  const pollRetranslate = (segmentId: string, requestId: string) => {
    const startedAt = Date.now();
    const tick = async () => {
      try {
        const status = await proofreadingBridge.retranslateStatus(requestId);
        if (status.status === "completed") {
          setSnapshot((prev) =>
            prev
              ? {
                  ...prev,
                  items: prev.items.map((it) =>
                    it.segment_id === segmentId
                      ? { ...it, dst: status.result_dst }
                      : it,
                  ),
                }
              : prev,
          );
          if (selectedSegmentId === segmentId) setDraftDst(status.result_dst);
          setFeedback({ kind: "success", text: m.retranslateSuccess });
          finish();
          return;
        }
        if (status.status === "stale") {
          setFeedback({ kind: "info", text: m.retranslateStale });
          finish();
          return;
        }
        if (status.status === "failed") {
          setFeedback({
            kind: "error",
            text: format(m.retranslateFailed, { reason: status.error }),
          });
          finish();
          return;
        }
      } catch (err) {
        setFeedback({
          kind: "error",
          text: format(m.retranslateFailed, {
            reason: BridgeError.isBridgeError(err)
              ? `${err.code}: ${err.message}`
              : String(err),
          }),
        });
        finish();
        return;
      }
      if (Date.now() - startedAt > 60_000) {
        setFeedback({ kind: "error", text: m.retranslateTimeout });
        finish();
        return;
      }
      setTimeout(tick, 500);
    };
    const finish = () => {
      setInflightRetranslates((prev) => {
        const next = { ...prev };
        delete next[segmentId];
        return next;
      });
    };
    void tick();
  };

  const handleRetranslate = async () => {
    if (!activeTaskId || !selectedItem) return;
    if (inflightRetranslates[selectedItem.segment_id]) return;
    setFeedback(null);
    try {
      const { request_id } = await proofreadingBridge.retranslateSegment(
        activeTaskId,
        selectedItem.segment_id,
      );
      setInflightRetranslates((prev) => ({
        ...prev,
        [selectedItem.segment_id]: request_id,
      }));
      pollRetranslate(selectedItem.segment_id, request_id);
    } catch (err) {
      const text = BridgeError.isBridgeError(err)
        ? err.code === "bridge.conflict"
          ? m.retranslateRejectedRunning
          : `${err.code}: ${err.message}`
        : String(err);
      setFeedback({ kind: "error", text });
    }
  };

  if (tasks !== null && tasks.length === 0) {
    return (
      <Panel title={m.title} subtitle={m.sub}>
        <div className={styles.empty}>{m.noTasks}</div>
      </Panel>
    );
  }

  const lowConfCount =
    snapshot?.items.filter((item) => item.low_confidence).length ?? 0;
  const totalCount = snapshot?.items.length ?? 0;

  return (
    <Panel title={m.title} subtitle={m.sub}>
      <div className={styles.headerRow}>
        <select
          className={styles.taskSelect}
          value={activeTaskId ?? ""}
          onChange={(e) => setActiveTaskId(e.target.value || null)}
          aria-label={m.taskPicker}
        >
          {(tasks ?? []).map((task) => (
            <option key={task.id} value={task.id}>
              {task.id} · {task.status}
            </option>
          ))}
        </select>
        <span className={styles.grow}>
          {snapshot ? (
            <span className={styles.editorHint}>
              {format(m.taskFolderHint, { path: snapshot.output_dir })}
            </span>
          ) : null}
        </span>
        <Pill
          onClick={() => void handleRegenerate()}
          disabled={regenerating || !activeTaskId}
        >
          {regenerating ? m.regenerating : m.regenerateAction}
        </Pill>
      </div>

      <div className={styles.toggleRow}>
        <span>
          {format(m.stats.total, { n: totalCount })} ·{" "}
          {format(m.stats.lowConfidence, { n: lowConfCount })}
        </span>
        <label
          style={{ display: "flex", alignItems: "center", gap: 6 }}
          title={m.filterOnlyLowConfidence}
        >
          <input
            type="checkbox"
            checked={onlyLowConf}
            onChange={(e) => setOnlyLowConf(e.target.checked)}
          />
          {m.filterOnlyLowConfidence}
        </label>
      </div>

      <div style={{ marginBottom: 8 }}>
        <input
          type="text"
          className={styles.searchInput}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={m.filterPlaceholder}
        />
      </div>

      <div className={styles.layout}>
        <div className={styles.tableContainer}>
          <div className={styles.tableHead}>
            <span>{m.columns.index}</span>
            <span>{m.columns.src}</span>
            <span>{m.columns.dst}</span>
            <span>{m.columns.status}</span>
          </div>
          <div
            className={styles.tableBody}
            ref={tableBodyRef}
            onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
          >
            {loading ? (
              <div className={styles.empty}>{m.loading}</div>
            ) : filteredItems.length === 0 ? (
              <div className={styles.empty}>{m.empty}</div>
            ) : (
              <div
                style={{
                  height: filteredItems.length * ROW_HEIGHT,
                  position: "relative",
                }}
              >
                {visibleItems.map((item, i) => {
                  const active = item.segment_id === selectedSegmentId;
                  const status = item.dst
                    ? item.low_confidence
                      ? "low"
                      : "ok"
                    : "empty";
                  return (
                    <div
                      key={item.segment_id}
                      className={`${styles.row} ${active ? styles.rowActive : ""}`.trim()}
                      style={{
                        position: "absolute",
                        top: (startIndex + i) * ROW_HEIGHT,
                        left: 0,
                        right: 0,
                      }}
                      onClick={() => setSelectedSegmentId(item.segment_id)}
                    >
                      <span
                        className={`${styles.cell} ${styles.cellIndex}`.trim()}
                      >
                        {item.segment_id}
                      </span>
                      <span className={styles.cell}>{item.src}</span>
                      <span className={styles.cell}>{item.dst || "—"}</span>
                      <span>
                        <span
                          className={`${styles.statusChip} ${
                            status === "low"
                              ? styles.statusLow
                              : status === "empty"
                                ? styles.statusEmpty
                                : styles.statusOk
                          }`}
                        >
                          {status === "low"
                            ? m.statusLowConfidence
                            : status === "empty"
                              ? m.statusEmpty
                              : m.statusOk}
                        </span>
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {selectedItem ? (
          <div className={styles.editor}>
            <div>
              <div className={styles.label}>{m.editorSrcLabel}</div>
              <div className={styles.editorSrc}>{selectedItem.src}</div>
            </div>
            <div>
              <div className={styles.label}>{m.editorDstLabel}</div>
              <textarea
                className={styles.editorTextarea}
                value={draftDst}
                onChange={(e) => setDraftDst(e.target.value)}
              />
            </div>
            <div className={styles.editorActions}>
              <span className={styles.editorHint}>
                {dirty ? (
                  <span className={styles.dirty}>{m.editorDirty}</span>
                ) : savedTick > 0 ? (
                  m.editorSavedHint
                ) : null}
              </span>
              <span style={{ display: "flex", gap: 8 }}>
                <Pill
                  variant="ghost"
                  onClick={() => void handleRetranslate()}
                  disabled={Boolean(
                    inflightRetranslates[selectedItem.segment_id],
                  )}
                >
                  {inflightRetranslates[selectedItem.segment_id]
                    ? m.retranslating
                    : m.retranslateAction}
                </Pill>
                <Pill onClick={() => void handleSave()} disabled={!dirty}>
                  {m.editorSaveAction}
                </Pill>
              </span>
            </div>
          </div>
        ) : (
          <div className={styles.editorEmpty}>{m.editorEmpty}</div>
        )}
      </div>

      {feedback ? (
        <div
          className={`${styles.banner} ${
            feedback.kind === "error"
              ? styles.bannerError
              : feedback.kind === "success"
                ? styles.bannerSuccess
                : ""
          }`}
        >
          {feedback.text}
        </div>
      ) : null}
    </Panel>
  );
}
