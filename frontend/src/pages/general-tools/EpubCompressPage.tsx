import { useEffect, useState } from "react";

import {
  BridgeError,
  dialogsBridge,
  epubCompressBridge,
  type EpubCompressAction,
  type EpubCompressArtifacts,
  type EpubCompressOptions,
  type EpubCompressPlan,
  type EpubCompressReport,
} from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { useMessages } from "@/locales";
import {
  hasShownCleanCompletionToast,
  markCleanCompletionToastShown,
  usePollRunSnapshot,
  useRunSnapshot,
  useRuntimeStore,
} from "@/store/useRuntimeStore";
import { useToastStore } from "@/store/useToastStore";
import { useLocalState } from "@/utils/localState";
import { useSessionState } from "@/utils/sessionState";
import styles from "./EpubCompressPage.module.css";

const NUM = new Intl.NumberFormat("en");
const MODE_SESSION_KEY = "transoria.generalTools.epubCompress.mode";
const INPUT_LOCAL_KEY = "transoria.generalTools.epubCompress.inputPath";
const OPTIONS_SESSION_KEY = "transoria.generalTools.epubCompress.options";
const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

function defaultOptions(suffix: string): EpubCompressOptions {
  return {
    suffix,
    replace_original: false,
    preserve_first_cover: false,
    font_mode: "deduplicate",
    quality: 50,
    max_size: 1200,
    recursive: true,
  };
}

export function EpubCompressPage({ embedded = false }: { embedded?: boolean } = {}) {
  const messages = useMessages();
  const text = messages.epubCompressTool;
  const [mode, setMode] = useSessionState<"file" | "folder">(
    MODE_SESSION_KEY,
    "folder",
  );
  const [inputPath, setInputPath] = useLocalState(INPUT_LOCAL_KEY, "");
  const [options, setOptions] = useSessionState<EpubCompressOptions>(
    OPTIONS_SESSION_KEY,
    defaultOptions(text.defaultSuffix),
  );
  const [plan, setPlan] = useState<EpubCompressPlan | null>(null);
  const [actions, setActions] = useState<EpubCompressAction[]>([]);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [artifacts, setArtifacts] = useState<EpubCompressArtifacts | null>(null);
  const [report, setReport] = useState<EpubCompressReport | null>(null);
  const [showReport, setShowReport] = useState(false);
  const [artifactFeedback, setArtifactFeedback] = useState<string | null>(null);
  const snapshot = useRunSnapshot("epub_compress");
  usePollRunSnapshot("epub_compress");
  const setActiveTaskId = useRuntimeStore((state) => state.setActiveTaskId);
  const activeTaskId = useRuntimeStore(
    (state) => state.epub_compress.activeTaskId,
  );

  useEffect(() => {
    setOptions((prev) =>
      prev.suffix ? prev : { ...prev, suffix: text.defaultSuffix },
    );
  }, [text.defaultSuffix]);

  useEffect(() => {
    if (!activeTaskId) return;
    if (snapshot.status !== "completed") return;
    if (snapshot.progress.failed > 0) return;
    if (snapshot.progress.completed <= 0) return;
    if (hasShownCleanCompletionToast(activeTaskId)) return;
    markCleanCompletionToastShown(activeTaskId);
    useToastStore.getState().push({
      variant: "success",
      title: messages.runCompleted.title,
    });
  }, [
    activeTaskId,
    snapshot.status,
    snapshot.progress.failed,
    snapshot.progress.completed,
    messages.runCompleted.title,
  ]);

  useEffect(() => {
    if (!activeTaskId || !TERMINAL.has(snapshot.status)) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await epubCompressBridge.readArtifacts(activeTaskId);
        if (!cancelled) setArtifacts(result);
      } catch (error) {
        if (BridgeError.isBridgeError(error) && !cancelled) {
          setActionError(error);
        }
      }
      try {
        const result = await epubCompressBridge.readReport(activeTaskId);
        if (!cancelled) setReport(result);
      } catch (error) {
        if (
          BridgeError.isBridgeError(error) &&
          error.code !== "bridge.not_found" &&
          !cancelled
        ) {
          setActionError(error);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTaskId, snapshot.status]);

  const selectedCount = actions.filter((action) => action.selected).length;
  const isRunning =
    activeTaskId !== null &&
    (snapshot.status === "running" || snapshot.status === "pending");
  const settled = snapshot.progress.completed;
  const percent =
    snapshot.progress.total > 0
      ? Math.floor((settled / snapshot.progress.total) * 100)
      : 0;

  const handleChooseFile = async () => {
    try {
      const result = await dialogsBridge.chooseEpubFile(inputPath || undefined);
      if (result.path) {
        setMode("file");
        setInputPath(result.path);
      }
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handlePreview = async () => {
    setActionError(null);
    setArtifacts(null);
    setReport(null);
    setShowReport(false);
    setArtifactFeedback(null);
    const requestOptions = normalizeOptions(options, text.defaultSuffix);
    setOptions(requestOptions);
    try {
      const next = await epubCompressBridge.preview(inputPath, mode, requestOptions);
      setPlan(next);
      setActions(next.actions);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const handleExecute = async () => {
    setActionError(null);
    setArtifacts(null);
    setReport(null);
    setShowReport(false);
    setArtifactFeedback(null);
    const requestOptions = normalizeOptions(options, text.defaultSuffix);
    setOptions(requestOptions);
    try {
      const requestId = `epub-compress-${Date.now().toString(36)}`;
      const { task_id } = await epubCompressBridge.startTask(
        requestId,
        inputPath,
        mode,
        requestOptions,
        actions,
      );
      setActiveTaskId("epub_compress", task_id);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const handleStop = async () => {
    if (!activeTaskId) return;
    setActionError(null);
    try {
      await epubCompressBridge.stopTask(activeTaskId);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const patchAction = (id: string, patch: Partial<EpubCompressAction>) => {
    setActions((prev) =>
      prev.map((action) =>
        action.id === id ? { ...action, ...patch } : action,
      ),
    );
  };

  return (
    <>
      <Panel
        title={embedded ? undefined : text.title}
        subtitle={embedded ? undefined : text.sub}
      >
        <div className={styles.configLine}>
          <div className={styles.modeRow}>
            <button
              type="button"
              className={mode === "folder" ? styles.modeActive : styles.modeButton}
              onClick={() => setMode("folder")}
            >
              {text.folderMode}
            </button>
            <button
              type="button"
              className={mode === "file" ? styles.modeActive : styles.modeButton}
              onClick={() => setMode("file")}
            >
              {text.fileMode}
            </button>
          </div>
          {mode === "folder" ? (
            <FolderPickerRow
              label={text.inputFolder}
              value={inputPath}
              variant="input"
              onChange={setInputPath}
              historyKey="general_tools:folders"
              compact
            />
          ) : (
            <div className={styles.fileRow}>
              <input
                className={styles.pathInput}
                value={inputPath}
                onChange={(event) => setInputPath(event.target.value)}
                placeholder={text.filePlaceholder}
              />
              <Pill variant="ghost" onClick={handleChooseFile}>
                {text.chooseFile}
              </Pill>
            </div>
          )}
        </div>
        <div className={styles.optionsGrid}>
          <label className={styles.option}>
            <input
              type="checkbox"
              checked={options.replace_original}
              onChange={(event) =>
                setOptions((prev) => ({
                  ...prev,
                  replace_original: event.target.checked,
                }))
              }
            />
            {text.replaceOriginal}
          </label>
          <label className={styles.option}>
            <input
              type="checkbox"
              checked={options.preserve_first_cover}
              onChange={(event) =>
                setOptions((prev) => ({
                  ...prev,
                  preserve_first_cover: event.target.checked,
                }))
              }
            />
            {text.preserveCover}
          </label>
          <label className={styles.option}>
            <input
              type="checkbox"
              checked={options.recursive}
              onChange={(event) =>
                setOptions((prev) => ({
                  ...prev,
                  recursive: event.target.checked,
                }))
              }
              disabled={mode === "file"}
            />
            {text.recursive}
          </label>
          <label className={styles.field}>
            <span>{text.fontMode}</span>
            <select
              value={options.font_mode}
              onChange={(event) =>
                setOptions((prev) => ({
                  ...prev,
                  font_mode: event.target.value,
                }))
              }
            >
              <option value="deduplicate">{text.fontModeDeduplicate}</option>
              <option value="remove">{text.fontModeRemove}</option>
            </select>
          </label>
          <label className={styles.field}>
            <span>{text.suffix}</span>
            <input
              value={options.suffix}
              disabled={options.replace_original}
              onBlur={() =>
                setOptions((prev) => normalizeOptions(prev, text.defaultSuffix))
              }
              onChange={(event) =>
                setOptions((prev) => ({ ...prev, suffix: event.target.value }))
              }
            />
          </label>
        </div>
        <div className={styles.actionRow}>
          <Pill onClick={handlePreview} disabled={!inputPath || isRunning}>
            {text.scan}
          </Pill>
          <Pill
            onClick={handleExecute}
            disabled={!inputPath || selectedCount === 0 || isRunning}
          >
            {text.execute}
          </Pill>
          <Pill variant="ghost" onClick={handleStop} disabled={!isRunning}>
            {text.stop}
          </Pill>
          {actionError ? (
            <span className={styles.actionError}>
              <code>{actionError.code}</code> {actionError.message}
            </span>
          ) : null}
        </div>
      </Panel>

      <Panel label={text.previewLabel}>
        {plan ? (
          <>
            <div className={styles.summaryGrid}>
              <Stat label={text.epubsFound} value={NUM.format(plan.totals.epub_files)} />
              <Stat
                label={text.selectedCount}
                value={`${NUM.format(selectedCount)} / ${NUM.format(actions.length)}`}
              />
            </div>
            {actions.length > 0 ? (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>{text.select}</th>
                      <th>{text.source}</th>
                      <th>{text.output}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {actions.map((action) => (
                      <tr key={action.id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={action.selected}
                            onChange={(event) =>
                              patchAction(action.id, {
                                selected: event.target.checked,
                              })
                            }
                          />
                        </td>
                        <td>{action.source_path}</td>
                        <td>{action.output_path}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className={styles.empty}>{text.noActions}</div>
            )}
          </>
        ) : (
          <div className={styles.empty}>{text.noPlan}</div>
        )}
      </Panel>

      {activeTaskId ? (
        <Panel label={text.progressLabel}>
          <div className={styles.summaryGrid}>
            <Stat label={text.statusLabel} value={snapshot.status} />
            <Stat
              label={text.processedFiles}
              value={`${NUM.format(settled)} / ${NUM.format(snapshot.progress.total)} (${percent}%)`}
            />
            <Stat
              label={text.failedFiles}
              value={NUM.format(snapshot.progress.failed)}
            />
          </div>
        </Panel>
      ) : null}

      {artifacts ? (
        <Panel label={text.artifactsLabel}>
          <div className={styles.summaryGrid}>
            <Stat
              label={text.compressedCount}
              value={NUM.format(artifacts.compressed_count)}
            />
            <Stat
              label={text.failedFiles}
              value={NUM.format(artifacts.failed_count)}
            />
            <Stat
              label={text.outputFiles}
              value={NUM.format(artifacts.output_files.length)}
            />
          </div>
          <div className={styles.artifactActions}>
            <Pill
              variant="ghost"
              onClick={() =>
                void openOutputFolder(artifacts.output_folder, setArtifactFeedback)
              }
            >
              {text.openOutputFolder}
            </Pill>
            <Pill
              variant="ghost"
              onClick={() =>
                void copyOutputPaths(
                  artifacts.output_files,
                  text.copyOutputPathsDone,
                  setArtifactFeedback,
                )
              }
              disabled={artifacts.output_files.length === 0}
            >
              {text.copyOutputPaths}
            </Pill>
            {artifactFeedback ? (
              <span className={styles.artifactFeedback}>{artifactFeedback}</span>
            ) : null}
          </div>
          {report ? (
            <div className={styles.reportToggle}>
              <Pill variant="ghost" onClick={() => setShowReport((v) => !v)}>
                {showReport ? text.hideReport : text.viewReport}
              </Pill>
            </div>
          ) : null}
          {showReport && report ? (
            <div className={styles.tableWrap}>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>{text.source}</th>
                    <th>{text.output}</th>
                    <th>{text.saved}</th>
                    <th>{text.images}</th>
                    <th>{text.fonts}</th>
                    <th>{text.result}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.results.map((row) => (
                    <tr key={row.action_id}>
                      <td>{row.source_path}</td>
                      <td>{row.output_path}</td>
                      <td>{formatSaved(row.saved_bytes, row.saved_percent)}</td>
                      <td>{row.images_compressed}</td>
                      <td>{row.fonts_removed}</td>
                      <td>
                        {row.status === "compressed" ? text.compressed : row.error}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Panel>
      ) : null}
    </>
  );
}

async function openOutputFolder(
  folder: string,
  setFeedback: (message: string | null) => void,
) {
  try {
    await dialogsBridge.openDirectory(folder);
    setFeedback(null);
  } catch (error) {
    setFeedback(
      BridgeError.isBridgeError(error)
        ? `${error.code}: ${error.message}`
        : String(error),
    );
  }
}

async function copyOutputPaths(
  paths: string[],
  successMessage: string,
  setFeedback: (message: string | null) => void,
) {
  try {
    await navigator.clipboard.writeText(paths.join("\n"));
    setFeedback(successMessage);
  } catch (error) {
    setFeedback(
      BridgeError.isBridgeError(error)
        ? `${error.code}: ${error.message}`
        : String(error),
    );
  }
}

function normalizeOptions(
  options: EpubCompressOptions,
  defaultSuffix: string,
): EpubCompressOptions {
  const suffix = options.suffix.trim() || defaultSuffix;
  return suffix === options.suffix ? options : { ...options, suffix };
}

function formatSaved(bytes: number, percent: number): string {
  const mb = Math.abs(bytes) / 1024 / 1024;
  const sign = bytes >= 0 ? "" : "+";
  return `${sign}${mb.toFixed(2)} MB (${percent.toFixed(1)}%)`;
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
