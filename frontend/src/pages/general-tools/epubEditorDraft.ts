import type { EpubTocEntry } from "@/bridge";

const OPEN_KEY = "transoria.epubEditor.open";
const DRAFT_KEY = "transoria.epubEditor.draft";

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
  scope: "current" | "text" | "styles" | "all";
  caseSensitive: boolean;
  sidebarWidth?: number;
  sourceWidth?: number;
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
    const raw = window.sessionStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const draft = JSON.parse(raw) as EpubEditorDraft;
    return typeof draft.sessionId === "string" && typeof draft.inputPath === "string" ? draft : null;
  } catch {
    return null;
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
  try {
    window.sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    // The backend session is still closed explicitly.
  }
}
