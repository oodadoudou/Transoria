import { useEffect, useMemo, useState, type MouseEvent } from "react";
import { format, useI18n, useMessages } from "@/locales";
import {
  BridgeError,
  proofreadingBridge,
  type ModelProfile,
  type ProofreadingItem,
  type ProofreadingSnapshot,
  type TaskHeader,
} from "@/bridge";
import {
  DEFAULT_PROOFREADING_FILTERS,
  useTaskStore,
  type ProofreadingFilterKey,
} from "@/store/useTaskStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { GuidedEmptyState } from "@/components/GuidedEmptyState";
import { ChevronDownIcon } from "@/components/Icon";
import {
  QuickSwitchModal,
  type QuickSwitchItem,
} from "@/components/QuickSwitchModal";
import { useVirtualWindow } from "@/hooks/useVirtualWindow";
import { useModelProfiles } from "@/store/useModelProfilesStore";
import { usePromptPresets } from "@/store/usePromptPresetsStore";
import { useModuleSettings } from "@/store/useSettingsStore";
import { useWorkflowPresets } from "@/store/useWorkflowPresetsStore";
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

type RetranslateStatus = "completed" | "stale" | "skipped" | "failed";

interface RetranslateOutcome {
  status: RetranslateStatus;
  reason?: string;
}

interface RetranslateUndo {
  entries: Array<{ segmentId: string; dst: string }>;
}

interface BatchRetranslateProgress {
  total: number;
  processed: number;
  completed: number;
  stale: number;
  failed: number;
  current: number;
}

interface ProofreadingItemMeta {
  rank: number;
  fileIndex: number;
  segmentIndex: number;
  sourceResidue: boolean;
  glossaryNotApplied: boolean;
  termInconsistency: boolean;
  possibleDuplicate: boolean;
  modelAnomaly: boolean;
  untranslated: boolean;
  formatRescue: boolean;
}

interface RiskCounts {
  lowConf: number;
  residue: number;
  glossaryNotApplied: number;
  termInconsistency: number;
  possibleDuplicate: number;
  modelAnomaly: number;
  untranslated: number;
  formatRescue: number;
  total: number;
}

const EMPTY_RISK_COUNTS: RiskCounts = {
  lowConf: 0,
  residue: 0,
  glossaryNotApplied: 0,
  termInconsistency: 0,
  possibleDuplicate: 0,
  modelAnomaly: 0,
  untranslated: 0,
  formatRescue: 0,
  total: 0,
};

const REVIEW_RISK_MAX_RANK = 6;
const RETRANSLATE_CONCURRENCY_FALLBACK = 4;
const RETRANSLATE_CONCURRENCY_AUTO_MAX = 48;
const RETRANSLATE_FRONTEND_JOB_CAP = 50;
const RETRANSLATE_RATE_WINDOW_MS = 60_000;
const RETRANSLATE_RATE_WINDOW_BUFFER_MS = 25;
const RETRANSLATE_FAST_POLL_MS = 500;
const RETRANSLATE_SLOW_AFTER_MS = 60_000;
const RETRANSLATE_SLOW_POLL_MS = 2_000;
const RETRANSLATE_QUEUE_STORAGE_KEY =
  "transoria:proofreading:retranslate-queue:v1";

const MODEL_ANOMALY_TAGS = new Set([
  "function_word_residue",
  "target_language_weak",
  "model_chatter",
  "verbatim_echo",
]);

const STRUCTURAL_REVIEW_TAGS = new Set([
  "length_ratio_anomaly",
  "punctuation_anomaly",
]);

interface TermAuditGroup {
  key: string;
  src: string;
  dst: string;
  info: string;
  total: number;
  applied: number;
  missing: number;
  inconsistent: boolean;
  segmentIds: string[];
}

interface StoredRetranslateQueueEntry {
  taskId: string;
  segmentId: string;
  segmentIds?: string[];
  requestId: string;
}

function readStoredRetranslateQueue(): StoredRetranslateQueueEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(RETRANSLATE_QUEUE_STORAGE_KEY) ?? "[]",
    );
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is StoredRetranslateQueueEntry =>
        typeof item?.taskId === "string" &&
        typeof item.segmentId === "string" &&
        typeof item.requestId === "string",
    );
  } catch {
    return [];
  }
}

function writeStoredRetranslateQueue(
  entries: StoredRetranslateQueueEntry[],
): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(
    RETRANSLATE_QUEUE_STORAGE_KEY,
    JSON.stringify(entries.slice(-500)),
  );
}

function rememberRetranslateRequest(
  taskId: string,
  segmentIds: string[],
  requestId: string,
): void {
  const requested = new Set(segmentIds);
  const entries = readStoredRetranslateQueue().filter(
    (entry) =>
      !(
        entry.requestId === requestId ||
        (entry.taskId === taskId &&
          (entry.segmentIds ?? [entry.segmentId]).some((id) =>
            requested.has(id),
          ))
      ),
  );
  entries.push({
    taskId,
    segmentId: segmentIds[0] ?? "",
    segmentIds,
    requestId,
  });
  writeStoredRetranslateQueue(entries);
}

function forgetRetranslateRequest(requestId: string): void {
  writeStoredRetranslateQueue(
    readStoredRetranslateQueue().filter(
      (entry) => entry.requestId !== requestId,
    ),
  );
}

function storedRetranslateRequestsForTask(
  taskId: string,
): StoredRetranslateQueueEntry[] {
  return readStoredRetranslateQueue().filter(
    (entry) => entry.taskId === taskId,
  );
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

function hasReasonText(
  reasons: string[] | undefined,
  ...needles: string[]
): boolean {
  return (reasons ?? []).some((reason) =>
    needles.every((needle) => reason.toLowerCase().includes(needle)),
  );
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

function summarizeReasons(reasons: string[]): string {
  const counts = new Map<string, number>();
  for (const raw of reasons) {
    const reason = raw.trim() || "unknown";
    counts.set(reason, (counts.get(reason) ?? 0) + 1);
  }
  return Array.from(counts.entries())
    .map(([reason, count]) => `${reason} ×${count}`)
    .join("；");
}

function getRetranslateConcurrency(
  model: ModelProfile | undefined,
  total: number,
): number {
  if (total <= 0) return 0;
  const configuredConcurrency = Math.floor(model?.concurrency_limit ?? 0);
  const rpmLimit = Math.floor(model?.rpm_limit ?? 0);
  let limit =
    configuredConcurrency > 0
      ? configuredConcurrency
      : rpmLimit > 0
        ? Math.min(RETRANSLATE_CONCURRENCY_AUTO_MAX, rpmLimit)
        : RETRANSLATE_CONCURRENCY_FALLBACK;
  if (rpmLimit > 0) {
    limit = Math.min(limit, rpmLimit);
  }
  return Math.max(1, Math.min(total, RETRANSLATE_FRONTEND_JOB_CAP, limit));
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function createRetranslateRateGate(
  model: ModelProfile | undefined,
): () => Promise<void> {
  const rpmLimit = Math.floor(model?.rpm_limit ?? 0);
  if (rpmLimit <= 0) return async () => undefined;
  const starts: number[] = [];
  let queue = Promise.resolve();

  return async () => {
    let releaseQueue: () => void = () => undefined;
    const turn = queue.then(async () => {
      while (true) {
        const now = Date.now();
        while (starts.length > 0) {
          const oldest = starts[0];
          if (
            oldest === undefined ||
            now - oldest < RETRANSLATE_RATE_WINDOW_MS
          ) {
            break;
          }
          starts.shift();
        }
        if (starts.length < rpmLimit) {
          starts.push(now);
          return;
        }
        const oldest = starts[0] ?? now;
        await sleep(
          RETRANSLATE_RATE_WINDOW_MS -
            (now - oldest) +
            RETRANSLATE_RATE_WINDOW_BUFFER_MS,
        );
      }
    });
    queue = new Promise((resolve) => {
      releaseQueue = resolve;
    });
    try {
      await turn;
    } finally {
      releaseQueue();
    }
  };
}

function formatRegenerateFailure(
  file: RegenerateFailedFile,
  messages: ReturnType<typeof useMessages>["translation"]["proofreadingPage"],
): string {
  let reason: string;
  if (file.code === "no_matching_translations") {
    reason = messages.regenerateFailureReasons.noMatchingTranslations;
  } else if (file.code === "cache_segment_mismatch") {
    reason = format(messages.regenerateFailureReasons.cacheSegmentMismatch, {
      missing: numberDetail(file.details, "missing_segments"),
      expected: numberDetail(file.details, "expected_segments"),
      matched: numberDetail(file.details, "matched_segments"),
      parsedFingerprint: stringDetail(
        file.details,
        "parsed_source_fingerprint",
        "-",
      ),
      cacheFingerprint: stringDetail(
        file.details,
        "cache_source_fingerprint",
        "-",
      ),
      firstMissing: stringDetail(file.details, "first_missing_segment_id", "-"),
    });
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
  const [termAuditOpen, setTermAuditOpen] = useState(false);
  const consumeProofreadingLaunch = useTaskStore(
    (state) => state.consumeProofreadingLaunch,
  );
  const navigate = useTaskStore((state) => state.navigate);
  const [filters, setFilters] = useState<ReadonlySet<ProofreadingFilterKey>>(
    () => new Set(DEFAULT_PROOFREADING_FILTERS),
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
  const [regenerateFeedback, setRegenerateFeedback] = useState<Feedback | null>(
    null,
  );
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [inflightRetranslates, setInflightRetranslates] = useState<
    Record<string, string>
  >({});
  const [resumeStartedForTask, setResumeStartedForTask] = useState<
    string | null
  >(null);
  const [batchRetranslating, setBatchRetranslating] = useState(false);
  const [batchRetranslateProgress, setBatchRetranslateProgress] =
    useState<BatchRetranslateProgress | null>(null);
  const [retranslateUndo, setRetranslateUndo] =
    useState<RetranslateUndo | null>(null);
  const [undoingRetranslate, setUndoingRetranslate] = useState(false);
  const appSettings = useModuleSettings("app");
  const profiles = useModelProfiles();
  const promptPresets = usePromptPresets("translation");
  const translationPromptSlice = promptPresets.translation;
  const workflow = useWorkflowPresets("translation");
  const workflowSlice = workflow.translation;
  const locale = useI18n((state) => state.locale);
  const [proofreadingModelId, setProofreadingModelId] = useState<string | null>(
    null,
  );
  const [proofreadingPromptId, setProofreadingPromptId] = useState<
    string | null
  >(null);
  const [proofreadingModelOverridden, setProofreadingModelOverridden] =
    useState(false);
  const [proofreadingPromptOverridden, setProofreadingPromptOverridden] =
    useState(false);
  const [switchOpen, setSwitchOpen] = useState<
    "preset" | "model" | "prompt" | null
  >(null);
  const activeTranslationModelId =
    appSettings.draft?.active_translation_model_id ?? null;
  const activeTranslationPromptId =
    appSettings.draft?.active_translation_prompt_id ??
    translationPromptSlice.activeId ??
    null;
  const localeDefaultPromptId = `default-translation-${locale}`;
  const visiblePromptPresets = useMemo(
    () =>
      translationPromptSlice.presets.filter(
        (preset) =>
          preset.enabled &&
          (!preset.is_system || preset.id === localeDefaultPromptId),
      ),
    [localeDefaultPromptId, translationPromptSlice.presets],
  );
  const activeTranslationPrompt = activeTranslationPromptId
    ? translationPromptSlice.presets.find(
        (preset) => preset.id === activeTranslationPromptId,
      )
    : undefined;
  const fallbackPromptId =
    activeTranslationPrompt &&
    (!activeTranslationPrompt.is_system ||
      activeTranslationPrompt.id === localeDefaultPromptId)
      ? activeTranslationPrompt.id
      : (visiblePromptPresets.find(
          (preset) => preset.id === localeDefaultPromptId,
        )?.id ??
        visiblePromptPresets[0]?.id ??
        null);
  const effectiveProofreadingPromptId =
    proofreadingPromptId &&
    visiblePromptPresets.some((preset) => preset.id === proofreadingPromptId)
      ? proofreadingPromptId
      : fallbackPromptId;
  const modelItems = useMemo<QuickSwitchItem[]>(
    () =>
      profiles.profiles
        .filter((profile) => profile.api_key_status !== "missing")
        .map((profile) => ({
          id: profile.id,
          name: profile.display_name,
          description: profile.model_id,
        })),
    [profiles.profiles],
  );
  const promptItems = useMemo<QuickSwitchItem[]>(
    () =>
      visiblePromptPresets.map((preset) => ({
        id: preset.id,
        name: preset.name,
        description: preset.description || preset.system_prompt.slice(0, 80),
      })),
    [visiblePromptPresets],
  );
  const presetItems = useMemo<QuickSwitchItem[]>(
    () =>
      workflowSlice.presets.map((preset) => {
        const model = profiles.profiles.find(
          (profile) => profile.id === preset.model_profile_id,
        );
        const prompt = translationPromptSlice.presets.find(
          (item) => item.id === preset.prompt_preset_id,
        );
        const languagePair = `${messages.language.options[preset.source_language]} → ${
          messages.language.options[preset.target_language]
        }`;
        return {
          id: preset.id,
          name: preset.name,
          description: [
            languagePair,
            model?.display_name ?? preset.model_profile_id,
            prompt?.name ?? preset.prompt_preset_id,
          ].join(" · "),
        };
      }),
    [
      messages.language.options,
      profiles.profiles,
      translationPromptSlice.presets,
      workflowSlice.presets,
    ],
  );
  const activeProofreadingPresetId =
    workflowSlice.presets.find(
      (preset) =>
        preset.model_profile_id === proofreadingModelId &&
        preset.prompt_preset_id === effectiveProofreadingPromptId,
    )?.id ?? null;
  const activeProofreadingPreset = activeProofreadingPresetId
    ? workflowSlice.presets.find(
        (preset) => preset.id === activeProofreadingPresetId,
      )
    : null;
  const selectedProofreadingModel = profiles.profiles.find(
    (profile) => profile.id === proofreadingModelId,
  );
  const selectedProofreadingPrompt = visiblePromptPresets.find(
    (preset) => preset.id === effectiveProofreadingPromptId,
  );

  useEffect(() => {
    if (proofreadingModelOverridden) return;
    setProofreadingModelId(
      activeTranslationModelId ?? modelItems[0]?.id ?? null,
    );
  }, [activeTranslationModelId, modelItems, proofreadingModelOverridden]);

  useEffect(() => {
    if (proofreadingPromptOverridden) return;
    setProofreadingPromptId(fallbackPromptId ?? null);
  }, [fallbackPromptId, proofreadingPromptOverridden]);

  useEffect(() => {
    if (!proofreadingPromptOverridden || !proofreadingPromptId) return;
    if (visiblePromptPresets.some((preset) => preset.id === proofreadingPromptId)) {
      return;
    }
    setProofreadingPromptOverridden(false);
    setProofreadingPromptId(fallbackPromptId ?? null);
  }, [
    fallbackPromptId,
    proofreadingPromptId,
    proofreadingPromptOverridden,
    visiblePromptPresets,
  ]);

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
          setFilters(
            new Set(
              launch.filters.length > 0
                ? launch.filters
                : DEFAULT_PROOFREADING_FILTERS,
            ),
          );
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

  const proofreadingIndex = useMemo(() => {
    const itemsBySegmentId = new Map<string, ProofreadingItem>();
    const metaBySegmentId = new Map<string, ProofreadingItemMeta>();
    const riskCounts = {
      ...EMPTY_RISK_COUNTS,
      total: snapshot?.items.length ?? 0,
    };
    for (const item of snapshot?.items ?? []) {
      itemsBySegmentId.set(item.segment_id, item);
      const [fileIndex, segmentIndex] = segmentSortKey(item.segment_id);
      const sourceResidue = item.tags?.includes("source_residue") ?? false;
      const glossaryNotApplied =
        item.tags?.includes("glossary_not_applied") ?? false;
      const termInconsistency =
        item.tags?.includes("term_inconsistency") ?? false;
      const possibleDuplicate =
        item.tags?.includes("possible_duplicate") ?? false;
      const modelAnomaly =
        item.tags?.some((tag) => MODEL_ANOMALY_TAGS.has(tag)) ?? false;
      const untranslated = isUntranslated(item);
      const formatRescue =
        hasReasonText(item.reasons, "format", "rescue") ||
        hasReasonText(item.reasons, "format", "fallback");
      let rank = 7;
      if (sourceResidue) rank = 0;
      else if (glossaryNotApplied) rank = 1;
      else if (termInconsistency) rank = 2;
      else if (possibleDuplicate) rank = 3;
      else if (modelAnomaly) rank = 4;
      else if (untranslated) rank = 5;
      else if (item.low_confidence) rank = 6;
      metaBySegmentId.set(item.segment_id, {
        rank,
        fileIndex,
        segmentIndex,
        sourceResidue,
        glossaryNotApplied,
        termInconsistency,
        possibleDuplicate,
        modelAnomaly,
        untranslated,
        formatRescue,
      });
      if (item.low_confidence) riskCounts.lowConf += 1;
      if (sourceResidue) riskCounts.residue += 1;
      if (glossaryNotApplied) riskCounts.glossaryNotApplied += 1;
      if (termInconsistency) riskCounts.termInconsistency += 1;
      if (possibleDuplicate) riskCounts.possibleDuplicate += 1;
      if (modelAnomaly) riskCounts.modelAnomaly += 1;
      if (untranslated) riskCounts.untranslated += 1;
      if (formatRescue) riskCounts.formatRescue += 1;
    }
    const sortedItems = [...(snapshot?.items ?? [])].sort((left, right) => {
      const leftMeta = metaBySegmentId.get(left.segment_id);
      const rightMeta = metaBySegmentId.get(right.segment_id);
      if (!leftMeta || !rightMeta) return 0;
      return (
        leftMeta.rank - rightMeta.rank ||
        leftMeta.fileIndex - rightMeta.fileIndex ||
        leftMeta.segmentIndex - rightMeta.segmentIndex
      );
    });
    return { itemsBySegmentId, metaBySegmentId, riskCounts, sortedItems };
  }, [snapshot]);

  const termAuditGroups = useMemo<TermAuditGroup[]>(() => {
    const groups = new Map<string, TermAuditGroup>();
    for (const item of snapshot?.items ?? []) {
      for (const term of item.glossary_terms ?? []) {
        if (term.applied && !term.inconsistent) continue;
        const key = `${term.src}\0${term.dst}\0${term.info}`;
        const existing = groups.get(key) ?? {
          key,
          src: term.src,
          dst: term.dst,
          info: term.info,
          total: 0,
          applied: 0,
          missing: 0,
          inconsistent: false,
          segmentIds: [],
        };
        existing.total += 1;
        if (term.applied) {
          existing.applied += 1;
        } else {
          existing.missing += 1;
        }
        existing.inconsistent = existing.inconsistent || term.inconsistent;
        if (!existing.segmentIds.includes(item.segment_id)) {
          existing.segmentIds.push(item.segment_id);
        }
        groups.set(key, existing);
      }
    }
    return [...groups.values()].sort(
      (left, right) =>
        Number(right.inconsistent) - Number(left.inconsistent) ||
        right.missing - left.missing ||
        right.total - left.total ||
        left.src.localeCompare(right.src),
    );
  }, [snapshot]);

  // Sync draft when selection changes.
  const selectedItem = useMemo<ProofreadingItem | null>(() => {
    if (!selectedSegmentId) return null;
    return proofreadingIndex.itemsBySegmentId.get(selectedSegmentId) ?? null;
  }, [proofreadingIndex, selectedSegmentId]);

  useEffect(() => {
    setDraftDst(selectedItem?.dst ?? "");
  }, [selectedItem]);

  const filteredItems = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q && filters.size === 0) return proofreadingIndex.sortedItems;
    return proofreadingIndex.sortedItems.filter((item) => {
      const meta = proofreadingIndex.metaBySegmentId.get(item.segment_id);
      if (
        filters.size > 0 &&
        !(
          (filters.has("low_conf") && item.low_confidence) ||
          (filters.has("source_residue") && meta?.sourceResidue) ||
          (filters.has("glossary_not_applied") && meta?.glossaryNotApplied) ||
          (filters.has("term_inconsistency") && meta?.termInconsistency) ||
          (filters.has("possible_duplicate") && meta?.possibleDuplicate) ||
          (filters.has("model_anomaly") && meta?.modelAnomaly) ||
          (filters.has("untranslated") && meta?.untranslated) ||
          (filters.has("format_rescue") && meta?.formatRescue)
        )
      ) {
        return false;
      }
      if (!q) return true;
      return (
        item.src.toLowerCase().includes(q) || item.dst.toLowerCase().includes(q)
      );
    });
  }, [proofreadingIndex, search, filters]);

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
  const filteredHasRisk = useMemo(
    () =>
      filteredItems.some(
        (item) =>
          (proofreadingIndex.metaBySegmentId.get(item.segment_id)?.rank ?? 5) <=
          REVIEW_RISK_MAX_RANK,
      ),
    [filteredItems, proofreadingIndex],
  );

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
      setRetranslateUndo(null);
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
      setRetranslateUndo(null);
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
        const base =
          item.segment_id === selectedSegmentId ? draftDst : item.dst;
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
      setRetranslateUndo(null);
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

  const copyToClipboard = async (
    text: string,
    successText: string,
  ): Promise<void> => {
    try {
      await navigator.clipboard.writeText(text);
      setFeedback({ kind: "success", text: successText });
    } catch (err) {
      setFeedback({
        kind: "error",
        text: BridgeError.isBridgeError(err)
          ? `${err.code}: ${err.message}`
          : String(err),
      });
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

  const reloadProofreadingSnapshot = async (
    preferredSegmentId: string | null = selectedSegmentId,
  ) => {
    if (!activeTaskId) return null;
    const next = await proofreadingBridge.loadSnapshot(activeTaskId);
    const itemIds = new Set(next.items.map((item) => item.segment_id));
    const nextSelected =
      preferredSegmentId && itemIds.has(preferredSegmentId)
        ? preferredSegmentId
        : selectedSegmentId && itemIds.has(selectedSegmentId)
          ? selectedSegmentId
          : (next.items[0]?.segment_id ?? null);
    setSnapshot(next);
    setSelectedSegmentId(nextSelected);
    setSelectionAnchorId((prev) =>
      prev && itemIds.has(prev) ? prev : nextSelected,
    );
    setSelectedSegmentIds((prev) => {
      const kept = new Set([...prev].filter((id) => itemIds.has(id)));
      if (kept.size > 0) return kept;
      return nextSelected ? new Set([nextSelected]) : new Set();
    });
    return next;
  };

  const patchCompletedRetranslateResult = (
    segmentId: string,
    resultDst: string,
  ) => {
    setSnapshot((prev) =>
      prev
        ? {
            ...prev,
            items: prev.items.map((it) =>
              it.segment_id === segmentId ? { ...it, dst: resultDst } : it,
            ),
          }
        : prev,
    );
    if (selectedSegmentId === segmentId) setDraftDst(resultDst);
  };

  const pollRetranslate = (
    segmentId: string,
    requestId: string,
    showFeedback = true,
    refreshOnComplete = showFeedback,
  ): Promise<RetranslateOutcome> =>
    new Promise((resolve) => {
      const startedAt = Date.now();
      const tick = async () => {
        try {
          const status = await proofreadingBridge.retranslateStatus(requestId);
          if (status.status === "completed") {
            if (refreshOnComplete) {
              try {
                await reloadProofreadingSnapshot(segmentId);
              } catch {
                patchCompletedRetranslateResult(segmentId, status.result_dst);
              }
            } else {
              patchCompletedRetranslateResult(segmentId, status.result_dst);
            }
            finish(true);
            resolve({ status: "completed" });
            return;
          }
          if (status.status === "stale") {
            finish(true);
            resolve({ status: "stale" });
            return;
          }
          if (status.status === "skipped") {
            finish(true);
            resolve({
              status: "skipped",
              reason: status.error || status.last_error,
            });
            return;
          }
          if (status.status === "failed") {
            const reason = status.error || status.last_error;
            if (showFeedback) {
              setFeedback({
                kind: "error",
                text: format(m.retranslateFailed, { reason }),
              });
            }
            finish(true);
            resolve({ status: "failed", reason });
            return;
          }
        } catch (err) {
          const reason = BridgeError.isBridgeError(err)
            ? `${err.code}: ${err.message}`
            : String(err);
          if (showFeedback) {
            setFeedback({
              kind: "error",
              text: format(m.retranslateFailed, { reason }),
            });
          }
          finish(true);
          resolve({ status: "failed", reason });
          return;
        }
        const elapsed = Date.now() - startedAt;
        setTimeout(
          tick,
          elapsed > RETRANSLATE_SLOW_AFTER_MS
            ? RETRANSLATE_SLOW_POLL_MS
            : RETRANSLATE_FAST_POLL_MS,
        );
      };
      const finish = (forgetRequest: boolean) => {
        if (forgetRequest) forgetRetranslateRequest(requestId);
        setInflightRetranslates((prev) => {
          const next = { ...prev };
          delete next[segmentId];
          return next;
        });
      };
      void tick();
    });

  const pollRetranslateBatch = (
    segmentIds: string[],
    requestId: string,
  ): Promise<Map<string, RetranslateOutcome>> =>
    new Promise((resolve) => {
      const tick = async () => {
        try {
          const status = await proofreadingBridge.retranslateStatus(requestId);
          if (status.status === "completed") {
            const outcomes = new Map<string, RetranslateOutcome>();
            for (const item of status.results) {
              const outcome: RetranslateOutcome = {
                status: item.status,
                reason: item.error,
              };
              outcomes.set(item.segment_id, outcome);
              if (item.status === "completed" && item.result_dst !== undefined) {
                patchCompletedRetranslateResult(item.segment_id, item.result_dst);
              }
            }
            for (const segmentId of segmentIds) {
              if (!outcomes.has(segmentId)) {
                outcomes.set(segmentId, {
                  status: "failed",
                  reason: "retranslate batch returned no segment result",
                });
              }
            }
            finish();
            resolve(outcomes);
            return;
          }
          if (status.status === "failed") {
            const reason = status.error || status.last_error;
            finish();
            resolve(
              new Map(
                segmentIds.map((segmentId) => [
                  segmentId,
                  { status: "failed" as const, reason },
                ]),
              ),
            );
            return;
          }
        } catch (err) {
          const reason = BridgeError.isBridgeError(err)
            ? `${err.code}: ${err.message}`
            : String(err);
          finish();
          resolve(
            new Map(
              segmentIds.map((segmentId) => [
                segmentId,
                { status: "failed" as const, reason },
              ]),
            ),
          );
          return;
        }
        window.setTimeout(tick, RETRANSLATE_FAST_POLL_MS);
      };
      const finish = () => {
        forgetRetranslateRequest(requestId);
        setInflightRetranslates((prev) => {
          const next = { ...prev };
          for (const segmentId of segmentIds) delete next[segmentId];
          return next;
        });
      };
      void tick();
    });

  useEffect(() => {
    if (!activeTaskId || !snapshot || resumeStartedForTask === activeTaskId) {
      return;
    }
    setResumeStartedForTask(activeTaskId);
    const segmentIds = new Set(snapshot.items.map((item) => item.segment_id));
    for (const entry of storedRetranslateRequestsForTask(activeTaskId)) {
      const storedIds = (entry.segmentIds ?? [entry.segmentId]).filter((id) =>
        segmentIds.has(id),
      );
      if (storedIds.length === 0) {
        forgetRetranslateRequest(entry.requestId);
        continue;
      }
      setInflightRetranslates((prev) => {
        const next = { ...prev };
        for (const segmentId of storedIds) next[segmentId] = entry.requestId;
        return next;
      });
      void proofreadingBridge
        .resumeRetranslate(entry.requestId)
        .then(() => {
          if (storedIds.length === 1) {
            void pollRetranslate(storedIds[0], entry.requestId, false, true);
          } else {
            void pollRetranslateBatch(storedIds, entry.requestId);
          }
        })
        .catch(() => {
          forgetRetranslateRequest(entry.requestId);
          setInflightRetranslates((prev) => {
            const next = { ...prev };
            for (const segmentId of storedIds) delete next[segmentId];
            return next;
          });
        });
    }
  }, [
    activeTaskId,
    pollRetranslate,
    pollRetranslateBatch,
    resumeStartedForTask,
    snapshot,
  ]);

  const runRetranslateSegment = async (
    segmentId: string,
    options: { showFeedback?: boolean; refreshOnComplete?: boolean } = {},
  ): Promise<RetranslateOutcome> => {
    const showFeedback = options.showFeedback ?? true;
    const refreshOnComplete = options.refreshOnComplete ?? showFeedback;
    if (!activeTaskId || inflightRetranslates[segmentId]) {
      return {
        status: "failed",
        reason: "retranslate request is already running",
      };
    }
    if (showFeedback) setFeedback(null);
    try {
      const { request_id } = await proofreadingBridge.retranslateSegment(
        activeTaskId,
        segmentId,
        {
          modelId: proofreadingModelId,
          promptPresetId: effectiveProofreadingPromptId,
        },
      );
      setInflightRetranslates((prev) => ({
        ...prev,
        [segmentId]: request_id,
      }));
      rememberRetranslateRequest(activeTaskId, [segmentId], request_id);
      return await pollRetranslate(
        segmentId,
        request_id,
        showFeedback,
        refreshOnComplete,
      );
    } catch (err) {
      const text = BridgeError.isBridgeError(err)
        ? err.code === "bridge.conflict"
          ? m.retranslateRejectedRunning
          : `${err.code}: ${err.message}`
        : String(err);
      if (showFeedback) setFeedback({ kind: "error", text });
      return { status: "failed", reason: text };
    }
  };

  const runRetranslateBatch = async (
    segmentIds: string[],
  ): Promise<Map<string, RetranslateOutcome>> => {
    const firstSegmentId = segmentIds[0];
    if (!firstSegmentId) return new Map();
    if (!activeTaskId || segmentIds.some((id) => inflightRetranslates[id])) {
      return new Map(
        segmentIds.map((segmentId) => [
          segmentId,
          {
            status: "failed" as const,
            reason: "retranslate request is already running",
          },
        ]),
      );
    }
    try {
      const { request_id } = await proofreadingBridge.retranslateSegment(
        activeTaskId,
        firstSegmentId,
        {
          modelId: proofreadingModelId,
          promptPresetId: effectiveProofreadingPromptId,
          segmentIds,
        },
      );
      setInflightRetranslates((prev) => {
        const next = { ...prev };
        for (const segmentId of segmentIds) next[segmentId] = request_id;
        return next;
      });
      rememberRetranslateRequest(activeTaskId, segmentIds, request_id);
      return await pollRetranslateBatch(segmentIds, request_id);
    } catch (err) {
      const reason = BridgeError.isBridgeError(err)
        ? `${err.code}: ${err.message}`
        : String(err);
      return new Map(
        segmentIds.map((segmentId) => [
          segmentId,
          { status: "failed" as const, reason },
        ]),
      );
    }
  };

  const handleRetranslate = async () => {
    if (!selectedItem) return;
    const previous = selectedItem.dst;
    const result = await runRetranslateSegment(selectedItem.segment_id);
    if (result.status === "completed") {
      setRetranslateUndo({
        entries: [{ segmentId: selectedItem.segment_id, dst: previous }],
      });
      setFeedback({ kind: "success", text: m.retranslateSuccess });
    } else if (result.status === "stale" || result.status === "skipped") {
      setFeedback({ kind: "info", text: m.retranslateStale });
    }
  };

  const handleRetranslateSelected = async () => {
    if (selectedSegmentIds.size === 0 || batchRetranslating) return;
    if (
      dirty &&
      selectedSegmentId &&
      selectedSegmentIds.has(selectedSegmentId)
    ) {
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
    const batches = Array.from(
      { length: Math.ceil(ids.length / 5) },
      (_, index) => ids.slice(index * 5, index * 5 + 5),
    );
    const concurrency = getRetranslateConcurrency(
      selectedProofreadingModel,
      batches.length,
    );
    const waitForRateSlot = createRetranslateRateGate(
      selectedProofreadingModel,
    );
    setBatchRetranslating(true);
    setBatchRetranslateProgress({
      total: ids.length,
      processed: 0,
      completed: 0,
      stale: 0,
      failed: 0,
      current: concurrency,
    });
    const previousById = new Map(
      (snapshot?.items ?? []).map((item) => [item.segment_id, item.dst]),
    );
    const undoEntries: RetranslateUndo["entries"] = [];
    let nextIndex = 0;
    let launchedCount = 0;
    let processedCount = 0;
    let completedCount = 0;
    let staleCount = 0;
    let failedCount = 0;
    const failedReasons: string[] = [];
    const updateProgress = () => {
      setBatchRetranslateProgress({
        total: ids.length,
        processed: processedCount,
        completed: completedCount,
        stale: staleCount,
        failed: failedCount,
        current: Math.min(launchedCount, ids.length),
      });
    };
    try {
      const workerCount = Math.max(1, concurrency);
      await Promise.all(
        Array.from({ length: workerCount }, async () => {
          while (nextIndex < batches.length) {
            const index = nextIndex;
            nextIndex += 1;
            const batch = batches[index] ?? [];
            launchedCount += batch.length;
            updateProgress();
            await waitForRateSlot();
            const outcomes = await runRetranslateBatch(batch);
            for (const segmentId of batch) {
              const result = outcomes.get(segmentId) ?? {
                status: "failed" as const,
                reason: "missing batch result",
              };
              if (result.status === "completed") {
                completedCount += 1;
                const previous = previousById.get(segmentId);
                if (previous !== undefined) {
                  undoEntries.push({ segmentId, dst: previous });
                }
              } else if (
                result.status === "stale" ||
                result.status === "skipped"
              ) {
                staleCount += 1;
              } else {
                failedCount += 1;
                failedReasons.push(result.reason ?? "unknown");
              }
              processedCount += 1;
            }
            updateProgress();
          }
        }),
      );
      if (completedCount > 0) {
        try {
          await reloadProofreadingSnapshot(selectedSegmentId);
        } catch {
          // Individual completed rows were already patched; keep the batch result visible.
        }
      }
      const baseText = format(m.retranslateSelectedDone, {
        done: completedCount,
        stale: staleCount,
        failed: failedCount,
      });
      setFeedback({
        kind: failedCount > 0 ? "error" : staleCount > 0 ? "info" : "success",
        text:
          failedReasons.length > 0
            ? format(m.retranslateSelectedDoneWithReasons, {
                summary: baseText,
                reasons: summarizeReasons(failedReasons),
              })
            : baseText,
      });
      setRetranslateUndo(
        undoEntries.length > 0 ? { entries: undoEntries } : null,
      );
    } finally {
      setBatchRetranslating(false);
      setBatchRetranslateProgress(null);
    }
  };

  const handleUndoRetranslate = async () => {
    if (!activeTaskId || !retranslateUndo || undoingRetranslate || dirty)
      return;
    setUndoingRetranslate(true);
    setFeedback(null);
    try {
      for (const entry of retranslateUndo.entries) {
        await saveReplacement(entry.segmentId, entry.dst);
      }
      const selectedEntry = retranslateUndo.entries.find(
        (entry) => entry.segmentId === selectedSegmentId,
      );
      if (selectedEntry) setDraftDst(selectedEntry.dst);
      setSavedTick((t) => t + 1);
      setFeedback({
        kind: "success",
        text: format(m.retranslateUndoDone, {
          n: retranslateUndo.entries.length,
        }),
      });
      setRetranslateUndo(null);
    } catch (err) {
      setFeedback({
        kind: "error",
        text: BridgeError.isBridgeError(err)
          ? `${err.code}: ${err.message}`
          : String(err),
      });
    } finally {
      setUndoingRetranslate(false);
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
          new Set(
            filteredItems.slice(from, to + 1).map((item) => item.segment_id),
          ),
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

  const handleJumpToTermSegment = (segmentId: string) => {
    if (!filteredItems.some((item) => item.segment_id === segmentId)) {
      setSearch("");
      setFilters(new Set(["glossary_not_applied", "term_inconsistency"]));
    }
    selectSingleSegment(segmentId);
    const visibleIndex = filteredItems.findIndex(
      (item) => item.segment_id === segmentId,
    );
    if (visibleIndex >= 0) virtual.scrollToIndex(visibleIndex);
  };

  const handleClearProofreadingView = () => {
    setSearch("");
    setFilters(new Set());
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
    const next = ordered.find(
      (item) =>
        (proofreadingIndex.metaBySegmentId.get(item.segment_id)?.rank ?? 5) <=
        REVIEW_RISK_MAX_RANK,
    );
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
        <GuidedEmptyState
          label={m.emptyState.label}
          title={m.emptyState.noTasksTitle}
          body={m.emptyState.noTasksBody}
          actionLabel={m.emptyState.noTasksAction}
          onAction={() => navigate({ module: "translation", page: "run" })}
        />
      </Panel>
    );
  }

  const lowConfCount = proofreadingIndex.riskCounts.lowConf;
  const residueCount = proofreadingIndex.riskCounts.residue;
  const glossaryNotAppliedCount =
    proofreadingIndex.riskCounts.glossaryNotApplied;
  const termInconsistencyCount = proofreadingIndex.riskCounts.termInconsistency;
  const possibleDuplicateCount = proofreadingIndex.riskCounts.possibleDuplicate;
  const modelAnomalyCount = proofreadingIndex.riskCounts.modelAnomaly;
  const untranslatedCount = proofreadingIndex.riskCounts.untranslated;
  const formatRescueCount = proofreadingIndex.riskCounts.formatRescue;
  const batchProgressPercent =
    batchRetranslateProgress && batchRetranslateProgress.total > 0
      ? Math.round(
          (batchRetranslateProgress.processed /
            batchRetranslateProgress.total) *
            100,
        )
      : 0;
  const riskSummaries: Array<{
    key: ProofreadingFilterKey;
    label: string;
    count: number;
  }> = [
    {
      key: "low_conf",
      label: m.filterOnlyLowConfidence,
      count: lowConfCount,
    },
    {
      key: "source_residue",
      label: m.filterOnlySourceResidue,
      count: residueCount,
    },
    {
      key: "glossary_not_applied",
      label: m.filterOnlyGlossaryNotApplied,
      count: glossaryNotAppliedCount,
    },
    {
      key: "term_inconsistency",
      label: m.filterOnlyTermInconsistency,
      count: termInconsistencyCount,
    },
    {
      key: "possible_duplicate",
      label: m.filterOnlyPossibleDuplicate,
      count: possibleDuplicateCount,
    },
    {
      key: "model_anomaly",
      label: m.filterOnlyModelAnomaly,
      count: modelAnomalyCount,
    },
    {
      key: "untranslated",
      label: m.filterOnlyUntranslated,
      count: untranslatedCount,
    },
    {
      key: "format_rescue",
      label: m.filterOnlyFormatRescue,
      count: formatRescueCount,
    },
  ];
  const riskIssueSummaries = riskSummaries.filter((item) => item.count > 0);
  const visibleRiskSummaries = riskIssueSummaries.slice(0, 4);
  const hiddenRiskSummaryCount =
    riskIssueSummaries.length - visibleRiskSummaries.length;
  const riskIssueTotal = riskIssueSummaries.reduce(
    (sum, card) => sum + card.count,
    0,
  );
  const qualitySummary =
    riskIssueTotal === 0
      ? m.qualitySummaryClean
      : format(m.qualitySummaryNeedsReview, {
          n: riskIssueTotal,
          focus: riskIssueSummaries
            .slice(0, 3)
            .map((card) =>
              format(m.qualitySummaryFocus, {
                label: card.label,
                n: card.count,
              }),
            )
            .join(" · "),
        });
  const filterPresets: Array<{
    label: string;
    keys: ProofreadingFilterKey[];
  }> = [
    {
      label: m.filterPresetDefault,
      keys: ["low_conf", "source_residue", "possible_duplicate"],
    },
    {
      label: m.filterPresetHighRisk,
      keys: [
        "source_residue",
        "glossary_not_applied",
        "term_inconsistency",
        "possible_duplicate",
        "model_anomaly",
      ],
    },
    {
      label: m.filterPresetCompletion,
      keys: ["untranslated", "format_rescue"],
    },
    {
      label: m.filterPresetAll,
      keys: [
        "low_conf",
        "source_residue",
        "glossary_not_applied",
        "term_inconsistency",
        "possible_duplicate",
        "model_anomaly",
        "untranslated",
        "format_rescue",
      ],
    },
  ];
  const setFilterPreset = (keys: ProofreadingFilterKey[]) =>
    setFilters(new Set(keys));
  const isPresetActive = (keys: ProofreadingFilterKey[]) =>
    keys.length === filters.size && keys.every((key) => filters.has(key));
  const buildRiskTitle = (item: ProofreadingItem) => {
    const record = item as ProofreadingItem & {
      reasons?: string[];
      tags?: string[];
    };
    const reasons = Array.isArray(record.reasons)
      ? record.reasons.filter(Boolean)
      : [];
    const tags = Array.isArray(record.tags) ? record.tags.filter(Boolean) : [];
    const parts: string[] = [];
    if (reasons.length > 0) {
      parts.push(`${m.riskReasonPrefix}${reasons.join("；")}`);
    }
    if (tags.length > 0) {
      parts.push(`${m.riskTagsPrefix}${tags.join(", ")}`);
    }
    return parts.join("\n") || undefined;
  };

  return (
    <Panel title={m.title} subtitle={m.sub}>
      <div className={styles.taskToolbar}>
        <div className={styles.taskIdentity}>
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
          {activeTaskId ? (
            <button
              type="button"
              className={styles.copyButton}
              onClick={() =>
                void copyToClipboard(activeTaskId, m.copyTaskIdDone)
              }
            >
              {m.copyTaskId}
            </button>
          ) : null}
          {snapshot?.output_dir ? (
            <span className={styles.outputPathHint}>
              <span className={styles.outputPathLabel}>
                {m.taskFolderLabel}
              </span>
              <span
                className={styles.outputPathValue}
                title={snapshot.output_dir}
              >
                {snapshot.output_dir}
              </span>
            </span>
          ) : null}
          {snapshot?.output_dir ? (
            <button
              type="button"
              className={`${styles.copyButton} ${styles.copyPathButton}`}
              onClick={() =>
                void copyToClipboard(snapshot.output_dir, m.copyOutputPathDone)
              }
              aria-label={m.copyOutputPath}
              title={m.copyOutputPath}
            >
              {m.copyOutputPath}
            </button>
          ) : null}
        </div>
        <div className={styles.taskActions}>
          <Pill
            onClick={() => void handleRegenerate(false)}
            disabled={Boolean(regenerating) || !activeTaskId}
          >
            {regenerating === "translated"
              ? m.regenerating
              : m.regenerateAction}
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
        </div>
        {regenerateFeedback ? (
          <span
            className={`${styles.inlineFeedback} ${
              regenerateFeedback.kind === "error"
                ? styles.inlineFeedbackError
                : regenerateFeedback.kind === "success"
                  ? styles.inlineFeedbackSuccess
                  : ""
            }`}
          >
            {regenerateFeedback.text}
          </span>
        ) : null}
      </div>

      <div className={styles.retranslateConfigRow}>
        <button
          type="button"
          className={styles.retranslateConfigButton}
          onClick={() => setSwitchOpen("model")}
        >
          <span className={styles.retranslateConfigLabel}>
            {m.retranslateModel}
          </span>
          <span className={styles.retranslateConfigName}>
            {selectedProofreadingModel?.display_name ?? "—"}
          </span>
          <span className={styles.retranslateConfigSwitch}>
            {m.switchModelPrompt}
          </span>
        </button>
        <button
          type="button"
          className={styles.retranslateConfigButton}
          onClick={() => setSwitchOpen("prompt")}
        >
          <span className={styles.retranslateConfigLabel}>
            {m.retranslatePrompt}
          </span>
          <span className={styles.retranslateConfigName}>
            {selectedProofreadingPrompt?.name ?? "—"}
          </span>
          <span className={styles.retranslateConfigSwitch}>
            {m.switchModelPrompt}
          </span>
        </button>
        <button
          type="button"
          className={styles.retranslateConfigButton}
          onClick={() => setSwitchOpen("preset")}
        >
          <span className={styles.retranslateConfigLabel}>
            {m.retranslatePreset}
          </span>
          <span className={styles.retranslateConfigName}>
            {activeProofreadingPreset?.name ?? messages.runConfig.customPreset}
          </span>
          <span className={styles.retranslateConfigSwitch}>
            {m.switchModelPrompt}
          </span>
        </button>
      </div>

      {switchOpen === "preset" ? (
        <QuickSwitchModal
          title={m.retranslatePresetPickerTitle}
          items={presetItems}
          activeId={activeProofreadingPresetId}
          emptyMessage={messages.quickSwitch.emptyPreset}
          onSelect={(id) => {
            const preset = workflowSlice.presets.find((item) => item.id === id);
            if (!preset) return;
            setProofreadingModelOverridden(true);
            setProofreadingPromptOverridden(true);
            setProofreadingModelId(preset.model_profile_id);
            setProofreadingPromptId(preset.prompt_preset_id);
          }}
          onClose={() => setSwitchOpen(null)}
          onManage={() => navigate({ module: "translation", page: "presets" })}
          allowReselect
        />
      ) : null}
      {switchOpen === "model" ? (
        <QuickSwitchModal
          title={m.retranslateModelPickerTitle}
          items={modelItems}
          activeId={proofreadingModelId}
          emptyMessage={messages.quickSwitch.emptyModel}
          onSelect={(id) => {
            setProofreadingModelOverridden(true);
            setProofreadingModelId(id);
          }}
          onClose={() => setSwitchOpen(null)}
        />
      ) : null}
      {switchOpen === "prompt" ? (
        <QuickSwitchModal
          title={m.retranslatePromptPickerTitle}
          items={promptItems}
          activeId={effectiveProofreadingPromptId}
          emptyMessage={messages.quickSwitch.emptyPrompt}
          onSelect={(id) => {
            setProofreadingPromptOverridden(true);
            setProofreadingPromptId(id);
          }}
          onClose={() => setSwitchOpen(null)}
        />
      ) : null}

      <div className={styles.filterPanel}>
        <div className={styles.filterPanelTop}>
          {snapshot ? (
            <div
              className={`${styles.reviewOverview} ${
                riskIssueTotal === 0 ? styles.reviewOverviewGood : ""
              }`.trim()}
            >
              <span className={styles.reviewOverviewText}>
                {qualitySummary}
              </span>
              {visibleRiskSummaries.length > 0 ? (
                <span className={styles.reviewMetricList}>
                  {visibleRiskSummaries.map((item) => (
                    <button
                      type="button"
                      key={item.key}
                      className={styles.reviewMetric}
                      onClick={() => setFilters(new Set([item.key]))}
                    >
                      <span>{item.label}</span>
                      <strong>{item.count}</strong>
                    </button>
                  ))}
                  {hiddenRiskSummaryCount > 0 ? (
                    <span className={styles.reviewMetricMore}>
                      +{hiddenRiskSummaryCount}
                    </span>
                  ) : null}
                </span>
              ) : null}
            </div>
          ) : null}
          <span className={styles.filterPanelActions}>
            <span className={styles.filterPresetRow}>
              {filterPresets.map((preset) => {
                const active = isPresetActive(preset.keys);
                return (
                  <button
                    key={preset.label}
                    type="button"
                    className={`${styles.filterChip} ${
                      active ? styles.filterChipActive : ""
                    }`.trim()}
                    aria-pressed={active}
                    onClick={() => setFilterPreset(preset.keys)}
                  >
                    {preset.label}
                  </button>
                );
              })}
            </span>
            <button
              type="button"
              className={`${styles.filterChip} ${styles.filterActionChip}`.trim()}
              onClick={() => setFilters(new Set())}
              disabled={filters.size === 0}
              title={filters.size === 0 ? m.filterClearDisabled : undefined}
            >
              {m.filterClear}
            </button>
          </span>
        </div>
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
            className={`${styles.filterChip} ${filters.has("glossary_not_applied") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("glossary_not_applied")}
            onClick={() => toggleFilter("glossary_not_applied")}
          >
            {m.filterOnlyGlossaryNotApplied}
          </button>
          <button
            type="button"
            className={`${styles.filterChip} ${filters.has("term_inconsistency") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("term_inconsistency")}
            onClick={() => toggleFilter("term_inconsistency")}
          >
            {m.filterOnlyTermInconsistency}
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
            className={`${styles.filterChip} ${filters.has("model_anomaly") ? styles.filterChipActive : ""}`.trim()}
            aria-pressed={filters.has("model_anomaly")}
            onClick={() => toggleFilter("model_anomaly")}
          >
            {m.filterOnlyModelAnomaly}
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
            disabled={!filteredHasRisk}
            title={!filteredHasRisk ? m.nextRiskDisabled : undefined}
          >
            {m.nextRiskAction}
          </button>
          <button
            type="button"
            className={styles.filterChip}
            onClick={() => void handleRetranslateFiltered()}
            disabled={batchRetranslating || filteredItems.length === 0}
            title={
              !batchRetranslating && filteredItems.length === 0
                ? m.retranslateFilteredDisabled
                : undefined
            }
          >
            {batchRetranslating
              ? m.retranslating
              : format(m.retranslateFilteredAction, {
                  n: filteredItems.length,
                })}
          </button>
        </span>
      </div>

      {batchRetranslateProgress ? (
        <div
          className={styles.retranslateProgress}
          role="status"
          aria-live="polite"
        >
          <div className={styles.retranslateProgressHeader}>
            <span>
              {format(m.retranslateProgressLabel, {
                processed: batchRetranslateProgress.processed,
                total: batchRetranslateProgress.total,
                percent: batchProgressPercent,
              })}
            </span>
            <span>
              {format(m.retranslateProgressDetail, {
                current: batchRetranslateProgress.current,
                completed: batchRetranslateProgress.completed,
                stale: batchRetranslateProgress.stale,
                failed: batchRetranslateProgress.failed,
              })}
            </span>
          </div>
          <div className={styles.retranslateProgressTrack}>
            <div
              className={styles.retranslateProgressBar}
              style={{ width: `${batchProgressPercent}%` }}
            />
          </div>
        </div>
      ) : null}

      {termAuditGroups.length > 0 ? (
        <div
          className={`${styles.termAuditPanel} ${
            termAuditOpen ? "" : styles.termAuditPanelCollapsed
          }`.trim()}
        >
          <button
            type="button"
            className={styles.termAuditHeaderButton}
            onClick={() => setTermAuditOpen((open) => !open)}
            aria-expanded={termAuditOpen}
          >
            <span className={styles.termAuditHeaderText}>
              <span
                className={`${styles.termAuditChevron} ${
                  termAuditOpen ? styles.termAuditChevronOpen : ""
                }`.trim()}
                aria-hidden="true"
              >
                <ChevronDownIcon size={14} />
              </span>
              <span>
                <span className={styles.termAuditTitle}>
                  {m.termAuditTitle}
                </span>
                {termAuditOpen ? (
                  <span className={styles.termAuditSub}>{m.termAuditSub}</span>
                ) : null}
              </span>
            </span>
            <span className={styles.termAuditHeaderActions}>
              <span className={styles.termAuditCount}>
                {format(m.termAuditGroupCount, { n: termAuditGroups.length })}
              </span>
              <span className={styles.termAuditToggleText}>
                {termAuditOpen ? m.termAuditCollapse : m.termAuditExpand}
              </span>
            </span>
          </button>
          {termAuditOpen ? (
            <div className={styles.termAuditList}>
              {termAuditGroups.map((group) => {
                const visibleSegments = group.segmentIds.slice(0, 4);
                const hiddenCount =
                  group.segmentIds.length - visibleSegments.length;
                return (
                  <div className={styles.termAuditRow} key={group.key}>
                    <div className={styles.termAuditMain}>
                      <div className={styles.termAuditTerm}>
                        <span>{group.src}</span>
                        <span aria-hidden="true">-&gt;</span>
                        <span>{group.dst}</span>
                        {group.info ? (
                          <span className={styles.termAuditInfo}>
                            {group.info}
                          </span>
                        ) : null}
                      </div>
                      <div className={styles.termAuditMeta}>
                        {format(m.termAuditApplied, { n: group.applied })}
                        <span aria-hidden="true">·</span>
                        {format(m.termAuditMissing, { n: group.missing })}
                        {group.inconsistent ? (
                          <>
                            <span aria-hidden="true">·</span>
                            {m.termAuditInconsistent}
                          </>
                        ) : null}
                      </div>
                    </div>
                    <div className={styles.termAuditSegments}>
                      <span>{m.termAuditSegments}</span>
                      {visibleSegments.map((segmentId) => (
                        <button
                          type="button"
                          className={styles.termAuditSegment}
                          key={segmentId}
                          onClick={() => handleJumpToTermSegment(segmentId)}
                        >
                          {segmentId}
                        </button>
                      ))}
                      {hiddenCount > 0 ? (
                        <span className={styles.termAuditMore}>
                          {format(m.termAuditMoreSegments, { n: hiddenCount })}
                        </span>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className={styles.listToolbar}>
        <input
          type="text"
          className={styles.searchInput}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={m.filterPlaceholder}
        />
        <label className={styles.checkLabel}>
          <input
            type="checkbox"
            checked={replacementEnabled}
            onChange={(e) => setReplacementEnabled(e.target.checked)}
          />
          <span>{m.replacementToggle}</span>
        </label>
      </div>

      <div className={styles.replacementWrap}>
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
              <GuidedEmptyState
                label={m.emptyState.label}
                title={m.emptyState.noItemsTitle}
                body={m.emptyState.noItemsBody}
                actionLabel={
                  filters.size > 0 || search.trim()
                    ? m.emptyState.noItemsAction
                    : undefined
                }
                onAction={
                  filters.size > 0 || search.trim()
                    ? handleClearProofreadingView
                    : undefined
                }
              />
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
                  const riskTitle = buildRiskTitle(item);
                  return (
                    <div
                      key={item.segment_id}
                      className={`${styles.row} ${selected ? styles.rowSelected : ""} ${active ? styles.rowActive : ""}`.trim()}
                      title={riskTitle}
                      style={{
                        position: "absolute",
                        top: virtual.topForIndex(startIndex + i),
                        left: 0,
                        right: 0,
                      }}
                      onClick={(event) =>
                        handleRowSelect(item.segment_id, event)
                      }
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
                      <span className={styles.statusStack}>
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
                        {item.tags?.some((tag) =>
                          STRUCTURAL_REVIEW_TAGS.has(tag),
                        ) ? (
                          <span
                            className={`${styles.statusChip} ${styles.statusStructure}`}
                            title={m.statusStructureDriftHint}
                          >
                            {m.statusStructureDrift}
                          </span>
                        ) : null}
                        {item.tags?.includes("source_residue") ? (
                          <span
                            className={`${styles.statusChip} ${styles.statusResidue}`}
                            title={m.statusSourceResidueHint}
                          >
                            {m.statusSourceResidue}
                          </span>
                        ) : null}
                        {item.tags?.includes("glossary_not_applied") ? (
                          <span
                            className={`${styles.statusChip} ${styles.statusGlossary}`}
                            title={m.statusGlossaryNotAppliedHint}
                          >
                            {m.statusGlossaryNotApplied}
                          </span>
                        ) : null}
                        {item.tags?.includes("term_inconsistency") ? (
                          <span
                            className={`${styles.statusChip} ${styles.statusTerm}`}
                            title={m.statusTermInconsistencyHint}
                          >
                            {m.statusTermInconsistency}
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
                        {item.tags?.some((tag) =>
                          MODEL_ANOMALY_TAGS.has(tag),
                        ) ? (
                          <span
                            className={`${styles.statusChip} ${styles.statusLow}`}
                            title={m.statusModelAnomalyHint}
                          >
                            {m.statusModelAnomaly}
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
                <Pill
                  variant="ghost"
                  onClick={() => void handleUndoRetranslate()}
                  disabled={!retranslateUndo || undoingRetranslate || dirty}
                >
                  {undoingRetranslate
                    ? m.retranslateUndoRunning
                    : m.retranslateUndoAction}
                </Pill>
                <Pill onClick={() => void handleSave()} disabled={!dirty}>
                  {m.editorSaveAction}
                </Pill>
              </span>
            </div>
            {selectedItem.subtask_ids?.length ? (
              <div className={styles.debugHintRow}>
                <div className={styles.debugHint}>
                  {format(m.subtaskHint, {
                    ids: selectedItem.subtask_ids.join(", "),
                  })}
                </div>
                <button
                  type="button"
                  className={styles.copyButton}
                  onClick={() =>
                    void copyToClipboard(
                      selectedItem.subtask_ids?.join(", ") ?? "",
                      m.copySubtaskIdsDone,
                    )
                  }
                >
                  {m.copySubtaskIds}
                </button>
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
