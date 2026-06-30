import { describe, expect, it } from "vitest";

import type { ProofreadingItem, TaskHeader } from "@/bridge";
import {
  parseProofreadingViewState,
  resolveInitialProofreadingTask,
  resolveProofreadingSelection,
} from "./ProofreadingPage.viewState";

const tasks: TaskHeader[] = [
  {
    id: "task-a",
    kind: "translation",
    status: "completed",
    created_at: "2026-06-30T00:00:00Z",
    updated_at: "2026-06-30T00:00:00Z",
  },
  {
    id: "task-b",
    kind: "translation",
    status: "completed",
    created_at: "2026-06-30T00:00:00Z",
    updated_at: "2026-06-30T00:00:00Z",
  },
];

const items: ProofreadingItem[] = [
  { segment_id: "seg-1", src: "a", dst: "A", low_confidence: false },
  { segment_id: "seg-2", src: "b", dst: "B", low_confidence: true },
];

describe("proofreading view state", () => {
  it("parses valid persisted view state", () => {
    expect(
      parseProofreadingViewState(
        JSON.stringify({
          activeTaskId: "task-a",
          selectedSegmentId: "seg-2",
          scrollTop: 480,
        }),
      ),
    ).toEqual({
      activeTaskId: "task-a",
      selectedSegmentId: "seg-2",
      scrollTop: 480,
    });
  });

  it("rejects malformed or incomplete persisted view state", () => {
    expect(parseProofreadingViewState("not-json")).toBeNull();
    expect(parseProofreadingViewState(JSON.stringify({ scrollTop: 10 }))).toBeNull();
  });

  it("prefers explicit launch task over stored state", () => {
    expect(
      resolveInitialProofreadingTask(tasks, "task-b", {
        activeTaskId: "task-a",
        selectedSegmentId: "seg-2",
        scrollTop: 480,
      }),
    ).toEqual({ taskId: "task-b", restoreState: null });
  });

  it("restores stored task when there is no explicit launch", () => {
    const stored = {
      activeTaskId: "task-b",
      selectedSegmentId: "seg-2",
      scrollTop: 480,
    };

    expect(resolveInitialProofreadingTask(tasks, null, stored)).toEqual({
      taskId: "task-b",
      restoreState: stored,
    });
  });

  it("falls back to the first task when stored task is stale", () => {
    expect(
      resolveInitialProofreadingTask(tasks, null, {
        activeTaskId: "missing",
        selectedSegmentId: "seg-2",
        scrollTop: 480,
      }),
    ).toEqual({ taskId: "task-a", restoreState: null });
  });

  it("restores a valid selected segment and falls back when stale", () => {
    expect(resolveProofreadingSelection(items, "seg-2")).toBe("seg-2");
    expect(resolveProofreadingSelection(items, "missing")).toBe("seg-1");
    expect(resolveProofreadingSelection([], "missing")).toBeNull();
  });
});
