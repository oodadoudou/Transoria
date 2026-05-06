import { useEffect, useState } from "react";

import {
  BridgeError,
  epubOrganizeBridge,
  type EpubOrganizeAction,
  type EpubOrganizeArtifacts,
  type EpubOrganizePlan,
  type EpubOrganizeReport,
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
import styles from "./EpubOrganizePage.module.css";

const NUM = new Intl.NumberFormat("en");
const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

export function EpubOrganizePage() {
  const messages = useMessages();
  const text = messages.epubOrganize;
  const [inputDir, setInputDir] = useState("");
  const [plan, setPlan] = useState<EpubOrganizePlan | null>(null);
  const [actions, setActions] = useState<EpubOrganizeAction[]>([]);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [artifacts, setArtifacts] = useState<EpubOrganizeArtifacts | null>(null);
  const [report, setReport] = useState<EpubOrganizeReport | null>(null);
  const [showReport, setShowReport] = useState(false);
  const snapshot = useRunSnapshot("epub_organize");
  usePollRunSnapshot("epub_organize");
  const setActiveTaskId = useRuntimeStore((state) => state.setActiveTaskId);
  const activeTaskId = useRuntimeStore(
    (state) => state.epub_organize.activeTaskId,
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
        const result = await epubOrganizeBridge.readArtifacts(activeTaskId);
        if (!cancelled) setArtifacts(result);
      } catch (error) {
        if (BridgeError.isBridgeError(error) && !cancelled) {
          setActionError(error);
        }
      }
      try {
        const result = await epubOrganizeBridge.readReport(activeTaskId);
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
      const next = await epubOrganizeBridge.preview(inputDir);
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
    try {
      const requestId = `epub-organize-${Date.now().toString(36)}`;
      const { task_id } = await epubOrganizeBridge.startTask(
        requestId,
        inputDir,
        actions,
      );
      setActiveTaskId("epub_organize", task_id);
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
      await epubOrganizeBridge.stopTask(activeTaskId);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const patchAction = (id: string, patch: Partial<EpubOrganizeAction>) => {
    setActions((prev) =>
      prev.map((action) =>
        action.id === id ? { ...action, ...patch } : action,
      ),
    );
  };

  return (
    <>
      <Panel title={text.title} subtitle={text.sub}>
        <div className={styles.pickerStack}>
          <FolderPickerRow
            label={text.inputFolder}
            value={inputDir}
            variant="input"
            onChange={setInputDir}
          />
        </div>
        <div className={styles.actionRow}>
          <Pill onClick={handlePreview} disabled={!inputDir || isRunning}>
            {text.scan}
          </Pill>
          <Pill
            onClick={handleExecute}
            disabled={!inputDir || selectedCount === 0 || isRunning}
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
                label={text.existingFolders}
                value={NUM.format(plan.totals.folders)}
              />
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
                      <th>{text.targetFolder}</th>
                      <th>{text.targetName}</th>
                      <th>{text.operation}</th>
                      <th>{text.score}</th>
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
                        <td>{action.source_name}</td>
                        <td>
                          <input
                            className={styles.inlineInput}
                            value={action.target_folder}
                            onChange={(event) =>
                              patchAction(action.id, {
                                target_folder: event.target.value,
                              })
                            }
                          />
                        </td>
                        <td>{action.target_name}</td>
                        <td>{operationLabel(text, action.operation)}</td>
                        <td>{action.score}</td>
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
              label={text.movedCount}
              value={NUM.format(artifacts.moved_count)}
            />
            <Stat
              label={text.failedFiles}
              value={NUM.format(artifacts.failed_count)}
            />
            <Stat
              label={text.createdFolders}
              value={NUM.format(artifacts.created_folders?.length ?? 0)}
            />
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
                    <th>{text.targetFolder}</th>
                    <th>{text.targetName}</th>
                    <th>{text.result}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.results.map((row) => (
                    <tr key={row.action_id}>
                      <td>{row.source_name}</td>
                      <td>{row.target_folder}</td>
                      <td>{row.target_name}</td>
                      <td>{row.status === "moved" ? text.moved : row.error}</td>
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

function operationLabel(
  text: ReturnType<typeof useMessages>["epubOrganize"],
  operation: string,
): string {
  return operation === "move_existing" ? text.moveExisting : text.createFolder;
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
