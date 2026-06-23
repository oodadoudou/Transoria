import { type ReactNode, useEffect, useState } from "react";

import {
  BridgeError,
  dialogsBridge,
  epubMergeBridge,
  type EpubMergeAction,
  type EpubMergeArtifacts,
  type EpubMergeOptions,
  type EpubMergePlan,
  type EpubMergeReport,
} from "@/bridge";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { CompactPath } from "@/components/CompactPath";
import { HelpTip } from "@/components/HelpTip";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
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
import styles from "./EpubMergePage.module.css";

const NUM = new Intl.NumberFormat("en");
const INPUT_DIR_LOCAL_KEY = "transoria.generalTools.epubMerge.inputDir";
const OUTPUT_DIR_LOCAL_KEY = "transoria.generalTools.epubMerge.outputDir";
const OUTPUT_FILENAME_SESSION_KEY =
  "transoria.generalTools.epubMerge.outputFilename";
const OPTIONS_SESSION_KEY = "transoria.generalTools.epubMerge.options";
const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

function defaultOptions(): EpubMergeOptions {
  return {
    output_path: "",
    output_format: "epub",
    quality: 60,
    max_size: 1600,
    keep_original_images: false,
    smart_cover: true,
    recursive: true,
  };
}

export function EpubMergePage({ embedded = false }: { embedded?: boolean } = {}) {
  const messages = useMessages();
  const text = messages.epubMergeTool;
  const [inputDir, setInputDir] = useLocalState(INPUT_DIR_LOCAL_KEY, "");
  const [outputDir, setOutputDir] = useLocalState(OUTPUT_DIR_LOCAL_KEY, "");
  const [outputFilename, setOutputFilename] = useSessionState(
    OUTPUT_FILENAME_SESSION_KEY,
    text.defaultOutputFilename,
  );
  const [options, setOptions] = useSessionState<EpubMergeOptions>(
    OPTIONS_SESSION_KEY,
    defaultOptions(),
  );
  const [plan, setPlan] = useState<EpubMergePlan | null>(null);
  const [actions, setActions] = useState<EpubMergeAction[]>([]);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [artifacts, setArtifacts] = useState<EpubMergeArtifacts | null>(null);
  const [report, setReport] = useState<EpubMergeReport | null>(null);
  const [showReport, setShowReport] = useState(false);
  const [artifactFeedback, setArtifactFeedback] = useState<string | null>(null);
  const [draggingActionId, setDraggingActionId] = useState<string | null>(null);
  const snapshot = useRunSnapshot("epub_merge");
  usePollRunSnapshot("epub_merge");
  const setActiveTaskId = useRuntimeStore((state) => state.setActiveTaskId);
  const activeTaskId = useRuntimeStore((state) => state.epub_merge.activeTaskId);
  const outputFormat: "epub" | "txt" =
    options.output_format === "txt" ? "txt" : "epub";

  useEffect(() => {
    setOutputFilename((prev) => prev || text.defaultOutputFilename);
  }, [text.defaultOutputFilename]);

  useEffect(() => {
    setOutputFilename((prev) =>
      withOutputExtension(
        prev || text.defaultOutputFilename,
        outputFormat,
        text.defaultOutputFilename,
      ),
    );
  }, [outputFormat, setOutputFilename, text.defaultOutputFilename]);

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
        const result = await epubMergeBridge.readArtifacts(activeTaskId);
        if (!cancelled) setArtifacts(result);
      } catch (error) {
        if (BridgeError.isBridgeError(error) && !cancelled) {
          setActionError(error);
        }
      }
      try {
        const result = await epubMergeBridge.readReport(activeTaskId);
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

  const handlePreview = async () => {
    setActionError(null);
    setArtifacts(null);
    setReport(null);
    setShowReport(false);
    setArtifactFeedback(null);
    try {
      const requestedOutput = composeOutputPath(
        outputDir,
        outputFilename,
        inputDir,
      );
      const previewOptions = {
        ...options,
        output_format: outputFormat,
        output_path: requestedOutput,
      };
      const next = await epubMergeBridge.preview(inputDir, previewOptions);
      const outputParts = splitOutputPath(next.output_path);
      setPlan(next);
      setActions(next.actions);
      setOutputDir((prev) => prev || outputParts.dir);
      setOutputFilename((prev) => prev || outputParts.name);
      setOptions((prev) => ({ ...prev, output_path: next.output_path }));
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
    try {
      const requestId = `epub-merge-${Date.now().toString(36)}`;
      const outputPath =
        composeOutputPath(outputDir, outputFilename, inputDir) ||
        plan?.output_path ||
        "";
      const executeOptions = {
        ...options,
        output_format: outputFormat,
        output_path: outputPath,
      };
      const { task_id } = await epubMergeBridge.startTask(
        requestId,
        inputDir,
        outputPath,
        executeOptions,
        actions,
      );
      setActiveTaskId("epub_merge", task_id);
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
      await epubMergeBridge.stopTask(activeTaskId);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const patchAction = (id: string, patch: Partial<EpubMergeAction>) => {
    setActions((prev) =>
      prev.map((action) =>
        action.id === id ? { ...action, ...patch } : action,
      ),
    );
  };

  const moveActionToOrder = (index: number, rawOrder: string) => {
    const order = Number.parseInt(rawOrder, 10);
    if (!Number.isFinite(order)) return;
    setActions((prev) => {
      const next = [...prev];
      const target = Math.min(Math.max(order - 1, 0), next.length - 1);
      if (target < 0 || target >= next.length) return prev;
      if (target === index) return prev;
      const [moved] = next.splice(index, 1);
      next.splice(target, 0, moved);
      return next.map((action, order) => ({ ...action, order }));
    });
  };

  const moveActionByDrag = (
    sourceId: string,
    targetId: string,
    insertAfter: boolean,
  ) => {
    if (sourceId === targetId) return;
    setActions((prev) => {
      const sourceIndex = prev.findIndex((action) => action.id === sourceId);
      const targetIndex = prev.findIndex((action) => action.id === targetId);
      if (sourceIndex < 0 || targetIndex < 0) return prev;
      const next = [...prev];
      const [moved] = next.splice(sourceIndex, 1);
      const adjustedTarget =
        sourceIndex < targetIndex ? targetIndex - 1 : targetIndex;
      const insertIndex = adjustedTarget + (insertAfter ? 1 : 0);
      next.splice(insertIndex, 0, moved);
      return next.map((action, order) => ({ ...action, order }));
    });
  };

  return (
    <>
      <Panel
        title={embedded ? undefined : text.title}
        subtitle={embedded ? undefined : text.sub}
      >
        <div className={styles.folderGrid}>
          <FolderPickerRow
            label={text.inputFolder}
            value={inputDir}
            variant="input"
            onChange={setInputDir}
            historyKey="general_tools:epub_merge:input_folder"
            compact
          />
          <FolderPickerRow
            label={text.outputFolder}
            value={outputDir}
            variant="output"
            onChange={setOutputDir}
            historyKey="general_tools:epub_merge:output_folder"
            compact
          />
        </div>
        <div className={styles.folderHelp}>
          <HelpTip>{text.outputFolderHint}</HelpTip>
        </div>
        <div className={styles.fileRow}>
          <label className={`${styles.field} ${styles.compactField}`}>
            <span>{text.outputFormat}</span>
            <select
              value={outputFormat}
              onChange={(event) =>
                setOptions((prev) => ({
                  ...prev,
                  output_format: event.target.value === "txt" ? "txt" : "epub",
                }))
              }
            >
              <option value="epub">{text.outputFormatEpub}</option>
              <option value="txt">{text.outputFormatTxt}</option>
            </select>
          </label>
          <label className={`${styles.field} ${styles.compactField}`}>
            <span className={styles.labelWithHelp}>
              {text.outputFilename}
              <HelpTip>{text.outputFilenameHint}</HelpTip>
            </span>
            <input
              value={outputFilename}
              onChange={(event) => setOutputFilename(event.target.value)}
            />
          </label>
        </div>
        <div className={styles.optionsGrid}>
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
            />
            {text.recursive}
          </label>
          {outputFormat === "epub" ? (
            <>
              <label className={styles.option}>
                <input
                  type="checkbox"
                  checked={options.smart_cover}
                  onChange={(event) =>
                    setOptions((prev) => ({
                      ...prev,
                      smart_cover: event.target.checked,
                    }))
                  }
                />
                {text.smartCover}
              </label>
              <label className={styles.option}>
                <input
                  type="checkbox"
                  checked={options.keep_original_images}
                  onChange={(event) =>
                    setOptions((prev) => ({
                      ...prev,
                      keep_original_images: event.target.checked,
                    }))
                  }
                />
                {text.keepOriginalImages}
              </label>
            </>
          ) : null}
        </div>
        <div className={styles.actionRow}>
          <Pill onClick={handlePreview} disabled={!inputDir || isRunning}>
            {text.scan}
          </Pill>
          <Pill
            onClick={handleExecute}
            disabled={!inputDir || selectedCount < 1 || isRunning}
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
              <Stat label={text.txtFound} value={NUM.format(plan.totals.txt_files ?? 0)} />
              <Stat
                label={text.selectedCount}
                value={`${NUM.format(selectedCount)} / ${NUM.format(actions.length)}`}
              />
              <Stat
                label={text.outputFile}
                value={
                  <CompactPath
                    value={
                      composeOutputPath(outputDir, outputFilename, inputDir) ||
                      plan.output_path
                    }
                    copyLabel={messages.common.copyPath}
                    emptyLabel="-"
                  />
                }
              />
            </div>
            {actions.length > 0 ? (
              <div className={styles.tableWrap}>
                <div className={styles.tableHelp}>
                  <HelpTip>{text.orderHint}</HelpTip>
                </div>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>{text.select}</th>
                      <th>{text.order}</th>
                      <th>{text.source}</th>
                      <th>{text.size}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {actions.map((action, index) => (
                      <tr
                        key={action.id}
                        className={
                          draggingActionId === action.id
                            ? styles.dragging
                            : undefined
                        }
                        onDragOver={(event) => {
                          if (!draggingActionId || isRunning) return;
                          event.preventDefault();
                          event.dataTransfer.dropEffect = "move";
                        }}
                        onDrop={(event) => {
                          if (!draggingActionId || isRunning) return;
                          event.preventDefault();
                          const rect = event.currentTarget.getBoundingClientRect();
                          const insertAfter =
                            event.clientY > rect.top + rect.height / 2;
                          moveActionByDrag(
                            draggingActionId,
                            action.id,
                            insertAfter,
                          );
                          setDraggingActionId(null);
                        }}
                      >
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
                        <td>
                          <div className={styles.orderControls}>
                            <button
                              type="button"
                              className={styles.dragHandle}
                              draggable={!isRunning}
                              disabled={isRunning}
                              onDragStart={(event) => {
                                setDraggingActionId(action.id);
                                event.dataTransfer.effectAllowed = "move";
                                event.dataTransfer.setData(
                                  "text/plain",
                                  action.id,
                                );
                              }}
                              onDragEnd={() => setDraggingActionId(null)}
                              aria-label={`${text.order} ${index + 1}`}
                            >
                              ::
                            </button>
                            <input
                              className={styles.orderInput}
                              type="number"
                              min={1}
                              max={actions.length}
                              value={index + 1}
                              onFocus={(event) => event.currentTarget.select()}
                              onChange={(event) =>
                                moveActionToOrder(index, event.target.value)
                              }
                              disabled={isRunning}
                              aria-label={`${text.order} ${index + 1}`}
                            />
                          </div>
                        </td>
                        <td>
                          <CompactPath
                            value={action.source_path}
                            copyLabel={messages.common.copyPath}
                          />
                        </td>
                        <td>{formatBytes(action.size_bytes)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className={styles.empty}>{text.noActions}</p>
            )}
          </>
        ) : (
          <p className={styles.empty}>{text.noPlan}</p>
        )}
      </Panel>

      <Panel label={text.progressLabel}>
        <div className={styles.summaryGrid}>
          <Stat label={text.statusLabel} value={`${snapshot.status} · ${percent}%`} />
          <Stat label={text.processedFiles} value={NUM.format(snapshot.progress.completed)} />
          <Stat label={text.failedFiles} value={NUM.format(snapshot.progress.failed)} />
        </div>
      </Panel>

      <Panel label={text.artifactsLabel}>
        {artifacts ? (
          <>
            <div className={styles.summaryGrid}>
              <Stat label={text.mergedCount} value={NUM.format(artifacts.merged_count)} />
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
            {artifacts.output_files.length > 0 ? (
              <div className={styles.artifactList}>
                {artifacts.output_files.map((path) => (
                  <CompactPath
                    key={path}
                    value={path}
                    copyLabel={messages.common.copyPath}
                  />
                ))}
              </div>
            ) : null}
            {report ? (
              <div className={styles.reportToggle}>
                <Pill variant="ghost" onClick={() => setShowReport((prev) => !prev)}>
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
                      <th>{text.result}</th>
                      <th>{text.chapters}</th>
                      <th>{text.resources}</th>
                      <th>{text.fonts}</th>
                      <th>{text.warnings}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.result.processed_files.map((row) => (
                      <tr key={row.source_path}>
                        <td>
                          <CompactPath
                            value={row.source_path}
                            copyLabel={messages.common.copyPath}
                          />
                        </td>
                        <td>{row.status}</td>
                        <td>{NUM.format(row.chapters)}</td>
                        <td>{NUM.format(row.resources)}</td>
                        <td>{NUM.format(row.fonts_removed)}</td>
                        <td>{row.warnings.join("\n") || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </>
        ) : (
          <p className={styles.empty}>-</p>
        )}
      </Panel>
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

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue}>{value}</span>
    </div>
  );
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function composeOutputPath(folder: string, filename: string, fallbackFolder: string) {
  const name = filename.trim();
  if (!name) return "";
  const dir = (folder.trim() || fallbackFolder.trim()).replace(/[\\/]+$/, "");
  if (!dir) return name;
  const separator = dir.includes("\\") && !dir.includes("/") ? "\\" : "/";
  return `${dir}${separator}${name}`;
}

function splitOutputPath(path: string) {
  const slash = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (slash < 0) return { dir: "", name: path };
  return {
    dir: path.slice(0, slash),
    name: path.slice(slash + 1),
  };
}

function withOutputExtension(
  filename: string,
  format: "epub" | "txt",
  defaultFilename = "merged.epub",
) {
  const clean = filename.trim();
  const suffix = `.${format}`;
  if (!clean) return defaultFilename.replace(/\.(epub|txt)$/i, suffix);
  return clean.replace(/\.(epub|txt)$/i, suffix) || clean;
}
