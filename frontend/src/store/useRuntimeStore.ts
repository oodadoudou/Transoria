import { useEffect } from 'react';
import { create } from 'zustand';

import {
  BridgeError,
  glossaryBridge,
  translationBridge,
} from '@/bridge';
import type {
  TaskFailure,
  TaskHeader,
  TaskSnapshot,
  TaskStatus,
} from '@/bridge';

export type RunKind = 'translation' | 'glossary';

const TERMINAL_STATUSES: ReadonlySet<TaskStatus> = new Set([
  'completed',
  'failed',
  'stopped',
]);

interface KindRuntime {
  activeTaskId: string | null;
  header: TaskHeader | null;
  snapshot: TaskSnapshot | null;
  failures: TaskFailure[];
  loading: boolean;
  lastError: BridgeError | null;
  lastUpdatedAt: number | null;
}

const emptyRuntime: KindRuntime = {
  activeTaskId: null,
  header: null,
  snapshot: null,
  failures: [],
  loading: false,
  lastError: null,
  lastUpdatedAt: null,
};

interface RuntimeState {
  translation: KindRuntime;
  glossary: KindRuntime;
  refreshActiveTask: (kind: RunKind) => Promise<void>;
  pollSnapshot: (kind: RunKind) => Promise<void>;
  setActiveTaskId: (kind: RunKind, taskId: string | null) => void;
  setLastError: (kind: RunKind, error: BridgeError | null) => void;
  clearError: (kind: RunKind) => void;
}

const bridges = {
  translation: translationBridge,
  glossary: glossaryBridge,
} as const;

function pickActive(tasks: TaskHeader[]): TaskHeader | null {
  const running = tasks.find(
    (task) =>
      task.status === 'running' ||
      task.status === 'stopping' ||
      task.status === 'pending',
  );
  if (running) return running;
  return tasks[0] ?? null;
}

function withKind(
  state: RuntimeState,
  kind: RunKind,
  patch: Partial<KindRuntime>,
): Pick<RuntimeState, RunKind> {
  return { [kind]: { ...state[kind], ...patch } } as Pick<RuntimeState, RunKind>;
}

function asBridgeError(error: unknown): BridgeError {
  if (BridgeError.isBridgeError(error)) return error;
  return new BridgeError({
    code: 'bridge.io_error',
    message: error instanceof Error ? error.message : String(error),
    retryable: true,
  });
}

export const useRuntimeStore = create<RuntimeState>((set, get) => ({
  translation: emptyRuntime,
  glossary: emptyRuntime,

  setActiveTaskId: (kind, taskId) =>
    set((state) =>
      withKind(state, kind, {
        activeTaskId: taskId,
        snapshot: taskId === state[kind].activeTaskId ? state[kind].snapshot : null,
        failures: taskId === state[kind].activeTaskId ? state[kind].failures : [],
      }),
    ),

  setLastError: (kind, error) =>
    set((state) => withKind(state, kind, { lastError: error })),

  clearError: (kind) =>
    set((state) => withKind(state, kind, { lastError: null })),

  refreshActiveTask: async (kind) => {
    set((state) => withKind(state, kind, { loading: true, lastError: null }));
    try {
      const { tasks } = await bridges[kind].listRecentTasks(1);
      const header = pickActive(tasks);
      set((state) =>
        withKind(state, kind, {
          loading: false,
          header,
          activeTaskId: header?.id ?? null,
          snapshot: header ? state[kind].snapshot : null,
          failures: header ? state[kind].failures : [],
        }),
      );
      if (header) {
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
      if (bridgeError.code === 'bridge.not_found') {
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
  progress: TaskSnapshot['progress'];
  usage: TaskSnapshot['usage'];
  failures: TaskFailure[];
  isIdle: boolean;
  isRunning: boolean;
  lastError: BridgeError | null;
};

export function useRunSnapshot(kind: RunKind): SnapshotShape {
  return useRuntimeStore((state) => {
    const runtime = state[kind];
    const status = runtime.snapshot?.header.status ?? 'pending';
    return {
      status,
      progress:
        runtime.snapshot?.progress ?? {
          total: 0,
          pending: 0,
          running: 0,
          completed: 0,
          failed: 0,
          skipped: 0,
          rate_per_second: 0,
          eta_seconds: 0,
        },
      usage:
        runtime.snapshot?.usage ?? {
          input_tokens: 0,
          output_tokens: 0,
          total_tokens: 0,
        },
      failures: runtime.failures,
      isIdle: runtime.activeTaskId === null,
      isRunning: status === 'running',
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
export function usePollRunSnapshot(
  kind: RunKind,
  intervalMs = 2000,
): void {
  const activeTaskId = useRuntimeStore((state) => state[kind].activeTaskId);
  const status =
    useRuntimeStore((state) => state[kind].snapshot?.header.status) ?? 'pending';
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
