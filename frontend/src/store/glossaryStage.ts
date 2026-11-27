import { create } from "zustand";
import { useRuntimeStore } from "./useRuntimeStore";

export type GlossarySource = "extract" | "existing";

const SOURCE_STORAGE_KEY = "transoria.glossary.source";

// A review stays in charge of the controls until it finishes; after that Start begins a new extraction.
const REVIEW_HOLDS_CONTROLS = new Set(["pending", "running", "pausing", "paused", "stopping", "stopped", "failed"]);

function loadSource(): GlossarySource {
  try {
    return window.localStorage.getItem(SOURCE_STORAGE_KEY) === "existing" ? "existing" : "extract";
  } catch {
    return "extract";
  }
}

interface GlossarySourceState {
  source: GlossarySource;
  setSource: (source: GlossarySource) => void;
}

export const useGlossarySource = create<GlossarySourceState>((set) => ({
  source: typeof window === "undefined" ? "extract" : loadSource(),
  setSource: (source) => {
    try {
      window.localStorage.setItem(SOURCE_STORAGE_KEY, source);
    } catch {
      // The choice still applies for this session.
    }
    set({ source });
  },
}));

export function useReviewLinkedToExtraction(): boolean {
  return useRuntimeStore((state) => {
    const extractionId = state.glossary.activeTaskId;
    const source = state.glossary_review.snapshot?.metadata?.source_task_id;
    return Boolean(extractionId) && source === extractionId;
  });
}

export function useGlossaryStageKind(): "glossary" | "glossary_review" {
  const source = useGlossarySource((state) => state.source);
  const linked = useReviewLinkedToExtraction();
  const reviewStatus = useRuntimeStore((state) => state.glossary_review.snapshot?.header.status);
  if (source === "existing") return "glossary_review";
  return linked && reviewStatus && REVIEW_HOLDS_CONTROLS.has(reviewStatus) ? "glossary_review" : "glossary";
}
