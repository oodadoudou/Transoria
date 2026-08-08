import { create } from "zustand";

export interface BatchRetranslateProgress {
  total: number;
  processed: number;
  completed: number;
  unresolved: number;
  stale: number;
  failed: number;
  submitted: number;
  active: number;
  longestSeconds: number;
}

export interface RetranslateActivity {
  status: "pending" | "running";
  elapsedSeconds: number;
}

export interface ProofreadingRetranslateSession {
  inflightRetranslates: Record<string, string>;
  retranslateActivities: Record<string, RetranslateActivity>;
  batchRetranslating: boolean;
  batchRetranslateProgress: BatchRetranslateProgress | null;
  completedRevision: number;
}

export type RetranslateValueUpdater<T> = T | ((previous: T) => T);

const EMPTY_SESSION: ProofreadingRetranslateSession = {
  inflightRetranslates: {},
  retranslateActivities: {},
  batchRetranslating: false,
  batchRetranslateProgress: null,
  completedRevision: 0,
};

export const EMPTY_PROOFREADING_RETRANSLATE_SESSION = EMPTY_SESSION;

interface ProofreadingRetranslateStore {
  sessions: Record<string, ProofreadingRetranslateSession>;
  setInflightRetranslates: (
    taskId: string,
    updater: RetranslateValueUpdater<Record<string, string>>,
  ) => void;
  setRetranslateActivities: (
    taskId: string,
    updater: RetranslateValueUpdater<Record<string, RetranslateActivity>>,
  ) => void;
  setBatchRetranslating: (taskId: string, running: boolean) => void;
  setBatchRetranslateProgress: (
    taskId: string,
    progress: BatchRetranslateProgress | null,
  ) => void;
  markRetranslateCompleted: (taskId: string) => void;
}

function resolveUpdater<T>(updater: RetranslateValueUpdater<T>, current: T): T {
  return typeof updater === "function"
    ? (updater as (previous: T) => T)(current)
    : updater;
}

export const useProofreadingRetranslateStore =
  create<ProofreadingRetranslateStore>((set) => ({
    sessions: {},
    setInflightRetranslates: (taskId, updater) =>
      set((state) => {
        const current = state.sessions[taskId] ?? EMPTY_SESSION;
        return {
          sessions: {
            ...state.sessions,
            [taskId]: {
              ...current,
              inflightRetranslates: resolveUpdater(
                updater,
                current.inflightRetranslates,
              ),
            },
          },
        };
      }),
    setRetranslateActivities: (taskId, updater) =>
      set((state) => {
        const current = state.sessions[taskId] ?? EMPTY_SESSION;
        return {
          sessions: {
            ...state.sessions,
            [taskId]: {
              ...current,
              retranslateActivities: resolveUpdater(
                updater,
                current.retranslateActivities,
              ),
            },
          },
        };
      }),
    setBatchRetranslating: (taskId, running) =>
      set((state) => {
        const current = state.sessions[taskId] ?? EMPTY_SESSION;
        return {
          sessions: {
            ...state.sessions,
            [taskId]: { ...current, batchRetranslating: running },
          },
        };
      }),
    setBatchRetranslateProgress: (taskId, progress) =>
      set((state) => {
        const current = state.sessions[taskId] ?? EMPTY_SESSION;
        return {
          sessions: {
            ...state.sessions,
            [taskId]: { ...current, batchRetranslateProgress: progress },
          },
        };
      }),
    markRetranslateCompleted: (taskId) =>
      set((state) => {
        const current = state.sessions[taskId] ?? EMPTY_SESSION;
        return {
          sessions: {
            ...state.sessions,
            [taskId]: {
              ...current,
              completedRevision: current.completedRevision + 1,
            },
          },
        };
      }),
  }));
