import { useEffect, useState } from "react";

import {
  BridgeError,
  epubMergeBridge,
  type EpubMergeAction,
  type EpubMergeArtifacts,
  type EpubMergeOptions,
  type EpubMergePlan,
  type EpubMergeReport,
} from "@/bridge";
import { FolderPickerRow } from "@/components/FolderPickerRow";
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
import styles from "./EpubMergePage.module.css";

const NUM = new Intl.NumberFormat("en");
const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

export function EpubMergePage() {
  const messages = useMessages();
  const text = messages.epubMergeTool;
  const [inputDir, setInputDir] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const [outputFilename, setOutputFilename] = useState(
    text.defaultOutputFilename,
  );
  const [options, setOptions] = useState<EpubMergeOptions>({
    output_path: "",
    quality: 60,
    max_size: 1600,
    keep_original_images: false,
    smart_cover: true,
    recursive: true,
  });
  const [plan, setPlan] = useState<EpubMergePlan | null>(null);
  const [actions, setActions] = useState<EpubMergeAction[]>([]);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [artifacts, setArtifacts] = useState<EpubMergeArtifacts | null>(null);
  const [report, setReport] = useState<EpubMergeReport | null>(null);
  const [showReport, setShowReport] = useState(false);
  const snapshot = useRunSnapshot("epub_merge");
  usePollRunSnapshot("epub_merge");
  const setActiveTaskId = useRuntimeStore((state) => state.setActiveTaskId);
  const activeTaskId = useRuntimeStore((state) => state.epub_merge.activeTaskId);

  useEffect(() => {
    setOutputFilename((prev) => prev || text.defaultOutputFilename);
  }, [text.defaultOutputFilename]);

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
  const settled = snapshot.progress.completed + snapshot.progress.skipped;
  const percent =
    snapshot.progress.total > 0
      ? Math.floor((settled / snapshot.progress.total) * 100)
      : 0;

  const handlePreview = async () => {
    setActionError(null);
    setArtifacts(null);
    setReport(null);
    setShowReport(false);
    try {
      const requestedOutput = composeOutputPath(
        outputDir,
        outputFilename,
        inputDir,
      );
      const previewOptions = { ...options, output_path: requestedOutput };
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
    try {
      const requestId = `epub-merge-${Date.now().toString(36)}`;
      const outputPath =
        composeOutputPath(outputDir, outputFilename, inputDir) ||
        plan?.output_path ||
        "";
      const executeOptions = { ...options, output_path: outputPath };
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

  const moveAction = (index: number, direction: -1 | 1) => {
    setActions((prev) => {
      const next = [...prev];
      const target = index + direction;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next.map((action, order) => ({ ...action, order }));
    });
  };

  return (
    <>
      <Panel title={text.title} subtitle={text.sub}>
        <div className={styles.folderGrid}>
          <FolderPickerRow
            label={text.inputFolder}
            value={inputDir}
            variant="input"
            onChange={setInputDir}
            compact
          />
          <FolderPickerRow
            label={text.outputFolder}
            value={outputDir}
            variant="output"
            onChange={setOutputDir}
            compact
          />
        </div>
        <div className={styles.fileRow}>
          <label className={`${styles.field} ${styles.compactField}`}>
            <span>{text.outputFilename}</span>
            <input
              value={outputFilename}
              onChange={(event) => setOutputFilename(event.target.value)}
            />
            <small>{text.outputFilenameHint}</small>
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
        </div>
        <div className={styles.actionRow}>
          <Pill onClick={handlePreview} disabled={!inputDir || isRunning}>
            {text.scan}
          </Pill>
          <Pill
            onClick={handleExecute}
            disabled={!inputDir || selectedCount < 2 || isRunning}
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
              <Stat
                label={text.outputFile}
                value={
                  composeOutputPath(outputDir, outputFilename, inputDir) ||
                  plan.output_path
                }
              />
            </div>
            {actions.length > 0 ? (
              <div className={styles.tableWrap}>
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
                        <td>
                          <div className={styles.orderControls}>
                            <span>{index + 1}</span>
                            <button
                              type="button"
                              className={styles.miniButton}
                              onClick={() => moveAction(index, -1)}
                              disabled={index === 0 || isRunning}
                            >
                              {text.moveUp}
                            </button>
                            <button
                              type="button"
                              className={styles.miniButton}
                              onClick={() => moveAction(index, 1)}
                              disabled={index === actions.length - 1 || isRunning}
                            >
                              {text.moveDown}
                            </button>
                          </div>
                        </td>
                        <td>{action.source_path}</td>
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
                value={artifacts.output_files.join("\n") || "-"}
              />
            </div>
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
                        <td>{row.source_path}</td>
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

function Stat({ label, value }: { label: string; value: string }) {
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
