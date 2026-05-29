import { useState } from "react";

import {
  BridgeError,
  dialogsBridge,
  epubMetadataBridge,
  type EpubMetadataApplyResult,
  type EpubMetadataInfo,
} from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { useMessages } from "@/locales";
import { useLocalState } from "@/utils/localState";
import styles from "./EpubMetadataPage.module.css";

const INPUT_LOCAL_KEY = "transoria.generalTools.epubMetadata.inputPath";
const OUTPUT_LOCAL_KEY = "transoria.generalTools.epubMetadata.outputPath";
const COVER_LOCAL_KEY = "transoria.generalTools.epubMetadata.coverPath";

function splitPath(path: string): { dir: string; name: string } {
  const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (index < 0) return { dir: "", name: path };
  return { dir: path.slice(0, index + 1), name: path.slice(index + 1) };
}

function defaultOutputPath(inputPath: string): string {
  return inputPath.trim();
}

function defaultSaveName(inputPath: string): string {
  const { name } = splitPath(inputPath.trim());
  return name || "metadata.epub";
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

export function EpubMetadataPage() {
  const text = useMessages().epubMetadataTool;
  const [inputPath, setInputPath] = useLocalState(INPUT_LOCAL_KEY, "");
  const [outputPath, setOutputPath] = useLocalState(OUTPUT_LOCAL_KEY, "");
  const [coverPath, setCoverPath] = useLocalState(COVER_LOCAL_KEY, "");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [compressOutput, setCompressOutput] = useState(false);
  const [info, setInfo] = useState<EpubMetadataInfo | null>(null);
  const [result, setResult] = useState<EpubMetadataApplyResult | null>(null);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [confirmOverwriteOpen, setConfirmOverwriteOpen] = useState(false);

  const resolvedOutputPath = outputPath.trim() || defaultOutputPath(inputPath);

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
      if (resetOutputPath || !outputPath.trim()) {
        setOutputPath(defaultOutputPath(path));
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

  const handleChooseOutput = async () => {
    try {
      const selected = await dialogsBridge.chooseSavePath(
        defaultSaveName(inputPath),
        ["epub"],
      );
      if (selected.path) setOutputPath(selected.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleChooseCover = async () => {
    try {
      const selected = await dialogsBridge.chooseImageFile(coverPath || undefined);
      if (selected.path) setCoverPath(selected.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleOpenEditor = async () => {
    if (!inputPath.trim()) return;
    await loadMetadata(inputPath.trim(), false);
  };

  const applyMetadata = async (overwrite: boolean) => {
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
        setActionError(error);
      } else {
        throw error;
      }
    } finally {
      setLoading(false);
    }
  };

  const handleApplyRequest = () => {
    if (!inputPath.trim() || !resolvedOutputPath.trim()) return;
    if (isSamePath(inputPath, resolvedOutputPath)) {
      setConfirmOverwriteOpen(true);
      return;
    }
    void applyMetadata(false);
  };

  const handleRevealOutput = async () => {
    const path = result?.output_path || resolvedOutputPath;
    if (!path.trim()) return;
    try {
      await dialogsBridge.revealFile(path);
      setFeedback(null);
    } catch {
      const folder = outputFolder(path);
      if (!folder) return;
      await dialogsBridge.openDirectory(folder);
    }
  };

  return (
    <>
      <Panel title={text.title} subtitle={text.sub}>
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
          <label className={styles.field}>
            <span>{text.outputFile}</span>
            <div className={styles.fileRow}>
              <input
                value={outputPath}
                onChange={(event) => setOutputPath(event.target.value)}
                placeholder={defaultOutputPath(inputPath)}
              />
              <Pill variant="ghost" onClick={handleChooseOutput} disabled={loading}>
                {text.chooseOutput}
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
            disabled={!result && !resolvedOutputPath}
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
              label={text.currentOutput}
              value={result?.output_path || resolvedOutputPath || "-"}
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
                  <span>{info?.has_cover ? text.coverPresent : text.coverMissing}</span>
                  {info?.cover_archive_path ? <code>{info.cover_archive_path}</code> : null}
                </div>
                <label className={styles.field}>
                  <span>{text.coverFile}</span>
                  <div className={styles.fileRow}>
                    <input
                      value={coverPath}
                      onChange={(event) => setCoverPath(event.target.value)}
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
                  <span>{text.outputFile}</span>
                  <div className={styles.fileRow}>
                    <input
                      value={outputPath}
                      onChange={(event) => setOutputPath(event.target.value)}
                      placeholder={defaultOutputPath(inputPath)}
                    />
                    <Pill variant="ghost" onClick={handleChooseOutput}>
                      {text.chooseOutput}
                    </Pill>
                  </div>
                </label>
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

interface StatProps {
  label: string;
  value: string;
}

function Stat({ label, value }: StatProps) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue}>{value}</span>
    </div>
  );
}
