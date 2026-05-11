import { useEffect, useMemo, useState, type MouseEvent } from "react";
import { format, useMessages } from "@/locales";
import {
  BridgeError,
  proofreadingBridge,
  type ProofreadingItem,
  type ProofreadingSnapshot,
  type TaskHeader,
} from "@/bridge";
import {
  useTaskStore,
  type ProofreadingFilterKey,
} from "@/store/useTaskStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { useVirtualWindow } from "@/hooks/useVirtualWindow";
import styles from "./ProofreadingPage.module.css";

type FeedbackKind = "info" | "error" | "success";
interface Feedback {
  kind: FeedbackKind;
  text: string;
}

interface ReplacementPlan {
  apply: (text: string) => string;
}

interface RegenerateFailedFile {
  path: string;
  reason: string;
  code?: string;
  details?: Record<string, unknown>;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function stringDetail(
  details: Record<string, unknown> | undefined,
  key: string,
  fallback = "",
): string {
  const value = details?.[key];
  return typeof value === "string" ? value : fallback;
}

function numberDetail(
  details: Record<string, unknown> | undefined,
  key: string,
): number {
  const value = details?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

// "Untranslated" covers two model failure shapes the user wants to spot
// quickly: empty translation and verbatim source echo (terminal source-
// fallback path). Trim before comparing so trailing whitespace doesn't
// hide an otherwise-identical echo.
function isUntranslated(item: ProofreadingItem): boolean {
  const dst = item.dst.trim();
  if (!dst) return true;
  return dst === item.src.trim();
}

function hasReason(item: ProofreadingItem, ...needles: string[]): boolean {
  const reasons = item.reasons ?? [];
  return reasons.some((reason) =>
    needles.every((needle) => reason.toLowerCase().includes(needle)),
  );
}

function riskRank(item: ProofreadingItem): number {
  if (item.tags?.includes("source_residue")) return 0;
  if (item.tags?.includes("possible_duplicate")) return 1;
  if (isUntranslated(item)) return 2;
  if (item.low_confidence) return 3;
  return 4;
}

function segmentSortKey(segmentId: string): [number, number] {
  const [file, segment] = segmentId.split(":", 2);
  const fileIndex = Number.parseInt(file ?? "0", 10);
  const segmentIndex = Number.parseInt(segment ?? "0", 10);
  return [
    Number.isFinite(fileIndex) ? fileIndex : 0,
    Number.isFinite(segmentIndex) ? segmentIndex : 0,
  ];
}

function compareSegmentIds(left: string, right: string): number {
  const [leftFile, leftSegment] = segmentSortKey(left);
  const [rightFile, rightSegment] = segmentSortKey(right);
  return leftFile - rightFile || leftSegment - rightSegment;
}

function formatRegenerateFailure(
  file: RegenerateFailedFile,
  messages: ReturnType<typeof useMessages>["translation"]["proofreadingPage"],
): string {
  let reason: string;
  if (file.code === "no_matching_translations") {
    reason = messages.regenerateFailureReasons.noMatchingTranslations;
  } else if (file.code === "missing_translations") {
    reason = format(messages.regenerateFailureReasons.missingTranslations, {
      missing: numberDetail(file.details, "missing_segments"),
    });
  } else if (file.code === "writer_error") {
    reason = format(messages.regenerateFailureReasons.writerError, {
      errorType: stringDetail(file.details, "error_type", file.reason),
    });
  } else {
    reason = format(messages.regenerateFailureReasons.unknown, {
      reason: file.reason,
    });
  }
  return `${file.path}: ${reason}`;
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
  const [selectedSegmentIds, setSelectedSegmentIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [selectionAnchorId, setSelectionAnchorId] = useState<string | null>(
    null,
  );
  const [draftDst, setDraftDst] = useState<string>("");
  const [search, setSearch] = useState("");
  const [replacementEnabled, setReplacementEnabled] = useState(false);
  const [replacementNeedle, setReplacementNeedle] = useState("");
  const [replacementValue, setReplacementValue] = useState("");
  const [replacementRegex, setReplacementRegex] = useState(false);
  const [replacing, setReplacing] = useState<"one" | "all" | null>(null);
  const consumeProofreadingLaunch = useTaskStore(
    (state) => state.consumeProofreadingLaunch,
  );
  const [filters, setFilters] = useState<ReadonlySet<ProofreadingFilterKey>>(
    () => new Set(),
  );
  const toggleFilter = (key: ProofreadingFilterKey) =>
    setFilters((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const [savedTick, setSavedTick] = useState(0);
  const [regenerating, setRegenerating] = useState<
    "translated" | "bilingual" | null
  >(null);
  const [regenerateFeedback, setRegenerateFeedback] =
    useState<Feedback | null>(null);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [inflightRetranslates, setInflightRetranslates] = useState<
    Record<string, string>
  >({});
  const [batchRetranslating, setBatchRetranslating] = useState(false);

  // Initial: load task list.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    proofreadingBridge
      .listTasks()
      .then((res) => {
        if (cancelled) return;
        setTasks(res.tasks);
        const launch = consumeProofreadingLaunch();
        if (res.tasks.length > 0) {
          const requested = launch.taskId
            ? res.tasks.find((task) => task.id === launch.taskId)
            : null;
          setActiveTaskId(requested?.id ?? res.tasks[0].id);
          if (launch.filters.length > 0) {
            setFilters(new Set(launch.filters));
          }
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
  }, [consumeProofreadingLaunch]);

  // Load snapshot when active task changes.
  useEffect(() => {
    if (!activeTaskId) {
      setSnapshot(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setSelectedSegmentId(null);
    setSelectedSegmentIds(new Set());
    setSelectionAnchorId(null);
    setRegenerateFeedback(null);
    proofreadingBridge
      .loadSnapshot(activeTaskId)
      .then((next) => {
        if (cancelled) return;
        setSnapshot(next);
        const firstId = next.items[0]?.segment_id ?? null;
        setSelectedSegmentId(firstId);
        setSelectionAnchorId(firstId);
        setSelectedSegmentIds(firstId ? new Set([firstId]) : new Set());
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
    return [...snapshot.items]
      .filter((item) => {
        if (filters.has("low_conf") && !item.low_confidence) return false;
        if (
          filters.has("source_residue") &&
          !item.tags?.includes("source_residue")
        )
          return false;
        if (
          filters.has("possible_duplicate") &&
          !item.tags?.includes("possible_duplicate")
        )
          return false;
        if (filters.has("untranslated") && !isUntranslated(item)) return false;
        if (
          filters.has("too_short") &&
          !hasReason(item, "length ratio", "< min")
        ) {
          return false;
        }
        if (
          filters.has("too_long") &&
          !hasReason(item, "length ratio", "> max")
        ) {
          return false;
        }
        if (
          filters.has("format_rescue") &&
          !hasReason(item, "positional_rescue_after_format_failure")
        ) {
          return false;
        }
        if (!q) return true;
        return (
          item.src.toLowerCase().includes(q) ||
          item.dst.toLowerCase().includes(q)
        );
      })
      .sort(
        (left, right) =>
          riskRank(left) - riskRank(right) ||
          compareSegmentIds(left.segment_id, right.segment_id),
      );
  }, [snapshot, search, filters]);

  const dirty = selectedItem !== null && draftDst !== (selectedItem?.dst ?? "");

  const ROW_HEIGHT = 48;
  const virtual = useVirtualWindow({
    count: filteredItems.length,
    rowHeight: ROW_HEIGHT,
  });
  const startIndex = virtual.startIndex;
  const endIndex = virtual.endIndex;
  const visibleItems = filteredItems.slice(startIndex, endIndex);
  const selectedCount = selectedSegmentIds.size;

  const handleSave = async () => {
    if (!activeTaskId || !selectedItem || !dirty) return;
    setFeedback(null);
    try {
      const updated = await proofreadingBridge.updateSegment(
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
              ? {
                  ...item,
                  dst: updated.dst,
                  low_confidence: updated.low_confidence,
                  tags: updated.tags,
                  reasons: updated.reasons,
                }
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

  const makeReplacementPlan = (): ReplacementPlan | null => {
    if (!replacementNeedle) {
      setFeedback({ kind: "error", text: m.replacementEmptyNeedle });
      return null;
    }
    if (replacementRegex) {
      try {
        const regex = new RegExp(replacementNeedle, "g");
        return {
          apply: (text) => {
            regex.lastIndex = 0;
            return text.replace(regex, replacementValue);
          },
        };
      } catch (err) {
        setFeedback({
          kind: "error",
          text: format(m.replacementInvalidRegex, {
            reason: err instanceof Error ? err.message : String(err),
          }),
        });
        return null;
      }
    }
    if (/\s/.test(replacementNeedle)) {
      const parts = replacementNeedle.trim().split(/\s+/).filter(Boolean);
      if (parts.length === 0) {
        setFeedback({ kind: "error", text: m.replacementEmptyNeedle });
        return null;
      }
      const regex = new RegExp(parts.map(escapeRegExp).join("\\s+"), "g");
      return {
        apply: (text) => {
          regex.lastIndex = 0;
          return text.replace(regex, replacementValue);
        },
      };
    }
    return {
      apply: (text) => text.split(replacementNeedle).join(replacementValue),
    };
  };

  const saveReplacement = async (segmentId: string, dst: string) => {
    if (!activeTaskId) return;
    const updated = await proofreadingBridge.updateSegment(
      activeTaskId,
      segmentId,
      dst,
    );
    setSnapshot((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        items: prev.items.map((item) =>
          item.segment_id === segmentId
            ? {
                ...item,
                dst: updated.dst,
                low_confidence: updated.low_confidence,
                tags: updated.tags,
                reasons: updated.reasons,
              }
            : item,
        ),
      };
    });
  };

  const handleReplaceSelected = async () => {
    if (!selectedItem) {
      setFeedback({ kind: "error", text: m.replacementNoSelection });
      return;
    }
    const plan = makeReplacementPlan();
    if (!plan) return;
    const next = plan.apply(draftDst);
    if (next === draftDst) {
      setFeedback({ kind: "info", text: m.replacementNoMatch });
      return;
    }
    setReplacing("one");
    setFeedback(null);
    try {
      await saveReplacement(selectedItem.segment_id, next);
      setDraftDst(next);
      setSavedTick((t) => t + 1);
      setFeedback({
        kind: "success",
        text: format(m.replacementDone, { n: 1 }),
      });
    } catch (err) {
      setFeedback({
        kind: "error",
        text: BridgeError.isBridgeError(err)
          ? `${err.code}: ${err.message}`
          : String(err),
      });
    } finally {
      setReplacing(null);
    }
  };

  const handleReplaceAll = async () => {
    if (!activeTaskId) return;
    const plan = makeReplacementPlan();
    if (!plan) return;
    const changes = filteredItems
      .map((item) => {
        const base = item.segment_id === selectedSegmentId ? draftDst : item.dst;
        const next = plan.apply(base);
        return next === base ? null : { segmentId: item.segment_id, dst: next };
      })
      .filter(
        (change): change is { segmentId: string; dst: string } =>
          change !== null,
      );
    if (changes.length === 0) {
      setFeedback({ kind: "info", text: m.replacementNoMatch });
      return;
    }
    setReplacing("all");
    setFeedback(null);
    try {
      for (const change of changes) {
        await saveReplacement(change.segmentId, change.dst);
      }
      const selectedChange = changes.find(
        (change) => change.segmentId === selectedSegmentId,
      );
      if (selectedChange) setDraftDst(selectedChange.dst);
      setSavedTick((t) => t + 1);
      setFeedback({
        kind: "success",
        text: format(m.replacementDone, { n: changes.length }),
      });
    } catch (err) {
      setFeedback({
        kind: "error",
        text: BridgeError.isBridgeError(err)
          ? `${err.code}: ${err.message}`
          : String(err),
      });
    } finally {
      setReplacing(null);
    }
  };

  const handleRegenerate = async (bilingual = false) => {
    if (!activeTaskId || regenerating) return;
    setRegenerating(bilingual ? "bilingual" : "translated");
    setRegenerateFeedback(null);
    try {
      const result = await proofreadingBridge.regenerateOutputs(
        activeTaskId,
        bilingual,
      );
      const total =
        result.translated_files.length + result.bilingual_files.length;
      if (result.failed_files.length > 0) {
        const reason = result.failed_files
          .map((f) => formatRegenerateFailure(f, m))
          .join("; ");
        setRegenerateFeedback({
          kind: "error",
          text:
            total > 0
              ? format(m.regeneratePartial, { n: total, reason })
              : format(m.regenerateFailed, { reason }),
        });
      } else {
        setRegenerateFeedback({
          kind: "success",
          text: format(m.regenerateSuccess, { n: total }),
        });
      }
    } catch (err) {
      setRegenerateFeedback({
        kind: "error",
        text: format(m.regenerateFailed, {
          reason: BridgeError.isBridgeError(err)
            ? `${err.code}: ${err.message}`
            : String(err),
        }),
      });
    } finally {
      setRegenerating(null);
    }
  };

  const pollRetranslate = (
    segmentId: string,
    requestId: string,
  ): Promise<"completed" | "stale" | "failed" | "timeout"> =>
    new Promise((resolve) => {
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
            finish();
            resolve("completed");
            return;
          }
          if (status.status === "stale") {
            finish();
            resolve("stale");
            return;
          }
          if (status.status === "failed") {
            setFeedback({
              kind: "error",
              text: format(m.retranslateFailed, { reason: status.error }),
            });
            finish();
            resolve("failed");
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
          resolve("failed");
          return;
        }
        if (Date.now() - startedAt > 60_000) {
          finish();
          resolve("timeout");
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
    });

  const runRetranslateSegment = async (segmentId: string) => {
    if (!activeTaskId || inflightRetranslates[segmentId]) return "failed";
    setFeedback(null);
    try {
      const { request_id } = await proofreadingBridge.retranslateSegment(
        activeTaskId,
        segmentId,
      );
      setInflightRetranslates((prev) => ({
        ...prev,
        [segmentId]: request_id,
      }));
      return await pollRetranslate(segmentId, request_id);
    } catch (err) {
      const text = BridgeError.isBridgeError(err)
        ? err.code === "bridge.conflict"
          ? m.retranslateRejectedRunning
          : `${err.code}: ${err.message}`
        : String(err);
      setFeedback({ kind: "error", text });
      return "failed";
    }
  };

  const handleRetranslate = async () => {
    if (!selectedItem) return;
    const result = await runRetranslateSegment(selectedItem.segment_id);
    if (result === "completed") {
      setFeedback({ kind: "success", text: m.retranslateSuccess });
    } else if (result === "stale") {
      setFeedback({ kind: "info", text: m.retranslateStale });
    } else if (result === "timeout") {
      setFeedback({ kind: "error", text: m.retranslateTimeout });
    }
  };

  const handleRetranslateSelected = async () => {
    if (selectedSegmentIds.size === 0 || batchRetranslating) return;
    if (dirty && selectedSegmentId && selectedSegmentIds.has(selectedSegmentId)) {
      setFeedback({ kind: "error", text: m.retranslateSaveDirtyFirst });
      return;
    }
    const ids = Array.from(selectedSegmentIds).sort(compareSegmentIds);
    await retranslateIds(ids);
  };

  const handleRetranslateFiltered = async () => {
    if (filteredItems.length === 0 || batchRetranslating) return;
    if (
      dirty &&
      selectedSegmentId &&
      filteredItems.some((item) => item.segment_id === selectedSegmentId)
    ) {
      setFeedback({ kind: "error", text: m.retranslateSaveDirtyFirst });
      return;
    }
    const ids = filteredItems.map((item) => item.segment_id);
    await retranslateIds(ids);
  };

  const retranslateIds = async (ids: string[]) => {
    setBatchRetranslating(true);
    let completedCount = 0;
    let staleCount = 0;
    let failedCount = 0;
    try {
      for (const segmentId of ids) {
        const result = await runRetranslateSegment(segmentId);
        if (result === "completed") completedCount += 1;
        else if (result === "stale") staleCount += 1;
        else failedCount += 1;
      }
      setFeedback({
        kind: failedCount > 0 ? "error" : staleCount > 0 ? "info" : "success",
        text: format(m.retranslateSelectedDone, {
          done: completedCount,
          stale: staleCount,
          failed: failedCount,
        }),
      });
    } finally {
      setBatchRetranslating(false);
    }
  };

  const handleRowSelect = (
    segmentId: string,
    event: MouseEvent,
    forceToggle = false,
  ) => {
    setSelectedSegmentId(segmentId);
    if (event.shiftKey && selectionAnchorId) {
      const anchorIndex = filteredItems.findIndex(
        (item) => item.segment_id === selectionAnchorId,
      );
      const targetIndex = filteredItems.findIndex(
        (item) => item.segment_id === segmentId,
      );
      if (anchorIndex >= 0 && targetIndex >= 0) {
        const [from, to] =
          anchorIndex < targetIndex
            ? [anchorIndex, targetIndex]
            : [targetIndex, anchorIndex];
        setSelectedSegmentIds(
          new Set(filteredItems.slice(from, to + 1).map((item) => item.segment_id)),
        );
        return;
      }
    }
    if (event.metaKey || event.ctrlKey || forceToggle) {
      setSelectedSegmentIds((prev) => {
        const next = new Set(prev);
        if (next.has(segmentId) && next.size > 1) {
          next.delete(segmentId);
        } else {
          next.add(segmentId);
        }
        return next;
      });
      setSelectionAnchorId(segmentId);
      return;
    }
    setSelectedSegmentIds(new Set([segmentId]));
    setSelectionAnchorId(segmentId);
  };

  const selectSingleSegment = (segmentId: string) => {
    setSelectedSegmentId(segmentId);
    setSelectedSegmentIds(new Set([segmentId]));
    setSelectionAnchorId(segmentId);
  };

  const handleSelectNextRisk = () => {
    if (filteredItems.length === 0) return;
    const currentIndex = filteredItems.findIndex(
      (item) => item.segment_id === selectedSegmentId,
    );
    const start = currentIndex >= 0 ? currentIndex + 1 : 0;
    const ordered = [
      ...filteredItems.slice(start),
      ...filteredItems.slice(0, start),
    ];
    const next = ordered.find((item) => riskRank(item) < 4);
    if (!next) return;
    const nextIndex = filteredItems.findIndex(
      (item) => item.segment_id === next.segment_id,
    );
    selectSingleSegment(next.segment_id);
    if (nextIndex >= 0) virtual.scrollToIndex(nextIndex);
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
  const residueCount =
    snapshot?.items.filter((item) => item.tags?.includes("source_residue"))
      .length ?? 0;
  const possibleDuplicateCount =
    snapshot?.items.filter((item) => item.tags?.includes("possible_duplicate"))
      .length ?? 0;
  const untranslatedCount =
    snapshot?.items.filter((item) => isUntranslated(item)).length ?? 0;
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
          onClick={() => void handleRegenerate(false)}
          disabled={Boolean(regenerating) || !activeTaskId}
        >
          {regenerating === "translated" ? m.regenerating : m.regenerateAction}
        </Pill>
        <Pill
          variant="ghost"
          onClick={() => void handleRegenerate(true)}
          disabled={Boolean(regenerating) || !activeTaskId}
        >
          {regenerating === "bilingual"
            ? m.regenerating
            : m.regenerateBilingualAction}
        </Pill>
        {regenerateFeedback ? (
          <span
            className={`${styles.regenerateNotice} ${
              regenerateFeedback.kind === "error"
                ? styles.regenerateNoticeError
                : styles.regenerateNoticeSuccess
            }`}
          >
            {regenerateFeedback.text}
          </span>
        ) : null}
      </div>

      <div className={styles.toggleRow}>
        <span>
          {format(m.stats.total, { n: totalCount })} ·{" "}
          {format(m.stats.lowConfidence, { n: lowConfCount })} ·{" "}
          {format(m.stats.sourceResidue, { n: residueCount })} ·{" "}
          {format(m.stats.possibleDuplicate, { n: possibleDuplicateCount })} ·{" "}
          {format(m.stats.untranslated, { n: untranslatedCount })}
        </span>
        <span className={styles.filterChips}>
          <button
            type="button"
            className={`${styles.filterChip} ${filters.has("low_conf") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("low_conf")}
            onClick={() => toggleFilter("low_conf")}
          >
            {m.filterOnlyLowConfidence}
          </button>
          <button
            type="button"
            className={`${styles.filterChip} ${filters.has("source_residue") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("source_residue")}
            onClick={() => toggleFilter("source_residue")}
          >
            {m.filterOnlySourceResidue}
          </button>
          <button
            type="button"
            className={`${styles.filterChip} ${filters.has("possible_duplicate") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("possible_duplicate")}
            onClick={() => toggleFilter("possible_duplicate")}
          >
            {m.filterOnlyPossibleDuplicate}
          </button>
          <button
            type="button"
            className={`${styles.filterChip} ${filters.has("untranslated") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("untranslated")}
            onClick={() => toggleFilter("untranslated")}
          >
            {m.filterOnlyUntranslated}
          </button>
          <button
            type="button"
            className={`${styles.filterChip} ${filters.has("too_short") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("too_short")}
            onClick={() => toggleFilter("too_short")}
          >
            {m.filterOnlyTooShort}
          </button>
          <button
            type="button"
            className={`${styles.filterChip} ${filters.has("too_long") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("too_long")}
            onClick={() => toggleFilter("too_long")}
          >
            {m.filterOnlyTooLong}
          </button>
          <button
            type="button"
            className={`${styles.filterChip} ${filters.has("format_rescue") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("format_rescue")}
            onClick={() => toggleFilter("format_rescue")}
          >
            {m.filterOnlyFormatRescue}
          </button>
          <button
            type="button"
            className={styles.filterChip}
            onClick={handleSelectNextRisk}
            disabled={!filteredItems.some((item) => riskRank(item) < 4)}
          >
            {m.nextRiskAction}
          </button>
          <button
            type="button"
            className={styles.filterChip}
            onClick={() => void handleRetranslateFiltered()}
            disabled={batchRetranslating || filteredItems.length === 0}
          >
            {batchRetranslating
              ? m.retranslating
              : format(m.retranslateFilteredAction, {
                  n: filteredItems.length,
                })}
          </button>
        </span>
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

      <div className={styles.replacementWrap}>
        <label className={styles.checkLabel}>
          <input
            type="checkbox"
            checked={replacementEnabled}
            onChange={(e) => setReplacementEnabled(e.target.checked)}
          />
          <span>{m.replacementToggle}</span>
        </label>
        {replacementEnabled ? (
          <div className={styles.replacementPanel}>
            <input
              type="text"
              className={styles.replacementInput}
              value={replacementNeedle}
              onChange={(e) => setReplacementNeedle(e.target.value)}
              placeholder={m.replacementFindPlaceholder}
            />
            <input
              type="text"
              className={styles.replacementInput}
              value={replacementValue}
              onChange={(e) => setReplacementValue(e.target.value)}
              placeholder={m.replacementValuePlaceholder}
            />
            <label className={styles.checkLabel}>
              <input
                type="checkbox"
                checked={replacementRegex}
                onChange={(e) => setReplacementRegex(e.target.checked)}
              />
              <span>{m.replacementRegex}</span>
            </label>
            <Pill
              variant="ghost"
              onClick={() => void handleReplaceSelected()}
              disabled={replacing !== null || !selectedItem}
            >
              {replacing === "one"
                ? m.replacementRunning
                : m.replacementSelected}
            </Pill>
            <Pill
              onClick={() => void handleReplaceAll()}
              disabled={replacing !== null || filteredItems.length === 0}
            >
              {replacing === "all" ? m.replacementRunning : m.replacementAll}
            </Pill>
          </div>
        ) : null}
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
            ref={virtual.containerRef}
            onScroll={virtual.handleScroll}
          >
            {loading ? (
              <div className={styles.empty}>{m.loading}</div>
            ) : filteredItems.length === 0 ? (
              <div className={styles.empty}>{m.empty}</div>
            ) : (
              <div
                style={{
                  height: virtual.totalHeight,
                  position: "relative",
                }}
              >
                {visibleItems.map((item, i) => {
                  const active = item.segment_id === selectedSegmentId;
                  const selected = selectedSegmentIds.has(item.segment_id);
                  const status = item.dst
                    ? item.low_confidence
                      ? "low"
                      : "ok"
                    : "empty";
                  return (
                    <div
                      key={item.segment_id}
                      className={`${styles.row} ${selected ? styles.rowSelected : ""} ${active ? styles.rowActive : ""}`.trim()}
                      style={{
                        position: "absolute",
                        top: virtual.topForIndex(startIndex + i),
                        left: 0,
                        right: 0,
                      }}
                      onClick={(event) => handleRowSelect(item.segment_id, event)}
                    >
                      <span
                        className={`${styles.cell} ${styles.cellIndex}`.trim()}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          readOnly
                          tabIndex={-1}
                          aria-label={format(m.selectRowLabel, {
                            id: item.segment_id,
                          })}
                          onClick={(event) => {
                            event.stopPropagation();
                            handleRowSelect(item.segment_id, event, true);
                          }}
                        />
                        <span>{item.segment_id}</span>
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
                        {item.tags?.includes("source_residue") ? (
                          <span
                            className={`${styles.statusChip} ${styles.statusResidue}`}
                            title={m.statusSourceResidueHint}
                          >
                            {m.statusSourceResidue}
                          </span>
                        ) : null}
                        {item.tags?.includes("possible_duplicate") ? (
                          <span
                            className={`${styles.statusChip} ${styles.statusDuplicate}`}
                            title={m.statusPossibleDuplicateHint}
                          >
                            {m.statusPossibleDuplicate}
                          </span>
                        ) : null}
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
              <textarea
                className={styles.editorSrc}
                value={selectedItem.src}
                readOnly
                onFocus={(e) => e.currentTarget.select()}
              />
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
                <Pill
                  variant="ghost"
                  onClick={() => void handleRetranslateSelected()}
                  disabled={batchRetranslating || selectedCount === 0}
                >
                  {batchRetranslating
                    ? m.retranslating
                    : format(m.retranslateSelectedAction, {
                        n: selectedCount,
                      })}
                </Pill>
                <Pill onClick={() => void handleSave()} disabled={!dirty}>
                  {m.editorSaveAction}
                </Pill>
              </span>
            </div>
            {selectedItem.subtask_ids?.length ? (
              <div className={styles.debugHint}>
                {format(m.subtaskHint, {
                  ids: selectedItem.subtask_ids.join(", "),
                })}
              </div>
            ) : null}
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
