import { useEffect, useMemo, useState } from "react";
import { useMessages, useI18n } from "@/locales";
import { BridgeError, glossaryReviewBridge, type GlossaryReviewReport } from "@/bridge";
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
  const [rerunPending, setRerunPending] = useState(false);
  const [report, setReport] = useState<GlossaryReviewReport | null>(null);
  const [reportOpen, setReportOpen] = useState(false);

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

  const handleRerunFailed = async () => {
    if (!activeTaskId || rerunPending) return;
    setRerunPending(true);
    try {
      await glossaryReviewBridge.continueTask(activeTaskId);
      setCompletionPromptOpen(false);
      await useRuntimeStore.getState().refreshActiveTask("glossary_review");
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        useRuntimeStore.getState().setLastError("glossary_review", error);
      }
    } finally {
      setRerunPending(false);
    }
  };

  const handleOpenReport = async () => {
    if (!activeTaskId) return;
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

  const total = snapshot.progress.total;
  const completed = snapshot.progress.completed;
  const failed = snapshot.progress.failed;
  const skipped = snapshot.progress.skipped;
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  const settled = completed + skipped;
  const percent = total > 0 ? Math.floor((settled / total) * 100) : 0;
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const elapsedSeconds = Math.floor(snapshot.progress.elapsed_seconds);

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

      {snapshot.failures.length > 0 ? (
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
          <span className={styles.failuresHint}>
            {failedModalMessages.continueHint}
          </span>
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
          rerunPending={rerunPending}
          onRerun={handleRerunFailed}
          onAccept={handleAcceptCompletion}
        />
      ) : null}

      <Panel label={run.progress}>
        <div className={styles.progressCard}>
          <ProgressRing percent={percent} completed={settled} total={total} />
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
        {snapshot.subtasks.length > 0 ? (
          <>
            <LiveRequestCounter
              progress={snapshot.progress}
              label={run.liveCounter.progressLabel}
              inflightLabel={run.liveCounter.inflightLabel}
            />
            <ChunkStatusGrid
              subtasks={snapshot.subtasks}
              itemLabel={run.liveCounter.chunksLabel}
            />
          </>
        ) : null}
      </Panel>

      <div className={styles.failuresPillRow}>
        <Pill
          variant="ghost"
          onClick={handleOpenReport}
          title={activeTaskId ? run.viewReport : run.reportUnavailable}
        >
          {run.viewReport}
        </Pill>
      </div>

      <RunControls kind="glossary_review" />

      {reportOpen && report ? (
        <ReportModal report={report} onClose={() => setReportOpen(false)} />
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
}

function ReportModal({ report, onClose }: ReportModalProps) {
  const messages = useMessages();
  const labels = messages.glossaryReview.report;
  const [query, setQuery] = useState("");
  const [action, setAction] = useState("all");
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
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={`${row.round}-${row.row_index}-${row.action}`}>
                    <td>{row.round}</td>
                    <td>{row.action}</td>
                    <td>{row.src}</td>
                    <td>{row.original_dst}</td>
                    <td>{row.suggested_dst}</td>
                    <td>{row.original_info}</td>
                    <td>{row.suggested_info}</td>
                    <td>{row.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
