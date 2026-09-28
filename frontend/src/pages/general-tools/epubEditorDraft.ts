import type { EpubTocEntry } from "@/bridge";

const OPEN_KEY = "transoria.epubEditor.open";
const DRAFT_KEY = "transoria.epubEditor.draft";
const STAGED_KEY = "transoria.epubEditor.staged";

export interface EpubEditorDraft {
  sessionId: string;
  inputPath: string;
  selectedPath: string;
  sourceDraft: string | null;
  dirty: boolean;
  tocDraft: EpubTocEntry[];
  sideView: "files" | "toc" | "spine" | "book";
  bookIndex?: number;
  paneView: "source" | "preview";
  searchOpen: boolean;
  query: string;
  replacement: string;
  scope: "current" | "text" | "styles" | "all" | "selection";
  selectionRange?: { path: string; start: number; end: number } | null;
  caseSensitive: boolean;
  regularExpression?: boolean;
  sidebarWidth?: number;
  sourceWidth?: number;
  previewZoom?: number;
  previewWrap?: boolean;
  sourceZoom?: number;
}

export function editorWasOpen(): boolean {
  try {
    return window.sessionStorage.getItem(OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

export function markEditorOpen(open: boolean): void {
  try {
    if (open) window.sessionStorage.setItem(OPEN_KEY, "1");
    else window.sessionStorage.removeItem(OPEN_KEY);
  } catch {
    // The current page still works without session storage.
  }
}

export function readEditorDraft(): EpubEditorDraft | null {
  try {
    const raw = window.sessionStorage.getItem(DRAFT_KEY) ?? window.localStorage.getItem(STAGED_KEY);
    if (!raw) return null;
    const draft = JSON.parse(raw) as EpubEditorDraft;
    return typeof draft.sessionId === "string" && typeof draft.inputPath === "string" ? draft : null;
  } catch {
    return null;
  }
}

export function stageEditorDraft(draft: EpubEditorDraft): boolean {
  try {
    window.localStorage.setItem(STAGED_KEY, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function clearStagedEditorDraft(): void {
  try {
    window.localStorage.removeItem(STAGED_KEY);
  } catch {
    // The backend session remains available until its cache expires.
  }
}

export function writeEditorDraft(draft: EpubEditorDraft): boolean {
  try {
    window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function clearEditorDraft(): void {
  clearStagedEditorDraft();
  try {
    window.sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // The backend session is still closed explicitly.
  }
}
