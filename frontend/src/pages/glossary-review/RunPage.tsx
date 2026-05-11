import { useEffect, useMemo, useState } from "react";
import { format, useMessages, useI18n } from "@/locales";
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
  usePollRunSnapshot,
  useRuntimeStore,
} from "@/store/useRuntimeStore";
import { useToastStore } from "@/store/useToastStore";
import {
  useModelProfiles,
  useModelProfilesStore,
} from "@/store/useModelProfilesStore";
import { usePromptPresets } from "@/store/usePromptPresetsStore";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { ProgressRing } from "@/components/ProgressRing";
import { ChunkStatusGrid } from "@/components/ChunkStatusGrid";
import { LiveRequestCounter } from "@/components/LiveRequestCounter";
import { RunErrorBanner } from "@/components/RunErrorBanner";
import { FailedSubtasksModal } from "@/components/FailedSubtasksModal";
import { CompletionWithFailuresDialog } from "@/components/CompletionWithFailuresDialog";
import { RunControls } from "@/components/RunControls";
import {
  QuickSwitchModal,
  type QuickSwitchItem,
} from "@/components/QuickSwitchModal";
import styles from "../glossary/RunPage.module.css";
import reportStyles from "./ReportModal.module.css";
import { ImportFinalGlossaryConfirmModal } from "./ImportFinalGlossaryConfirmModal";
import {
  importFinalGlossaryToTranslation,
  type ImportFinalGlossaryMode,
} from "./importFinalGlossary";

const NUM = new Intl.NumberFormat("en");

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function RunPage() {
  const messages = useMessages();
  const { run } = messages.glossaryReview;
  const failedModalMessages = messages.failedSubtasksModal;
  const navigate = useTaskStore((state) => state.navigate);
  const profiles = useModelProfiles();
  const prompts = usePromptPresets("glossary_review");
  const promptSlice = prompts.glossary_review;
  const appSettings = useModuleSettings("app");
  const snapshot = useRunSnapshot("glossary_review");
  const activeTaskId = useRuntimeStore(
    (state) => state.glossary_review.activeTaskId,
  );
  usePollRunSnapshot("glossary_review");

  useEffect(() => {
    void useRuntimeStore.getState().refreshActiveTask("glossary_review");
  }, []);

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
  const locale = useI18n((state) => state.locale);
  const localeDefaultPromptId = `default-glossary-review-${locale}`;
  const displayedPromptId =
    promptSlice.activeId ??
    (promptSlice.presets.some((p) => p.id === localeDefaultPromptId)
      ? localeDefaultPromptId
      : null);
  const activePrompt = displayedPromptId
    ? promptSlice.presets.find((p) => p.id === displayedPromptId)
    : undefined;

  const [switchOpen, setSwitchOpen] = useState<"model" | "prompt" | null>(null);

  const modelItems: QuickSwitchItem[] = profiles.profiles
    .filter((p) => p.api_key_status !== "missing")
    .map((p) => ({
      id: p.id,
      name: p.display_name,
      description: p.model_id,
    }));
  const promptItems: QuickSwitchItem[] = promptSlice.presets
    .filter((preset) => !preset.is_system || preset.id === localeDefaultPromptId)
    .map((preset) => ({
      id: preset.id,
      name: preset.name,
      description: preset.description,
    }));

  const handleSelectModel = async (id: string) => {
    await useModelProfilesStore.getState().selectActive("glossary_review", id);
  };
  const handleSelectPrompt = async (id: string) => {
    await prompts.selectActive("glossary_review", id);
  };

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
  const skipped = snapshot.progress.skipped;
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  const settled = completed + skipped;
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
    roundPercent ?? (total > 0 ? Math.floor((settled / total) * 100) : 0);
  const percent =
    snapshot.status === "completed" ? rawPercent : Math.min(rawPercent, 99);
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const elapsedSeconds = Math.floor(snapshot.progress.elapsed_seconds);
  const isPreparing =
    !snapshot.isIdle && snapshot.status === "running" && total === 0;
  const hasProgressBlocks = snapshot.subtasks.length > 0 || total > 0;
  const roundDetail = roundProgress
    ? format(run.roundOverall, {
        current: roundCurrent,
        total: roundProgress.total_rounds,
      })
    : undefined;
  const canViewReport = Boolean(activeTaskId && snapshot.status === "completed");
  const showFailures = snapshot.failures.length > 0;

  return (
    <>
      <Panel title={run.title} subtitle={run.sub} />

      <RunErrorBanner kind="glossary_review" />

      <Panel label={run.activeConfig}>
        <div className={styles.activeStrip}>
          <ActiveCard
            label={run.activeModel}
            primary={activeModel?.display_name ?? "—"}
            secondary={activeModel?.model_id ?? ""}
            onSwitch={() => setSwitchOpen("model")}
            switchLabel={run.switch}
          />
          <ActiveCard
            label={run.activePrompt}
            primary={activePrompt?.name ?? "—"}
            secondary={activePrompt?.description ?? ""}
            onSwitch={() => setSwitchOpen("prompt")}
            switchLabel={run.switch}
          />
        </div>
      </Panel>

      {switchOpen === "model" ? (
        <QuickSwitchModal
          title={messages.quickSwitch.titleModel}
          items={modelItems}
          activeId={activeModelId}
          emptyMessage={messages.quickSwitch.emptyModel}
          onSelect={handleSelectModel}
          onClose={() => setSwitchOpen(null)}
          onManage={() => navigate({ module: "model", page: "general" })}
        />
      ) : null}

      {switchOpen === "prompt" ? (
        <QuickSwitchModal
          title={messages.quickSwitch.titlePrompt}
          items={promptItems}
          activeId={displayedPromptId}
          emptyMessage={messages.quickSwitch.emptyPrompt}
          onSelect={handleSelectPrompt}
          onClose={() => setSwitchOpen(null)}
          onManage={() =>
            navigate({ module: "glossary-review", page: "prompt" })
          }
        />
      ) : null}

      {showFailures ? (
        <div className={styles.failuresPillRow}>
          <Pill
            variant="ghost"
            onClick={() => setFailedModalOpen(true)}
            title={
              snapshot.status === "running"
                ? failedModalMessages.autoFixingHint
                : undefined
            }
          >
            {snapshot.status === "running"
              ? `${failedModalMessages.autoFixingPrefix}${snapshot.failures.length}${failedModalMessages.autoFixingSuffix}`
              : `${failedModalMessages.triggerPrefix}${snapshot.failures.length}${failedModalMessages.triggerSuffix}`}
          </Pill>
          {snapshot.status === "failed" ||
          snapshot.status === "stopped" ||
          snapshot.status === "paused" ||
          (snapshot.status === "completed" && snapshot.progress.failed > 0) ? (
            <span className={styles.failuresHint}>
              {failedModalMessages.continueHint}
            </span>
          ) : null}
        </div>
      ) : null}

      {failedModalOpen ? (
        <FailedSubtasksModal
          failures={snapshot.failures}
          onClose={() => setFailedModalOpen(false)}
        />
      ) : null}

      {completionPromptOpen ? (
        <CompletionWithFailuresDialog
          failedCount={snapshot.progress.failed}
          onAccept={handleAcceptCompletion}
        />
      ) : null}

      <Panel label={run.progress}>
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
        {hasProgressBlocks ? (
          <>
            <LiveRequestCounter
              progress={snapshot.progress}
              label={run.liveCounter.progressLabel}
              inflightLabel={run.liveCounter.inflightLabel}
              longestLabel={run.liveCounter.longestLabel}
            />
            <ChunkStatusGrid
              subtasks={snapshot.subtasks}
              progress={snapshot.progress}
              itemLabel={run.liveCounter.chunksLabel}
            />
          </>
        ) : isPreparing ? (
          <div className={styles.preparingNotice}>{run.preparing}</div>
        ) : null}
      </Panel>

      {canViewReport ? (
        <div className={styles.failuresPillRow}>
          <Pill
            variant="ghost"
            onClick={() => void handleUseFinalGlossary()}
            disabled={importingFinal}
            title={run.importFinalToTranslation}
          >
            {importingFinal ? run.importingFinal : run.importFinalToTranslation}
          </Pill>
          <Pill variant="ghost" onClick={handleOpenReport} title={run.viewReport}>
            {run.viewReport}
          </Pill>
        </div>
      ) : null}

      <RunControls kind="glossary_review" />

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

interface ActiveCardProps {
  label: string;
  primary: string;
  secondary: string;
  onSwitch: () => void;
  switchLabel: string;
}

function ActiveCard({
  label,
  primary,
  secondary,
  onSwitch,
  switchLabel,
}: ActiveCardProps) {
  return (
    <div className={styles.activeCard}>
      <div className={styles.activeMeta}>
        <span className={styles.activeLabel}>{label}</span>
        <span className={styles.activePrimary}>{primary}</span>
        {secondary ? (
          <span className={styles.activeSecondary}>{secondary}</span>
        ) : null}
      </div>
      <button type="button" className={styles.activeSwitch} onClick={onSwitch}>
        {switchLabel}
      </button>
    </div>
  );
}

interface StatProps {
  label: string;
  value: string;
  delta?: string;
}

function Stat({ label, value, delta }: StatProps) {
  return (
    <div className={styles.stat}>
      <div className={styles.statLabel}>{label}</div>
      <b className="tnum">
        {value}
        {delta ? <span className={styles.delta}>{delta}</span> : null}
      </b>
    </div>
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
    <div className={reportStyles.overlay} role="dialog" aria-modal="true">
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
                      <td>{row.action}</td>
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
    </div>
  );
}
