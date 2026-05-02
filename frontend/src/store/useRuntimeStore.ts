import { useEffect } from "react";
import { create } from "zustand";

import {
  BridgeError,
  dialogsBridge,
  glossaryBridge,
  replacementBridge,
  translationBridge,
} from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";
import type {
  TaskFailure,
  TaskHeader,
  TaskSnapshot,
  TaskStatus,
} from "@/bridge";

export type RunKind = "translation" | "glossary" | "replacement";

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
const autoOpenedTaskIds = new Set<string>();
// Per-task-id dismissal tracking for the completion-with-failures
// dialog. Module-level so navigating away and back doesn't re-open
// the dialog the user already answered.
const completionWithFailuresDismissed = new Set<string>();

export function hasDismissedCompletionWithFailures(taskId: string): boolean {
  return completionWithFailuresDismissed.has(taskId);
}

export function markCompletionWithFailuresDismissed(taskId: string): void {
  completionWithFailuresDismissed.add(taskId);
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
  replacement: KindRuntime;
  refreshActiveTask: (kind: RunKind) => Promise<void>;
  pollSnapshot: (kind: RunKind) => Promise<void>;
  setActiveTaskId: (kind: RunKind, taskId: string | null) => void;
  setLastError: (kind: RunKind, error: BridgeError | null) => void;
  clearError: (kind: RunKind) => void;
}

const bridges = {
  translation: translationBridge,
  glossary: glossaryBridge,
  replacement: replacementBridge,
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
  replacement: emptyRuntime,

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
  usage: TaskSnapshot["usage"];
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
      },
      usage: runtime.snapshot?.usage ?? {
        input_tokens: 0,
        output_tokens: 0,
        total_tokens: 0,
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
  if (status !== "completed") return;
  if (autoOpenedTaskIds.has(taskId)) return;
  const settings = useSettingsStore.getState();
  if (kind === "replacement") return;
  const enabled =
    kind === "translation"
      ? settings.translation.draft?.auto_open_output_folder
      : settings.glossary.draft?.auto_open_output_folder;
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
