import { useEffect } from "react";
import { create } from "zustand";

import {
  BridgeError,
  dialogsBridge,
  epubCompressBridge,
  epubConvertBridge,
  epubMergeBridge,
  txtToEpubBridge,
  glossaryBridge,
  glossaryReviewBridge,
  replacementBridge,
  translationBridge,
} from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";
import { playTaskSound } from "@/utils/taskSounds";
import type {
  TaskFailure,
  GlossaryReviewRoundProgress,
  TaskHeader,
  TaskSnapshot,
  TaskStatus,
} from "@/bridge";

export type RunKind =
  | "translation"
  | "glossary"
  | "glossary_review"
  | "replacement"
  | "epub_compress"
  | "epub_merge"
  | "epub_convert"
  | "txt_to_epub";

const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set([
  "completed",
  "failed",
  "stopped",
]);

interface KindRuntime {
  activeTaskId: string | null;
  header: TaskHeader | null;
  snapshot: TaskSnapshot | null;
  failures: TaskFailure[];
  loading: boolean;
  lastError: BridgeError | null;
  /** Most-recent N errors (newest last) so the banner can hint
   * "+N earlier" when several errors fire while the user is paused
   * on the page. Without this every new error replaces the prior one
   * in the single-slot ``lastError`` and the diagnostic trail is
   * gone the moment the next click fails. */
  errorHistory: BridgeError[];
  lastUpdatedAt: number | null;
}

const ERROR_HISTORY_CAP = 10;
// Auto-open the output folder only when we observed a running→completed
// transition WITHIN the current session. Restarting the app onto an
// already-completed task means no transition happened in this session,
// so we don't pop the Finder on launch. Both sets are session-scoped:
// fresh on each app start.
const seenInFlightTaskIds = new Set<string>();
const autoOpenedTaskIds = new Set<string>();
const soundNotifiedTaskIds = new Set<string>();
// Per-task-id dismissal tracking for the completion-with-failures
// dialog. Module-level so navigating away and back doesn't re-open
// the dialog the user already answered.
const completionWithFailuresDismissed = new Set<string>();
// Per-task-id tracking so the "fully successful run" toast only fires
// once per task even as the RunPage re-mounts on tab switches.
const cleanCompletionToastShown = new Set<string>();
// Same idea for the "completed with low-confidence segments" toast.
const lowConfToastShown = new Set<string>();

export function hasShownLowConfToast(taskId: string): boolean {
  return lowConfToastShown.has(taskId);
}

export function markLowConfToastShown(taskId: string): void {
  lowConfToastShown.add(taskId);
}

export function hasDismissedCompletionWithFailures(taskId: string): boolean {
  return completionWithFailuresDismissed.has(taskId);
}

export function markCompletionWithFailuresDismissed(taskId: string): void {
  completionWithFailuresDismissed.add(taskId);
}

export function hasShownCleanCompletionToast(taskId: string): boolean {
  return cleanCompletionToastShown.has(taskId);
}

export function markCleanCompletionToastShown(taskId: string): void {
  cleanCompletionToastShown.add(taskId);
}

const emptyRuntime: KindRuntime = {
  activeTaskId: null,
  header: null,
  snapshot: null,
  failures: [],
  loading: false,
  lastError: null,
  errorHistory: [],
  lastUpdatedAt: null,
};

interface RuntimeState {
  translation: KindRuntime;
  glossary: KindRuntime;
  glossary_review: KindRuntime;
  replacement: KindRuntime;
  epub_compress: KindRuntime;
  epub_merge: KindRuntime;
  epub_convert: KindRuntime;
  txt_to_epub: KindRuntime;
  refreshActiveTask: (kind: RunKind) => Promise<void>;
  pollSnapshot: (kind: RunKind) => Promise<void>;
  setActiveTaskId: (kind: RunKind, taskId: string | null) => void;
  setLastError: (kind: RunKind, error: BridgeError | null) => void;
  clearError: (kind: RunKind) => void;
}

const bridges = {
  translation: translationBridge,
  glossary: glossaryBridge,
  glossary_review: glossaryReviewBridge,
  replacement: replacementBridge,
  epub_compress: epubCompressBridge,
  epub_merge: epubMergeBridge,
  epub_convert: epubConvertBridge,
  txt_to_epub: txtToEpubBridge,
} as const;

function pickActive(tasks: TaskHeader[]): TaskHeader | null {
  const running = tasks.find(
    (task) =>
      task.status === "running" ||
      task.status === "stopping" ||
      task.status === "pending",
  );
  if (running) return running;
  return tasks[0] ?? null;
}

function withKind(
  state: RuntimeState,
  kind: RunKind,
  patch: Partial<KindRuntime>,
): Pick<RuntimeState, RunKind> {
  return { [kind]: { ...state[kind], ...patch } } as Pick<
    RuntimeState,
    RunKind
  >;
}

function asBridgeError(error: unknown): BridgeError {
  if (BridgeError.isBridgeError(error)) return error;
  return new BridgeError({
    code: "bridge.io_error",
    message: error instanceof Error ? error.message : String(error),
    retryable: true,
  });
}

export const useRuntimeStore = create<RuntimeState>((set, get) => ({
  translation: emptyRuntime,
  glossary: emptyRuntime,
  glossary_review: emptyRuntime,
  replacement: emptyRuntime,
  epub_compress: emptyRuntime,
  epub_merge: emptyRuntime,
  epub_convert: emptyRuntime,
  txt_to_epub: emptyRuntime,

  setActiveTaskId: (kind, taskId) =>
    set((state) =>
      withKind(state, kind, {
        activeTaskId: taskId,
        snapshot:
          taskId === state[kind].activeTaskId ? state[kind].snapshot : null,
        failures:
          taskId === state[kind].activeTaskId ? state[kind].failures : [],
      }),
    ),

  setLastError: (kind, error) =>
    set((state) => {
      const next = state[kind];
      const history =
        error === null
          ? next.errorHistory
          : [...next.errorHistory, error].slice(-ERROR_HISTORY_CAP);
      return withKind(state, kind, { lastError: error, errorHistory: history });
    }),

  clearError: (kind) =>
    set((state) =>
      withKind(state, kind, { lastError: null, errorHistory: [] }),
    ),

  refreshActiveTask: async (kind) => {
    set((state) => withKind(state, kind, { loading: true, lastError: null }));
    try {
      const { tasks } = await bridges[kind].listRecentTasks(1);
      const header = pickActive(tasks);
      let recoveredTaskId: string | null = null;
      if (!header) {
        const probe = await bridges[kind].probeContinuable();
        recoveredTaskId = probe.continuable ? probe.task_id : null;
      }
      const activeTaskId = header?.id ?? recoveredTaskId;
      set((state) =>
        withKind(state, kind, {
          loading: false,
          header,
          activeTaskId,
          snapshot:
            activeTaskId === state[kind].activeTaskId
              ? state[kind].snapshot
              : null,
          failures:
            activeTaskId === state[kind].activeTaskId
              ? state[kind].failures
              : [],
        }),
      );
      if (activeTaskId) {
        await get().pollSnapshot(kind);
      }
    } catch (error) {
      set((state) =>
        withKind(state, kind, {
          loading: false,
          lastError: asBridgeError(error),
        }),
      );
    }
  },

  pollSnapshot: async (kind) => {
    const taskId = get()[kind].activeTaskId;
    if (!taskId) return;
    try {
      const [{ snapshot }, { failures }] = await Promise.all([
        bridges[kind].readSnapshot(taskId),
        bridges[kind].listFailedSubtasks(taskId),
      ]);
      void maybeOpenOutputFolder(kind, taskId, snapshot.header.status);
      maybePlayTaskSound(taskId, snapshot);
      set((state) =>
        withKind(state, kind, {
          snapshot,
          header: snapshot.header,
          failures,
          lastUpdatedAt: Date.now(),
          lastError: null,
        }),
      );
    } catch (error) {
      const bridgeError = asBridgeError(error);
      if (bridgeError.code === "bridge.not_found") {
        set((state) =>
          withKind(state, kind, {
            activeTaskId: null,
            header: null,
            snapshot: null,
            failures: [],
            lastError: null,
          }),
        );
        return;
      }
      set((state) => withKind(state, kind, { lastError: bridgeError }));
    }
  },
}));

export type SnapshotShape = {
  status: TaskStatus;
  progress: TaskSnapshot["progress"];
  roundProgress: GlossaryReviewRoundProgress | null;
  usage: TaskSnapshot["usage"];
  lowConfidence: { total: number; sourceResidue: number };
  subtasks: TaskSnapshot["subtasks"];
  failures: TaskFailure[];
  isIdle: boolean;
  isRunning: boolean;
  lastError: BridgeError | null;
};

export function useRunSnapshot(kind: RunKind): SnapshotShape {
  return useRuntimeStore((state) => {
    const runtime = state[kind];
    const status = runtime.snapshot?.header.status ?? "pending";
    return {
      status,
      progress: runtime.snapshot?.progress ?? {
        total: 0,
        pending: 0,
        running: 0,
        completed: 0,
        failed: 0,
        skipped: 0,
        elapsed_seconds: 0,
        rate_per_second: 0,
        longest_running_seconds: 0,
      },
      roundProgress: runtime.snapshot?.round_progress ?? null,
      usage: runtime.snapshot?.usage ?? {
        input_tokens: 0,
        output_tokens: 0,
        cached_input_tokens: 0,
        total_tokens: 0,
      },
      lowConfidence: {
        total: runtime.snapshot?.low_confidence?.total ?? 0,
        sourceResidue: runtime.snapshot?.low_confidence?.source_residue ?? 0,
      },
      subtasks: runtime.snapshot?.subtasks ?? [],
      failures: runtime.failures,
      isIdle: runtime.activeTaskId === null,
      isRunning: status === "running",
      lastError: runtime.lastError,
    };
  });
}

/**
 * Polls the bridge for snapshot/failure updates while the kind has an active
 * task in a non-terminal state. Stops polling once the task reaches
 * `completed`, `failed`, or `stopped`. Safe to call from multiple components
 * — each mounts its own interval and they read the same store.
 */
export function usePollRunSnapshot(kind: RunKind, intervalMs = 2000): void {
  const activeTaskId = useRuntimeStore((state) => state[kind].activeTaskId);
  const status =
    useRuntimeStore((state) => state[kind].snapshot?.header.status) ??
    "pending";
  const pollSnapshot = useRuntimeStore((state) => state.pollSnapshot);

  useEffect(() => {
    if (!activeTaskId) return;
    if (TERMINAL_STATUSES.has(status)) return;
    let cancelled = false;
    const tick = () => {
      if (cancelled) return;
      void pollSnapshot(kind);
    };
    tick();
    const handle = window.setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [kind, activeTaskId, status, intervalMs, pollSnapshot]);
}

async function maybeOpenOutputFolder(
  kind: RunKind,
  taskId: string,
  status: TaskStatus,
): Promise<void> {
  // Track whether we've seen this task in an in-flight state during
  // this session. App restarts onto an already-completed task never
  // pass through this branch, so the auto-open never fires for them.
  if (status === "running" || status === "pending" || status === "stopping") {
    seenInFlightTaskIds.add(taskId);
    return;
  }
  if (status !== "completed") return;
  if (!seenInFlightTaskIds.has(taskId)) return;
  if (autoOpenedTaskIds.has(taskId)) return;
  const settings = useSettingsStore.getState();
  if (
    kind === "replacement" ||
    kind === "epub_compress" ||
    kind === "epub_merge" ||
    kind === "epub_convert" ||
    kind === "txt_to_epub"
  ) {
    return;
  }
  const enabled =
    kind === "translation"
      ? settings.translation.draft?.auto_open_output_folder
      : kind === "glossary"
        ? settings.glossary.draft?.auto_open_output_folder
        : settings.glossary_review.draft?.auto_open_output_folder;
  if (!enabled) return;
  autoOpenedTaskIds.add(taskId);
  try {
    const artifacts = await bridges[kind].readArtifacts(taskId);
    if (artifacts.output_folder) {
      await dialogsBridge.openDirectory(artifacts.output_folder);
    }
  } catch (error) {
    useRuntimeStore.getState().setLastError(kind, asBridgeError(error));
  }
}

function maybePlayTaskSound(taskId: string, snapshot: TaskSnapshot): void {
  const status = snapshot.header.status;
  if (status === "running" || status === "pending" || status === "stopping") {
    seenInFlightTaskIds.add(taskId);
    return;
  }
  if (!TERMINAL_STATUSES.has(status)) return;
  if (status === "stopped") return;
  if (!seenInFlightTaskIds.has(taskId)) return;
  if (soundNotifiedTaskIds.has(taskId)) return;
  const enabled =
    useSettingsStore.getState().app.draft?.task_sound_notifications ?? false;
  if (!enabled) return;
  soundNotifiedTaskIds.add(taskId);
  const hasFailures = snapshot.progress.failed > 0;
  playTaskSound(status === "failed" || hasFailures ? "attention" : "success");
}
