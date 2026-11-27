import { ModalOverlay } from "@/components/ModalOverlay";
import { useEffect, useMemo, useState } from "react";
import { format, useMessages } from "@/locales";
import {
  BridgeError,
  glossaryReviewBridge,
  type GlossaryReviewReport,
  type GlossaryReviewReportRow,
} from "@/bridge";
import { useTaskStore } from "@/store/useTaskStore";
import {
  hasDismissedCompletionWithFailures,
  hasShownCleanCompletionToast,
  markCleanCompletionToastShown,
  markCompletionWithFailuresDismissed,
  useRunSnapshot,
  useRuntimeStore,
} from "@/store/useRuntimeStore";
import { useToastStore } from "@/store/useToastStore";
import { useModelProfiles } from "@/store/useModelProfilesStore";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/Button";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { ProgressRing } from "@/components/ProgressRing";
import { ChunkStatusGrid } from "@/components/ChunkStatusGrid";
import { LiveRequestCounter } from "@/components/LiveRequestCounter";
import { FailedSubtasksModal } from "@/components/FailedSubtasksModal";
import { CompletionWithFailuresDialog } from "@/components/CompletionWithFailuresDialog";
import styles from "../glossary/RunPage.module.css";
import { formatDuration, NUM, Stat } from "../glossary/RunStats";
import reportStyles from "./ReportModal.module.css";
import { ImportFinalGlossaryConfirmModal } from "./ImportFinalGlossaryConfirmModal";
import {
  importFinalGlossaryToTranslation,
  type ImportFinalGlossaryMode,
} from "./importFinalGlossary";

export function ReviewProgress({ label }: { label: string }) {
  const messages = useMessages();
  const { run } = messages.glossaryReview;
  const failedModalMessages = messages.failedSubtasksModal;
  const navigate = useTaskStore((state) => state.navigate);
  const profiles = useModelProfiles();
  const appSettings = useModuleSettings("app");
  const glossaryReviewSettings = useModuleSettings("glossary_review");
  const snapshot = useRunSnapshot("glossary_review");
  const activeTaskId = useRuntimeStore(
    (state) => state.glossary_review.activeTaskId,
  );

  const [failedModalOpen, setFailedModalOpen] = useState(false);
  const [completionPromptOpen, setCompletionPromptOpen] = useState(false);
  const [report, setReport] = useState<GlossaryReviewReport | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [importingFinal, setImportingFinal] = useState(false);
  const [importDecision, setImportDecision] = useState<{
    outputPath: string;
    existingCount: number;
  } | null>(null);

  useEffect(() => {
    if (!activeTaskId) return;
    if (snapshot.status !== "completed" && snapshot.status !== "failed") return;
    if (snapshot.progress.failed <= 0) return;
    if (hasDismissedCompletionWithFailures(activeTaskId)) return;
    setCompletionPromptOpen(true);
  }, [activeTaskId, snapshot.status, snapshot.progress.failed]);

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

  const activeModelId =
    appSettings.draft?.active_glossary_review_model_id ?? null;
  const activeModel = activeModelId
    ? profiles.profiles.find((p) => p.id === activeModelId)
    : undefined;
  const handleAcceptCompletion = () => {
    if (activeTaskId) markCompletionWithFailuresDismissed(activeTaskId);
    setCompletionPromptOpen(false);
  };

  const handleOpenReport = async () => {
    if (!activeTaskId || snapshot.status !== "completed") return;
    try {
      const next = await glossaryReviewBridge.readReport(activeTaskId);
      setReport(next);
      setReportOpen(true);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        useRuntimeStore.getState().setLastError("glossary_review", error);
      }
    }
  };

  const importFinalFromPath = async (
    outputPath: string,
    mode?: ImportFinalGlossaryMode,
  ) => {
    setImportingFinal(true);
    try {
      const result = await importFinalGlossaryToTranslation(
        outputPath,
        {
          empty: run.importFinalEmpty,
        },
        mode,
      );
      if (result.status === "needs_decision") {
        setImportDecision({
          outputPath,
          existingCount: result.existingCount,
        });
        return;
      }
      useToastStore.getState().push({
        variant: "success",
        title: format(run.importFinalSuccess, { n: result.count }),
      });
      setImportDecision(null);
      navigate({ module: "translation", page: "glossary" });
    } catch (error) {
      useToastStore.getState().push({
        variant: "error",
        title: format(run.importFinalFailed, {
          reason: BridgeError.isBridgeError(error)
            ? `${error.code}: ${error.message}`
            : String(error),
        }),
      });
    } finally {
      setImportingFinal(false);
    }
  };

  const handleUseFinalGlossary = async () => {
    if (!activeTaskId || importingFinal) return;
    try {
      const artifacts = await glossaryReviewBridge.readArtifacts(activeTaskId);
      if (!artifacts.output_path) {
        throw new Error(run.importFinalUnavailable);
      }
      await importFinalFromPath(artifacts.output_path);
    } catch (error) {
      useToastStore.getState().push({
        variant: "error",
        title: format(run.importFinalFailed, {
          reason: BridgeError.isBridgeError(error)
            ? `${error.code}: ${error.message}`
            : String(error),
        }),
      });
    }
  };

  const handleRestoreDeletedRow = async (row: GlossaryReviewReportRow) => {
    if (!activeTaskId) return;
    await glossaryReviewBridge.restoreDeletedReportRow(activeTaskId, {
      src: row.src,
      dst: row.original_dst,
      info: row.original_info,
    });
  };

  const total = snapshot.progress.total;
  const completed = snapshot.progress.completed;
  const failed = snapshot.progress.failed;
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  const settled = completed;
  const roundProgress = snapshot.roundProgress;
  const roundCurrent = roundProgress
    ? Math.max(
        1,
        Math.min(roundProgress.current_round || 1, roundProgress.total_rounds),
      )
    : 0;
  const roundBatchRatio =
    roundProgress && roundProgress.current_total_batches > 0
      ? Math.min(
          1,
          roundProgress.current_completed_batches /
            roundProgress.current_total_batches,
        )
      : 0;
  const roundPercent = roundProgress
    ? Math.floor(
        (Math.min(
          roundProgress.total_rounds,
          Math.max(
            roundProgress.completed_rounds,
            roundProgress.completed_rounds >= roundCurrent
              ? roundProgress.completed_rounds
              : roundProgress.completed_rounds + roundBatchRatio,
          ),
        ) /
          roundProgress.total_rounds) *
          100,
      )
    : null;
  const rawPercent =
    roundPercent ??
    (total > 0
      ? Math.floor((settled / total) * 100)
      : snapshot.status === "completed"
        ? 100
        : 0);
  const percent =
    snapshot.status === "completed" ? rawPercent : Math.min(rawPercent, 99);
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const elapsedSeconds = Math.floor(snapshot.progress.elapsed_seconds);
  const roundDetail = roundProgress
    ? format(run.roundOverall, {
        current: roundCurrent,
        total: roundProgress.total_rounds,
      })
    : undefined;
  const canViewReport = Boolean(activeTaskId && snapshot.status === "completed");
  const showFailures =
    snapshot.failures.length > 0 &&
    snapshot.status !== "running" &&
    snapshot.status !== "pending";
  const showStartupNotice =
    Boolean(activeTaskId) &&
    (snapshot.status === "pending" || snapshot.status === "running") &&
    snapshot.progress.total === 0;

  return (
    <>
      {showFailures ? (
        <div className={styles.failuresPillRow}>
          <Button
            variant="ghost"
            onClick={() => setFailedModalOpen(true)}
          >
            {`${failedModalMessages.triggerPrefix}${snapshot.failures.length}${failedModalMessages.triggerSuffix}`}
          </Button>
          <span className={styles.failuresHint}>
            {failedModalMessages.continueHint}
          </span>
        </div>
      ) : null}

      {failedModalOpen ? (
        <FailedSubtasksModal
          failures={snapshot.failures}
          runtimeConfig={
            activeModel && glossaryReviewSettings.draft
              ? {
                  concurrencyLimit: activeModel.concurrency_limit,
                  rpmLimit: activeModel.rpm_limit,
                  timeoutSeconds: activeModel.timeout_seconds,
                  retryAttempts: glossaryReviewSettings.draft.retry_attempts,
                }
              : undefined
          }
          onClose={() => setFailedModalOpen(false)}
        />
      ) : null}

      {completionPromptOpen ? (
        <CompletionWithFailuresDialog
          failedCount={snapshot.progress.failed}
          onAccept={handleAcceptCompletion}
        />
      ) : null}

      <Panel label={label} labelExtra={activeTaskId}>
        <div className={styles.progressCard}>
          <ProgressRing
            percent={percent}
            completed={settled}
            total={total}
            detail={roundDetail}
          />
          <div className={styles.statGrid}>
            <Stat label={run.stats.completed} value={NUM.format(completed)} />
            <Stat label={run.stats.failed} value={NUM.format(failed)} />
            <Stat label={run.stats.remaining} value={NUM.format(remaining)} />
            <Stat
              label={run.stats.elapsed}
              value={snapshot.isIdle ? "—" : formatDuration(elapsedSeconds)}
            />
            <Stat
              label={run.stats.avgSpeed}
              value={NUM.format(ratePerMinute)}
              delta="/min"
            />
          </div>
        </div>
        {showStartupNotice ? (
          <p className={styles.startupNotice}>{run.startupNotice}</p>
        ) : null}
        {roundProgress ? (
          <div className={styles.roundStrip}>
            <span>
              <b>{run.roundCurrent}</b>
              {roundDetail}
            </span>
            <span>
              {format(run.roundBatches, {
                done: roundProgress.current_completed_batches,
                total: roundProgress.current_total_batches,
              })}
            </span>
          </div>
        ) : null}
        {snapshot.subtasks.length > 0 ? (
          <>
            <LiveRequestCounter
              progress={snapshot.progress}
              label={run.liveCounter.progressLabel}
              inflightLabel={run.liveCounter.inflightLabel}
              longestLabel={run.liveCounter.longestLabel}
            />
            <ChunkStatusGrid
              subtasks={snapshot.subtasks}
              itemLabel={run.liveCounter.chunksLabel}
              statusLabels={messages.status}
            />
          </>
        ) : null}
      </Panel>

      {canViewReport ? (
        <div className={styles.failuresPillRow}>
          <Button
            variant="ghost"
            onClick={() => void handleUseFinalGlossary()}
            disabled={importingFinal}
            title={run.importFinalToTranslation}
          >
            {importingFinal ? run.importingFinal : run.importFinalToTranslation}
          </Button>
          <Button variant="ghost" onClick={handleOpenReport} title={run.viewReport}>
            {run.viewReport}
          </Button>
        </div>
      ) : null}


      {reportOpen && report ? (
        <ReportModal
          report={report}
          onClose={() => setReportOpen(false)}
          onRestoreDelete={handleRestoreDeletedRow}
        />
      ) : null}
      {importDecision ? (
        <ImportFinalGlossaryConfirmModal
          existingCount={importDecision.existingCount}
          labels={{
            title: run.importFinalDecisionTitle,
            body: run.importFinalDecisionBody,
            replaceBadge: run.importFinalReplaceBadge,
            replaceAction: run.importFinalReplaceAction,
            replaceHint: run.importFinalReplaceHint,
            appendBadge: run.importFinalAppendBadge,
            appendAction: run.importFinalAppendAction,
            appendHint: run.importFinalAppendHint,
            cancelAction: run.importFinalCancelAction,
          }}
          onPick={(mode) => {
            const { outputPath } = importDecision;
            setImportDecision(null);
            void importFinalFromPath(outputPath, mode);
          }}
          onCancel={() => setImportDecision(null)}
        />
      ) : null}
    </>
  );
}

interface ReportModalProps {
  report: GlossaryReviewReport;
  onClose: () => void;
  onRestoreDelete: (row: GlossaryReviewReportRow) => Promise<void>;
}

function ReportModal({ report, onClose, onRestoreDelete }: ReportModalProps) {
  const messages = useMessages();
  const labels = messages.glossaryReview.report;
  useEscapeKey(onClose);
  const [query, setQuery] = useState("");
  const [action, setAction] = useState("all");
  const [restoring, setRestoring] = useState<string | null>(null);
  const [restored, setRestored] = useState<ReadonlySet<string>>(() => new Set());
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return report.rows.filter((row) => {
      if (action !== "all" && row.action !== action) return false;
      if (!q) return true;
      return [
        row.src,
        row.original_dst,
        row.suggested_dst,
        row.original_info,
        row.suggested_info,
        row.reason,
        row.context_excerpt,
      ]
        .join("\n")
        .toLowerCase()
        .includes(q);
    });
  }, [action, query, report.rows]);
  const restoreKey = (row: GlossaryReviewReportRow) =>
    `${row.round}-${row.row_index}-${row.src}`;
  const actionLabel = (row: GlossaryReviewReportRow) => {
    switch (row.action) {
      case "modify":
        return labels.actionModify;
      case "delete":
        return labels.actionDelete;
      case "category":
        return labels.actionCategory;
      case "modify_category":
        return labels.actionModifyCategory;
      case "name_consistency":
        return labels.actionNameConsistency;
      default:
        return row.action;
    }
  };
  const restoreDelete = async (row: GlossaryReviewReportRow) => {
    const key = restoreKey(row);
    setRestoring(key);
    try {
      await onRestoreDelete(row);
      setRestored((prev) => new Set(prev).add(key));
      useToastStore.getState().push({
        variant: "success",
        title: labels.restoreSuccess,
      });
    } catch (error) {
      useToastStore.getState().push({
        variant: "error",
        title: format(labels.restoreFailed, {
          reason: BridgeError.isBridgeError(error)
            ? `${error.code}: ${error.message}`
            : String(error),
        }),
      });
    } finally {
      setRestoring(null);
    }
  };

  return (
    <ModalOverlay className={reportStyles.overlay} role="dialog" aria-modal="true">
      <div className={reportStyles.modal}>
        <header className={reportStyles.header}>
          <h2>{labels.title}</h2>
          <button type="button" onClick={onClose}>
            {labels.close}
          </button>
        </header>
        <div className={reportStyles.toolbar}>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={labels.searchPlaceholder}
          />
          <select value={action} onChange={(event) => setAction(event.target.value)}>
            <option value="all">{labels.actionAll}</option>
            <option value="modify">{labels.actionModify}</option>
            <option value="delete">{labels.actionDelete}</option>
            <option value="category">{labels.actionCategory}</option>
            <option value="modify_category">{labels.actionModifyCategory}</option>
            <option value="name_consistency">{labels.actionNameConsistency}</option>
          </select>
        </div>
        {rows.length === 0 ? (
          <p className={reportStyles.empty}>{labels.empty}</p>
        ) : (
          <div className={reportStyles.tableWrap}>
            <table className={reportStyles.table}>
              <thead>
                <tr>
                  <th>{labels.columns.round}</th>
                  <th>{labels.columns.action}</th>
                  <th>{labels.columns.src}</th>
                  <th>{labels.columns.originalDst}</th>
                  <th>{labels.columns.suggestedDst}</th>
                  <th>{labels.columns.originalInfo}</th>
                  <th>{labels.columns.suggestedInfo}</th>
                  <th>{labels.columns.reason}</th>
                  <th>{labels.columns.actions}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const key = restoreKey(row);
                  const canRestore =
                    row.action === "delete" && !restored.has(key);
                  return (
                    <tr key={`${row.round}-${row.row_index}-${row.action}`}>
                      <td>{row.round}</td>
                      <td>{actionLabel(row)}</td>
                      <td>{row.src}</td>
                      <td>{row.original_dst}</td>
                      <td>{row.suggested_dst}</td>
                      <td>{row.original_info}</td>
                      <td>{row.suggested_info}</td>
                      <td>{row.reason}</td>
                      <td>
                        {row.action === "delete" ? (
                          <button
                            type="button"
                            className={reportStyles.restoreButton}
                            disabled={!canRestore || restoring === key}
                            onClick={() => void restoreDelete(row)}
                          >
                            {restored.has(key)
                              ? labels.restored
                              : restoring === key
                                ? labels.restoring
                                : labels.restoreDelete}
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </ModalOverlay>
  );
}
