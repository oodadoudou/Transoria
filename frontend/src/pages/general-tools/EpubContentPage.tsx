import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { EditorView } from "@codemirror/view";
import { css } from "@codemirror/lang-css";
import { xml } from "@codemirror/lang-xml";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, CornerDownRight, CornerUpLeft, CornerUpRight, Download, FileCode2, FileText, FolderOpen, ListTree, Pencil, Plus, Replace, Save, Search, Trash2, WrapText, X, ZoomIn, ZoomOut } from "lucide-react";

import { dialogsBridge, epubContentBridge, type EpubContentFile, type EpubContentMatch, type EpubContentSession, type EpubTocEntry } from "@/bridge";
import { useMessages } from "@/locales";
import { useSettingsStore } from "@/store/useSettingsStore";
import { clearEditorDraft, clearStagedEditorDraft, readEditorDraft, stageEditorDraft, writeEditorDraft, type EpubEditorDraft } from "./epubEditorDraft";
import styles from "./EpubContentPage.module.css";

type SideView = "files" | "toc" | "spine" | "book";
type Scope = "current" | "text" | "styles" | "all" | "selection";
type ResourceAction = "add" | "rename" | "replace" | "export" | "delete";
type ReplaceProposal = Awaited<ReturnType<typeof epubContentBridge.previewReplace>> & {
  selection?: { path: string; start: number; end: number };
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

function fileTree(files: EpubContentFile[]): FileNode[] {
  const roots: FileNode[] = [];
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
  const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });
  const sort = (items: FileNode[]) => {
    items.sort((left, right) => rankNode(left) - rankNode(right)
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

function previewAtZoom(markup: string, zoom: number, wrap: boolean): string {
  const scaled = markup
    .replace("max-width:100%!important;", `max-width:${zoom}%!important;`)
    .replace("max-height:calc(100vh - 24px)!important;", `max-height:calc(${zoom}vh - 24px)!important;`);
  if (!wrap) return scaled;
  const reflow = "<style>html,body{min-width:0!important;overflow-x:hidden!important}" +
    "body{white-space:normal!important}body :is(p,li,blockquote,div,td,th){white-space:normal!important;overflow-wrap:anywhere!important;word-break:break-word!important}" +
    "pre,code{white-space:pre-wrap!important;overflow-wrap:anywhere!important}</style>";
  return scaled.replace("</style>", `</style>${reflow}`);
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
  const previewFrame = useRef<HTMLIFrameElement | null>(null);
  const bookFrame = useRef<HTMLIFrameElement | null>(null);
  const sourceEditor = useRef<EditorView | null>(null);
  const [inputPath, setInputPath] = useState(initialPath);
  const [recent, setRecent] = useState(readRecent);
  const [restoring, setRestoring] = useState(Boolean(savedDraft.current && !initialPath));
  const [session, setSession] = useState<EpubContentSession | null>(null);
  const [selectedPath, setSelectedPath] = useState("");
  const [resourcePath, setResourcePath] = useState("");
  const [resourceAction, setResourceAction] = useState<ResourceAction | null>(null);
  const [resourceTarget, setResourceTarget] = useState("");
  const [resourceInput, setResourceInput] = useState("");
  const [resourceInSpine, setResourceInSpine] = useState(false);
  const [resourceReferences, setResourceReferences] = useState<string[]>([]);
  const [spineCandidate, setSpineCandidate] = useState("");
  const [previewPath, setPreviewPath] = useState("");
  const [loadedContent, setLoadedContent] = useState("");
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState("");
  const [tocDraft, setTocDraft] = useState<EpubTocEntry[]>([]);
  const [tocPickerIndex, setTocPickerIndex] = useState<number | null>(null);
  const [tocPickerPath, setTocPickerPath] = useState("");
  const [tocPickerAnchor, setTocPickerAnchor] = useState("");
  const [tocAnchors, setTocAnchors] = useState<Array<{ id: string; label: string }>>([]);
  const [tocPageOpen, setTocPageOpen] = useState(false);
  const [tocPageTitle, setTocPageTitle] = useState("");
  const [tocPatternOpen, setTocPatternOpen] = useState(false);
  const [tocPatterns, setTocPatterns] = useState(["", "", ""]);
  const [tocProposal, setTocProposal] = useState<EpubTocEntry[]>([]);
  const [sideView, setSideView] = useState<SideView>("files");
  const [paneView, setPaneView] = useState<"source" | "preview">("source");
  const [bookIndex, setBookIndex] = useState(0);
  const [bookHtml, setBookHtml] = useState("");
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
  const [scope, setScope] = useState<Scope>("current");
  const [selectionRange, setSelectionRange] = useState<{ path: string; start: number; end: number } | null>(null);
  const [matches, setMatches] = useState<EpubContentMatch[]>([]);
  const [replaceProposal, setReplaceProposal] = useState<ReplaceProposal | null>(null);
  const [matchIndex, setMatchIndex] = useState(-1);
  const [searchOpen, setSearchOpen] = useState(false);
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

  const editableFiles = useMemo(() => session?.files.filter((file) => file.editable) ?? [], [session?.files]);
  const nodes = useMemo(() => fileTree(session?.files ?? []), [session?.files]);
  const sourceDirty = content !== loadedContent;
  const tocDirty = Boolean(session && JSON.stringify(tocDraft) !== JSON.stringify(session.toc));
  const dirty = Boolean(session?.dirty || sourceDirty || tocDirty);
  const currentFile = session?.files.find((file) => file.path === selectedPath);
  const currentResource = session?.files.find((file) => file.path === resourcePath);
  const isHtml = currentFile?.media_type === "application/xhtml+xml" || currentFile?.media_type === "text/html";

  useEffect(() => {
    const match = matches[matchIndex];
    if (!match || match.path !== selectedPath || sideView === "book" || paneView !== "source") return;
    const editor = sourceEditor.current;
    if (!editor || editor.state.doc.toString() !== content) return;
    const anchor = editorOffset(content, match.start);
    const head = editorOffset(content, match.end);
    editor.dispatch({ selection: { anchor, head }, scrollIntoView: true });
    editor.focus();
  }, [matches, matchIndex, selectedPath, content, sideView, paneView]);

  useEffect(() => {
    if (!session) return;
    const draft: EpubEditorDraft = {
      sessionId: session.session_id,
      inputPath: session.input_path,
      selectedPath,
      sourceDraft: sourceDirty ? content : null,
      dirty,
      tocDraft,
      sideView,
      paneView,
      bookIndex,
      searchOpen,
      query,
      replacement,
      scope,
      selectionRange,
      caseSensitive,
      regularExpression,
      sidebarWidth,
      sourceWidth,
      sourceZoom,
      previewZoom,
      previewWrap,
    };
    currentDraft.current = draft;
    const timer = window.setTimeout(() => { writeEditorDraft(draft); }, 250);
    return () => window.clearTimeout(timer);
  }, [session?.session_id, session?.input_path, session?.dirty, selectedPath, content, loadedContent, tocDraft, sideView, paneView, bookIndex, searchOpen, query, replacement, scope, selectionRange, caseSensitive, regularExpression, sidebarWidth, sourceWidth, sourceZoom, previewZoom, previewWrap]);

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

  const loadResource = useCallback(async (sid: string, path: string, summary = session) => {
    const next = await epubContentBridge.read(sid, path);
    setSelectedPath(path);
    setResourcePath(path);
    setLoadedContent(next.content);
    setContent(next.content);
    const file = summary?.files.find((item) => item.path === path);
    if ((file?.media_type === "application/xhtml+xml" || file?.media_type === "text/html") && path !== summary?.nav_path) {
      setPreviewPath(path);
      try {
        setPreview((await epubContentBridge.preview(sid, path)).html);
        setPreviewError("");
      } catch (cause) {
        setPreview("");
        setPreviewError(cause instanceof Error ? cause.message : String(cause));
      }
    } else if (file?.media_type !== "text/css") {
      setPreviewPath("");
      setPreview("");
      setPreviewError("");
    }
  }, [session]);

  const commitSource = useCallback(async () => {
    if (!session || !selectedPath || content === loadedContent) return session;
    const next = await epubContentBridge.write(session.session_id, selectedPath, content);
    setSession(next);
    setLoadedContent(content);
    return next;
  }, [session, selectedPath, content, loadedContent]);

  useEffect(() => {
    if (!session || !selectedPath || !sourceDirty || !previewPath || (!isHtml && currentFile?.media_type !== "text/css")) return;
    const sid = session.session_id;
    const path = selectedPath;
    let active = true;
    const timer = window.setTimeout(() => {
      void epubContentBridge.previewDraft(sid, previewPath, path, content)
        .then((rendered) => { if (active) { setPreview(rendered.html); setPreviewError(""); } })
        .catch((cause: unknown) => { if (active) { setPreview(""); setPreviewError(cause instanceof Error ? cause.message : String(cause)); } });
    }, 900);
    return () => { active = false; window.clearTimeout(timer); };
  }, [session?.session_id, selectedPath, previewPath, sourceDirty, content, isHtml, currentFile?.media_type]);

  useEffect(() => {
    if (!session || sideView !== "book") return;
    const path = session.spine[bookIndex];
    if (!path) { setBookHtml(""); return; }
    let active = true;
    setBookLoading(true);
    setBookError("");
    const timer = window.setTimeout(() => {
      const request = sourceDirty
        ? epubContentBridge.previewDraft(session.session_id, path, selectedPath, content)
        : epubContentBridge.preview(session.session_id, path);
      void request.then((result) => { if (active) setBookHtml(result.html); })
        .catch((cause: unknown) => { if (active) { setBookHtml(""); setBookError(cause instanceof Error ? cause.message : String(cause)); } })
        .finally(() => { if (active) setBookLoading(false); });
    }, sourceDirty ? 500 : 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [session?.session_id, session?.spine, sideView, bookIndex, sourceDirty, selectedPath, content]);

  useEffect(() => {
    const frame = previewFrame.current;
    if (sideView === "book" || !frame) return;
    frame.srcdoc = previewAtZoom(preview, previewZoom, previewWrap);
    const timer = window.setTimeout(() => { if (previewFrame.current === frame) frame.srcdoc = previewAtZoom(preview, previewZoom, previewWrap); }, 120);
    return () => window.clearTimeout(timer);
  }, [preview, previewZoom, previewWrap, previewPath, previewError, sideView, session?.session_id]);

  useEffect(() => {
    const frame = bookFrame.current;
    if (sideView !== "book" || !frame) return;
    frame.srcdoc = previewAtZoom(bookHtml, previewZoom, previewWrap);
    const timer = window.setTimeout(() => { if (bookFrame.current === frame) frame.srcdoc = previewAtZoom(bookHtml, previewZoom, previewWrap); }, 120);
    return () => window.clearTimeout(timer);
  }, [bookHtml, previewZoom, previewWrap, bookIndex, bookLoading, bookError, sideView, session?.session_id]);

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
      setResourcePath("");
      setInputPath(path);
      setTocDraft(next.toc);
      setOutputPath(editedPath(path));
      setOverwriteSource(false);
      setCollapsedFolders([]);
      setMatches([]);
      setFeedback("");
      setBookIndex(0);
      const updatedRecent = [next.input_path, ...recent.filter((item) => item !== next.input_path)].slice(0, 4);
      setRecent(updatedRecent);
      try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(updatedRecent)); } catch { /* Optional history. */ }
      const first = next.spine.find((item) => next.files.some((file) => file.path === item && file.editable)) ?? next.files.find((file) => file.editable)?.path;
      if (first) {
        const file = await epubContentBridge.read(next.session_id, first);
        setSelectedPath(first);
        setResourcePath(first);
        setLoadedContent(file.content);
        setContent(file.content);
        if (first.toLowerCase().endsWith(".xhtml") || first.toLowerCase().endsWith(".html")) {
          setPreviewPath(first);
          try {
            setPreview((await epubContentBridge.preview(next.session_id, first)).html);
            setPreviewError("");
          } catch (cause) {
            setPreview("");
            setPreviewError(cause instanceof Error ? cause.message : String(cause));
          }
        }
      }
    });
  };

  const restoreBook = async (draft: EpubEditorDraft) => {
    await run(async () => {
      const next = await epubContentBridge.info(draft.sessionId);
      const path = next.files.some((file) => file.path === draft.selectedPath && file.editable)
        ? draft.selectedPath
        : next.spine.find((item) => next.files.some((file) => file.path === item && file.editable)) ?? "";
      setInputPath(next.input_path);
      setResourcePath(path);
      setOutputPath(editedPath(next.input_path));
      setTocDraft(Array.isArray(draft.tocDraft) ? draft.tocDraft : next.toc);
      setSideView(["files", "toc", "spine", "book"].includes(draft.sideView) ? draft.sideView : "files");
      setBookIndex(typeof draft.bookIndex === "number" && draft.bookIndex >= 0 && draft.bookIndex < next.spine.length ? draft.bookIndex : 0);
      setPaneView(draft.paneView === "preview" ? "preview" : "source");
      setSearchOpen(Boolean(draft.searchOpen));
      setQuery(draft.query ?? "");
      setReplacement(draft.replacement ?? "");
      setScope(["current", "text", "styles", "all", "selection"].includes(draft.scope) ? draft.scope : "current");
      setSelectionRange(draft.selectionRange ?? null);
      setCaseSensitive(Boolean(draft.caseSensitive));
      setRegularExpression(Boolean(draft.regularExpression));
      if (typeof draft.sidebarWidth === "number" && draft.sidebarWidth >= 10 && draft.sidebarWidth <= 70) setSidebarWidth(draft.sidebarWidth);
      if (typeof draft.sourceWidth === "number" && draft.sourceWidth >= 10 && draft.sourceWidth <= 90) setSourceWidth(draft.sourceWidth);
      if (typeof draft.sourceZoom === "number" && draft.sourceZoom >= 60 && draft.sourceZoom <= 200) setSourceZoom(draft.sourceZoom);
      if (typeof draft.previewZoom === "number" && draft.previewZoom >= 25 && draft.previewZoom <= 200) setPreviewZoom(draft.previewZoom);
      if (typeof draft.previewWrap === "boolean") setPreviewWrap(draft.previewWrap);
      if (path) {
        const file = await epubContentBridge.read(next.session_id, path);
        setSelectedPath(path);
        setLoadedContent(file.content);
        setContent(draft.sourceDraft ?? file.content);
        if (path.toLowerCase().endsWith(".xhtml") || path.toLowerCase().endsWith(".html")) {
          setPreviewPath(path);
          try {
            setPreview((await epubContentBridge.preview(next.session_id, path)).html);
            setPreviewError("");
          } catch (cause) {
            setPreview("");
            setPreviewError(cause instanceof Error ? cause.message : String(cause));
          }
        }
      }
      setSession(next);
    });
    setRestoring(false);
  };

  const chooseBook = async () => {
    const chosen = await run(() => dialogsBridge.chooseEpubFile(inputPath || undefined));
    if (chosen?.path) await openBook(chosen.path);
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
    if (!session.files.find((file) => file.path === path)?.editable || path === selectedPath) return;
    await run(async () => {
      await commitSource();
      await loadResource(session.session_id, path);
    });
  };

  const openResourceAction = async (action: ResourceAction) => {
    if (!session || (action !== "add" && !currentResource)) return;
    setResourceAction(action);
    setResourceTarget(action === "rename" ? resourcePath : "");
    setResourceInput("");
    setResourceInSpine(false);
    setResourceReferences([]);
    if (action === "delete") {
      const result = await run(() => epubContentBridge.references(session.session_id, resourcePath));
      if (result) setResourceReferences(result.inbound);
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
    const name = resourcePath.split("/").at(-1) || "resource";
    const extension = name.split(".").at(-1) || "";
    const chosen = await run(() => dialogsBridge.chooseSavePath(name, extension ? [extension] : []));
    if (chosen?.path) setResourceTarget(chosen.path);
  };

  const applyResourceAction = async () => {
    if (!session || !resourceAction) return;
    await run(async () => {
      await commitSource();
      let next: EpubContentSession | null = null;
      if (resourceAction === "add") next = await epubContentBridge.addResource(session.session_id, resourceInput, resourceTarget, resourceInSpine);
      if (resourceAction === "replace") next = await epubContentBridge.replaceResource(session.session_id, resourcePath, resourceInput);
      if (resourceAction === "rename") next = await epubContentBridge.renameResource(session.session_id, resourcePath, resourceTarget);
      if (resourceAction === "delete") next = await epubContentBridge.deleteResource(session.session_id, resourcePath);
      if (resourceAction === "export") await epubContentBridge.exportResource(session.session_id, resourcePath, resourceTarget, false);
      if (next) {
        setSession(next);
        setTocDraft(next.toc);
        const preferred = resourceAction === "rename" || resourceAction === "add" ? resourceTarget : resourcePath;
        setResourcePath(resourceAction === "delete" ? "" : preferred);
        if (resourceAction === "delete" && selectedPath === resourcePath) {
          const fallback = next.files.find((file) => file.editable)?.path;
          if (fallback) await loadResource(session.session_id, fallback, next);
          else { setSelectedPath(""); setContent(""); setLoadedContent(""); setPreview(""); }
        } else if (resourceAction === "rename" && selectedPath === resourcePath) {
          await loadResource(session.session_id, resourceTarget, next);
        } else if (resourceAction === "replace" && selectedPath === resourcePath && currentResource?.editable) {
          await loadResource(session.session_id, resourcePath, next);
        } else if (resourceAction === "add" && next.files.find((file) => file.path === resourceTarget)?.editable) {
          await loadResource(session.session_id, resourceTarget, next);
        }
      }
      setResourceAction(null);
      setFeedback(t.resourceSaved);
      setFeedbackWarning(false);
    });
  };

  const applyToc = async () => {
    if (!session || !tocDirty) return;
    await run(async () => {
      await commitSource();
      const next = await epubContentBridge.setToc(session.session_id, tocDraft);
      setSession(next);
      setTocDraft(next.toc);
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
      setFeedback(t.generateTocPage);
      setFeedbackWarning(false);
    });
  };

  const previewGeneratedToc = async () => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      const patterns = tocPatterns.some(Boolean) ? tocPatterns : undefined;
      const result = await epubContentBridge.previewToc(session.session_id, patterns);
      setTocProposal(result.entries);
    });
  };

  const generateToc = async () => {
    if (!session || !tocProposal.length) return;
    await run(async () => {
      const patterns = tocPatterns.some(Boolean) ? tocPatterns : undefined;
      const next = await epubContentBridge.generateToc(session.session_id, patterns);
      setSession(next);
      setTocDraft(next.toc);
      setTocPatternOpen(false);
      setTocProposal([]);
      setFeedback(`${next.generated_entries} ${t.generatedToc}${next.approximate_targets ? `; ${next.approximate_targets} ${t.approximateToc}` : ""}`);
      setFeedbackWarning(next.approximate_targets > 0);
      if (selectedPath) await loadResource(session.session_id, selectedPath);
    });
  };

  const moveSpine = async (index: number, shift: number) => {
    if (!session || index + shift < 0 || index + shift >= session.spine.length) return;
    await run(async () => {
      await commitSource();
      const order = session.spine.slice();
      [order[index], order[index + shift]] = [order[index + shift], order[index]];
      setSession(await epubContentBridge.reorderSpine(session.session_id, order));
    });
  };

  const updateSpine = async (entries: Array<{ path: string; linear: boolean }>) => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      const next = await epubContentBridge.setSpine(session.session_id, entries);
      setSession(next);
      setSpineCandidate("");
    });
  };

  const spineEntries = () => session?.spine.map((path) => ({ path, linear: session.spine_linear[path] !== false })) ?? [];

  const history = async (direction: "undo" | "redo") => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      const next = await epubContentBridge.history(session.session_id, direction);
      setSession(next);
      setTocDraft(next.toc);
      if (selectedPath) await loadResource(session.session_id, selectedPath);
      setMatches([]);
    });
  };

  const scopePaths = (): string[] => {
    if (scope === "current" || scope === "selection") return selectedPath ? [selectedPath] : [];
    if (scope === "text") return editableFiles.filter((file) => ["application/xhtml+xml", "text/html", "text/plain"].includes(file.media_type)).map((file) => file.path);
    if (scope === "styles") return editableFiles.filter((file) => file.media_type === "text/css").map((file) => file.path);
    return editableFiles.map((file) => file.path);
  };

  const search = async () => {
    if (!session || !query) return;
    await run(async () => {
      let selection = selectionRange;
      if (scope === "selection") {
        const editor = sourceEditor.current;
        const range = editor?.state.selection.main;
        if (editor && range && !range.empty && editor.state.doc.toString() === content) {
          selection = {
            path: selectedPath,
            start: Array.from(content.slice(0, range.from)).length,
            end: Array.from(content.slice(0, range.to)).length,
          };
          setSelectionRange(selection);
        }
        if (!selection || selection.path !== selectedPath) throw new Error(t.selectionExpired);
      }
      await commitSource();
      const found = await epubContentBridge.search(session.session_id, query, scopePaths(), caseSensitive, regularExpression, scope === "selection" ? selection ?? undefined : undefined);
      setMatches(found.matches);
      setMatchIndex(found.matches.length ? 0 : -1);
      if (found.matches.length) {
        setSideView("files");
        setPaneView("source");
        if (found.matches[0].path !== selectedPath) await loadResource(session.session_id, found.matches[0].path);
      }
    });
  };

  const navigateMatch = async (direction: -1 | 1) => {
    if (!session || !matches.length) return;
    const next = (matchIndex + direction + matches.length) % matches.length;
    setMatchIndex(next);
    setSideView("files");
    setPaneView("source");
    if (matches[next].path !== selectedPath) await selectResource(matches[next].path);
  };

  const replaceAll = async () => {
    if (!session || !query || !matches.length || matches.length >= 5000) return;
    await run(async () => {
      await commitSource();
      const selection = scope === "selection" ? selectionRange ?? undefined : undefined;
      if (scope === "selection" && !selection) throw new Error(t.selectionExpired);
      const proposal = await epubContentBridge.previewReplace(session.session_id, query, replacement, scopePaths(), caseSensitive, regularExpression, selection);
      if (!proposal.replacements) { setFeedback(t.noMatches); return; }
      setReplaceProposal({ ...proposal, selection });
    });
  };

  const applyReplaceAll = async () => {
    if (!session || !replaceProposal) return;
    await run(async () => {
      const result = await epubContentBridge.replace(session.session_id, query, replacement, scopePaths(), caseSensitive, replaceProposal.replacements, regularExpression, replaceProposal.selection, replaceProposal.fingerprints);
      setSession(result);
      if (selectedPath) await loadResource(session.session_id, selectedPath);
      setFeedback(`${result.replacements} ${t.matches}`);
      setReplaceProposal(null);
      setMatches([]);
      setMatchIndex(-1);
    });
  };

  const replaceOne = async (match: EpubContentMatch) => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      const next = await epubContentBridge.replaceMatch(session.session_id, query, replacement, match, caseSensitive, regularExpression);
      setSession(next);
      if (selectedPath === match.path) await loadResource(session.session_id, selectedPath);
      setMatches([]);
      setMatchIndex(-1);
      setFeedback(`1 ${t.matches}`);
    });
  };

  const saveBook = async () => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      if (tocDirty) {
        const next = await epubContentBridge.setToc(session.session_id, tocDraft);
        setSession(next);
        setTocDraft(next.toc);
      }
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
      setFeedback(`${t.saved}: ${result.output_path}`);
      setFeedbackWarning(false);
      if (closeAfterSave) closeEditor();
    });
  };

  const stageSave = async (closeAfter = false) => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      if (tocDirty) {
        const next = await epubContentBridge.setToc(session.session_id, tocDraft);
        setSession(next);
        setTocDraft(next.toc);
      }
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

  const updateEntry = (index: number, patch: Partial<EpubTocEntry>) => {
    setTocDraft((current) => current.map((entry, position) => position === index ? { ...entry, ...patch } : entry));
  };

  const addTocEntry = (index: number | null, child = false) => {
    if (!session || tocDraft.length >= 5000) return;
    setTocDraft((current) => {
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
    setTocDraft((current) => {
      const end = subtreeEnd(current, index);
      if (shift < 0) return [...current.slice(0, sibling), ...current.slice(index, end), ...current.slice(sibling, index), ...current.slice(end)];
      const siblingEnd = subtreeEnd(current, sibling);
      return [...current.slice(0, index), ...current.slice(sibling, siblingEnd), ...current.slice(index, end), ...current.slice(siblingEnd)];
    });
  };

  const shiftDepth = (index: number, amount: number) => {
    setTocDraft((current) => current.map((entry, position) => position >= index && position < subtreeEnd(current, index) ? { ...entry, depth: entry.depth + amount } : entry));
  };

  const outdentEntry = (index: number) => {
    setTocDraft((current) => {
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
    setTocDraft((current) => [...current.slice(0, index), ...current.slice(subtreeEnd(current, index))]);
  };

  const renderTree = (items: FileNode[], depth = 0): ReactNode => items.map((node) => {
    if (node.kind === "folder") {
      const collapsed = collapsedFolders.includes(node.path);
      return <div key={node.path}><div className={styles.folderRow} style={{ paddingLeft: `${depth * 9 + 4}px` }}><button type="button" aria-expanded={!collapsed} title={node.path} onClick={() => setCollapsedFolders((current) => collapsed ? current.filter((path) => path !== node.path) : [...current, node.path])}>{collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}{node.name}</button></div>{!collapsed ? renderTree(node.children, depth + 1) : null}</div>;
    }
    return <div key={node.file.path} className={`${styles.fileRow} ${resourcePath === node.file.path ? styles.active : ""}`} style={{ paddingLeft: `${depth * 9 + 4}px` }}><button type="button" onClick={() => void selectResource(node.file.path)} title={node.file.path}>{node.name}</button></div>;
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
        <div className={styles.importPath}><label htmlFor="epub-editor-import-path">{t.manualPath}</label><div><input id="epub-editor-import-path" value={inputPath} onChange={(event) => setInputPath(event.target.value)} placeholder="/path/to/book.epub" aria-label={t.choose} /><button type="button" disabled={!inputPath.trim() || restoring || busy} onClick={() => void openBook(inputPath.trim())}>{t.open}</button></div></div>
      </section>
      {recent.length ? <section className={styles.recentSection}><h3>{t.recent}</h3><div className={styles.recentList}>{recent.map((path) => <button type="button" key={path} disabled={restoring || busy} onClick={() => void openBook(path)}><FileText size={18} /><span><strong>{path.split(/[\\/]/).at(-1)}</strong><small title={path}>{path}</small></span><ChevronRight size={17} /></button>)}</div></section> : null}
    </div>
  </div>;

  return <div className={styles.workspace}>
    <div className={styles.toolbar}>
      <div className={styles.bookPicker}>
        <button type="button" title={t.choose} aria-label={t.choose} onClick={() => void chooseBook()}><FolderOpen size={18} /></button>
        <input value={inputPath} onChange={(event) => setInputPath(event.target.value)} placeholder=".epub" aria-label={t.choose} />
        <button type="button" disabled={!inputPath.trim() || busy} onClick={() => void openBook(inputPath.trim())}>{t.open}</button>
      </div>
      <div className={styles.toolbarActions}>
        <button type="button" title={t.find} aria-label={t.find} onClick={() => setSearchOpen(!searchOpen)}><Search size={18} /></button>
        <button type="button" title={t.undo} aria-label={t.undo} disabled={!session?.can_undo || busy} onClick={() => void history("undo")}><CornerUpLeft size={18} /></button>
        <button type="button" title={t.redo} aria-label={t.redo} disabled={!session?.can_redo || busy} onClick={() => void history("redo")}><CornerUpRight size={18} /></button>
        <button type="button" disabled={!session || busy} onClick={() => void stageSave()}><Save size={17} />{t.stageSave}</button>
        <button type="button" className={styles.primary} disabled={!session || busy} onClick={() => { setCloseAfterSave(false); setSaveOpen(true); }}><Download size={17} />{t.save}{dirty ? " *" : ""}</button>
        <button type="button" title={t.close} aria-label={t.close} onClick={requestClose}><X size={18} /></button>
      </div>
    </div>
    {error ? <div className={styles.error} role="alert">{t.error}: {sessionExpired ? t.sessionExpired : error}{sessionExpired && session ? <button type="button" onClick={() => void openBook(session.input_path)}>{t.reopen}</button> : null}</div> : null}
    {feedback ? <div className={feedbackWarning ? styles.warning : styles.feedback} role="status">{feedback}</div> : null}
    {searchOpen && session ? <div className={styles.searchPanel}>
      <div className={styles.searchControls}>
        <label>{t.query}<input value={query} onChange={(event) => { setQuery(event.target.value); setMatches([]); setMatchIndex(-1); }} onKeyDown={(event) => { if (event.key === "Enter") { if (matches.length) void navigateMatch(event.shiftKey ? -1 : 1); else void search(); } }} /></label>
        <label>{t.replacement}<input value={replacement} onChange={(event) => setReplacement(event.target.value)} /></label>
        <label><span>{t.files}</span><select value={scope} onChange={(event) => { setScope(event.target.value as Scope); setSelectionRange(null); setMatches([]); setMatchIndex(-1); }}><option value="current">{t.current}</option><option value="selection">{t.selection}</option><option value="text">{t.textFiles}</option><option value="styles">{t.styleFiles}</option><option value="all">{t.all}</option></select></label>
        <label className={styles.checkbox}><input type="checkbox" checked={caseSensitive} onChange={(event) => { setCaseSensitive(event.target.checked); setMatches([]); setMatchIndex(-1); }} />{t.caseSensitive}</label>
        <label className={styles.checkbox}><input type="checkbox" checked={regularExpression} onChange={(event) => { setRegularExpression(event.target.checked); setMatches([]); setMatchIndex(-1); }} />{t.regularExpression}</label>
        <button type="button" disabled={!query || busy || !scopePaths().length} onClick={() => void search()}><Search size={16} />{t.find}</button>
        <div className={styles.matchNavigation} role="status"><span>{matches.length ? `${matchIndex + 1} / ${matches.length}${matches.length >= 5000 ? "+" : ""}` : t.noMatches}</span><button type="button" title={t.previousMatch} aria-label={t.previousMatch} disabled={!matches.length || busy} onClick={() => void navigateMatch(-1)}><ArrowUp size={16} /></button><button type="button" title={t.nextMatch} aria-label={t.nextMatch} disabled={!matches.length || busy} onClick={() => void navigateMatch(1)}><ArrowDown size={16} /></button><button type="button" disabled={matchIndex < 0 || busy} onClick={() => void replaceOne(matches[matchIndex])}>{t.replaceOne}</button></div>
        <button type="button" disabled={!matches.length || matches.length >= 5000 || busy} title={matches.length >= 5000 ? t.narrowSearch : undefined} onClick={() => void replaceAll()}>{t.replaceAll}</button>
      </div>
    </div> : null}
    {!session ? <div className={styles.empty}>{t.noBook}</div> : <div className={styles.main} data-resizing={resizing} style={{ "--sidebar-width": `${sidebarWidth}%` } as CSSProperties}>
      <aside className={styles.sidebar}>
        <div className={styles.tabs}><button type="button" aria-selected={sideView === "files"} onClick={() => setSideView("files")}><FileCode2 size={16} />{t.files}</button><button type="button" aria-selected={sideView === "toc"} onClick={() => setSideView("toc")}><ListTree size={16} />{t.toc}</button><button type="button" aria-selected={sideView === "spine"} onClick={() => setSideView("spine")}>{t.spine}</button><button type="button" aria-selected={sideView === "book"} onClick={() => setSideView("book")}><BookOpen size={16} />{t.bookPreview}</button></div>
        {sideView === "files" ? <div className={styles.fileActions}>
          <button type="button" title={t.importResource} aria-label={t.importResource} disabled={busy} onClick={() => void openResourceAction("add")}><Plus size={16} /></button>
          <button type="button" title={t.renameResource} aria-label={t.renameResource} disabled={!currentResource || busy || resourcePath === session.nav_path || resourcePath === session.ncx_path} onClick={() => void openResourceAction("rename")}><Pencil size={16} /></button>
          <button type="button" title={t.replaceResource} aria-label={t.replaceResource} disabled={!currentResource || busy} onClick={() => void openResourceAction("replace")}><Replace size={16} /></button>
          <button type="button" title={t.exportResource} aria-label={t.exportResource} disabled={!currentResource || busy} onClick={() => void openResourceAction("export")}><Download size={16} /></button>
          <button type="button" title={t.deleteResource} aria-label={t.deleteResource} disabled={!currentResource || busy || resourcePath === session.nav_path || resourcePath === session.ncx_path} onClick={() => void openResourceAction("delete")}><Trash2 size={16} /></button>
          {currentResource ? <span className={styles.resourceInfo} title={resourcePath}>{currentResource.media_type} · {currentResource.size} B{currentResource.editable ? "" : ` · ${t.resourceNotEditable}`}</span> : null}
        </div> : null}
        <div className={styles.sideBody}>
          {sideView === "files" ? renderTree(nodes) : null}
          {sideView === "spine" ? <><div className={styles.orderAdd}><select aria-label={t.chooseChapter} value={spineCandidate} onChange={(event) => setSpineCandidate(event.target.value)}><option value="">{t.chooseChapter}</option>{session.files.filter((file) => (file.media_type === "application/xhtml+xml" || file.media_type === "text/html") && file.path !== session.nav_path && !session.spine.includes(file.path)).map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}</select><button type="button" title={t.addToOrder} aria-label={t.addToOrder} disabled={!spineCandidate || busy} onClick={() => void updateSpine([...spineEntries(), { path: spineCandidate, linear: true }])}><Plus size={16} /></button></div>{session.spine.map((path, index) => <div key={path} className={styles.orderRow}><button type="button" onClick={() => void selectResource(path)}>{index + 1}. {path}</button><label className={styles.orderLinear} title={t.mainReading}><input type="checkbox" checked={session.spine_linear[path] !== false} disabled={busy} onChange={(event) => void updateSpine(spineEntries().map((entry) => entry.path === path ? { ...entry, linear: event.target.checked } : entry))} /><span>{t.mainReading}</span></label><button type="button" title={t.up} aria-label={`${t.up}: ${path}`} disabled={index === 0 || busy} onClick={() => void moveSpine(index, -1)}><ArrowUp size={16} /></button><button type="button" title={t.down} aria-label={`${t.down}: ${path}`} disabled={index === session.spine.length - 1 || busy} onClick={() => void moveSpine(index, 1)}><ArrowDown size={16} /></button><button type="button" title={t.removeFromOrder} aria-label={`${t.removeFromOrder}: ${path}`} disabled={session.spine.length <= 1 || busy} onClick={() => void updateSpine(spineEntries().filter((entry) => entry.path !== path))}><X size={16} /></button></div>)}</> : null}
          {sideView === "book" ? session.spine.map((path, index) => <button type="button" key={`${path}-${index}`} className={styles.bookChapter} aria-current={bookIndex === index ? "page" : undefined} title={path} onClick={() => setBookIndex(index)}><span>{index + 1}</span><strong>{session.toc.find((entry) => entry.href.split("#")[0] === path)?.label ?? path.split("/").at(-1)}</strong></button>) : null}
          {sideView === "toc" ? <>
            <div className={styles.tocActions}>
              <button type="button" disabled={(!session.nav_path && !session.ncx_path) || tocDraft.length >= 5000} onClick={() => addTocEntry(null)}>{t.addEntry}</button>
              <button type="button" disabled={!session.nav_path && !session.ncx_path || busy} onClick={() => { setTocProposal([]); setTocPatternOpen(true); }}>{t.generateToc}</button>
              <button type="button" disabled={!tocDraft.length || busy} onClick={() => { setTocPageTitle(t.tocPageTitle); setTocPageOpen(true); }}>{t.generateTocPage}</button>
              <button type="button" disabled={!tocDirty || busy} onClick={() => void applyToc()}><Check size={16} />{t.applyToc}</button>
            </div>
            {tocDraft.map((entry, index) => <div key={index} className={styles.tocRow} data-depth={entry.depth} style={{ marginLeft: `${Math.min(entry.depth, 8) * 18}px` }}>
              <div className={styles.tocTitleRow}><span>{index + 1}</span><input aria-label={`${t.label} ${index + 1}`} value={entry.label} placeholder={t.label} onChange={(event) => updateEntry(index, { label: event.target.value })} /></div>
              <div className={styles.tocTargetRow}><input aria-label={`${t.target} ${index + 1}`} value={entry.href} placeholder={t.target} onChange={(event) => updateEntry(index, { href: event.target.value })} /><button type="button" title={t.chooseTocTarget} aria-label={`${t.chooseTocTarget} ${index + 1}`} disabled={busy} onClick={() => void openTocPicker(index)}><ListTree size={15} /></button></div>
              <div className={styles.tocButtons}>
                <button type="button" title={t.addSibling} aria-label={`${t.addSibling} ${index + 1}`} disabled={tocDraft.length >= 5000} onClick={() => addTocEntry(index)}><Plus size={15} /></button>
                <button type="button" title={t.addChild} aria-label={`${t.addChild} ${index + 1}`} disabled={entry.depth >= 8 || tocDraft.length >= 5000} onClick={() => addTocEntry(index, true)}><CornerDownRight size={15} /></button>
                <button type="button" title={t.up} aria-label={`${t.up} ${index + 1}`} disabled={siblingIndex(tocDraft, index, -1) < 0} onClick={() => moveEntry(index, -1)}><ArrowUp size={15} /></button>
                <button type="button" title={t.down} aria-label={`${t.down} ${index + 1}`} disabled={siblingIndex(tocDraft, index, 1) < 0} onClick={() => moveEntry(index, 1)}><ArrowDown size={15} /></button>
                <button type="button" title={t.outdent} aria-label={`${t.outdent} ${index + 1}`} disabled={entry.depth === 0} onClick={() => outdentEntry(index)}><ArrowLeft size={15} /></button>
                <button type="button" title={t.indent} aria-label={`${t.indent} ${index + 1}`} disabled={index === 0 || entry.depth >= tocDraft[index - 1].depth + 1 || tocDraft.slice(index, subtreeEnd(tocDraft, index)).some((child) => child.depth >= 8)} onClick={() => shiftDepth(index, 1)}><ArrowRight size={15} /></button>
                <button type="button" title={t.removeEntry} aria-label={`${t.removeEntry} ${index + 1}`} onClick={() => removeEntry(index)}><X size={15} /></button>
              </div>
            </div>)}
          </> : null}
        </div>
      </aside>
      {separator("sidebar", sidebarWidth)}
      <div className={styles.editorArea}>
        {sideView === "book" ? <div className={styles.bookReader}><div className={styles.readerToolbar}><strong>{t.bookPreview}</strong><span>{bookIndex + 1} / {session.spine.length}</span>{wrapControl}{zoomControls(previewZoom, setPreviewZoom, t.previewZoom, 30)}<div><button type="button" title={t.previousChapter} aria-label={t.previousChapter} disabled={bookIndex === 0} onClick={() => setBookIndex((index) => index - 1)}><ArrowLeft size={18} /></button><button type="button" title={t.nextChapter} aria-label={t.nextChapter} disabled={bookIndex >= session.spine.length - 1} onClick={() => setBookIndex((index) => index + 1)}><ArrowRight size={18} /></button></div></div>{bookError ? <div className={styles.noPreview}>{t.previewInvalid}</div> : bookLoading ? <div className={styles.noPreview}>{t.loading}</div> : <div className={styles.bookPage}><div className={styles.bookViewport}><iframe key={session.spine[bookIndex]} ref={bookFrame} title={t.bookPreview} sandbox="" style={{ width: `${10000 / previewZoom}%`, height: `${10000 / previewZoom}%`, transform: `scale(${previewZoom / 100})` }} /></div></div>}</div> : <>
        <div className={styles.fileHeading}><strong title={selectedPath}>{selectedPath}</strong><div className={styles.paneSwitch}><button type="button" aria-selected={paneView === "source"} onClick={() => setPaneView("source")}>{t.source}</button><button type="button" aria-selected={paneView === "preview"} onClick={() => setPaneView("preview")}>{t.preview}</button></div>{busy ? <span>{t.loading}</span> : null}</div>
        <div className={styles.split} data-mode={paneView} style={{ "--source-width": `${sourceWidth}%` } as CSSProperties}>
          <section className={styles.sourcePane} style={{ "--source-font-size": `${13 * sourceZoom / 100}px` } as CSSProperties}><div className={styles.paneHeader}><h3>{t.source}</h3>{zoomControls(sourceZoom, setSourceZoom, t.sourceZoom, 60)}</div><CodeMirror value={content} editable={!busy} onCreateEditor={(view) => { sourceEditor.current = view; }} onChange={(value) => { setContent(value); setMatches([]); setMatchIndex(-1); }} extensions={[EditorView.lineWrapping, ...(currentFile?.media_type === "text/css" ? [css()] : currentFile?.media_type === "text/plain" || currentFile?.media_type?.includes("javascript") ? [] : [xml()])]} theme={colorTheme} height="100%" basicSetup={{ lineNumbers: true, foldGutter: true }} /></section>
          {separator("source", sourceWidth)}
          <section className={styles.previewPane}><div className={styles.paneHeader}><h3>{t.preview}</h3><div className={styles.previewHeaderControls}>{wrapControl}{zoomControls(previewZoom, setPreviewZoom, t.previewZoom, 30)}</div></div>{previewError ? <div className={styles.noPreview}>{t.previewInvalid}</div> : ((isHtml && selectedPath !== session.nav_path) || currentFile?.media_type === "text/css") && previewPath ? <div className={styles.previewViewport}><iframe ref={previewFrame} title={t.preview} sandbox="" style={{ width: `${10000 / previewZoom}%`, height: `${10000 / previewZoom}%`, transform: `scale(${previewZoom / 100})` }} /></div> : <div className={styles.noPreview}>{t.noPreview}</div>}</section>
        </div>
        </>}
      </div>
    </div>}
    {replaceProposal ? <div className={styles.modalBackdrop}><div className={`${styles.saveDialog} ${styles.tocPatternDialog}`} role="dialog" aria-modal="true" aria-label={t.replacePreviewTitle}><h3>{t.replacePreviewTitle}</h3><p>{replaceProposal.replacements} {t.matches} · {replaceProposal.files_changed} {t.filesChanged}</p><div className={styles.tocProposal}>{replaceProposal.samples.map((sample, index) => <div key={`${sample.path}-${sample.start}-${index}`}><small>{sample.path}</small><span>{sample.before} → {sample.after}</span></div>)}</div><div className={styles.saveActions}><button type="button" onClick={() => setReplaceProposal(null)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy} onClick={() => void applyReplaceAll()}>{t.replaceAll}</button></div></div></div> : null}
    {tocPatternOpen && session ? <div className={styles.modalBackdrop}><div className={`${styles.saveDialog} ${styles.tocPatternDialog}`} role="dialog" aria-modal="true" aria-label={t.generateToc}><h3>{t.generateToc}</h3><p>{t.tocPatternHelp}</p>{tocPatterns.map((pattern, index) => <label key={index}>{t.tocLevel} {index + 1}<input value={pattern} placeholder={t.tocPatternPlaceholder} onChange={(event) => { setTocPatterns((current) => current.map((value, position) => position === index ? event.target.value : value)); setTocProposal([]); }} /></label>)}<button type="button" disabled={busy} onClick={() => void previewGeneratedToc()}>{t.previewToc}</button>{tocProposal.length ? <div className={styles.tocProposal}>{tocProposal.map((entry, index) => <div key={`${entry.href}-${index}`} style={{ paddingLeft: `${entry.depth * 16}px` }}><strong>{entry.label}</strong><small>{entry.href}</small></div>)}</div> : null}<div className={styles.saveActions}><button type="button" onClick={() => setTocPatternOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={!tocProposal.length || busy} onClick={() => void generateToc()}>{t.applyToc}</button></div></div></div> : null}
    {tocPageOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.generateTocPage}><h3>{t.generateTocPage}</h3><label>{t.label}<input value={tocPageTitle} onChange={(event) => setTocPageTitle(event.target.value)} /></label><div className={styles.saveActions}><button type="button" onClick={() => setTocPageOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={!tocPageTitle.trim() || busy} onClick={() => void createTocPage()}>{t.generateTocPage}</button></div></div></div> : null}
    {tocPickerIndex !== null && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.chooseTocTarget}><h3>{t.chooseTocTarget}</h3><label>{t.chooseChapter}<select value={tocPickerPath} disabled={busy} onChange={(event) => void changeTocPickerPath(event.target.value)}>{session.files.filter((file) => (file.media_type === "application/xhtml+xml" || file.media_type === "text/html") && file.path !== session.nav_path).map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}</select></label><label>{t.chooseAnchor}<select value={tocPickerAnchor} onChange={(event) => setTocPickerAnchor(event.target.value)}><option value="">{t.chapterStart}</option>{tocAnchors.map((anchor) => <option key={anchor.id} value={anchor.id}>{anchor.label} (#{anchor.id})</option>)}</select></label><div className={styles.saveActions}><button type="button" onClick={() => setTocPickerIndex(null)}>{t.cancel}</button><button type="button" className={styles.primary} onClick={() => { updateEntry(tocPickerIndex, { href: tocPickerPath + (tocPickerAnchor ? `#${encodeURIComponent(tocPickerAnchor)}` : "") }); setTocPickerIndex(null); }}>{t.confirmTarget}</button></div></div></div> : null}
    {resourceAction && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={{ add: t.importResource, rename: t.renameResource, replace: t.replaceResource, export: t.exportResource, delete: t.deleteResource }[resourceAction]}>
      <h3>{{ add: t.importResource, rename: t.renameResource, replace: t.replaceResource, export: t.exportResource, delete: t.deleteResource }[resourceAction]}</h3>
      {resourceAction === "add" || resourceAction === "replace" ? <label>{t.localFile}<div className={styles.resourcePick}><input value={resourceInput} onChange={(event) => setResourceInput(event.target.value)} /><button type="button" title={t.chooseFile} aria-label={t.chooseFile} onClick={() => void chooseResourceFile()}><FolderOpen size={17} /></button></div></label> : null}
      {resourceAction === "add" || resourceAction === "rename" || resourceAction === "export" ? <label>{resourceAction === "export" ? t.outputPath : t.archivePath}<div className={styles.resourcePick}><input value={resourceTarget} onChange={(event) => setResourceTarget(event.target.value)} />{resourceAction === "export" ? <button type="button" title={t.chooseFile} aria-label={t.chooseFile} onClick={() => void chooseResourceOutput()}><FolderOpen size={17} /></button> : null}</div></label> : null}
      {resourceAction === "add" ? <label className={styles.checkbox}><input type="checkbox" checked={resourceInSpine} onChange={(event) => setResourceInSpine(event.target.checked)} />{t.addToSpine}</label> : null}
      {resourceAction === "delete" ? <><p>{t.confirmDeleteResource}</p>{resourceReferences.length ? <div className={styles.resourceReferences}><strong>{t.resourceReferences}</strong>{resourceReferences.map((reference) => <div key={reference}>{reference}</div>)}</div> : null}</> : null}
      <div className={styles.saveActions}><button type="button" onClick={() => setResourceAction(null)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy || ((resourceAction === "add" || resourceAction === "replace") && !resourceInput.trim()) || ((resourceAction === "add" || resourceAction === "rename" || resourceAction === "export") && !resourceTarget.trim()) || (resourceAction === "delete" && resourceReferences.length > 0)} onClick={() => void applyResourceAction()}>{t.applyResource}</button></div>
    </div></div> : null}
    {saveOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.save}><h3>{t.save}</h3><label className={styles.checkbox}><input type="checkbox" checked={overwriteSource} onChange={(event) => setOverwriteSource(event.target.checked)} />{t.overwrite}</label>{!overwriteSource ? <label>{t.outputPath}<input value={outputPath} onChange={(event) => setOutputPath(event.target.value)} /></label> : <p>{session.input_path}</p>}<div className={styles.saveActions}><button type="button" onClick={() => setSaveOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy} onClick={() => void saveBook()}><Save size={16} />{overwriteSource ? t.overwrite : t.saveAs}</button></div></div></div> : null}
    {closeOpen ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.closeTitle}><h3>{t.closeTitle}</h3><p>{t.dirtyClose}</p><div className={styles.saveActions}><button type="button" onClick={() => setCloseOpen(false)}>{t.cancel}</button><button type="button" onClick={() => closeEditor()}>{t.discardClose}</button><button type="button" onClick={() => { setCloseOpen(false); void stageSave(true); }}>{t.stageSave}</button><button type="button" onClick={() => { setCloseOpen(false); setCloseAfterSave(true); setOverwriteSource(false); setSaveOpen(true); }}>{t.saveAs}</button><button type="button" className={styles.primary} onClick={() => { setCloseOpen(false); setCloseAfterSave(true); setOverwriteSource(true); setSaveOpen(true); }}>{t.overwrite}</button></div></div></div> : null}
  </div>;
}
