import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { EditorView } from "@codemirror/view";
import type { EditorState } from "@codemirror/state";
import { undo, redo, undoDepth, redoDepth } from "@codemirror/commands";
import { css } from "@codemirror/lang-css";
import { xml } from "@codemirror/lang-xml";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, BookOpen, CaseSensitive, Check, ChevronDown, ChevronRight, CodeXml, CornerDownRight, CornerUpLeft, CornerUpRight, Copy, Download, FileCode2, FilePlus2, FileText, FolderOpen, ImagePlus, ListTree, MoreHorizontal, Pencil, Plus, Regex, Replace, Save, Scissors, Search, Trash2, WrapText, X, ZoomIn, ZoomOut, Maximize, ScanLine, Link2, Wrench } from "lucide-react";

import { dialogsBridge, epubContentBridge, type EpubContentFile, type EpubContentMatch, type EpubContentSession, type EpubTocEntry } from "@/bridge";
import { useMessages } from "@/locales";
import { useSettingsStore } from "@/store/useSettingsStore";
import { nextMatchIndex, previewDestination, relativeResourceHref, resourceAfterHistory, resourcePreviewPath, savedEditorHistory, searchScopePaths, xmlAttribute } from "./epubEditorActions";
import { numberedResourceNames, selectFiles } from "./epubFileSelection";
import { clearEditorDraft, clearStagedEditorDraft, readEditorDraft, stageEditorDraft, writeEditorDraft, type EpubEditorDraft } from "./epubEditorDraft";
import styles from "./EpubContentPage.module.css";
import { EpubPreviewFrame } from "./EpubPreviewFrame";
import { EpubEditorTools } from "./EpubEditorTools";
import { EpubSavedSearches } from "./EpubSavedSearches";
import type { SavedSearch, SearchOptions } from "./epubSearchLibrary";
import { SearchRequests } from "./epubSearchRequests";
import { previewAtZoom, type ReadingLocation } from "./epubPreview";
import { fixedSpreadMate, previewRendition } from "./epubRendition";

type SideView = "files" | "toc" | "book";
type Scope = "current" | "text" | "styles" | "all" | "selection";
type ResourceAction = "add" | "rename" | "replace" | "export" | "delete";
type ReplaceProposal = Awaited<ReturnType<typeof epubContentBridge.previewReplace>> & {
  selection?: { path: string; start: number; end: number };
  query: string;
  replacement: string;
  paths: string[];
  caseSensitive: boolean;
  regularExpression: boolean;
  ignoreMarkup: boolean;
};
type FileNode = { kind: "folder"; name: string; path: string; children: FileNode[] } | { kind: "file"; name: string; file: EpubContentFile };
const RECENT_KEY = "transoria.epubEditor.recent";

function readRecent(): string[] {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.toLowerCase().endsWith(".epub")).slice(0, 4) : [];
  } catch {
    return [];
  }
}

function fileTree(files: EpubContentFile[], spine: string[]): FileNode[] {
  const roots: FileNode[] = [];
  const readingIndex = new Map(spine.map((path, index) => [path, index]));
  for (const file of files) {
    const parts = file.path.split("/");
    let children = roots;
    let folderPath = "";
    for (const part of parts.slice(0, -1)) {
      folderPath = folderPath ? `${folderPath}/${part}` : part;
      let folder = children.find((node) => node.kind === "folder" && node.name === part);
      if (!folder) {
        folder = { kind: "folder", name: part, path: folderPath, children: [] };
        children.push(folder);
      }
      if (folder.kind === "folder") children = folder.children;
    }
    children.push({ kind: "file", name: parts[parts.length - 1], file });
  }
  const rankFile = (file: EpubContentFile): number => {
    const path = file.path.toLowerCase();
    if (path.endsWith("nav.xhtml") || path.endsWith("toc.ncx") || file.media_type === "application/x-dtbncx+xml") return 2;
    if (file.media_type === "application/xhtml+xml" || file.media_type === "text/html" || file.media_type === "text/plain") return 0;
    if (file.media_type === "text/css") return 1;
    if (file.media_type.startsWith("image/")) return 4;
    return 3;
  };
  const rankNode = (node: FileNode): number => node.kind === "file"
    ? rankFile(node.file)
    : Math.min(4, ...node.children.map(rankNode));
  const orderNode = (node: FileNode): number => node.kind === "file"
    ? readingIndex.get(node.file.path) ?? Number.POSITIVE_INFINITY
    : Math.min(Number.POSITIVE_INFINITY, ...node.children.map(orderNode));
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const sort = (items: FileNode[]) => {
    items.sort((left, right) => rankNode(left) - rankNode(right)
      || (rankNode(left) === 0 ? orderNode(left) - orderNode(right) || 0 : 0)
      || Number(right.kind === "folder") - Number(left.kind === "folder")
      || collator.compare(left.name, right.name));
    items.forEach((node) => { if (node.kind === "folder") sort(node.children); });
  };
  sort(roots);
  return roots;
}

function editedPath(path: string): string {
  return path.replace(/\.epub$/i, "_edited.epub");
}

function editorOffset(content: string, codePointOffset: number): number {
  return Array.from(content).slice(0, codePointOffset).join("").length;
}

function subtreeEnd(entries: EpubTocEntry[], index: number): number {
  let end = index + 1;
  while (end < entries.length && entries[end].depth > entries[index].depth) end += 1;
  return end;
}

function siblingIndex(entries: EpubTocEntry[], index: number, shift: number): number {
  const depth = entries[index].depth;
  if (shift > 0) {
    const next = subtreeEnd(entries, index);
    return next < entries.length && entries[next].depth === depth ? next : -1;
  }
  for (let previous = index - 1; previous >= 0; previous -= 1) {
    if (entries[previous].depth === depth) return previous;
    if (entries[previous].depth < depth) break;
  }
  return -1;
}

export function EpubContentPage({ onClose, initialPath = "" }: { onClose: () => void; initialPath?: string }) {
  const t = useMessages().epubContentTool;
  const colorTheme = useSettingsStore((state) => state.app.draft?.color_theme ?? "light");
  const savedDraft = useRef(readEditorDraft());
  const currentDraft = useRef<EpubEditorDraft | null>(null);
  const sourceEditor = useRef<EditorView | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const previewSelectingSource = useRef(false);
  const previewSource = useRef({ path: "", content: "" });
  const chapterTurnTime = useRef(0);
  const editorStates = useRef(new Map<string, { state: EditorState; scroll: number }>());
  const [editorEpoch, setEditorEpoch] = useState(0);
  const reloadCursor = useRef<{ path: string; position: number } | null>(null);
  const [sourceTarget, setSourceTarget] = useState<{ path: string; line: number } | null>(null);
  const [openPaths, setOpenPaths] = useState<string[]>([]);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [otherImportOpen, setOtherImportOpen] = useState(false);
  const [otherImportPath, setOtherImportPath] = useState("");
  const [converterPath, setConverterPath] = useState("");
  const [mergePaths, setMergePaths] = useState<string[]>([]);
  const [inputPath, setInputPath] = useState(initialPath);
  const [recent, setRecent] = useState(readRecent);
  const [restoring, setRestoring] = useState(Boolean(savedDraft.current && !initialPath));
  const [session, setSession] = useState<EpubContentSession | null>(null);
  const [selectedPath, setSelectedPath] = useState("");
  const [resourcePath, setResourcePath] = useState("");
  const [resourceAction, setResourceAction] = useState<ResourceAction | null>(null);
  const [resourceTarget, setResourceTarget] = useState("");
  const [resourceInput, setResourceInput] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<string[]>([]);
  const selectionAnchor = useRef("");
  const [fileMenu, setFileMenu] = useState<{ x: number; y: number } | null>(null);
  const [actionPaths, setActionPaths] = useState<string[]>([]);
  const [renameStart, setRenameStart] = useState(1);
  const fileMenuRef = useRef<HTMLDivElement>(null);
  const [resourceReferences, setResourceReferences] = useState<string[]>([]);
  const [chapterOpen, setChapterOpen] = useState(false);
  const [chapterPath, setChapterPath] = useState("");
  const [chapterTitle, setChapterTitle] = useState("");
  const [chapterText, setChapterText] = useState("");
  const [splitOpen, setSplitOpen] = useState(false);
  const [splitPoints, setSplitPoints] = useState<Array<{ index: number; label: string }>>([]);
  const [splitIndex, setSplitIndex] = useState(0);
  const [splitTarget, setSplitTarget] = useState("");
  const [imageOpen, setImageOpen] = useState(false);
  const [imageMode, setImageMode] = useState<"existing" | "import">("existing");
  const [imagePath, setImagePath] = useState("");
  const [imageInput, setImageInput] = useState("");
  const [imageTarget, setImageTarget] = useState("");
  const [imageAlt, setImageAlt] = useState("");
  const [tocControlsIndex, setTocControlsIndex] = useState<number | null>(null);
  const [previewPath, setPreviewPath] = useState("");
  const [loadedContent, setLoadedContent] = useState("");
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState("");
  const [tocDraft, replaceTocDraft] = useState<EpubTocEntry[]>([]);
  const tocUndo = useRef<EpubTocEntry[][]>([]);
  const tocRedo = useRef<EpubTocEntry[][]>([]);
  const lastTocEdit = useRef({ key: "", time: 0 });
  const [tocHistory, setTocHistory] = useState({ undo: 0, redo: 0 });
  const setTocDraft = (entries: EpubTocEntry[]) => {
    tocUndo.current = []; tocRedo.current = [];
    lastTocEdit.current = { key: "", time: 0 };
    setTocHistory({ undo: 0, redo: 0 });
    replaceTocDraft(entries);
  };
  const editTocDraft = (update: (entries: EpubTocEntry[]) => EpubTocEntry[], key = "") => {
    const next = update(tocDraft);
    if (JSON.stringify(next) === JSON.stringify(tocDraft)) return;
    const time = Date.now();
    if (!key || lastTocEdit.current.key !== key || time - lastTocEdit.current.time > 1000) tocUndo.current = [...tocUndo.current.slice(-99), tocDraft];
    lastTocEdit.current = { key, time };
    tocRedo.current = [];
    setTocHistory({ undo: tocUndo.current.length, redo: 0 });
    replaceTocDraft(next);
  };
  const [tocPickerIndex, setTocPickerIndex] = useState<number | null>(null);
  const [tocPickerPath, setTocPickerPath] = useState("");
  const [tocPickerAnchor, setTocPickerAnchor] = useState("");
  const [tocAnchors, setTocAnchors] = useState<Array<{ id: string; label: string }>>([]);
  const [tocPageOpen, setTocPageOpen] = useState(false);
  const [tocPageTitle, setTocPageTitle] = useState("");
  const [tocPatternOpen, setTocPatternOpen] = useState(false);
  const [tocSource, setTocSource] = useState<"headings" | "files" | "xpath">("headings");
  const [tocPatterns, setTocPatterns] = useState(["", "", ""]);
  const [tocXpaths, setTocXpaths] = useState(["//h:h1", "//h:h2", "//h:h3"]);
  const [tocProposal, setTocProposal] = useState<EpubTocEntry[]>([]);
  const [sideView, setSideView] = useState<SideView>("files");
  const [paneView, setPaneView] = useState<"source" | "preview">("source");
  const [bookIndex, setBookIndex] = useState(0);
  const [bookHtml, setBookHtml] = useState("");
  const [bookSpread, setBookSpread] = useState(false);
  const [spreadMate, setSpreadMate] = useState<{ index: number; html: string } | null>(null);
  const bookRendition = useMemo(() => previewRendition(bookHtml), [bookHtml]);
  const visibleBookStart = spreadMate ? Math.min(bookIndex, spreadMate.index) : bookIndex;
  const visibleBookEnd = spreadMate ? Math.max(bookIndex, spreadMate.index) : bookIndex;
  const [bookMode, setBookMode] = useState<"continuous" | "paged">("continuous");
  const readingLocations = useRef<Record<string, ReadingLocation>>({});
  const [bookPage, setBookPage] = useState({ page: 0, pages: 1 });
  const [bookReady, setBookReady] = useState(false);
  const [pageStep, setPageStep] = useState(0);
  const [inspectPreview, setInspectPreview] = useState(false);
  const [syncPreview, setSyncPreview] = useState(true);
  const [sourceLine, setSourceLine] = useState(0);
  const [sourceColumn, setSourceColumn] = useState(0);
  const [sourceRequest, setSourceRequest] = useState(0);
  const [localHistory, setLocalHistory] = useState({ undo: 0, redo: 0 });
  const [previewFragment, setPreviewFragment] = useState({ path: "", fragment: "", request: 0 });
  const [computedStyles, setComputedStyles] = useState<Record<string, string> | null>(null);
  const [bookError, setBookError] = useState("");
  const [bookLoading, setBookLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [sidebarWidth, setSidebarWidth] = useState(28);
  const [sourceWidth, setSourceWidth] = useState(50);
  const [sourceZoom, setSourceZoom] = useState(100);
  const [previewZoom, setPreviewZoom] = useState(80);
  const [previewWrap, setPreviewWrap] = useState(true);
  const [resizing, setResizing] = useState(false);
  const [collapsedFolders, setCollapsedFolders] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [replacement, setReplacement] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [regularExpression, setRegularExpression] = useState(false);
  const [ignoreMarkup, setIgnoreMarkup] = useState(false);
  const [scope, setScope] = useState<Scope>("current");
  const [selectionRange, setSelectionRange] = useState<{ path: string; start: number; end: number } | null>(null);
  const [matches, setMatches] = useState<EpubContentMatch[]>([]);
  const [replaceProposal, setReplaceProposal] = useState<ReplaceProposal | null>(null);
  const [matchIndex, setMatchIndex] = useState(-1);
  const [searchOpen, setSearchOpen] = useState(false);
  const [savedSearchOpen, setSavedSearchOpen] = useState(false);
  const searchRequests = useRef(new SearchRequests());
  const [searchRequest, setSearchRequest] = useState(0);
  const [outputPath, setOutputPath] = useState("");
  const [overwriteSource, setOverwriteSource] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [closeOpen, setCloseOpen] = useState(false);
  const [closeAfterSave, setCloseAfterSave] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [feedbackWarning, setFeedbackWarning] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const initialOpenStarted = useRef(false);
  const operationInFlight = useRef(false);

  const nodes = useMemo(() => fileTree(session?.files ?? [], session?.spine ?? []), [session?.files, session?.spine]);
  const visibleFiles = useMemo(() => {
    const paths: string[] = [];
    const visit = (items: FileNode[]) => items.forEach((node) => {
      if (node.kind === "file") paths.push(node.file.path);
      else if (!collapsedFolders.includes(node.path)) visit(node.children);
    });
    visit(nodes);
    return paths;
  }, [nodes, collapsedFolders]);
  const fileSelection = selectedFiles.filter((path) => session?.files.some((file) => file.path === path));
  const sourceDirty = content !== loadedContent;
  const tocDirty = Boolean(session && JSON.stringify(tocDraft) !== JSON.stringify(session.toc));
  const dirty = Boolean(session?.dirty || sourceDirty || tocDirty);
  const currentFile = session?.files.find((file) => file.path === selectedPath);
  const currentResource = session?.files.find((file) => file.path === resourcePath);
  const previewFile = session?.files.find((file) => file.path === previewPath);
  const operationPaths = fileSelection.length ? fileSelection : currentResource ? [resourcePath] : [];
  const isHtml = currentFile?.media_type === "application/xhtml+xml" || currentFile?.media_type === "text/html";
  const isPreviewable = isHtml || currentFile?.media_type === "image/svg+xml";
  const chapterPreviewHtml = useMemo(() => previewAtZoom(preview, previewZoom, previewWrap, previewRendition(preview).layout === "pre-paginated"), [preview, previewZoom, previewWrap]);
  const bookPreviewHtml = useMemo(() => previewAtZoom(bookHtml, previewZoom, previewWrap, bookRendition.layout === "pre-paginated"), [bookHtml, bookRendition, previewZoom, previewWrap]);

  useEffect(() => {
    const match = matches[matchIndex];
    if (!match || match.path !== selectedPath || sideView === "book" || paneView !== "source") return;
    const editor = sourceEditor.current;
    if (!editor || editor.state.doc.toString() !== content) return;
    const anchor = editorOffset(content, match.start);
    const head = editorOffset(content, match.end);
    editor.dispatch({ selection: { anchor, head }, effects: EditorView.scrollIntoView(anchor, { y: "center" }) });
  }, [matches, matchIndex, selectedPath, content, sideView, paneView, editorEpoch]);

  useEffect(() => {
    if (!sourceTarget || busy || sourceTarget.path !== selectedPath || sideView !== "files" || paneView !== "source") return;
    const editor = sourceEditor.current;
    if (!editor || editor.state.doc.toString() !== content) return;
    const target = editor.state.doc.line(Math.max(1, Math.min(sourceTarget.line, editor.state.doc.lines)));
    editor.dispatch({ selection: { anchor: target.from, head: target.to }, scrollIntoView: true });
    editor.focus();
    setSourceTarget(null);
  }, [sourceTarget, busy, selectedPath, content, sideView, paneView, editorEpoch]);

  useEffect(() => {
    if (!session) return;
    const draft: EpubEditorDraft = {
      sessionId: session.session_id,
      inputPath: session.input_path,
      selectedPath,
      openPaths,
      sourceDraft: sourceDirty ? content : null,
      dirty,
      tocDraft,
      sideView,
      paneView,
      bookIndex,
      bookMode,
      readingLocations: readingLocations.current,
      searchOpen,
      query,
      replacement,
      scope,
      selectionRange,
      caseSensitive,
      regularExpression,
      ignoreMarkup,
      sidebarWidth,
      sourceWidth,
      sourceZoom,
      previewZoom,
      previewWrap,
    };
    currentDraft.current = draft;
    const timer = window.setTimeout(() => { writeEditorDraft(draft); }, 250);
    return () => window.clearTimeout(timer);
  }, [session?.session_id, session?.input_path, session?.dirty, selectedPath, openPaths, content, loadedContent, tocDraft, sideView, paneView, bookIndex, bookMode, searchOpen, query, replacement, scope, selectionRange, caseSensitive, regularExpression, ignoreMarkup, sidebarWidth, sourceWidth, sourceZoom, previewZoom, previewWrap]);

  useEffect(() => {
    const flush = () => {
      if (currentDraft.current) writeEditorDraft(currentDraft.current);
    };
    const onVisibility = () => { if (document.visibilityState === "hidden") flush(); };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", flush);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", flush);
    };
  }, []);

  const run = useCallback(async <T,>(operation: () => Promise<T>): Promise<T | undefined> => {
    if (operationInFlight.current) return undefined;
    operationInFlight.current = true;
    setError("");
    setSessionExpired(false);
    setBusy(true);
    try {
      return await operation();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSessionExpired(message.includes("Editor session expired"));
      setError(message);
      return undefined;
    } finally {
      operationInFlight.current = false;
      setBusy(false);
    }
  }, []);

  const resetEditorHistory = (cursor?: { path: string; position: number }, keepSearch = false) => {
    const editor = sourceEditor.current;
    reloadCursor.current = cursor ?? (editor ? { path: selectedPath, position: Array.from(editor.state.doc.sliceString(0, editor.state.selection.main.head)).length } : null);
    editorStates.current.clear();
    sourceEditor.current = null;
    setLocalHistory({ undo: 0, redo: 0 });
    setEditorEpoch((value) => value + 1);
    if (!keepSearch) { setSelectionRange(null); setMatches([]); setMatchIndex(-1); }
  };

  const loadResource = useCallback(async (sid: string, path: string, summary = session) => {
    const previous = sourceEditor.current;
    if (previous && selectedPath) editorStates.current.set(selectedPath, { state: previous.state, scroll: previous.scrollDOM.scrollTop });
    const next = await epubContentBridge.read(sid, path);
    setSourceLine(0);
    setSelectedPath(path);
    setResourcePath(path);
    setLoadedContent(next.content);
    setContent(next.content);
    setOpenPaths((current) => [...current.filter((item) => summary?.files.some((file) => file.path === item && file.editable)), ...(current.includes(path) ? [] : [path])]);
    const target = resourcePreviewPath(path, summary?.files ?? [], summary?.spine ?? [], summary?.nav_path ?? "", previewPath);
    if (target) {
      setPreviewPath(target);
      try {
        setPreview((await epubContentBridge.preview(sid, target)).html);
        previewSource.current = { path, content: target === path ? next.content : "" };
        setPreviewError("");
      } catch (cause) {
        setPreview("");
        setPreviewError(cause instanceof Error ? cause.message : String(cause));
      }
    } else {
      setPreviewPath("");
      setPreview("");
      setPreviewError("");
    }
  }, [session, selectedPath, previewPath]);

  const clearResource = () => {
    setSelectedPath(""); setResourcePath(""); setContent(""); setLoadedContent("");
    setPreviewPath(""); setPreview(""); setPreviewError(""); setOpenPaths([]);
  };

  useEffect(() => {
    if (!session || !currentResource || currentResource.editable) return;
    const target = resourcePreviewPath(currentResource.path, session.files, session.spine, session.nav_path);
    let active = true;
    setPreviewPath(target);
    setPreview(""); setPreviewError(""); setComputedStyles(null);
    previewSource.current = { path: "", content: "" };
    if (target) {
      void epubContentBridge.preview(session.session_id, target)
        .then((rendered) => { if (active) setPreview(rendered.html); })
        .catch((cause: unknown) => { if (active) setPreviewError(cause instanceof Error ? cause.message : String(cause)); });
    }
    return () => { active = false; };
  }, [session, currentResource]);

  const commitSource = useCallback(async () => {
    if (!session || !selectedPath || content === loadedContent) return session;
    const next = await epubContentBridge.write(session.session_id, selectedPath, content);
    setSession(next);
    if (!tocDirty && (selectedPath === session.nav_path || selectedPath === session.ncx_path)) setTocDraft(next.toc);
    setLoadedContent(content);
    return next;
  }, [session, selectedPath, content, loadedContent, tocDirty]);

  const commitDirectory = async () => {
    const current = await commitSource();
    if (session && tocDirty) {
      const next = await epubContentBridge.setToc(session.session_id, tocDraft);
      setSession(next);
      setTocDraft(next.toc);
      if (selectedPath === session.nav_path || selectedPath === session.ncx_path) {
        await loadResource(next.session_id, selectedPath, next);
        resetEditorHistory();
        setSelectionRange(null); setMatches([]); setMatchIndex(-1);
      }
      return next;
    }
    return current;
  };

  useEffect(() => {
    if (!session || !selectedPath || currentResource?.editable === false || !sourceDirty || !previewPath || (!isPreviewable && currentFile?.media_type !== "text/css")) return;
    const sid = session.session_id;
    const path = selectedPath;
    let active = true;
    const timer = window.setTimeout(() => {
      void epubContentBridge.previewDraft(sid, previewPath, path, content)
        .then((rendered) => { if (active) { previewSource.current = { path, content }; setPreview(rendered.html); setPreviewError(""); } })
        .catch((cause: unknown) => { if (active) { setPreview(""); setPreviewError(cause instanceof Error ? cause.message : String(cause)); } });
    }, 900);
    return () => { active = false; window.clearTimeout(timer); };
  }, [session?.session_id, selectedPath, currentResource?.editable, previewPath, sourceDirty, content, isPreviewable, currentFile?.media_type]);

  useEffect(() => {
    if (!session || sideView !== "book") return;
    const path = session.spine[bookIndex];
    if (!path) {
      setBookHtml(""); setBookError(""); setBookLoading(false);
      if (bookIndex > 0) setBookIndex(Math.max(0, session.spine.length - 1));
      return;
    }
    let active = true;
    setBookLoading(true);
    setBookReady(false);
    setBookPage({ page: 0, pages: 1 });
    setBookError("");
    const timer = window.setTimeout(() => {
      const request = sourceDirty
        ? epubContentBridge.previewDraft(session.session_id, path, selectedPath, content)
        : epubContentBridge.preview(session.session_id, path);
      setSpreadMate(null);
      void request.then(async (result) => {
        if (!active) return;
        setBookHtml(result.html);
        const layout = previewRendition(result.html);
        const mate = bookSpread ? fixedSpreadMate(layout, bookIndex, session.spine.length) : null;
        if (mate === null) return;
        try {
          const partner = sourceDirty
            ? await epubContentBridge.previewDraft(session.session_id, session.spine[mate], selectedPath, content)
            : await epubContentBridge.preview(session.session_id, session.spine[mate]);
          if (active && fixedSpreadMate(previewRendition(partner.html), mate, session.spine.length) === bookIndex) setSpreadMate({ index: mate, html: partner.html });
        } catch {
          if (active) { setFeedback(t.previewInvalid); setFeedbackWarning(true); }
        }
      })
        .catch((cause: unknown) => { if (active) { setBookHtml(""); setBookError(cause instanceof Error ? cause.message : String(cause)); } })
        .finally(() => { if (active) setBookLoading(false); });
    }, sourceDirty ? 500 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [session, sideView, bookIndex, bookSpread, sourceDirty, selectedPath, content]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const openBook = async (path: string) => {
    await run(async () => {
      if (session && dirty && !window.confirm(sessionExpired ? t.sessionExpired : t.dirtyClose)) return;
      if (!session && savedDraft.current && (savedDraft.current.dirty || savedDraft.current.sourceDraft !== null) && !window.confirm(t.replaceDraft)) return;
      const next = await epubContentBridge.open(path);
      if (session) await epubContentBridge.close(session.session_id);
      clearStagedEditorDraft();
      savedDraft.current = null;
      setSession(next);
      clearResource();
      editorStates.current.clear();
      setOpenPaths([]);
      setResourcePath("");
      setInputPath(path);
      setTocDraft(next.toc);
      setOutputPath(editedPath(path));
      setOverwriteSource(false);
      setCollapsedFolders([]);
      setFileMenu(null); setSelectedFiles([]);
      setMatches([]);
      setMatchIndex(-1);
      setSelectionRange(null);
      setFeedback("");
      setBookIndex(0);
      readingLocations.current = {};
      setPreviewFragment({ path: "", fragment: "", request: 0 });
      setLocalHistory({ undo: 0, redo: 0 });
      const updatedRecent = [next.input_path, ...recent.filter((item) => item !== next.input_path)].slice(0, 4);
      setRecent(updatedRecent);
      try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(updatedRecent)); } catch { /* Optional history. */ }
      const first = next.spine.find((item) => next.files.some((file) => file.path === item && file.editable)) ?? next.files.find((file) => file.editable)?.path;
      if (first) {
        await loadResource(next.session_id, first, next);
        setOpenPaths([first]);
      }
      resetEditorHistory({ path: first ?? "", position: 0 });
    });
  };

  const restoreBook = async (draft: EpubEditorDraft) => {
    await run(async () => {
      const next = await epubContentBridge.info(draft.sessionId);
      const path = next.files.some((file) => file.path === draft.selectedPath && file.editable)
        ? draft.selectedPath
        : next.spine.find((item) => next.files.some((file) => file.path === item && file.editable)) ?? "";
      clearResource();
      setInputPath(next.input_path);
      setResourcePath(path);
      setOpenPaths((draft.openPaths ?? [path]).filter((item) => next.files.some((file) => file.path === item && file.editable)));
      setOutputPath(editedPath(next.input_path));
      setTocDraft(Array.isArray(draft.tocDraft) ? draft.tocDraft : next.toc);
      setSideView(["files", "toc", "book"].includes(draft.sideView) ? draft.sideView as SideView : "files");
      setBookIndex(typeof draft.bookIndex === "number" && draft.bookIndex >= 0 && draft.bookIndex < next.spine.length ? draft.bookIndex : 0);
      setBookMode(draft.bookMode === "paged" ? "paged" : "continuous");
      readingLocations.current = draft.readingLocations ?? {};
      setPaneView(draft.paneView === "preview" ? "preview" : "source");
      setSearchOpen(Boolean(draft.searchOpen));
      setQuery(draft.query ?? "");
      setReplacement(draft.replacement ?? "");
      setScope(["current", "text", "styles", "all", "selection"].includes(draft.scope) ? draft.scope : "current");
      setSelectionRange(path === draft.selectedPath ? draft.selectionRange ?? null : null);
      setCaseSensitive(Boolean(draft.caseSensitive));
      setRegularExpression(Boolean(draft.regularExpression));
      setIgnoreMarkup(Boolean(draft.ignoreMarkup));
      if (typeof draft.sidebarWidth === "number" && draft.sidebarWidth >= 10 && draft.sidebarWidth <= 70) setSidebarWidth(draft.sidebarWidth);
      if (typeof draft.sourceWidth === "number" && draft.sourceWidth >= 10 && draft.sourceWidth <= 90) setSourceWidth(draft.sourceWidth);
      if (typeof draft.sourceZoom === "number" && draft.sourceZoom >= 60 && draft.sourceZoom <= 200) setSourceZoom(draft.sourceZoom);
      if (typeof draft.previewZoom === "number" && draft.previewZoom >= 25 && draft.previewZoom <= 200) setPreviewZoom(draft.previewZoom);
      if (typeof draft.previewWrap === "boolean") setPreviewWrap(draft.previewWrap);
      if (path) {
        await loadResource(next.session_id, path, next);
        if (path === draft.selectedPath && typeof draft.sourceDraft === "string") setContent(draft.sourceDraft);
      }
      setSession(next);
    });
    setRestoring(false);
  };

  const chooseBook = async () => {
    const chosen = await run(() => dialogsBridge.chooseEpubFile(inputPath || undefined));
    if (chosen?.path) await openBook(chosen.path);
  };

  const importOtherBook = async () => {
    const result = await run(() => epubContentBridge.importBook(otherImportPath, converterPath));
    if (!result) return;
    await run(() => epubContentBridge.close(result.session_id));
    setOtherImportOpen(false);
    await openBook(result.input_path);
  };

  useEffect(() => {
    if (initialOpenStarted.current) return;
    initialOpenStarted.current = true;
    if (savedDraft.current) void restoreBook(savedDraft.current);
    else if (initialPath) void openBook(initialPath);
  }, [initialPath]);

  const selectResource = async (path: string) => {
    if (!session) return;
    setResourcePath(path);
    if (!session.files.find((file) => file.path === path)?.editable || (path === selectedPath && previewPath === resourcePreviewPath(path, session.files, session.spine, session.nav_path, previewPath))) return;
    await run(async () => {
      await commitSource();
      await loadResource(session.session_id, path);
    });
  };

  const chooseTreeResource = (path: string, modifiers: { metaKey: boolean; ctrlKey: boolean; shiftKey: boolean }) => {
    setSelectedFiles(selectFiles(visibleFiles, fileSelection, selectionAnchor.current, path, modifiers.metaKey || modifiers.ctrlKey, modifiers.shiftKey));
    if (!modifiers.shiftKey) selectionAnchor.current = path;
    setFileMenu(null);
    if (!modifiers.metaKey && !modifiers.ctrlKey && !modifiers.shiftKey) void selectResource(path);
    else setResourcePath(path);
  };

  const copyFiles = async () => {
    if (!session || !operationPaths.length) return;
    setFileMenu(null);
    await run(async () => {
      await commitDirectory();
      const next = await epubContentBridge.copyResources(session.session_id, operationPaths);
      setSession(next); setTocDraft(next.toc);
      setSelectedFiles(Object.values(next.copies));
      resetEditorHistory();
      setFeedback(t.resourceSaved); setFeedbackWarning(false);
    });
  };

  useEffect(() => {
    if (!fileMenu) return;
    const close = (event: PointerEvent) => { if (!fileMenuRef.current?.contains(event.target as Node)) setFileMenu(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setFileMenu(null); };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", escape);
    const resize = () => setFileMenu(null);
    window.addEventListener("resize", resize);
    fileMenuRef.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    return () => { window.removeEventListener("pointerdown", close); window.removeEventListener("keydown", escape); window.removeEventListener("resize", resize); };
  }, [fileMenu]);

  const openResourceAction = async (action: ResourceAction) => {
    if (!session || (action !== "add" && !currentResource)) return;
    setResourceAction(action);
    setActionPaths(action === "add" ? [] : [...operationPaths]);
    setFileMenu(null);
    setRenameStart(1);
    setResourceTarget(action === "rename" ? operationPaths.length > 1 ? "resource-" : operationPaths[0] : "");
    setResourceInput("");
    setResourceReferences([]);
    if (action === "delete") {
      const result = await run(async () => {
        const references = new Set<string>();
        for (const path of operationPaths) {
          const found = await epubContentBridge.references(session.session_id, path);
          found.inbound.filter((reference) => !operationPaths.includes(reference) && ![session.nav_path, session.ncx_path, "OPF spine", "EPUB navigation"].includes(reference)).forEach((reference) => references.add(reference));
        }
        return [...references];
      });
      if (result) setResourceReferences(result);
    }
  };

  const chooseResourceFile = async () => {
    const chosen = await run(() => dialogsBridge.chooseAnyFile(resourceInput || undefined));
    if (!chosen?.path) return;
    setResourceInput(chosen.path);
    if (resourceAction === "add" && !resourceTarget) {
      const root = (session?.nav_path || session?.files[0]?.path || "").split("/").slice(0, -1).join("/");
      setResourceTarget([root, chosen.path.split(/[\\/]/).at(-1)].filter(Boolean).join("/"));
    }
  };

  const chooseResourceOutput = async () => {
    const name = actionPaths.length > 1 ? "epub-resources.zip" : actionPaths[0]?.split("/").at(-1) || "resource";
    const extension = name.split(".").at(-1) || "";
    const chosen = await run(() => dialogsBridge.chooseSavePath(name, extension ? [extension] : []));
    if (chosen?.path) setResourceTarget(chosen.path);
  };

  const applyResourceAction = async () => {
    if (!session || !resourceAction) return;
    await run(async () => {
      await commitDirectory();
      let next: EpubContentSession | null = null;
      if (resourceAction === "add") next = await epubContentBridge.addResource(session.session_id, resourceInput, resourceTarget, /\.(xhtml|html|htm)$/i.test(resourceTarget));
      if (resourceAction === "replace") next = await epubContentBridge.replaceResource(session.session_id, actionPaths[0], resourceInput);
      const renamed = resourceAction === "rename" ? actionPaths.length > 1 ? numberedResourceNames(actionPaths, resourceTarget, renameStart) : { [actionPaths[0]]: resourceTarget } : {};
      if (resourceAction === "rename") next = await epubContentBridge.renameResources(session.session_id, renamed);
      if (resourceAction === "delete") next = await epubContentBridge.deleteResources(session.session_id, actionPaths);
      if (resourceAction === "export") {
        if (actionPaths.length > 1) await epubContentBridge.exportResources(session.session_id, actionPaths, resourceTarget);
        else await epubContentBridge.exportResource(session.session_id, actionPaths[0], resourceTarget, false);
      }
      if (next) {
        setSession(next);
        setTocDraft(next.toc);
        const preferred = resourceAction === "rename" ? renamed[resourcePath] ?? resourcePath : resourceAction === "add" ? resourceTarget : resourcePath;
        const active = renamed[selectedPath] ?? resourceAfterHistory(selectedPath, session.spine, next.files);
        if (active) await loadResource(next.session_id, active, next);
        else clearResource();
        setResourcePath(resourceAction === "delete" ? active : preferred);
        setSelectedFiles(resourceAction === "delete" ? [] : resourceAction === "rename" ? Object.values(renamed) : [preferred]);
        setSelectionRange(null); setMatches([]); setMatchIndex(-1);
        resetEditorHistory();
      }
      setResourceAction(null);
      setFeedback(t.resourceSaved);
      setFeedbackWarning(false);
    });
  };

  const openChapter = () => {
    const folder = selectedPath.includes("/") ? selectedPath.slice(0, selectedPath.lastIndexOf("/")) : "Text";
    const used = new Set(session?.files.map((file) => file.path) ?? []);
    let path = `${folder}/new_chapter.xhtml`;
    for (let number = 2; used.has(path); number += 1) path = `${folder}/new_chapter_${number}.xhtml`;
    setChapterPath(path);
    setChapterTitle("");
    setChapterText("");
    setChapterOpen(true);
  };

  const createChapter = async () => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      const after = session.spine.includes(selectedPath) ? selectedPath : session.spine.at(-1) ?? "";
      const next = await epubContentBridge.createChapter(session.session_id, chapterPath.trim(), chapterTitle.trim(), chapterText, after);
      setSession(next);
      setChapterOpen(false);
      setSideView("files");
      await loadResource(session.session_id, chapterPath.trim(), next);
      resetEditorHistory();
    });
  };

  const openSplit = async () => {
    if (!session || !selectedPath) return;
    await run(async () => {
      await commitSource();
      const isStyle = currentFile?.media_type === "text/css";
      const result = isStyle ? await epubContentBridge.styleSplitPoints(session.session_id, selectedPath) : await epubContentBridge.splitPoints(session.session_id, selectedPath);
      if (!result.points.length) return;
      const extension = isStyle ? ".css" : ".xhtml";
      const base = selectedPath.slice(0, -extension.length);
      const used = new Set(session.files.map((file) => file.path));
      let target = `${base}_part2${extension}`;
      for (let number = 3; used.has(target); number += 1) target = `${base}_part${number}${extension}`;
      setSplitPoints(result.points);
      setSplitIndex(result.points[0].index);
      setSplitTarget(target);
      setSplitOpen(true);
    });
  };

  const splitChapter = async () => {
    if (!session || !selectedPath) return;
    await run(async () => {
      await commitDirectory();
      const next = currentFile?.media_type === "text/css"
        ? await epubContentBridge.splitStyle(session.session_id, selectedPath, splitTarget.trim(), splitIndex)
        : await epubContentBridge.splitChapter(session.session_id, selectedPath, splitTarget.trim(), splitIndex);
      setSession(next);
      setSplitOpen(false);
      setSideView("files");
      await loadResource(session.session_id, splitTarget.trim(), next);
      resetEditorHistory();
    });
  };

  const mergeFiles = async () => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      if (tocDirty) await epubContentBridge.setToc(session.session_id, tocDraft);
      const next = await epubContentBridge.mergeResources(session.session_id, mergePaths);
      setSession(next);
      setTocDraft(next.toc);
      setMergeOpen(false);
      await loadResource(session.session_id, next.merged_path, next);
      resetEditorHistory();
    });
  };

  const chooseImageFile = async () => {
    const chosen = await run(() => dialogsBridge.chooseAnyFile(imageInput || undefined));
    if (!chosen?.path || !session) return;
    const name = chosen.path.split(/[\\/]/).at(-1) ?? "image.png";
    const root = (session.nav_path || selectedPath).split("/").slice(0, -1).join("/");
    const folder = [root, "Images"].filter(Boolean).join("/");
    const extension = name.lastIndexOf(".");
    const stem = extension > 0 ? name.slice(0, extension) : name;
    const suffix = extension > 0 ? name.slice(extension) : "";
    const used = new Set(session.files.map((file) => file.path.toLocaleLowerCase()));
    let target = `${folder}/${name}`;
    for (let number = 2; used.has(target.toLocaleLowerCase()); number += 1) target = `${folder}/${stem}_${number}${suffix}`;
    setImageInput(chosen.path);
    setImageTarget(target);
  };

  const restoreSourceEditor = (view: EditorView) => {
    sourceEditor.current = view;
    setLocalHistory({ undo: undoDepth(view.state), redo: redoDepth(view.state) });
    const cached = editorStates.current.get(selectedPath);
    if (cached && cached.state.doc.toString() === content) {
      view.scrollDOM.scrollTop = cached.scroll;
    }
    const cursor = reloadCursor.current;
    if (cursor?.path === selectedPath) {
      view.dispatch({ selection: { anchor: editorOffset(content, cursor.position) }, scrollIntoView: true });
      reloadCursor.current = null;
    }
  };

  const locateSource = (line: number, column = 0) => {
    if (!syncPreview || previewPath !== selectedPath || previewSource.current.path !== selectedPath || previewSource.current.content !== content) return;
    const editor = sourceEditor.current;
    if (!editor || editor.state.doc.toString() !== content || line < 1 || line > editor.state.doc.lines) return;
    const target = editor.state.doc.line(line);
    const position = target.from + editorOffset(target.text, column);
    previewSelectingSource.current = true;
    try {
      editor.dispatch({ selection: { anchor: position }, effects: EditorView.scrollIntoView(position, { y: "center", x: "nearest" }) });
      setSourceLine(0);
    } finally {
      previewSelectingSource.current = false;
    }
  };

  const openSearch = () => {
    setSearchOpen(true);
    const editor = sourceEditor.current;
    if (editor) {
      const selected = editor.state.sliceDoc(editor.state.selection.main.from, editor.state.selection.main.to);
      if (selected && selected.length <= 500 && !selected.includes("\n")) { setQuery(selected); setMatches([]); setMatchIndex(-1); }
    }
    searchInput.current?.focus(); searchInput.current?.select();
  };

  useEffect(() => {
    if (searchOpen) { searchInput.current?.focus(); searchInput.current?.select(); }
  }, [searchOpen]);

  useEffect(() => {
    if (!session) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.isComposing) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
        event.preventDefault(); event.stopPropagation(); openSearch();
      } else if (event.key === "Escape" && searchOpen && !fileMenu && !document.querySelector(`.${styles.saveDialog}`)) {
        event.preventDefault(); setSearchOpen(false); sourceEditor.current?.focus();
      }
    };
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  }, [session?.session_id, searchOpen, fileMenu]);

  const openBookPreview = () => {
    const editor = sourceEditor.current;
    if (editor && selectedPath) editorStates.current.set(selectedPath, { state: editor.state, scroll: editor.scrollDOM.scrollTop });
    sourceEditor.current = null;
    setSideView("book");
  };

  const inspectElement = (line: number, properties: Record<string, string>) => {
    setComputedStyles(properties);
    if (previewPath !== selectedPath) return;
    const editor = sourceEditor.current;
    if (!editor) return;
    const target = editor.state.doc.line(Math.max(1, Math.min(line, editor.state.doc.lines)));
    editor.dispatch({ selection: { anchor: target.from, head: target.to }, scrollIntoView: true });
  };

  const previewLink = (target: string) => {
    const { path, fragment } = previewDestination(target);
    if (!session?.files.some((file) => file.path === path && file.editable)) return;
    if (sideView === "book") {
      const index = session.spine.indexOf(path);
      if (index >= 0) setBookIndex(index);
    } else void selectResource(path);
    setPreviewFragment((previous) => ({ path, fragment, request: previous.request + 1 }));
  };

  const rememberLocation = (path: string, location: ReadingLocation) => {
    readingLocations.current[path] = location;
    if (currentDraft.current) currentDraft.current.readingLocations = readingLocations.current;
  };

  const turnPage = (direction: -1 | 1) => {
    if (!bookReady || bookLoading || !session) return;
    if (bookMode === "paged" && ((direction < 0 && bookPage.page > 0) || (direction > 0 && bookPage.page < bookPage.pages - 1))) setPageStep((value) => value + direction);
    else {
      const index = Math.max(0, Math.min(session.spine.length - 1, spreadMate ? (direction > 0 ? Math.max(bookIndex, spreadMate.index) + 1 : Math.min(bookIndex, spreadMate.index) - 1) : bookIndex + direction));
      if (index === bookIndex) return;
      readingLocations.current[session.spine[index]] = { page: direction < 0 ? -1 : 0, scroll: 0, scrollX: 0, anchorLine: 0 };
      setPreviewFragment((previous) => ({ path: "", fragment: "", request: previous.request + 1 }));
      setBookReady(false);
      setBookIndex(index);
    }
  };

  const turnChapter = (direction: -1 | 1) => {
    if (!session || busy || Date.now() - chapterTurnTime.current < 600) return;
    if (sideView === "book") {
      if (!bookReady || bookLoading) return;
      chapterTurnTime.current = Date.now();
      turnPage(direction);
      return;
    }
    const index = session.spine.indexOf(previewPath);
    const path = session.spine[index + direction];
    if (index < 0 || !path || !session.files.some((file) => file.path === path && file.editable)) return;
    chapterTurnTime.current = Date.now();
    readingLocations.current[`source:${path}`] = { page: direction < 0 ? -1 : 0, scroll: 0, scrollX: 0 };
    setSourceLine(0);
    void selectResource(path);
  };

  const previewWarning = (reason: string) => {
    if (reason === "pagination-limit") { setFeedback(t.paginationLimit); setFeedbackWarning(true); }
  };

  const insertImage = async () => {
    const editor = sourceEditor.current;
    if (!editor || !session || !selectedPath) return;
    let target = imagePath;
    if (imageMode === "import") {
      if (!imageInput || !imageTarget || !/\.(?:apng|avif|gif|jpe?g|png|svg|webp)$/i.test(imageTarget)) return;
      const added = await run(async () => {
        await commitSource();
        return epubContentBridge.addResource(session.session_id, imageInput, imageTarget, false);
      });
      if (!added) return;
      setSession(added);
      target = imageTarget;
    }
    if (!target) return;
    const snippet = `<img src="${xmlAttribute(relativeResourceHref(selectedPath, target))}" alt="${xmlAttribute(imageAlt)}"/>`;
    const range = editor.state.selection.main;
    editor.dispatch({ changes: { from: range.from, to: range.to, insert: snippet }, selection: { anchor: range.from + snippet.length }, scrollIntoView: true });
    editor.focus();
    setImageOpen(false);
  };

  const applyToc = async () => {
    if (!session || !tocDirty) return;
    await run(async () => {
      await commitSource();
      const next = await epubContentBridge.setToc(session.session_id, tocDraft);
      setSession(next);
      setTocDraft(next.toc);
      if (selectedPath === session.nav_path || selectedPath === session.ncx_path) await loadResource(session.session_id, selectedPath, next);
      resetEditorHistory();
    });
  };

  const openTocPicker = async (index: number) => {
    if (!session) return;
    const href = tocDraft[index].href;
    const path = href.split("#", 1)[0];
    const selected = session.files.some((file) => file.path === path && file.path !== session.nav_path) ? path : session.spine.find((item) => item !== session.nav_path) ?? "";
    const result = await run(async () => {
      await commitSource();
      return epubContentBridge.anchors(session.session_id, selected);
    });
    if (!result) return;
    setTocPickerIndex(index);
    setTocPickerPath(selected);
    setTocAnchors(result.anchors);
    try { setTocPickerAnchor(decodeURIComponent(href.split("#")[1] ?? "")); }
    catch { setTocPickerAnchor(""); }
  };

  const changeTocPickerPath = async (path: string) => {
    if (!session) return;
    const result = await run(() => epubContentBridge.anchors(session.session_id, path));
    if (!result) return;
    setTocPickerPath(path);
    setTocPickerAnchor("");
    setTocAnchors(result.anchors);
  };

  const createTocPage = async () => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      if (tocDirty) await epubContentBridge.setToc(session.session_id, tocDraft);
      const next = await epubContentBridge.generateTocPage(session.session_id, tocPageTitle);
      setSession(next);
      setTocDraft(next.toc);
      setTocPageOpen(false);
      setSideView("files");
      await loadResource(session.session_id, next.generated_path, next);
      resetEditorHistory();
      setFeedback(t.generateTocPage);
      setFeedbackWarning(false);
    });
  };

  const previewGeneratedToc = async () => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      const patterns = tocSource === "xpath" ? tocXpaths : tocSource === "headings" && tocPatterns.some(Boolean) ? tocPatterns : undefined;
      const result = await epubContentBridge.previewToc(session.session_id, patterns, tocSource);
      setTocProposal(result.entries);
    });
  };

  const generateToc = async () => {
    if (!session || !tocProposal.length) return;
    await run(async () => {
      const patterns = tocSource === "xpath" ? tocXpaths : tocSource === "headings" && tocPatterns.some(Boolean) ? tocPatterns : undefined;
      const next = await epubContentBridge.generateToc(session.session_id, patterns, tocSource);
      setSession(next);
      setTocDraft(next.toc);
      setTocPatternOpen(false);
      setTocProposal([]);
      setFeedback(`${next.generated_entries} ${t.generatedToc}${next.approximate_targets ? `; ${next.approximate_targets} ${t.approximateToc}` : ""}`);
      setFeedbackWarning(next.approximate_targets > 0);
      if (selectedPath) await loadResource(session.session_id, selectedPath);
      resetEditorHistory();
    });
  };

  const history = async (direction: "undo" | "redo") => {
    if (!session) return;
    if (sideView === "toc") {
      const from = direction === "undo" ? tocUndo.current : tocRedo.current;
      const to = direction === "undo" ? tocRedo.current : tocUndo.current;
      const previous = from.pop();
      if (previous) {
        lastTocEdit.current = { key: "", time: 0 };
        to.push(tocDraft);
        replaceTocDraft(previous);
        setTocHistory({ undo: tocUndo.current.length, redo: tocRedo.current.length });
        return;
      }
    }
    const editor = sourceEditor.current;
    if (sideView !== "book" && editor && (direction === "undo" ? undoDepth(editor.state) : redoDepth(editor.state)) > 0) {
      (direction === "undo" ? undo : redo)(editor);
      return;
    }
    await run(async () => {
      await commitDirectory();
      const next = await epubContentBridge.history(session.session_id, direction);
      setSession(next);
      setTocDraft(next.toc);
      const path = resourceAfterHistory(selectedPath, session.spine, next.files);
      if (path) await loadResource(session.session_id, path, next);
      else { setSelectedPath(""); setResourcePath(""); setContent(""); setLoadedContent(""); setPreview(""); setPreviewPath(""); }
      resetEditorHistory();
      setMatches([]);
      setMatchIndex(-1);
      setSelectionRange(null);
      setFeedback("");
    });
  };

  const scopePaths = () => searchScopePaths(scope, selectedPath, session?.files ?? [], session?.spine ?? []);

  const search = async (direction: -1 | 1 = 1, request?: number) => {
    if (!session || !query || busy) return;
    const revision = request ?? searchRequests.current.invalidate();
    await run(async () => {
      const directoryChangedSource = tocDirty && (selectedPath === session.nav_path || selectedPath === session.ncx_path);
      await commitDirectory();
      if (!searchRequests.current.isCurrent(revision)) return;
      if (scope === "selection" && directoryChangedSource) throw new Error(t.selectionExpired);
      let selection = selectionRange;
      if (scope === "selection") {
        const editor = sourceEditor.current;
        const range = editor?.state.selection.main;
        if (!selection && editor && range && !range.empty && editor.state.doc.toString() === content) {
          selection = {
            path: selectedPath,
            start: Array.from(content.slice(0, range.from)).length,
            end: Array.from(content.slice(0, range.to)).length,
          };
          setSelectionRange(selection);
        }
        if (!selection || selection.path !== selectedPath) throw new Error(t.selectionExpired);
      }
      const paths = scopePaths();
      const cursor = sourceEditor.current?.state.selection.main.head ?? 0;
      const position = scope === "selection" ? selection?.start ?? 0 : Array.from(content.slice(0, cursor)).length;
      let found;
      try {
        found = await epubContentBridge.search(session.session_id, query, paths, caseSensitive, regularExpression, scope === "selection" ? selection ?? undefined : undefined, ignoreMarkup);
      } catch (cause) {
        if (searchRequests.current.isCurrent(revision)) throw cause;
        return;
      }
      if (!searchRequests.current.isCurrent(revision)) return;
      setMatches(found.matches);
      const index = nextMatchIndex(found.matches, paths, selectedPath, position, direction);
      setMatchIndex(index);
      if (found.matches.length) {
        setSideView("files");
        setPaneView("source");
        if (found.matches[index].path !== selectedPath) await loadResource(session.session_id, found.matches[index].path);
      }
    });
  };

  useEffect(() => {
    const revision = searchRequests.current.invalidate();
    setMatches([]); setMatchIndex(-1);
    if (!searchOpen || !session || !query) return;
    const timer = window.setTimeout(() => {
      if (searchRequests.current.enqueue(revision)) setSearchRequest(revision);
    }, 180);
    return () => window.clearTimeout(timer);
  }, [query, scope, caseSensitive, regularExpression, ignoreMarkup, searchOpen, session?.session_id]);

  useEffect(() => {
    if (busy) return;
    const revision = searchRequests.current.take();
    if (revision !== null) void search(1, revision);
  }, [searchRequest, busy]);

  const loadSearch = (value: SearchOptions) => {
    setQuery(value.query); setReplacement(value.replacement); setScope(value.scope);
    setCaseSensitive(value.caseSensitive); setRegularExpression(value.regularExpression);
    setIgnoreMarkup(Boolean(value.ignoreMarkup));
    setSelectionRange(null); setMatches([]); setMatchIndex(-1); setSearchOpen(true);
  };

  const runSavedSearches = async (rules: SavedSearch[], apply: boolean, fingerprint?: string) => {
    if (!session) throw new Error(t.noBook);
    const result = await run(async () => {
      await commitSource(); await commitDirectory();
      const prepared = rules.map((rule) => {
        if (rule.scope === "selection") throw new Error(t.selectionExpired);
        const paths = searchScopePaths(rule.scope, selectedPath, session.files, session.spine);
        return { query: rule.query, replacement: rule.replacement, case_sensitive: rule.caseSensitive, regular_expression: rule.regularExpression, ignore_markup: Boolean(rule.ignoreMarkup), paths };
      });
      const response = await epubContentBridge.tool(session.session_id, "replace_sequence", { rules: prepared, apply, fingerprint });
      if (apply) {
        setSession(response.session); setTocDraft(response.session.toc);
        if (selectedPath) await loadResource(session.session_id, selectedPath, response.session);
        resetEditorHistory(); setSelectionRange(null); setMatches([]); setMatchIndex(-1); setFeedback(t.toolApplied);
      }
      return response.result;
    });
    if (!result) throw new Error(t.error);
    return result;
  };

  const navigateMatch = async (direction: -1 | 1) => {
    if (!session || !matches.length) return;
    const next = (matchIndex + direction + matches.length) % matches.length;
    await run(async () => {
      await commitSource();
      if (matches[next].path !== selectedPath) await loadResource(session.session_id, matches[next].path);
      setMatchIndex(next);
      setSideView("files");
      setPaneView("source");
    });
  };

  const replaceAll = async () => {
    if (!session || !query || !matches.length || matches.length >= 5000) return;
    await run(async () => {
      const directoryChangedSource = tocDirty && (selectedPath === session.nav_path || selectedPath === session.ncx_path);
      await commitDirectory();
      if (scope === "selection" && directoryChangedSource) throw new Error(t.selectionExpired);
      const selection = scope === "selection" ? selectionRange ?? undefined : undefined;
      if (scope === "selection" && !selection) throw new Error(t.selectionExpired);
      const paths = scopePaths();
      const proposal = await epubContentBridge.previewReplace(session.session_id, query, replacement, paths, caseSensitive, regularExpression, selection, ignoreMarkup);
      if (!proposal.replacements) { setFeedback(t.noMatches); return; }
      setReplaceProposal({ ...proposal, selection, query, replacement, paths, caseSensitive, regularExpression, ignoreMarkup });
    });
  };

  const applyReplaceAll = async () => {
    if (!session || !replaceProposal) return;
    await run(async () => {
      const result = await epubContentBridge.replace(session.session_id, replaceProposal.query, replaceProposal.replacement, replaceProposal.paths, replaceProposal.caseSensitive, replaceProposal.replacements, replaceProposal.regularExpression, replaceProposal.selection, replaceProposal.fingerprints, replaceProposal.ignoreMarkup);
      setSession(result);
      if (selectedPath) await loadResource(session.session_id, selectedPath, result);
      resetEditorHistory();
      setFeedback(`${result.replacements} ${t.matches}`);
      if (!tocDirty) setTocDraft(result.toc);
      setReplaceProposal(null);
      setMatches([]);
      setMatchIndex(-1);
      setSelectionRange(null);
    });
  };

  const replaceOne = async (match: EpubContentMatch) => {
    if (!session) return;
    await run(async () => {
      await commitDirectory();
      const next = await epubContentBridge.replaceMatch(session.session_id, query, replacement, match, caseSensitive, regularExpression);
      setSession(next);
      if (!tocDirty) setTocDraft(next.toc);
      if (selectedPath === match.path) await loadResource(session.session_id, selectedPath);
      const paths = scopePaths();
      const selection = scope === "selection" && selectionRange
        ? { ...selectionRange, end: selectionRange.end + next.replaced_end - match.end }
        : undefined;
      if (selection) setSelectionRange(selection);
      const found = await epubContentBridge.search(session.session_id, query, paths, caseSensitive, regularExpression, selection, ignoreMarkup);
      const index = found.matches.length ? nextMatchIndex(found.matches, paths, match.path, next.replaced_end) : -1;
      setMatches(found.matches);
      setMatchIndex(index);
      if (index >= 0 && found.matches[index].path !== selectedPath) await loadResource(session.session_id, found.matches[index].path);
      resetEditorHistory({ path: match.path, position: next.replaced_end }, true);
      setFeedback(`1 ${t.matches}`);
    });
  };

  const saveBook = async () => {
    if (!session) return;
    await run(async () => {
      await commitDirectory();
      const destination = overwriteSource ? session.input_path : outputPath.trim();
      if (!destination) throw new Error(t.outputPath);
      const sameSource = destination === session.input_path;
      if ((overwriteSource || sameSource) && !window.confirm(t.confirmOverwrite)) return;
      const result = await epubContentBridge.save(session.session_id, destination, overwriteSource || sameSource);
      setSession(result);
      setInputPath(result.output_path);
      setOutputPath(editedPath(result.output_path));
      setSaveOpen(false);
      clearStagedEditorDraft();
      setFeedback(`${t.saved}: ${result.output_path}${result.cache_warning ? ` · ${t.savedCacheWarning}` : ""}`);
      setFeedbackWarning(Boolean(result.cache_warning));
      if (closeAfterSave && !result.cache_warning) closeEditor();
      setCloseAfterSave(false);
    });
  };

  const stageSave = async (closeAfter = false) => {
    if (!session) return;
    await run(async () => {
      await commitDirectory();
      await epubContentBridge.checkpoint(session.session_id);
      if (!currentDraft.current) throw new Error(t.stageFailed);
      const draft = { ...currentDraft.current, sourceDraft: null, tocDraft, dirty: true };
      writeEditorDraft(draft);
      if (!stageEditorDraft(draft)) throw new Error(t.stageFailed);
      setFeedback(t.staged);
      setFeedbackWarning(false);
      if (closeAfter) closeEditor(true);
    });
  };

  const closeEditor = (keepDraft = false) => {
    if (!keepDraft) {
      clearEditorDraft();
      currentDraft.current = null;
      if (session) void epubContentBridge.close(session.session_id).catch(() => undefined);
    }
    onClose();
  };

  const requestClose = () => {
    if (operationInFlight.current) return;
    if (!dirty) closeEditor();
    else setCloseOpen(true);
  };

  useEffect(() => {
    const close = (event: Event) => { event.preventDefault(); requestClose(); };
    window.addEventListener("transoria-editor-close", close);
    return () => window.removeEventListener("transoria-editor-close", close);
  });

  const updateEntry = (index: number, patch: Partial<EpubTocEntry>) => {
    editTocDraft((current) => current.map((entry, position) => position === index ? { ...entry, ...patch } : entry), `${index}:${Object.keys(patch).join(",")}`);
  };

  const addTocEntry = (index: number | null, child = false) => {
    if (!session || tocDraft.length >= 5000) return;
    editTocDraft((current) => {
      const insertion = index === null ? current.length : child ? index + 1 : subtreeEnd(current, index);
      const depth = index === null ? 0 : current[index].depth + Number(child);
      const href = index === null ? selectedPath || session.spine[0] || "" : current[index].href;
      const entry = { label: t.newTocEntry, href, depth };
      return [...current.slice(0, insertion), entry, ...current.slice(insertion)];
    });
  };

  const moveEntry = (index: number, shift: number) => {
    const sibling = siblingIndex(tocDraft, index, shift);
    if (sibling < 0) return;
    const movedIndex = shift < 0 ? sibling : subtreeEnd(tocDraft, sibling) - (subtreeEnd(tocDraft, index) - index);
    setTocControlsIndex(movedIndex);
    editTocDraft((current) => {
      const end = subtreeEnd(current, index);
      if (shift < 0) return [...current.slice(0, sibling), ...current.slice(index, end), ...current.slice(sibling, index), ...current.slice(end)];
      const siblingEnd = subtreeEnd(current, sibling);
      return [...current.slice(0, index), ...current.slice(sibling, siblingEnd), ...current.slice(index, end), ...current.slice(siblingEnd)];
    });
  };

  const shiftDepth = (index: number, amount: number) => {
    editTocDraft((current) => current.map((entry, position) => position >= index && position < subtreeEnd(current, index) ? { ...entry, depth: entry.depth + amount } : entry));
  };

  const outdentEntry = (index: number) => {
    setTocControlsIndex(null);
    editTocDraft((current) => {
      const depth = current[index].depth;
      if (depth === 0) return current;
      let parent = index - 1;
      while (parent >= 0 && current[parent].depth !== depth - 1) parent -= 1;
      if (parent < 0) return current;
      const end = subtreeEnd(current, index);
      const parentEnd = subtreeEnd(current, parent);
      const block = current.slice(index, end).map((entry) => ({ ...entry, depth: entry.depth - 1 }));
      const remaining = [...current.slice(0, index), ...current.slice(end)];
      const insertion = parentEnd - (end - index);
      return [...remaining.slice(0, insertion), ...block, ...remaining.slice(insertion)];
    });
  };

  const removeEntry = (index: number) => {
    setTocControlsIndex(null);
    editTocDraft((current) => [...current.slice(0, index), ...current.slice(subtreeEnd(current, index))]);
  };

  const renderTree = (items: FileNode[], depth = 0): ReactNode => items.map((node) => {
    if (node.kind === "folder") {
      const collapsed = collapsedFolders.includes(node.path);
      return <div key={node.path}><div className={styles.folderRow} style={{ paddingLeft: `${depth * 9 + 4}px` }}><button type="button" aria-expanded={!collapsed} title={node.path} onClick={() => setCollapsedFolders((current) => collapsed ? current.filter((path) => path !== node.path) : [...current, node.path])}>{collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}{node.name}</button></div>{!collapsed ? renderTree(node.children, depth + 1) : null}</div>;
    }
    const path = node.file.path;
    return <div key={path} className={`${styles.fileRow} ${fileSelection.includes(path) || (!fileSelection.length && resourcePath === path) ? styles.active : ""}`}
      style={{ paddingLeft: `${depth * 12 + 4}px` }}>
      <button type="button" title={path} data-resource-path={path} aria-pressed={fileSelection.includes(path)} disabled={busy}
        onClick={(event) => { event.currentTarget.focus(); chooseTreeResource(path, event); }}
        onContextMenu={(event) => {
          event.preventDefault();
          if (!fileSelection.includes(path)) { setSelectedFiles([path]); selectionAnchor.current = path; }
          setResourcePath(path);
          setFileMenu({ x: Math.max(8, Math.min(event.clientX, window.innerWidth - 230)), y: Math.max(8, Math.min(event.clientY, window.innerHeight - 310)) });
        }}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
            event.preventDefault();
            const index = visibleFiles.indexOf(path);
            const next = visibleFiles[event.key === "Home" ? 0 : event.key === "End" ? visibleFiles.length - 1 : Math.max(0, Math.min(visibleFiles.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
            if (next) {
              if (!event.metaKey && !event.ctrlKey) chooseTreeResource(next, event);
              [...event.currentTarget.closest("aside")!.querySelectorAll<HTMLButtonElement>('button[data-resource-path]')].find((button) => button.dataset.resourcePath === next)?.focus();
            }
          }
          if (event.key === " " && (event.metaKey || event.ctrlKey)) { event.preventDefault(); chooseTreeResource(path, { ...event, metaKey: true, ctrlKey: false, shiftKey: false }); }
          if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "a") { event.preventDefault(); setSelectedFiles(visibleFiles); }
          if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "d") { event.preventDefault(); void copyFiles(); }
          if (event.key === "Delete" || event.key === "Backspace") { event.preventDefault(); void openResourceAction("delete"); }
          if (event.key === "F2") { event.preventDefault(); void openResourceAction("rename"); }
          if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
            event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect();
            if (!fileSelection.includes(path)) setSelectedFiles([path]);
            setResourcePath(path); setFileMenu({ x: Math.min(rect.left + 20, window.innerWidth - 230), y: Math.min(rect.bottom, window.innerHeight - 310) });
          }
        }}>
        {node.name}
      </button>
    </div>;
  });

  const resizePane = (pane: "sidebar" | "source", clientX: number, handle: HTMLDivElement) => {
    const bounds = handle.parentElement?.getBoundingClientRect();
    if (!bounds) return;
    const available = bounds.width - 8;
    const minimum = pane === "sidebar" ? 200 : 220;
    const remainder = pane === "sidebar" ? 440 : 220;
    if (available < minimum + remainder) return;
    const pixels = Math.min(Math.max(clientX - bounds.left, minimum), available - remainder);
    const percentage = Math.round((pixels / available) * 1000) / 10;
    if (pane === "sidebar") setSidebarWidth(percentage);
    else setSourceWidth(percentage);
  };

  const separator = (pane: "sidebar" | "source", value: number) => <div
    className={styles.resizeHandle}
    role="separator"
    tabIndex={0}
    aria-label={pane === "sidebar" ? t.resizeFiles : t.resizePreview}
    aria-orientation="vertical"
    aria-valuemin={10}
    aria-valuemax={90}
    aria-valuenow={Math.round(value)}
    onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); setResizing(true); }}
    onPointerMove={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) resizePane(pane, event.clientX, event.currentTarget); }}
    onPointerUp={(event) => { event.currentTarget.releasePointerCapture(event.pointerId); setResizing(false); }}
    onPointerCancel={() => setResizing(false)}
    onKeyDown={(event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const bounds = event.currentTarget.parentElement?.getBoundingClientRect();
      if (bounds) resizePane(pane, bounds.left + (bounds.width - 8) * (value + (event.key === "ArrowRight" ? 2 : -2)) / 100, event.currentTarget);
    }}
  />;

  const zoomControls = (value: number, update: (next: number) => void, label: string, minimum: number) => <div className={styles.zoomControls} aria-label={label}>
    <button type="button" title={t.zoomOut} aria-label={`${label}: ${t.zoomOut}`} disabled={value <= minimum} onClick={() => update(Math.max(minimum, value - 10))}><ZoomOut size={15} /></button>
    <input type="range" aria-label={label} min={minimum} max="200" step="10" value={value} onChange={(event) => update(Number(event.target.value))} />
    <span>{value}%</span>
    <button type="button" title={t.zoomIn} aria-label={`${label}: ${t.zoomIn}`} disabled={value >= 200} onClick={() => update(Math.min(200, value + 10))}><ZoomIn size={15} /></button>
  </div>;

  const wrapControl = <button type="button" className={`${styles.wrapControl} ${previewWrap ? styles.wrapActive : ""}`} title={t.wrapPreview} aria-label={t.wrapPreview} aria-pressed={previewWrap} onClick={() => setPreviewWrap((value) => !value)}><WrapText size={17} /></button>;

  const otherImportDialog = otherImportOpen ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.importOther}><h3>{t.importOther}</h3>
    <label>{t.localFile}<div className={styles.resourcePick}><input value={otherImportPath} onChange={(event) => setOtherImportPath(event.target.value)} /><button type="button" title={t.chooseFile} disabled={busy} onClick={() => { void run(() => dialogsBridge.chooseAnyFile(otherImportPath || undefined)).then((chosen) => { if (chosen?.path) setOtherImportPath(chosen.path); }); }}><FolderOpen size={16} /></button></div></label>
    <label>{t.converter}<div className={styles.resourcePick}><input value={converterPath} onChange={(event) => setConverterPath(event.target.value)} /><button type="button" title={t.chooseFile} disabled={busy} onClick={() => { void run(() => dialogsBridge.chooseAnyFile(converterPath || undefined)).then((chosen) => { if (chosen?.path) setConverterPath(chosen.path); }); }}><FolderOpen size={16} /></button></div></label>
    {error ? <div className={styles.error} role="alert">{error}</div> : null}
    <div className={styles.saveActions}><button type="button" disabled={busy} onClick={() => setOtherImportOpen(false)}>{t.cancel}</button><button type="button" disabled={busy || !otherImportPath.trim()} onClick={() => void importOtherBook()}>{busy ? t.loading : t.importOther}</button></div>
  </div></div> : null;

  if (!session) return <div className={styles.importPage}>
    <div className={styles.importHeader}>
      <div><span className={styles.importKicker}>EPUB</span><h2>{t.importTitle}</h2><p>{t.importSubtitle}</p></div>
      <button type="button" title={t.close} aria-label={t.close} onClick={() => closeEditor()}><X size={18} /></button>
    </div>
    <div className={styles.importContent}>
      {error ? <div className={styles.error} role="alert">{t.error}: {sessionExpired ? t.sessionExpired : error}</div> : null}
      <section className={styles.importPicker}>
        <BookOpen size={40} strokeWidth={1.5} aria-hidden="true" />
        <h3>{restoring ? t.restoring : t.importPrompt}</h3>
        <button type="button" className={styles.importPrimary} disabled={restoring || busy} onClick={() => void chooseBook()}><FolderOpen size={18} />{t.choose}</button>
        <button type="button" disabled={restoring || busy} onClick={() => setOtherImportOpen(true)}>{t.importOther}</button>
        <div className={styles.importPath}><label htmlFor="epub-editor-import-path">{t.manualPath}</label><div><input id="epub-editor-import-path" value={inputPath} onChange={(event) => setInputPath(event.target.value)} placeholder="/path/to/book.epub" aria-label={t.choose} /><button type="button" disabled={!inputPath.trim() || restoring || busy} onClick={() => void openBook(inputPath.trim())}>{t.open}</button></div></div>
      </section>
      {recent.length ? <section className={styles.recentSection}><h3>{t.recent}</h3><div className={styles.recentList}>{recent.map((path) => <button type="button" key={path} disabled={restoring || busy} onClick={() => void openBook(path)}><FileText size={18} /><span><strong>{path.split(/[\\/]/).at(-1)}</strong><small title={path}>{path}</small></span><ChevronRight size={17} /></button>)}</div></section> : null}
    </div>
    {otherImportDialog}
  </div>;

  return <div className={styles.workspace}>
    {savedSearchOpen ? <EpubSavedSearches current={{ query, replacement, scope, caseSensitive, regularExpression, ignoreMarkup }} load={loadSearch} execute={runSavedSearches} close={() => setSavedSearchOpen(false)} /> : null}
    <div className={styles.toolbar}>
      <div className={styles.bookPicker}>
        <button type="button" title={t.choose} aria-label={t.choose} onClick={() => void chooseBook()}><FolderOpen size={18} /></button>
        <input value={inputPath} onChange={(event) => setInputPath(event.target.value)} placeholder=".epub" aria-label={t.choose} />
        <button type="button" disabled={!inputPath.trim() || busy} onClick={() => void openBook(inputPath.trim())}>{t.open}</button>
      </div>
      <div className={styles.toolbarActions}>
        <button type="button" title={t.tools} aria-label={t.tools} disabled={busy} onClick={() => setToolsOpen(true)}><Wrench size={18} /></button>
        <button type="button" title={t.find} aria-label={t.find} onClick={() => searchOpen ? setSearchOpen(false) : openSearch()}><Search size={18} /></button>
        <button type="button" title={t.undo} aria-label={t.undo} disabled={busy || (!session?.can_undo && !(sideView !== "book" && localHistory.undo) && !(sideView === "toc" && tocHistory.undo))} onClick={() => void history("undo")}><CornerUpLeft size={18} /></button>
        <button type="button" title={t.redo} aria-label={t.redo} disabled={busy || (!(sideView === "toc" && tocHistory.redo) && !(sideView !== "book" && localHistory.redo) && (!session?.can_redo || sourceDirty))} onClick={() => void history("redo")}><CornerUpRight size={18} /></button>
        <button type="button" disabled={!session || busy} onClick={() => void stageSave()}><Save size={17} />{t.stageSave}</button>
        <button type="button" className={styles.primary} disabled={!session || busy} onClick={() => { setCloseAfterSave(false); setSaveOpen(true); }}><Download size={17} />{t.save}{dirty ? " *" : ""}</button>
        <button type="button" title={t.close} aria-label={t.close} onClick={requestClose}><X size={18} /></button>
      </div>
    </div>
    {error ? <div className={styles.error} role="alert">{t.error}: {sessionExpired ? t.sessionExpired : error}{sessionExpired && session ? <button type="button" onClick={() => void openBook(session.input_path)}>{t.reopen}</button> : null}</div> : null}
    {feedback ? <div className={feedbackWarning ? styles.warning : styles.feedback} role="status">{feedback}</div> : null}
    {!session ? <div className={styles.empty}>{t.noBook}</div> : <div className={styles.main} data-resizing={resizing} style={{ "--sidebar-width": `${sidebarWidth}%` } as CSSProperties}>
      <aside className={styles.sidebar}>
        <div className={styles.tabs}><button type="button" aria-selected={sideView === "files"} onClick={() => setSideView("files")}><FileCode2 size={16} />{t.files}</button><button type="button" aria-selected={sideView === "toc"} onClick={() => setSideView("toc")}><ListTree size={16} />{t.toc}</button><button type="button" aria-selected={sideView === "book"} onClick={openBookPreview}><BookOpen size={16} />{t.bookPreview}</button></div>
        {sideView === "files" ? <div className={styles.fileActions}>
          <button type="button" title={t.importResource} aria-label={t.importResource} disabled={busy} onClick={() => void openResourceAction("add")}><Plus size={16} /></button>
          <button type="button" title={t.newChapter} aria-label={t.newChapter} disabled={busy} onClick={openChapter}><FilePlus2 size={16} /></button>
          <button type="button" title={currentFile?.media_type === "text/css" ? t.splitStyle : t.splitChapter} aria-label={currentFile?.media_type === "text/css" ? t.splitStyle : t.splitChapter} disabled={busy || (currentFile?.media_type !== "text/css" && (!session.spine.includes(selectedPath) || currentFile?.media_type !== "application/xhtml+xml"))} onClick={() => void openSplit()}><Scissors size={16} /></button>
          <button type="button" title={t.mergeFiles} aria-label={t.mergeFiles} disabled={busy || !["text/css", "application/xhtml+xml"].includes(currentFile?.media_type ?? "")} onClick={() => { setMergePaths(operationPaths); setMergeOpen(true); }}><CornerDownRight size={16} /></button>
          <button type="button" title={t.copyResource} aria-label={t.copyResource} disabled={!operationPaths.length || busy} onClick={() => void copyFiles()}><Copy size={16} /></button>
          <button type="button" title={t.renameResource} aria-label={t.renameResource} disabled={!currentResource || busy || resourcePath === session.nav_path || resourcePath === session.ncx_path} onClick={() => void openResourceAction("rename")}><Pencil size={16} /></button>
          <button type="button" title={t.replaceResource} aria-label={t.replaceResource} disabled={operationPaths.length !== 1 || busy} onClick={() => void openResourceAction("replace")}><Replace size={16} /></button>
          <button type="button" title={t.exportResource} aria-label={t.exportResource} disabled={!currentResource || busy} onClick={() => void openResourceAction("export")}><Download size={16} /></button>
          <button type="button" title={t.deleteResource} aria-label={t.deleteResource} disabled={!currentResource || busy || resourcePath === session.nav_path || resourcePath === session.ncx_path} onClick={() => void openResourceAction("delete")}><Trash2 size={16} /></button>
          {fileSelection.length > 1 ? <span className={styles.resourceInfo}>{fileSelection.length} {t.selectedFiles}</span> : null}
          {currentResource ? <span className={styles.resourceInfo} title={resourcePath}>{currentResource.media_type} · {currentResource.size} B{currentResource.editable ? "" : ` · ${t.resourceNotEditable}`}</span> : null}
        </div> : null}
        <div className={styles.sideBody}>
          {sideView === "files" ? renderTree(nodes) : null}
          {sideView === "book" ? session.spine.map((path, index) => <button type="button" key={`${path}-${index}`} className={styles.bookChapter} aria-current={bookIndex === index ? "page" : undefined} title={path} onClick={() => setBookIndex(index)}><span>{index + 1}</span><strong>{session.toc.find((entry) => entry.href.split("#")[0] === path)?.label ?? path.split("/").at(-1)}</strong></button>) : null}
          {sideView === "toc" ? <>
            <div className={styles.tocActions}>
              <button type="button" title={t.addEntry} aria-label={t.addEntry} disabled={busy || (!session.nav_path && !session.ncx_path) || tocDraft.length >= 5000} onClick={() => addTocEntry(null)}><Plus size={17} /></button>
              <button type="button" title={t.generateToc} aria-label={t.generateToc} disabled={!session.nav_path && !session.ncx_path || busy} onClick={() => { setTocProposal([]); setTocPatternOpen(true); }}><ListTree size={17} /></button>
              <button type="button" title={t.generateTocPage} aria-label={t.generateTocPage} disabled={!tocDraft.length || busy} onClick={() => { setTocPageTitle(t.tocPageTitle); setTocPageOpen(true); }}><FilePlus2 size={17} /></button>
              <button type="button" title={t.applyToc} aria-label={t.applyToc} disabled={!tocDirty || busy} onClick={() => void applyToc()}><Check size={17} /></button>
            </div>
            {tocDraft.map((entry, index) => <div key={index} className={styles.tocRow} data-depth={entry.depth} style={{ marginLeft: `${Math.min(entry.depth, 8) * 12}px` }}>
              <div className={styles.tocTitleRow}><span>{index + 1}</span><input aria-label={`${t.label} ${index + 1}`} title={entry.href} value={entry.label} placeholder={t.label} onChange={(event) => updateEntry(index, { label: event.target.value })} /><button type="button" className={styles.tocMore} title={t.chooseTocTarget} aria-label={`${t.chooseTocTarget} ${index + 1}`} disabled={busy} onClick={() => void openTocPicker(index)}><ListTree size={15} /></button><button type="button" className={styles.tocMore} title={t.tocEntryActions} aria-label={`${t.tocEntryActions} ${index + 1}`} aria-expanded={tocControlsIndex === index} onClick={() => setTocControlsIndex((current) => current === index ? null : index)}><MoreHorizontal size={16} /></button></div>
              {tocControlsIndex === index ? <div className={styles.tocTargetRow}><input aria-label={`${t.target} ${index + 1}`} value={entry.href} placeholder={t.target} onChange={(event) => updateEntry(index, { href: event.target.value })} /></div> : null}
              {tocControlsIndex === index ? <div className={styles.tocButtons}>
                <button type="button" title={t.addSibling} aria-label={`${t.addSibling} ${index + 1}`} disabled={tocDraft.length >= 5000} onClick={() => addTocEntry(index)}><Plus size={15} /></button>
                <button type="button" title={t.addChild} aria-label={`${t.addChild} ${index + 1}`} disabled={entry.depth >= 8 || tocDraft.length >= 5000} onClick={() => addTocEntry(index, true)}><CornerDownRight size={15} /></button>
                <button type="button" title={t.up} aria-label={`${t.up} ${index + 1}`} disabled={siblingIndex(tocDraft, index, -1) < 0} onClick={() => moveEntry(index, -1)}><ArrowUp size={15} /></button>
                <button type="button" title={t.down} aria-label={`${t.down} ${index + 1}`} disabled={siblingIndex(tocDraft, index, 1) < 0} onClick={() => moveEntry(index, 1)}><ArrowDown size={15} /></button>
                <button type="button" title={t.outdent} aria-label={`${t.outdent} ${index + 1}`} disabled={entry.depth === 0} onClick={() => outdentEntry(index)}><ArrowLeft size={15} /></button>
                <button type="button" title={t.indent} aria-label={`${t.indent} ${index + 1}`} disabled={index === 0 || entry.depth >= tocDraft[index - 1].depth + 1 || tocDraft.slice(index, subtreeEnd(tocDraft, index)).some((child) => child.depth >= 8)} onClick={() => shiftDepth(index, 1)}><ArrowRight size={15} /></button>
                <button type="button" title={t.removeEntry} aria-label={`${t.removeEntry} ${index + 1}`} onClick={() => removeEntry(index)}><X size={15} /></button>
              </div> : null}
            </div>)}
          </> : null}
        </div>
      </aside>
      {separator("sidebar", sidebarWidth)}
      <div className={styles.editorArea}>
        {sideView === "book" ? <div className={styles.bookReader}>
          <div className={styles.readerToolbar}><strong>{t.bookPreview}</strong><span>{visibleBookStart + 1}{spreadMate ? `-${visibleBookEnd + 1}` : ""} / {session.spine.length}</span>
            <select aria-label={t.bookPreview} value={bookMode} onChange={(event) => setBookMode(event.target.value as "continuous" | "paged")}><option value="continuous">{t.continuous}</option><option value="paged">{t.paged}</option></select>
            {bookRendition.layout === "pre-paginated" ? <label><input type="checkbox" checked={bookSpread} onChange={(event) => setBookSpread(event.target.checked)} />{t.spreads}</label> : null}
            {bookMode === "paged" ? <span>{t.page} {bookPage.page + 1} / {bookPage.pages}</span> : null}
            {wrapControl}{zoomControls(previewZoom, setPreviewZoom, t.previewZoom, 30)}<button type="button" title={t.fitPage} aria-label={t.fitPage} onClick={() => setPreviewZoom(100)}><Maximize size={16} /></button>
            <div><button type="button" title={t.previousPage} aria-label={t.previousPage} disabled={!bookReady || bookLoading || visibleBookStart === 0 && (bookMode !== "paged" || bookPage.page === 0)} onClick={() => turnPage(-1)}><ArrowLeft size={18} /></button><button type="button" title={t.nextPage} aria-label={t.nextPage} disabled={!bookReady || bookLoading || visibleBookEnd >= session.spine.length - 1 && (bookMode !== "paged" || bookPage.page >= bookPage.pages - 1)} onClick={() => turnPage(1)}><ArrowRight size={18} /></button></div>
          </div>
          {bookError ? <div className={styles.noPreview}>{t.previewInvalid}</div> : bookLoading ? <div className={styles.noPreview}>{t.loading}</div> : <div className={styles.bookPage} data-spread={!!spreadMate}><div className={styles.bookViewport} style={spreadMate ? { order: bookRendition.direction === "rtl" ? -bookIndex : bookIndex } : undefined}>
            <EpubPreviewFrame key={session.spine[bookIndex]} html={bookPreviewHtml} title={t.bookPreview} zoom={previewZoom} mode={bookMode} step={pageStep} fragment={previewFragment.path === session.spine[bookIndex] ? previewFragment.fragment : ""} navigation={previewFragment.request} location={readingLocations.current[session.spine[bookIndex]]} onLocation={(location) => { rememberLocation(session.spine[bookIndex], location); setBookPage({ page: location.page, pages: location.pages }); }} onBoundary={turnChapter} onFind={openSearch} onLink={previewLink} onWarning={previewWarning} onReady={setBookReady} />
          </div>{spreadMate ? <div className={styles.bookViewport} style={{ order: bookRendition.direction === "rtl" ? -spreadMate.index : spreadMate.index }}><EpubPreviewFrame html={spreadMate.html} title={`${t.bookPreview}: ${spreadMate.index + 1}`} zoom={previewZoom} onBoundary={turnChapter} onFind={openSearch} onLink={previewLink} /></div> : null}</div>}
        </div> : <>
        <div className={styles.fileHeading}><strong title={selectedPath}>{selectedPath}</strong><div className={styles.paneSwitch}><button type="button" aria-selected={paneView === "source"} onClick={() => setPaneView("source")}>{t.source}</button><button type="button" aria-selected={paneView === "preview"} onClick={() => setPaneView("preview")}>{t.preview}</button></div>{busy ? <span>{t.loading}</span> : null}</div>
        <div className={styles.documentTabs} role="tablist">{openPaths.filter((path) => session.files.some((file) => file.path === path)).map((path) => <div key={path} className={styles.documentTab} data-active={path === selectedPath}><button type="button" role="tab" aria-selected={path === selectedPath} title={path} disabled={busy} onClick={() => void selectResource(path)}>{path.split("/").at(-1)}{path === selectedPath && sourceDirty ? " *" : ""}</button><button type="button" title={t.closeTab} aria-label={`${t.closeTab}: ${path}`} disabled={busy || openPaths.length <= 1} onClick={() => { void run(async () => { await commitSource(); if (path === selectedPath) await loadResource(session.session_id, openPaths.find((item) => item !== path)!); setOpenPaths((current) => current.filter((item) => item !== path)); editorStates.current.delete(path); }); }}><X size={13} /></button></div>)}</div>
        <div className={styles.split} data-mode={paneView} style={{ "--source-width": `${sourceWidth}%` } as CSSProperties}>
          <section className={styles.sourcePane} style={{ "--source-font-size": `${13 * sourceZoom / 100}px` } as CSSProperties}>
            <div className={styles.paneHeader}><h3>{t.source}</h3><div className={styles.sourceHeaderControls}>
              <button type="button" title={t.insertImage} aria-label={t.insertImage} disabled={!isHtml || selectedPath === session.nav_path || busy} onClick={() => { const first = session.files.find((file) => file.media_type.startsWith("image/")); setImagePath(first?.path ?? ""); setImageMode(first ? "existing" : "import"); setImageInput(""); setImageTarget(""); setImageAlt(""); setImageOpen(true); }}><ImagePlus size={16} /></button>
              {zoomControls(sourceZoom, setSourceZoom, t.sourceZoom, 60)}
            </div></div>
            <CodeMirror key={`${selectedPath}:${editorEpoch}`} value={content} editable={!busy} onCreateEditor={restoreSourceEditor}
              initialState={savedEditorHistory(editorStates.current.get(selectedPath)?.state, content)}
              onUpdate={(update) => {
                const depth = { undo: undoDepth(update.state), redo: redoDepth(update.state) };
                setLocalHistory((previous) => previous.undo === depth.undo && previous.redo === depth.redo ? previous : depth);
                if (syncPreview && update.selectionSet && !previewSelectingSource.current && isPreviewable && selectedPath === previewPath) {
                  const position = update.state.selection.main.head, line = update.state.doc.lineAt(position);
                  setSourceLine(line.number); setSourceColumn(Array.from(line.text.slice(0, position - line.from)).length); setSourceRequest((request) => request + 1);
                }
              }}
              onChange={(value) => { if (value === content) return; setContent(value); setSelectionRange(null); setMatches([]); setMatchIndex(-1); }}
              extensions={[EditorView.lineWrapping, ...(currentFile?.media_type === "text/css" ? [css()] : currentFile?.media_type === "text/plain" || currentFile?.media_type?.includes("javascript") ? [] : [xml()])]}
              theme={colorTheme} height="100%" basicSetup={{ lineNumbers: true, foldGutter: true }} />
          </section>
          {separator("source", sourceWidth)}
          <section className={styles.previewPane}>
            <div className={styles.paneHeader}><h3>{t.preview}</h3>{previewFile?.media_type.startsWith("image/") ? <span className={styles.previewResourceName} title={previewFile.path}>{previewFile.path.split("/").at(-1)}</span> : null}<div className={styles.previewHeaderControls}>
              <button type="button" title={t.syncPreview} aria-label={t.syncPreview} aria-pressed={syncPreview} onClick={() => { if (sourceEditor.current) setSourceLine(sourceEditor.current.state.doc.lineAt(sourceEditor.current.state.selection.main.head).number); setSyncPreview((value) => !value); }}><Link2 size={16} /></button>
              <button type="button" title={t.inspectStyle} aria-label={t.inspectStyle} aria-pressed={inspectPreview} onClick={() => { setInspectPreview((value) => !value); setComputedStyles(null); }}><ScanLine size={16} /></button>
              {wrapControl}{zoomControls(previewZoom, setPreviewZoom, t.previewZoom, 30)}
            </div></div>
            {computedStyles ? <div className={styles.styleInspector}><strong>{t.styles}</strong><button type="button" title={t.close} onClick={() => setComputedStyles(null)}><X size={14} /></button><dl>{Object.entries(computedStyles).map(([property, value]) => <div key={property}><dt>{property}</dt><dd>{value}</dd></div>)}</dl></div> : null}
            {previewError ? <div className={styles.noPreview}>{t.previewInvalid}</div> : previewPath ? <div className={styles.previewViewport}>
              <EpubPreviewFrame key={previewPath} html={chapterPreviewHtml} title={t.preview} zoom={previewZoom} inspect={inspectPreview} sourceLine={syncPreview ? sourceLine : 0} sourceColumn={sourceColumn} sourceRequest={sourceRequest} fragment={previewFragment.path === previewPath ? previewFragment.fragment : ""} navigation={previewFragment.request} location={readingLocations.current[`source:${previewPath}`]} onLocation={(location) => { rememberLocation(`source:${previewPath}`, location); if (location.anchorLine && (location.origin === "user" || (location.origin === "layout" && !sourceLine))) locateSource(location.anchorLine, location.anchorColumn); }} onLocate={locateSource} onBoundary={turnChapter} onFind={openSearch} onInspect={inspectElement} onLink={previewLink} />
            </div> : <div className={styles.noPreview}>{t.noPreview}</div>}
          </section>
        </div>
        </>}
      </div>
    </div>}
    {searchOpen && session ? <div className={styles.searchPanel} role="search" aria-label={t.find}>
      <div className={styles.searchControls}>
        <input ref={searchInput} aria-label={t.query} placeholder={t.query} value={query} onChange={(event) => { setQuery(event.target.value); setMatches([]); setMatchIndex(-1); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); if (matches.length) void navigateMatch(event.shiftKey ? -1 : 1); else void search(event.shiftKey ? -1 : 1); } }} />
        <span className={styles.selectField}><select aria-label={t.files} value={scope} onChange={(event) => { setScope(event.target.value as Scope); setSelectionRange(null); setMatches([]); setMatchIndex(-1); }}><option value="current">{t.current}</option><option value="selection">{t.selection}</option><option value="text">{t.textFiles}</option><option value="styles">{t.styleFiles}</option><option value="all">{t.all}</option></select><ChevronDown size={12} /></span>
        <div className={styles.searchCommands}>
          <button type="button" title={t.caseSensitive} aria-label={t.caseSensitive} aria-pressed={caseSensitive} onClick={() => setCaseSensitive((value) => !value)}><CaseSensitive size={16} /></button>
          <button type="button" title={t.regularExpression} aria-label={t.regularExpression} aria-pressed={regularExpression} onClick={() => setRegularExpression((value) => !value)}><Regex size={16} /></button>
          <button type="button" title={t.ignoreMarkup} aria-label={t.ignoreMarkup} aria-pressed={ignoreMarkup} onClick={() => setIgnoreMarkup((value) => !value)}><CodeXml size={16} /></button>
          <button type="button" title={t.savedSearches} aria-label={t.savedSearches} disabled={busy} onClick={() => setSavedSearchOpen(true)}><Save size={14} /></button>
          <button type="button" title={t.find} aria-label={t.find} disabled={!query || busy || !scopePaths().length} onClick={() => matches.length ? void navigateMatch(1) : void search()}><Search size={14} /></button>
          <div className={styles.matchNavigation} role="status"><span>{matches.length ? `${matchIndex + 1} / ${matches.length}${matches.length >= 5000 ? "+" : ""}` : t.noMatches}</span><button type="button" title={t.previousMatch} aria-label={t.previousMatch} disabled={!query || busy || !scopePaths().length} onClick={() => matches.length ? void navigateMatch(-1) : void search(-1)}><ArrowUp size={14} /></button><button type="button" title={t.nextMatch} aria-label={t.nextMatch} disabled={!query || busy || !scopePaths().length} onClick={() => matches.length ? void navigateMatch(1) : void search()}><ArrowDown size={14} /></button></div>
        </div>
        <button type="button" title={t.close} aria-label={`${t.close}: ${t.find}`} onClick={() => { setSearchOpen(false); sourceEditor.current?.focus(); }}><X size={14} /></button>
        <input aria-label={t.replacement} placeholder={t.replacement} value={replacement} onChange={(event) => setReplacement(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing && matchIndex >= 0 && !busy) { event.preventDefault(); void replaceOne(matches[matchIndex]); } }} />
        <div className={styles.replaceCommands}>
          <button type="button" disabled={matchIndex < 0 || busy} onClick={() => void replaceOne(matches[matchIndex])}>{t.replaceOne}</button>
          <button type="button" disabled={!matches.length || matches.length >= 5000 || busy} title={matches.length >= 5000 ? t.narrowSearch : undefined} onClick={() => void replaceAll()}>{t.replaceAll}</button>
        </div>
      </div>
    </div> : null}
    {fileMenu ? <div ref={fileMenuRef} className={styles.fileContextMenu} role="menu" aria-label={t.fileOptions} style={{ left: fileMenu.x, top: fileMenu.y }} onKeyDown={(event) => {
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      buttons[event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length]?.focus();
    }}>
      <button type="button" role="menuitem" disabled={busy || operationPaths.length !== 1} onClick={() => { setFileMenu(null); void selectResource(operationPaths[0]); }}><FileCode2 size={16} />{t.open}</button>
      <button type="button" role="menuitem" disabled={busy || operationPaths.some((path) => path === session.nav_path || path === session.ncx_path)} onClick={() => void copyFiles()}><Copy size={16} />{t.copyResource}</button>
      <button type="button" role="menuitem" disabled={busy || operationPaths.some((path) => path === session.nav_path || path === session.ncx_path)} onClick={() => void openResourceAction("rename")}><Pencil size={16} />{operationPaths.length > 1 ? t.bulkRename : t.renameResource}</button>
      <button type="button" role="menuitem" disabled={busy || operationPaths.length !== 1} onClick={() => void openResourceAction("replace")}><Replace size={16} />{t.replaceResource}</button>
      <button type="button" role="menuitem" disabled={busy} onClick={() => void openResourceAction("export")}><Download size={16} />{t.exportResource}{operationPaths.length > 1 ? " (ZIP)" : ""}</button>
      <button type="button" role="menuitem" disabled={busy || operationPaths.length < 2 || !operationPaths.every((path) => session.files.find((file) => file.path === path)?.media_type === session.files.find((file) => file.path === operationPaths[0])?.media_type) || !["text/css", "application/xhtml+xml"].includes(session.files.find((file) => file.path === operationPaths[0])?.media_type ?? "")} onClick={() => { setFileMenu(null); setMergePaths(operationPaths); setMergeOpen(true); }}><CornerDownRight size={16} />{t.mergeFiles}</button>
      <button type="button" role="menuitem" disabled={busy || operationPaths.some((path) => path === session.nav_path || path === session.ncx_path)} onClick={() => void openResourceAction("delete")}><Trash2 size={16} />{t.deleteResource}</button>
    </div> : null}
    {chapterOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.newChapter}><h3>{t.newChapter}</h3><label>{t.archivePath}<input value={chapterPath} onChange={(event) => setChapterPath(event.target.value)} /></label><label>{t.label}<input value={chapterTitle} onChange={(event) => setChapterTitle(event.target.value)} /></label><label>{t.chapterText}<textarea value={chapterText} onChange={(event) => setChapterText(event.target.value)} rows={6} /></label><div className={styles.saveActions}><button type="button" onClick={() => setChapterOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy || !chapterPath.trim() || !chapterTitle.trim()} onClick={() => void createChapter()}>{t.newChapter}</button></div></div></div> : null}
    {splitOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.splitChapter}><h3>{t.splitChapter}</h3><label>{t.splitBefore}<select value={splitIndex} onChange={(event) => setSplitIndex(Number(event.target.value))}>{splitPoints.map((point) => <option key={point.index} value={point.index}>{point.index + 1}. {point.label}</option>)}</select></label><label>{t.archivePath}<input value={splitTarget} onChange={(event) => setSplitTarget(event.target.value)} /></label><div className={styles.saveActions}><button type="button" onClick={() => setSplitOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy || !splitTarget.trim()} onClick={() => void splitChapter()}>{t.splitChapter}</button></div></div></div> : null}
    {imageOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.insertImage}><h3>{t.insertImage}</h3><div className={styles.tocSourceModes}><button type="button" aria-pressed={imageMode === "existing"} disabled={!session.files.some((file) => file.media_type.startsWith("image/"))} onClick={() => setImageMode("existing")}>{t.imageResource}</button><button type="button" aria-pressed={imageMode === "import"} onClick={() => setImageMode("import")}>{t.importResource}</button></div>{imageMode === "existing" ? <label>{t.imageResource}<select value={imagePath} onChange={(event) => setImagePath(event.target.value)}>{session.files.filter((file) => file.media_type.startsWith("image/")).map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}</select></label> : <><label>{t.localFile}<div className={styles.resourcePick}><input value={imageInput} onChange={(event) => setImageInput(event.target.value)} /><button type="button" title={t.chooseFile} aria-label={t.chooseFile} onClick={() => void chooseImageFile()}><FolderOpen size={17} /></button></div></label><label>{t.archivePath}<input value={imageTarget} onChange={(event) => setImageTarget(event.target.value)} /></label></>}<label>{t.imageDescription}<input value={imageAlt} onChange={(event) => setImageAlt(event.target.value)} /></label><div className={styles.saveActions}><button type="button" onClick={() => setImageOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy || (imageMode === "existing" ? !imagePath : !imageInput || !imageTarget || !/\.(?:apng|avif|gif|jpe?g|png|svg|webp)$/i.test(imageTarget))} onClick={() => void insertImage()}>{t.insertImage}</button></div></div></div> : null}
    {replaceProposal ? <div className={styles.modalBackdrop}><div className={`${styles.saveDialog} ${styles.tocPatternDialog}`} role="dialog" aria-modal="true" aria-label={t.replacePreviewTitle}><h3>{t.replacePreviewTitle}</h3><p>{replaceProposal.replacements} {t.matches} · {replaceProposal.files_changed} {t.filesChanged}</p><div className={styles.tocProposal}>{replaceProposal.samples.map((sample, index) => <div key={`${sample.path}-${sample.start}-${index}`}><small>{sample.path}</small><span>{sample.before} → {sample.after}</span></div>)}</div><div className={styles.saveActions}><button type="button" onClick={() => setReplaceProposal(null)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy} onClick={() => void applyReplaceAll()}>{t.replaceAll}</button></div></div></div> : null}
    {tocPatternOpen && session ? <div className={styles.modalBackdrop}><div className={`${styles.saveDialog} ${styles.tocPatternDialog}`} role="dialog" aria-modal="true" aria-label={t.generateToc}>
      <h3>{t.generateToc}</h3>
      <div className={styles.tocSourceModes}>{(["headings", "files", "xpath"] as const).map((source) => <button key={source} type="button" aria-pressed={tocSource === source} onClick={() => { setTocSource(source); setTocProposal([]); }}>{source === "headings" ? t.fromHeadings : source === "files" ? t.fromFiles : "XPath"}</button>)}</div>
      <details><summary>?</summary><p>{tocSource === "xpath" ? t.tocXPathHelp : tocSource === "files" ? t.tocFromFilesHelp : t.tocPatternHelp}</p></details>
      {tocSource !== "files" ? (tocSource === "xpath" ? tocXpaths : tocPatterns).map((pattern, index) => <label key={`${tocSource}-${index}`}>{t.tocLevel} {index + 1}<input value={pattern} placeholder={tocSource === "xpath" ? "//h:h1" : t.tocPatternPlaceholder} onChange={(event) => { (tocSource === "xpath" ? setTocXpaths : setTocPatterns)((current) => current.map((value, position) => position === index ? event.target.value : value)); setTocProposal([]); }} /></label>) : null}
      <button type="button" disabled={busy} onClick={() => void previewGeneratedToc()}>{t.previewToc}</button>
      {tocProposal.length ? <div className={styles.tocProposal}>{tocProposal.map((entry, index) => <div key={`${entry.href}-${index}`} style={{ paddingLeft: `${entry.depth * 16}px` }}><strong>{entry.label}</strong><small>{entry.href}</small></div>)}</div> : null}
      <div className={styles.saveActions}><button type="button" onClick={() => setTocPatternOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={!tocProposal.length || busy} onClick={() => void generateToc()}>{t.applyToc}</button></div>
    </div></div> : null}
    {tocPageOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.generateTocPage}><h3>{t.generateTocPage}</h3><label>{t.label}<input value={tocPageTitle} onChange={(event) => setTocPageTitle(event.target.value)} /></label><div className={styles.saveActions}><button type="button" onClick={() => setTocPageOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={!tocPageTitle.trim() || busy} onClick={() => void createTocPage()}>{t.generateTocPage}</button></div></div></div> : null}
    {tocPickerIndex !== null && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.chooseTocTarget}><h3>{t.chooseTocTarget}</h3><label>{t.chooseChapter}<select value={tocPickerPath} disabled={busy} onChange={(event) => void changeTocPickerPath(event.target.value)}>{session.files.filter((file) => (file.media_type === "application/xhtml+xml" || file.media_type === "text/html") && file.path !== session.nav_path).map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}</select></label><label>{t.chooseAnchor}<select value={tocPickerAnchor} onChange={(event) => setTocPickerAnchor(event.target.value)}><option value="">{t.chapterStart}</option>{tocAnchors.map((anchor) => <option key={anchor.id} value={anchor.id}>{anchor.label} (#{anchor.id})</option>)}</select></label><div className={styles.saveActions}><button type="button" onClick={() => setTocPickerIndex(null)}>{t.cancel}</button><button type="button" className={styles.primary} onClick={() => { updateEntry(tocPickerIndex, { href: tocPickerPath + (tocPickerAnchor ? `#${encodeURIComponent(tocPickerAnchor)}` : "") }); setTocPickerIndex(null); }}>{t.confirmTarget}</button></div></div></div> : null}
    {mergeOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.mergeFiles}><h3>{t.mergeFiles}</h3><label>{t.mergeOrder}</label><div className={styles.mergeList}>{mergePaths.map((path, index) => <div key={path}><span>{index + 1}. {path}</span><button type="button" title={t.up} disabled={!index} onClick={() => setMergePaths((current) => { const next = [...current]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; return next; })}><ArrowUp size={14} /></button><button type="button" title={t.removeEntry} onClick={() => setMergePaths((current) => current.filter((item) => item !== path))}><X size={14} /></button></div>)}</div><select aria-label={t.files} value="" onChange={(event) => { if (event.target.value) setMergePaths((current) => [...current, event.target.value]); }}><option value="">{t.addEntry}</option>{session.files.filter((file) => file.media_type === currentFile?.media_type && file.path !== session.nav_path && !mergePaths.includes(file.path)).map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}</select><div className={styles.saveActions}><button type="button" onClick={() => setMergeOpen(false)}>{t.cancel}</button><button type="button" disabled={busy || mergePaths.length < 2} onClick={() => void mergeFiles()}>{t.mergeFiles}</button></div></div></div> : null}
    {toolsOpen && session ? <EpubEditorTools session={session} close={() => setToolsOpen(false)}
      commit={async () => { await commitDirectory(); }}
      update={async (next, resetHistory) => { setSession(next); setTocDraft(next.toc); const path = resourceAfterHistory(selectedPath, session.spine, next.files); if (path) await loadResource(next.session_id, path, next); if (resetHistory) { resetEditorHistory(); setSelectionRange(null); setMatches([]); setMatchIndex(-1); } }}
      select={(path, line) => { setSideView("files"); setPaneView("source"); setMatches([]); setMatchIndex(-1); setSourceTarget({ path, line }); void selectResource(path); }} /> : null}
    {resourceAction && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={{ add: t.importResource, rename: t.renameResource, replace: t.replaceResource, export: t.exportResource, delete: t.deleteResource }[resourceAction]}>
      <h3>{{ add: t.importResource, rename: t.renameResource, replace: t.replaceResource, export: t.exportResource, delete: t.deleteResource }[resourceAction]}</h3>
      {resourceAction === "add" || resourceAction === "replace" ? <label>{t.localFile}<div className={styles.resourcePick}><input value={resourceInput} onChange={(event) => setResourceInput(event.target.value)} /><button type="button" title={t.chooseFile} aria-label={t.chooseFile} onClick={() => void chooseResourceFile()}><FolderOpen size={17} /></button></div></label> : null}
      {actionPaths.length > 1 ? <p>{actionPaths.length} {t.selectedFiles}</p> : null}
      {resourceAction === "add" || resourceAction === "rename" || resourceAction === "export" ? <label>{resourceAction === "export" ? t.outputPath : resourceAction === "rename" && actionPaths.length > 1 ? t.renamePrefix : t.archivePath}<div className={styles.resourcePick}><input value={resourceTarget} onChange={(event) => setResourceTarget(event.target.value)} />{resourceAction === "export" ? <button type="button" title={t.chooseFile} aria-label={t.chooseFile} onClick={() => void chooseResourceOutput()}><FolderOpen size={17} /></button> : null}</div></label> : null}
      {resourceAction === "rename" && actionPaths.length > 1 ? <><label>{t.startNumber}<input type="number" min={1} step={1} value={renameStart} onChange={(event) => setRenameStart(Math.max(1, Math.floor(Number(event.target.value)) || 1))} /></label><div className={styles.resourceReferences}>{Object.values(numberedResourceNames(actionPaths, resourceTarget, renameStart)).map((path) => <div key={path}>{path}</div>)}</div></> : null}
      {resourceAction === "delete" ? <><p>{t.confirmDeleteResource}</p>{resourceReferences.length ? <div className={styles.resourceReferences}><strong>{t.resourceReferences}</strong>{resourceReferences.map((reference) => <div key={reference}>{reference}</div>)}</div> : null}</> : null}
      <div className={styles.saveActions}><button type="button" onClick={() => setResourceAction(null)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy || ((resourceAction === "add" || resourceAction === "replace") && !resourceInput.trim()) || ((resourceAction === "add" || resourceAction === "rename" || resourceAction === "export") && !resourceTarget.trim()) || (resourceAction === "delete" && resourceReferences.length > 0)} onClick={() => void applyResourceAction()}>{t.applyResource}</button></div>
    </div></div> : null}
    {saveOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.save}><h3>{t.save}</h3><label className={styles.checkbox}><input type="checkbox" checked={overwriteSource} onChange={(event) => setOverwriteSource(event.target.checked)} />{t.overwrite}</label>{!overwriteSource ? <label>{t.outputPath}<input value={outputPath} onChange={(event) => setOutputPath(event.target.value)} /></label> : <p>{session.input_path}</p>}<div className={styles.saveActions}><button type="button" onClick={() => setSaveOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy} onClick={() => void saveBook()}><Save size={16} />{overwriteSource ? t.overwrite : t.saveAs}</button></div></div></div> : null}
    {closeOpen ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.closeTitle}><h3>{t.closeTitle}</h3><p>{t.dirtyClose}</p><div className={styles.saveActions}><button type="button" onClick={() => setCloseOpen(false)}>{t.cancel}</button><button type="button" onClick={() => closeEditor()}>{t.discardClose}</button><button type="button" onClick={() => { setCloseOpen(false); void stageSave(true); }}>{t.stageSave}</button><button type="button" onClick={() => { setCloseOpen(false); setCloseAfterSave(true); setOverwriteSource(false); setSaveOpen(true); }}>{t.saveAs}</button><button type="button" className={styles.primary} onClick={() => { setCloseOpen(false); setCloseAfterSave(true); setOverwriteSource(true); setSaveOpen(true); }}>{t.overwrite}</button></div></div></div> : null}
  </div>;
}
