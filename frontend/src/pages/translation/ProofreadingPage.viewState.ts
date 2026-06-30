import type { ProofreadingItem, TaskHeader } from "@/bridge";

export const PROOFREADING_VIEW_STATE_KEY = "transoria:proofreading:view:v1";

export interface ProofreadingViewState {
  activeTaskId: string;
  selectedSegmentId: string | null;
  scrollTop: number;
}

export interface ProofreadingInitialTask {
  taskId: string | null;
  restoreState: ProofreadingViewState | null;
}

export function parseProofreadingViewState(
  raw: string | null,
): ProofreadingViewState | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<ProofreadingViewState>;
    if (!value || typeof value !== "object") return null;
    if (typeof value.activeTaskId !== "string" || !value.activeTaskId) {
      return null;
    }
    return {
      activeTaskId: value.activeTaskId,
      selectedSegmentId:
        typeof value.selectedSegmentId === "string" && value.selectedSegmentId
          ? value.selectedSegmentId
          : null,
      scrollTop:
        typeof value.scrollTop === "number" && Number.isFinite(value.scrollTop)
          ? Math.max(0, value.scrollTop)
          : 0,
    };
  } catch {
    return null;
  }
}

export function readProofreadingViewState(): ProofreadingViewState | null {
  if (typeof window === "undefined") return null;
  return parseProofreadingViewState(
    window.sessionStorage.getItem(PROOFREADING_VIEW_STATE_KEY),
  );
}

export function writeProofreadingViewState(state: ProofreadingViewState): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(PROOFREADING_VIEW_STATE_KEY, JSON.stringify(state));
}

export function resolveInitialProofreadingTask(
  tasks: TaskHeader[],
  launchTaskId: string | null,
  storedState: ProofreadingViewState | null,
): ProofreadingInitialTask {
  if (tasks.length === 0) return { taskId: null, restoreState: null };

  if (launchTaskId) {
    const launched = tasks.find((task) => task.id === launchTaskId);
    if (launched) return { taskId: launched.id, restoreState: null };
  }

  if (storedState) {
    const restored = tasks.find((task) => task.id === storedState.activeTaskId);
    if (restored) return { taskId: restored.id, restoreState: storedState };
  }

  return { taskId: tasks[0].id, restoreState: null };
}

export function resolveProofreadingSelection(
  items: ProofreadingItem[],
  preferredSegmentId: string | null,
): string | null {
  if (
    preferredSegmentId &&
    items.some((item) => item.segment_id === preferredSegmentId)
  ) {
    return preferredSegmentId;
  }
  return items[0]?.segment_id ?? null;
}
