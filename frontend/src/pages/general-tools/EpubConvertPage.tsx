import { type ReactNode, useEffect, useState } from "react";

import {
  BridgeError,
  dialogsBridge,
  epubConvertBridge,
  type EpubConvertAction,
  type EpubConvertArtifacts,
  type EpubConvertOptions,
  type EpubConvertPlan,
  type EpubConvertReport,
} from "@/bridge";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { HelpTip } from "@/components/HelpTip";
import { CompactPath } from "@/components/CompactPath";
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
import {
  EpubToolActionDock,
  EpubToolAdvancedSettings,
  EpubToolStage,
  EpubToolWorkspace,
} from "./EpubToolWorkflow";
import styles from "./EpubConvertPage.module.css";

const NUM = new Intl.NumberFormat("en");
const MODE_SESSION_KEY = "transoria.generalTools.epubConvert.mode";
const INPUT_LOCAL_KEY = "transoria.generalTools.epubConvert.inputPath";
const OPTIONS_SESSION_KEY = "transoria.generalTools.epubConvert.options";
const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

function defaultOptions(): EpubConvertOptions {
  return {
    output_dir: "",
    recursive: true,
  };
}

export function EpubConvertPage({ embedded = false }: { embedded?: boolean } = {}) {
  const messages = useMessages();
  const text = messages.epubConvertTool;
  const [mode, setMode] = useSessionState<"file" | "folder">(
    MODE_SESSION_KEY,
    "folder",
  );
  const [inputPath, setInputPath] = useLocalState(INPUT_LOCAL_KEY, "");
  const [options, setOptions] = useSessionState<EpubConvertOptions>(
    OPTIONS_SESSION_KEY,
    defaultOptions(),
  );
  const [plan, setPlan] = useState<EpubConvertPlan | null>(null);
  const [actions, setActions] = useState<EpubConvertAction[]>([]);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [artifacts, setArtifacts] = useState<EpubConvertArtifacts | null>(null);
  const [report, setReport] = useState<EpubConvertReport | null>(null);
  const [showReport, setShowReport] = useState(false);
  const [artifactFeedback, setArtifactFeedback] = useState<string | null>(null);
  const snapshot = useRunSnapshot("epub_convert");
  usePollRunSnapshot("epub_convert");
  const setActiveTaskId = useRuntimeStore((state) => state.setActiveTaskId);
  const activeTaskId = useRuntimeStore(
    (state) => state.epub_convert.activeTaskId,
  );

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
        const result = await epubConvertBridge.readArtifacts(activeTaskId);
        if (!cancelled) setArtifacts(result);
      } catch (error) {
        if (BridgeError.isBridgeError(error) && !cancelled) {
          setActionError(error);
        }
      }
      try {
        const result = await epubConvertBridge.readReport(activeTaskId);
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
  const canExecute =
    Boolean(inputPath) && !isRunning && (mode === "file" || selectedCount > 0);
  const stage = isRunning
    ? "progress"
    : artifacts
      ? "result"
      : mode === "folder"
        ? "preview"
        : activeTaskId
          ? "progress"
          : "idle";

  const clearPreviewState = () => {
    setPlan(null);
    setActions([]);
    setActionError(null);
    setArtifacts(null);
    setReport(null);
    setShowReport(false);
    setArtifactFeedback(null);
  };

  const handleModeChange = (nextMode: "file" | "folder") => {
    if (nextMode === mode) return;
    setMode(nextMode);
    clearPreviewState();
  };

  const handleInputPathChange = (nextPath: string) => {
    setInputPath(nextPath);
    clearPreviewState();
  };

  const handleChooseFile = async () => {
    try {
      const result = await dialogsBridge.chooseEpubFile(inputPath || undefined);
      if (result.path) {
        setMode("file");
        handleInputPathChange(result.path);
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
    try {
      const next = await epubConvertBridge.preview(inputPath, mode, {
        ...options,
      });
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
    try {
      let executionActions = actions;
      if (mode === "file") {
        const next = await epubConvertBridge.preview(inputPath, mode, {
          ...options,
        });
        executionActions = next.actions;
        setPlan(next);
        setActions(next.actions);
      }
      const requestId = `epub-convert-${Date.now().toString(36)}`;
      const { task_id } = await epubConvertBridge.startTask(
        requestId,
        inputPath,
        mode,
        { ...options },
        executionActions,
      );
      setActiveTaskId("epub_convert", task_id);
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
      await epubConvertBridge.stopTask(activeTaskId);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const patchAction = (id: string, patch: Partial<EpubConvertAction>) => {
    setActions((prev) =>
      prev.map((action) =>
        action.id === id ? { ...action, ...patch } : action,
      ),
    );
  };

  return (
    <EpubToolWorkspace>
      <Panel
        title={embedded ? undefined : text.title}
        subtitle={embedded ? undefined : text.sub}
      >
        <div className={styles.configLine}>
          <div className={styles.modeRow}>
            <button
              type="button"
              className={mode === "folder" ? styles.modeActive : styles.modeButton}
              onClick={() => handleModeChange("folder")}
            >
              {text.folderMode}
            </button>
            <button
              type="button"
              className={mode === "file" ? styles.modeActive : styles.modeButton}
              onClick={() => handleModeChange("file")}
            >
              {text.fileMode}
            </button>
          </div>
          <div className={styles.pathGrid}>
            {mode === "folder" ? (
              <FolderPickerRow
                label={text.inputFolder}
                value={inputPath}
                variant="input"
                onChange={handleInputPathChange}
                historyKey="general_tools:epub_convert:input_folder"
                compact
              />
            ) : (
              <label className={styles.fileField}>
                <span>{text.fileMode}</span>
                <div className={styles.fileRow}>
                  <input
                    className={styles.pathInput}
                    value={inputPath}
                    onChange={(event) => handleInputPathChange(event.target.value)}
                    placeholder={text.filePlaceholder}
                  />
                  <Pill variant="ghost" onClick={handleChooseFile}>
                    {text.chooseFile}
                  </Pill>
                </div>
              </label>
            )}
            <FolderPickerRow
              label={text.outputFolder}
              value={options.output_dir}
              variant="output"
              onChange={(path) =>
                setOptions((prev) => ({ ...prev, output_dir: path }))
              }
              historyKey="general_tools:epub_convert:output_folder"
              compact
              help={<HelpTip>{text.outputHint}</HelpTip>}
            />
          </div>
        </div>
        <EpubToolAdvancedSettings label={messages.common.advancedSettings}>
          {mode === "folder" ? (
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
            </div>
          ) : null}
        </EpubToolAdvancedSettings>
      </Panel>

      <EpubToolStage>
      {stage === "preview" ? (
        <Panel
          label={text.previewLabel}
          labelHelp={!plan ? text.noPlan : undefined}
        >
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
                          <td>
                            <CompactPath
                              value={action.source_path}
                              displayMode="filename"
                              copyLabel={messages.common.copyPath}
                            />
                          </td>
                          <td>
                            <CompactPath
                              value={action.output_path}
                              displayMode="filename"
                              copyLabel={messages.common.copyPath}
                            />
                          </td>
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
            <div className={styles.empty}>-</div>
          )}
        </Panel>
      ) : null}

      {stage === "progress" ? (
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

      {stage === "result" && artifacts ? (
        <Panel label={text.artifactsLabel}>
          <div className={styles.summaryGrid}>
            <Stat
              label={text.convertedCount}
              value={NUM.format(artifacts.converted_count)}
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
                    <th>{text.segments}</th>
                    <th>{text.characters}</th>
                    <th>{text.documents}</th>
                    <th>{text.result}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.results.map((row) => (
                    <tr key={row.action_id}>
                      <td>
                        <CompactPath
                          value={row.source_path}
                          displayMode="filename"
                          copyLabel={messages.common.copyPath}
                        />
                      </td>
                      <td>
                        <CompactPath
                          value={row.output_path}
                          displayMode="filename"
                          copyLabel={messages.common.copyPath}
                        />
                      </td>
                      <td>{NUM.format(row.segments_written)}</td>
                      <td>{NUM.format(row.characters_written)}</td>
                      <td>{NUM.format(row.spine_documents)}</td>
                      <td>
                        {row.status === "converted" ? text.converted : row.error}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Panel>
      ) : null}
      </EpubToolStage>

      <EpubToolActionDock
        statusLabel={text.statusLabel}
        status={
          stage === "preview" && plan
            ? `${text.selectedCount} ${NUM.format(selectedCount)} / ${NUM.format(actions.length)}`
            : stage === "progress"
              ? `${snapshot.status} · ${percent}%`
              : stage === "result"
                ? text.artifactsLabel
                : text.previewLabel
        }
        actions={
          <>
            {mode === "folder" ? (
              <Pill variant="ghost" onClick={handlePreview} disabled={!inputPath || isRunning}>
                {text.scan}
              </Pill>
            ) : null}
            <Pill onClick={handleExecute} disabled={!canExecute}>
              {text.execute}
            </Pill>
            <Pill variant="ghost" onClick={handleStop} disabled={!isRunning}>
              {text.stop}
            </Pill>
          </>
        }
        error={actionError ? (
          <span className={styles.actionError}>
            <code>{actionError.code}</code> {actionError.message}
          </span>
        ) : undefined}
      />
    </EpubToolWorkspace>
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
