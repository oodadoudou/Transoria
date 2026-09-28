import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import type { EditorView } from "@codemirror/view";
import { css } from "@codemirror/lang-css";
import { xml } from "@codemirror/lang-xml";
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, BookOpen, Check, ChevronDown, ChevronRight, CornerUpLeft, CornerUpRight, FileCode2, FileText, FolderOpen, ListTree, Save, Search, ShieldCheck, X } from "lucide-react";

import { dialogsBridge, epubContentBridge, type EpubContentFile, type EpubContentMatch, type EpubContentSession, type EpubTocEntry } from "@/bridge";
import { useMessages } from "@/locales";
import { useSettingsStore } from "@/store/useSettingsStore";
import { clearEditorDraft, readEditorDraft, writeEditorDraft, type EpubEditorDraft } from "./epubEditorDraft";
import styles from "./EpubContentPage.module.css";

type SideView = "files" | "toc" | "spine" | "book";
type Scope = "current" | "text" | "styles" | "all";
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
  const previewFrame = useRef<HTMLIFrameElement | null>(null);
  const bookFrame = useRef<HTMLIFrameElement | null>(null);
  const sourceEditor = useRef<EditorView | null>(null);
  const [inputPath, setInputPath] = useState(initialPath);
  const [recent, setRecent] = useState(readRecent);
  const [restoring, setRestoring] = useState(Boolean(savedDraft.current && !initialPath));
  const [session, setSession] = useState<EpubContentSession | null>(null);
  const [selectedPath, setSelectedPath] = useState("");
  const [previewPath, setPreviewPath] = useState("");
  const [loadedContent, setLoadedContent] = useState("");
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState("");
  const [tocDraft, setTocDraft] = useState<EpubTocEntry[]>([]);
  const [sideView, setSideView] = useState<SideView>("files");
  const [paneView, setPaneView] = useState<"source" | "preview">("source");
  const [bookIndex, setBookIndex] = useState(0);
  const [bookHtml, setBookHtml] = useState("");
  const [bookError, setBookError] = useState("");
  const [bookLoading, setBookLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [sidebarWidth, setSidebarWidth] = useState(28);
  const [sourceWidth, setSourceWidth] = useState(50);
  const [resizing, setResizing] = useState(false);
  const [collapsedFolders, setCollapsedFolders] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [replacement, setReplacement] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [scope, setScope] = useState<Scope>("current");
  const [matches, setMatches] = useState<EpubContentMatch[]>([]);
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

  const editableFiles = useMemo(() => session?.files.filter((file) => file.editable) ?? [], [session]);
  const nodes = useMemo(() => fileTree(editableFiles), [editableFiles]);
  const sourceDirty = content !== loadedContent;
  const tocDirty = Boolean(session && JSON.stringify(tocDraft) !== JSON.stringify(session.toc));
  const dirty = Boolean(session?.dirty || sourceDirty || tocDirty);
  const currentFile = session?.files.find((file) => file.path === selectedPath);
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
      caseSensitive,
      sidebarWidth,
      sourceWidth,
    };
    currentDraft.current = draft;
    const timer = window.setTimeout(() => { writeEditorDraft(draft); }, 250);
    return () => window.clearTimeout(timer);
  }, [session?.session_id, session?.input_path, session?.dirty, selectedPath, content, loadedContent, tocDraft, sideView, paneView, bookIndex, searchOpen, query, replacement, scope, caseSensitive, sidebarWidth, sourceWidth]);

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

  const loadResource = useCallback(async (sid: string, path: string) => {
    const next = await epubContentBridge.read(sid, path);
    setSelectedPath(path);
    setLoadedContent(next.content);
    setContent(next.content);
    const file = session?.files.find((item) => item.path === path);
    if ((file?.media_type === "application/xhtml+xml" || file?.media_type === "text/html") && path !== session?.nav_path) {
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
  }, [session?.files]);

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
    frame.srcdoc = preview;
    const timer = window.setTimeout(() => { if (previewFrame.current === frame) frame.srcdoc = preview; }, 120);
    return () => window.clearTimeout(timer);
  }, [preview, previewPath, previewError, sideView, session?.session_id]);

  useEffect(() => {
    const frame = bookFrame.current;
    if (sideView !== "book" || !frame) return;
    frame.srcdoc = bookHtml;
    const timer = window.setTimeout(() => { if (bookFrame.current === frame) frame.srcdoc = bookHtml; }, 120);
    return () => window.clearTimeout(timer);
  }, [bookHtml, bookIndex, bookLoading, bookError, sideView, session?.session_id]);

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
      if (!session && savedDraft.current && (savedDraft.current.dirty || savedDraft.current.sourceDraft !== null) && !window.confirm(t.sessionExpired)) return;
      const next = await epubContentBridge.open(path);
      if (session) await epubContentBridge.close(session.session_id);
      savedDraft.current = null;
      setSession(next);
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
      setOutputPath(editedPath(next.input_path));
      setTocDraft(Array.isArray(draft.tocDraft) ? draft.tocDraft : next.toc);
      setSideView(["files", "toc", "spine", "book"].includes(draft.sideView) ? draft.sideView : "files");
      setBookIndex(typeof draft.bookIndex === "number" && draft.bookIndex >= 0 && draft.bookIndex < next.spine.length ? draft.bookIndex : 0);
      setPaneView(draft.paneView === "preview" ? "preview" : "source");
      setSearchOpen(Boolean(draft.searchOpen));
      setQuery(draft.query ?? "");
      setReplacement(draft.replacement ?? "");
      setScope(["current", "text", "styles", "all"].includes(draft.scope) ? draft.scope : "current");
      setCaseSensitive(Boolean(draft.caseSensitive));
      if (typeof draft.sidebarWidth === "number" && draft.sidebarWidth >= 10 && draft.sidebarWidth <= 70) setSidebarWidth(draft.sidebarWidth);
      if (typeof draft.sourceWidth === "number" && draft.sourceWidth >= 10 && draft.sourceWidth <= 90) setSourceWidth(draft.sourceWidth);
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
    if (!session || path === selectedPath) return;
    await run(async () => {
      await commitSource();
      await loadResource(session.session_id, path);
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

  const moveSpine = async (index: number, shift: number) => {
    if (!session || index + shift < 0 || index + shift >= session.spine.length) return;
    await run(async () => {
      await commitSource();
      const order = session.spine.slice();
      [order[index], order[index + shift]] = [order[index + shift], order[index]];
      setSession(await epubContentBridge.reorderSpine(session.session_id, order));
    });
  };

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
    if (scope === "current") return selectedPath ? [selectedPath] : [];
    if (scope === "text") return editableFiles.filter((file) => ["application/xhtml+xml", "text/html", "text/plain"].includes(file.media_type)).map((file) => file.path);
    if (scope === "styles") return editableFiles.filter((file) => file.media_type === "text/css").map((file) => file.path);
    return editableFiles.map((file) => file.path);
  };

  const search = async () => {
    if (!session || !query) return;
    await run(async () => {
      await commitSource();
      const found = await epubContentBridge.search(session.session_id, query, scopePaths(), caseSensitive);
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
    if (!session || !query || !matches.length || matches.length >= 5000 || !window.confirm(`${t.confirmReplace} (${matches.length} ${t.matches})`)) return;
    await run(async () => {
      await commitSource();
      const result = await epubContentBridge.replace(session.session_id, query, replacement, scopePaths(), caseSensitive, matches.length);
      setSession(result);
      if (selectedPath) await loadResource(session.session_id, selectedPath);
      setFeedback(`${result.replacements} ${t.matches}`);
      setMatches([]);
      setMatchIndex(-1);
    });
  };

  const replaceOne = async (match: EpubContentMatch) => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      const next = await epubContentBridge.replaceMatch(session.session_id, query, replacement, match, caseSensitive);
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
      setFeedback(`${t.saved}: ${result.output_path}`);
      setFeedbackWarning(false);
      if (closeAfterSave) closeEditor();
    });
  };

  const validateBook = async () => {
    if (!session) return;
    await run(async () => {
      await commitSource();
      if (tocDirty) {
        const next = await epubContentBridge.setToc(session.session_id, tocDraft);
        setSession(next);
        setTocDraft(next.toc);
      }
      const result = await epubContentBridge.validate(session.session_id);
      const check = result.structure_check;
      const warnings = [...(check.warnings ?? []), ...(check.missing_entries ?? [])];
      setFeedbackWarning(check.status !== "ok");
      setFeedback(`${check.status === "ok" ? t.validationOk : t.validationWarning}${warnings.length ? `: ${warnings.slice(0, 3).join("; ")}` : ""}`);
    });
  };

  const closeEditor = () => {
    clearEditorDraft();
    currentDraft.current = null;
    if (session) void epubContentBridge.close(session.session_id).catch(() => undefined);
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
    return <div key={node.file.path} className={`${styles.fileRow} ${selectedPath === node.file.path ? styles.active : ""}`} style={{ paddingLeft: `${depth * 9 + 4}px` }}><button type="button" onClick={() => void selectResource(node.file.path)} title={node.file.path}>{node.name}</button></div>;
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

  if (!session) return <div className={styles.importPage}>
    <div className={styles.importHeader}>
      <div><span className={styles.importKicker}>EPUB</span><h2>{t.importTitle}</h2><p>{t.importSubtitle}</p></div>
      <button type="button" title={t.close} aria-label={t.close} onClick={closeEditor}><X size={18} /></button>
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
        <button type="button" title={t.validate} aria-label={t.validate} disabled={!session || busy} onClick={() => void validateBook()}><ShieldCheck size={18} /></button>
        <button type="button" className={styles.primary} disabled={!session || busy} onClick={() => { setCloseAfterSave(false); setSaveOpen(true); }}><Save size={17} />{t.save}{dirty ? " *" : ""}</button>
        <button type="button" title={t.close} aria-label={t.close} onClick={requestClose}><X size={18} /></button>
      </div>
    </div>
    {error ? <div className={styles.error} role="alert">{t.error}: {sessionExpired ? t.sessionExpired : error}{sessionExpired && session ? <button type="button" onClick={() => void openBook(session.input_path)}>{t.reopen}</button> : null}</div> : null}
    {feedback ? <div className={feedbackWarning ? styles.warning : styles.feedback} role="status">{feedback}</div> : null}
    {searchOpen && session ? <div className={styles.searchPanel}>
      <div className={styles.searchControls}>
        <label>{t.query}<input value={query} onChange={(event) => { setQuery(event.target.value); setMatches([]); setMatchIndex(-1); }} onKeyDown={(event) => { if (event.key === "Enter") { if (matches.length) void navigateMatch(event.shiftKey ? -1 : 1); else void search(); } }} /></label>
        <label>{t.replacement}<input value={replacement} onChange={(event) => setReplacement(event.target.value)} /></label>
        <label><span>{t.files}</span><select value={scope} onChange={(event) => { setScope(event.target.value as Scope); setMatches([]); setMatchIndex(-1); }}><option value="current">{t.current}</option><option value="text">{t.textFiles}</option><option value="styles">{t.styleFiles}</option><option value="all">{t.all}</option></select></label>
        <label className={styles.checkbox}><input type="checkbox" checked={caseSensitive} onChange={(event) => { setCaseSensitive(event.target.checked); setMatches([]); setMatchIndex(-1); }} />{t.caseSensitive}</label>
        <button type="button" disabled={!query || busy || !scopePaths().length} onClick={() => void search()}><Search size={16} />{t.find}</button>
        <div className={styles.matchNavigation} role="status"><span>{matches.length ? `${matchIndex + 1} / ${matches.length}${matches.length >= 5000 ? "+" : ""}` : t.noMatches}</span><button type="button" title={t.previousMatch} aria-label={t.previousMatch} disabled={!matches.length || busy} onClick={() => void navigateMatch(-1)}><ArrowUp size={16} /></button><button type="button" title={t.nextMatch} aria-label={t.nextMatch} disabled={!matches.length || busy} onClick={() => void navigateMatch(1)}><ArrowDown size={16} /></button><button type="button" disabled={matchIndex < 0 || busy} onClick={() => void replaceOne(matches[matchIndex])}>{t.replaceOne}</button></div>
        <button type="button" disabled={!matches.length || matches.length >= 5000 || busy} title={matches.length >= 5000 ? t.narrowSearch : undefined} onClick={() => void replaceAll()}>{t.replaceAll}</button>
      </div>
    </div> : null}
    {!session ? <div className={styles.empty}>{t.noBook}</div> : <div className={styles.main} data-resizing={resizing} style={{ "--sidebar-width": `${sidebarWidth}%` } as CSSProperties}>
      <aside className={styles.sidebar}>
        <div className={styles.tabs}><button type="button" aria-selected={sideView === "files"} onClick={() => setSideView("files")}><FileCode2 size={16} />{t.files}</button><button type="button" aria-selected={sideView === "toc"} onClick={() => setSideView("toc")}><ListTree size={16} />{t.toc}</button><button type="button" aria-selected={sideView === "spine"} onClick={() => setSideView("spine")}>{t.spine}</button><button type="button" aria-selected={sideView === "book"} onClick={() => setSideView("book")}><BookOpen size={16} />{t.bookPreview}</button></div>
        <div className={styles.sideBody}>
          {sideView === "files" ? renderTree(nodes) : null}
          {sideView === "spine" ? session.spine.map((path, index) => <div key={path} className={styles.orderRow}><button type="button" onClick={() => void selectResource(path)}>{index + 1}. {path}</button><button type="button" title={t.up} aria-label={`${t.up}: ${path}`} disabled={index === 0} onClick={() => void moveSpine(index, -1)}><ArrowUp size={16} /></button><button type="button" title={t.down} aria-label={`${t.down}: ${path}`} disabled={index === session.spine.length - 1} onClick={() => void moveSpine(index, 1)}><ArrowDown size={16} /></button></div>) : null}
          {sideView === "book" ? session.spine.map((path, index) => <button type="button" key={`${path}-${index}`} className={styles.bookChapter} aria-current={bookIndex === index ? "page" : undefined} title={path} onClick={() => setBookIndex(index)}><span>{index + 1}</span><strong>{session.toc.find((entry) => entry.href.split("#")[0] === path)?.label ?? path.split("/").at(-1)}</strong></button>) : null}
          {sideView === "toc" ? <><div className={styles.tocActions}><button type="button" disabled={!session.nav_path && !session.ncx_path} onClick={() => setTocDraft([...tocDraft, { label: "", href: selectedPath || session.spine[0] || "", depth: 0 }])}>{t.addEntry}</button><button type="button" disabled={!tocDirty || busy} onClick={() => void applyToc()}><Check size={16} />{t.applyToc}</button></div>{tocDraft.map((entry, index) => <div key={index} className={styles.tocRow} style={{ paddingLeft: `${Math.min(entry.depth, 8) * 12 + 8}px` }}><input aria-label={`${t.label} ${index + 1}`} value={entry.label} placeholder={t.label} onChange={(event) => updateEntry(index, { label: event.target.value })} /><input aria-label={`${t.target} ${index + 1}`} value={entry.href} placeholder={t.target} onChange={(event) => updateEntry(index, { href: event.target.value })} /><div className={styles.tocButtons}><button type="button" title={t.up} disabled={siblingIndex(tocDraft, index, -1) < 0} onClick={() => moveEntry(index, -1)}><ArrowUp size={15} /></button><button type="button" title={t.down} disabled={siblingIndex(tocDraft, index, 1) < 0} onClick={() => moveEntry(index, 1)}><ArrowDown size={15} /></button><button type="button" title={t.outdent} disabled={entry.depth === 0} onClick={() => outdentEntry(index)}><ArrowLeft size={15} /></button><button type="button" title={t.indent} disabled={index === 0 || entry.depth >= tocDraft[index - 1].depth + 1 || tocDraft.slice(index, subtreeEnd(tocDraft, index)).some((child) => child.depth >= 8)} onClick={() => shiftDepth(index, 1)}><ArrowRight size={15} /></button><button type="button" title={t.removeEntry} onClick={() => removeEntry(index)}><X size={15} /></button></div></div>)}</> : null}
        </div>
      </aside>
      {separator("sidebar", sidebarWidth)}
      <div className={styles.editorArea}>
        {sideView === "book" ? <div className={styles.bookReader}><div className={styles.readerToolbar}><strong>{t.bookPreview}</strong><span>{bookIndex + 1} / {session.spine.length}</span><div><button type="button" title={t.previousChapter} aria-label={t.previousChapter} disabled={bookIndex === 0} onClick={() => setBookIndex((index) => index - 1)}><ArrowLeft size={18} /></button><button type="button" title={t.nextChapter} aria-label={t.nextChapter} disabled={bookIndex >= session.spine.length - 1} onClick={() => setBookIndex((index) => index + 1)}><ArrowRight size={18} /></button></div></div>{bookError ? <div className={styles.noPreview}>{t.previewInvalid}</div> : bookLoading ? <div className={styles.noPreview}>{t.loading}</div> : <div className={styles.bookPage}><iframe key={session.spine[bookIndex]} ref={bookFrame} title={t.bookPreview} sandbox="" /></div>}</div> : <>
        <div className={styles.fileHeading}><strong title={selectedPath}>{selectedPath}</strong><div className={styles.paneSwitch}><button type="button" aria-selected={paneView === "source"} onClick={() => setPaneView("source")}>{t.source}</button><button type="button" aria-selected={paneView === "preview"} onClick={() => setPaneView("preview")}>{t.preview}</button></div>{busy ? <span>{t.loading}</span> : null}</div>
        <div className={styles.split} data-mode={paneView} style={{ "--source-width": `${sourceWidth}%` } as CSSProperties}>
          <section className={styles.sourcePane}><h3>{t.source}</h3><CodeMirror value={content} editable={!busy} onCreateEditor={(view) => { sourceEditor.current = view; }} onChange={(value) => { setContent(value); setMatches([]); setMatchIndex(-1); }} extensions={currentFile?.media_type === "text/css" ? [css()] : currentFile?.media_type === "text/plain" || currentFile?.media_type?.includes("javascript") ? [] : [xml()]} theme={colorTheme} height="100%" basicSetup={{ lineNumbers: true, foldGutter: true }} /></section>
          {separator("source", sourceWidth)}
          <section className={styles.previewPane}><h3>{t.preview}</h3>{previewError ? <div className={styles.noPreview}>{t.previewInvalid}</div> : ((isHtml && selectedPath !== session.nav_path) || currentFile?.media_type === "text/css") && previewPath ? <iframe ref={previewFrame} title={t.preview} sandbox="" /> : <div className={styles.noPreview}>{t.noPreview}</div>}</section>
        </div>
        </>}
      </div>
    </div>}
    {saveOpen && session ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.save}><h3>{t.save}</h3><label className={styles.checkbox}><input type="checkbox" checked={overwriteSource} onChange={(event) => setOverwriteSource(event.target.checked)} />{t.overwrite}</label>{!overwriteSource ? <label>{t.outputPath}<input value={outputPath} onChange={(event) => setOutputPath(event.target.value)} /></label> : <p>{session.input_path}</p>}<div className={styles.saveActions}><button type="button" onClick={() => setSaveOpen(false)}>{t.cancel}</button><button type="button" className={styles.primary} disabled={busy} onClick={() => void saveBook()}><Save size={16} />{overwriteSource ? t.overwrite : t.saveAs}</button></div></div></div> : null}
    {closeOpen ? <div className={styles.modalBackdrop}><div className={styles.saveDialog} role="dialog" aria-modal="true" aria-label={t.closeTitle}><h3>{t.closeTitle}</h3><p>{t.dirtyClose}</p><div className={styles.saveActions}><button type="button" onClick={() => setCloseOpen(false)}>{t.cancel}</button><button type="button" onClick={closeEditor}>{t.discardClose}</button><button type="button" onClick={() => { setCloseOpen(false); setCloseAfterSave(true); setOverwriteSource(false); setSaveOpen(true); }}>{t.saveAs}</button><button type="button" className={styles.primary} onClick={() => { setCloseOpen(false); setCloseAfterSave(true); setOverwriteSource(true); setSaveOpen(true); }}>{t.overwrite}</button></div></div></div> : null}
  </div>;
}
