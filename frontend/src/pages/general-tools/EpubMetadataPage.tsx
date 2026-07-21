import { type ReactNode, useState } from "react";

import {
  BridgeError,
  dialogsBridge,
  epubMetadataBridge,
  type EpubMetadataApplyResult,
  type EpubMetadataInfo,
} from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { CompactPath } from "@/components/CompactPath";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useMessages } from "@/locales";
import { useLocalState } from "@/utils/localState";
import styles from "./EpubMetadataPage.module.css";

const INPUT_LOCAL_KEY = "transoria.generalTools.epubMetadata.inputPath";
const OUTPUT_FOLDER_LOCAL_KEY = "transoria.generalTools.epubMetadata.outputFolder";
const COVER_LOCAL_KEY = "transoria.generalTools.epubMetadata.coverPath";

function splitPath(path: string): { dir: string; name: string } {
  const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (index < 0) return { dir: "", name: path };
  return { dir: path.slice(0, index + 1), name: path.slice(index + 1) };
}

function defaultOutputFolder(inputPath: string): string {
  return outputFolder(inputPath.trim());
}

function filenameFromTitle(title: string, inputPath: string): string {
  const fallback = splitPath(inputPath.trim()).name.replace(/\.epub$/i, "");
  const stem = (title.trim() || fallback || "metadata")
    .replace(/[\\/:*?"<>|\x00-\x1f]/g, "_")
    .replace(/\s+/g, " ")
    .trim();
  return `${stem || "metadata"}.epub`;
}

function normalizeOutputFilename(
  filename: string,
  title: string,
  inputPath: string,
): string {
  const fallback = filenameFromTitle(title, inputPath);
  const stem = (filename.trim() || fallback)
    .replace(/[\\/:*?"<>|\x00-\x1f]/g, "_")
    .replace(/\s+/g, " ")
    .trim();
  const name = stem && stem !== ".epub" ? stem : fallback;
  return name.toLowerCase().endsWith(".epub") ? name : `${name}.epub`;
}

function outputFolder(path: string): string {
  const { dir } = splitPath(path);
  return dir.replace(/[\\/]$/, "");
}

function normalizePath(path: string): string {
  return path.trim().replace(/\\/g, "/").replace(/\/+$/, "");
}

function isSamePath(left: string, right: string): boolean {
  const normalizedLeft = normalizePath(left);
  const normalizedRight = normalizePath(right);
  return Boolean(normalizedLeft && normalizedLeft === normalizedRight);
}

function joinPath(dir: string, name: string): string {
  const folder = dir.trim();
  if (!folder) return name;
  const separator = folder.includes("\\") && !folder.includes("/") ? "\\" : "/";
  return `${folder.replace(/[\\/]+$/, "")}${separator}${name}`;
}

export function EpubMetadataPage({ embedded = false }: { embedded?: boolean } = {}) {
  const messages = useMessages();
  const text = messages.epubMetadataTool;
  const [inputPath, setInputPath] = useLocalState(INPUT_LOCAL_KEY, "");
  const [outputFolderPath, setOutputFolderPath] = useLocalState(
    OUTPUT_FOLDER_LOCAL_KEY,
    "",
  );
  const [coverPath, setCoverPath] = useLocalState(COVER_LOCAL_KEY, "");
  const [coverPreviewUrl, setCoverPreviewUrl] = useState("");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [syncOutputFilename, setSyncOutputFilename] = useState(true);
  const [customOutputFilename, setCustomOutputFilename] = useState("");
  const [compressOutput, setCompressOutput] = useState(false);
  const [info, setInfo] = useState<EpubMetadataInfo | null>(null);
  const [result, setResult] = useState<EpubMetadataApplyResult | null>(null);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [confirmOverwriteOpen, setConfirmOverwriteOpen] = useState(false);

  const resolvedOutputFolder =
    outputFolderPath.trim() || defaultOutputFolder(inputPath);
  const resolvedOutputFilename = syncOutputFilename
    ? filenameFromTitle(title, inputPath)
    : normalizeOutputFilename(customOutputFilename, title, inputPath);
  const resolvedOutputPath = joinPath(
    resolvedOutputFolder,
    resolvedOutputFilename,
  );

  const loadMetadata = async (path: string, resetOutputPath = false) => {
    setActionError(null);
    setFeedback(null);
    setResult(null);
    setLoading(true);
    try {
      const next = await epubMetadataBridge.read(path);
      setInputPath(path);
      setInfo(next);
      setTitle(next.title);
      setAuthor(next.authors.join(", "));
      setCoverPath("");
      setCoverPreviewUrl(next.cover_preview_data_url);
      setSyncOutputFilename(true);
      setCustomOutputFilename(filenameFromTitle(next.title, path));
      if (resetOutputPath || !outputFolderPath.trim()) {
        setOutputFolderPath(defaultOutputFolder(path));
      }
      setEditorOpen(true);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    } finally {
      setLoading(false);
    }
  };

  const handleChooseInput = async () => {
    try {
      const selected = await dialogsBridge.chooseEpubFile(inputPath || undefined);
      if (selected.path) await loadMetadata(selected.path, true);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleChooseOutputFolder = async () => {
    try {
      const selected = await dialogsBridge.chooseOutputDirectory(
        resolvedOutputFolder || undefined,
      );
      if (selected.path) setOutputFolderPath(selected.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleChooseCover = async () => {
    try {
      const selected = await dialogsBridge.chooseImageFile(coverPath || undefined);
      if (selected.path) {
        setCoverPath(selected.path);
        const preview = await epubMetadataBridge.coverPreview(selected.path);
        setCoverPreviewUrl(preview.data_url);
      }
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleOpenEditor = async () => {
    if (!inputPath.trim()) return;
    await loadMetadata(inputPath.trim(), false);
  };

  const applyMetadata = async (overwrite: boolean, probe = false) => {
    setConfirmOverwriteOpen(false);
    setActionError(null);
    setFeedback(null);
    setLoading(true);
    try {
      const next = await epubMetadataBridge.apply(
        inputPath,
        resolvedOutputPath,
        title,
        author,
        coverPath,
        overwrite,
        compressOutput,
      );
      setResult(next);
      setFeedback(next.compressed ? text.savedCompressed : text.saved);
      setEditorOpen(false);
      setInfo(await epubMetadataBridge.read(next.output_path));
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        if (
          probe &&
          !overwrite &&
          /confirm overwrite first/i.test(error.message)
        ) {
          setConfirmOverwriteOpen(true);
          return;
        }
        setActionError(error);
      } else {
        throw error;
      }
    } finally {
      setLoading(false);
    }
  };

  const handleApplyRequest = async () => {
    if (!inputPath.trim() || !resolvedOutputPath.trim()) return;
    if (isSamePath(inputPath, resolvedOutputPath)) {
      setConfirmOverwriteOpen(true);
      return;
    }
    await applyMetadata(false, true);
  };

  const handleRevealOutput = async () => {
    const path = result?.output_path || resolvedOutputPath;
    if (!path.trim()) return;
    try {
      await dialogsBridge.revealFile(path);
      setFeedback(null);
    } catch {
      const folder = outputFolder(path) || resolvedOutputFolder;
      if (!folder) return;
      await dialogsBridge.openDirectory(folder);
    }
  };
  useEscapeKey(
    () => setConfirmOverwriteOpen(false),
    confirmOverwriteOpen && !loading,
  );
  useEscapeKey(
    () => setEditorOpen(false),
    editorOpen && !confirmOverwriteOpen && !loading,
  );

  return (
    <>
      <Panel
        title={embedded ? undefined : text.title}
        subtitle={embedded ? undefined : text.sub}
      >
        <div className={styles.pathGrid}>
          <label className={styles.field}>
            <span>{text.inputFile}</span>
            <div className={styles.fileRow}>
              <input
                value={inputPath}
                onChange={(event) => setInputPath(event.target.value)}
                placeholder={text.inputPlaceholder}
              />
              <Pill variant="ghost" onClick={handleChooseInput} disabled={loading}>
                {text.chooseEpub}
              </Pill>
            </div>
          </label>
        </div>

        <div className={styles.actionRow}>
          <Pill onClick={handleOpenEditor} disabled={!inputPath || loading}>
            {text.openEditor}
          </Pill>
          <Pill
            variant="ghost"
            onClick={handleRevealOutput}
            disabled={!result}
          >
            {text.openOutput}
          </Pill>
          {feedback ? <span className={styles.feedback}>{feedback}</span> : null}
          {actionError ? (
            <span className={styles.actionError}>
              <code>{actionError.code}</code> {actionError.message}
            </span>
          ) : null}
        </div>
      </Panel>

      <Panel label={text.currentLabel}>
        {info ? (
          <div className={styles.summaryGrid}>
            <Stat label={text.currentTitle} value={info.title || "-"} />
            <Stat
              label={text.currentAuthors}
              value={info.authors.length ? info.authors.join(", ") : "-"}
            />
            <Stat
              label={text.currentCover}
              value={info.has_cover ? text.coverPresent : text.coverMissing}
            />
            <Stat
              label={text.structure}
              value={outcomeLabel(result?.outcome ?? info.structure_check.status, text)}
            />
            <Stat
              label={text.currentOutput}
              value={
                <CompactPath
                  value={result?.output_path || resolvedOutputPath}
                  copyLabel={messages.common.copyPath}
                  emptyLabel="-"
                />
              }
            />
          </div>
        ) : (
          <div className={styles.empty}>{text.noMetadata}</div>
        )}
      </Panel>

      {editorOpen ? (
        <div className={styles.overlay} role="presentation">
          <div
            className={styles.dialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="epub-metadata-dialog-title"
          >
            <div className={styles.dialogHeader}>
              <div>
                <h2 id="epub-metadata-dialog-title">{text.dialogTitle}</h2>
                <p>{text.dialogSub}</p>
              </div>
              <button
                type="button"
                className={styles.closeButton}
                onClick={() => setEditorOpen(false)}
                aria-label={text.cancel}
              >
                x
              </button>
            </div>

            <div className={styles.dialogBody}>
              <div className={styles.coverPanel}>
                <div className={styles.coverBox}>
                  {coverPreviewUrl ? (
                    <img src={coverPreviewUrl} alt={text.currentCover} />
                  ) : (
                    <span>{info?.has_cover ? text.coverPresent : text.coverMissing}</span>
                  )}
                  {info?.cover_archive_path ? (
                    <CompactPath
                      value={info.cover_archive_path}
                      copyLabel={messages.common.copyPath}
                    />
                  ) : null}
                </div>
                <label className={styles.field}>
                  <span>{text.coverFile}</span>
                  <div className={styles.fileRow}>
                    <input
                      value={coverPath}
                      onChange={(event) => {
                        setCoverPath(event.target.value);
                        if (!event.target.value.trim()) {
                          setCoverPreviewUrl(info?.cover_preview_data_url ?? "");
                        }
                      }}
                      placeholder={text.coverPlaceholder}
                    />
                    <Pill variant="ghost" onClick={handleChooseCover}>
                      {text.chooseCover}
                    </Pill>
                  </div>
                </label>
              </div>

              <div className={styles.dialogFields}>
                <label className={styles.field}>
                  <span>{text.titleLabel}</span>
                  <input
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                </label>
                <label className={styles.field}>
                  <span>{text.authorLabel}</span>
                  <input
                    value={author}
                    onChange={(event) => setAuthor(event.target.value)}
                  />
                </label>
                <label className={styles.field}>
                  <span>{text.outputFolder}</span>
                  <div className={styles.fileRow}>
                    <input
                      value={outputFolderPath}
                      onChange={(event) => setOutputFolderPath(event.target.value)}
                      placeholder={defaultOutputFolder(inputPath)}
                    />
                    <Pill variant="ghost" onClick={handleChooseOutputFolder}>
                      {text.chooseOutputFolder}
                    </Pill>
                  </div>
                </label>
                <label className={styles.checkboxRow}>
                  <input
                    type="checkbox"
                    checked={syncOutputFilename}
                    onChange={(event) => {
                      const checked = event.target.checked;
                      setSyncOutputFilename(checked);
                      if (!checked && !customOutputFilename.trim()) {
                        setCustomOutputFilename(
                          filenameFromTitle(title, inputPath),
                        );
                      }
                    }}
                  />
                  <span>{text.syncFilenameWithTitle}</span>
                </label>
                {!syncOutputFilename ? (
                  <label className={styles.field}>
                    <span>{text.outputFilename}</span>
                    <input
                      value={customOutputFilename}
                      onChange={(event) =>
                        setCustomOutputFilename(event.target.value)
                      }
                      placeholder={filenameFromTitle(title, inputPath)}
                    />
                  </label>
                ) : null}
                <div className={styles.generatedOutput}>
                  <span>{text.generatedOutput}</span>
                  <code>{resolvedOutputPath}</code>
                </div>
                <label className={styles.checkboxRow}>
                  <input
                    type="checkbox"
                    checked={compressOutput}
                    onChange={(event) => setCompressOutput(event.target.checked)}
                  />
                  <span>{text.compressOutput}</span>
                </label>
              </div>
            </div>

            <div className={styles.dialogFooter}>
              <Pill variant="ghost" onClick={() => setEditorOpen(false)}>
                {text.cancel}
              </Pill>
              <Pill
                onClick={handleApplyRequest}
                disabled={!inputPath || !resolvedOutputPath || loading}
              >
                {text.ok}
              </Pill>
            </div>
          </div>
        </div>
      ) : null}

      {confirmOverwriteOpen ? (
        <div className={styles.overlay} role="presentation">
          <div
            className={styles.confirmDialog}
            role="dialog"
            aria-modal="true"
            aria-labelledby="epub-overwrite-dialog-title"
          >
            <h2 id="epub-overwrite-dialog-title">{text.overwriteTitle}</h2>
            <p>{text.overwriteBody}</p>
            <code>{resolvedOutputPath}</code>
            <div className={styles.dialogFooter}>
              <Pill
                variant="ghost"
                onClick={() => setConfirmOverwriteOpen(false)}
                disabled={loading}
              >
                {text.overwriteNo}
              </Pill>
              <Pill onClick={() => void applyMetadata(true)} disabled={loading}>
                {text.overwriteYes}
              </Pill>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function outcomeLabel(
  status: string,
  text: { success: string; successWithWarnings: string; failed: string },
): string {
  if (status === "ok" || status === "success") return text.success;
  if (status === "warning" || status === "success_with_warnings") return text.successWithWarnings;
  return text.failed;
}

interface StatProps {
  label: string;
  value: ReactNode;
}

function Stat({ label, value }: StatProps) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue}>{value}</span>
    </div>
  );
}
