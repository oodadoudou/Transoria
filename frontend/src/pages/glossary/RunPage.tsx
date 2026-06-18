import { useEffect, useState } from "react";
import { glossaryBridge } from "@/bridge";
import { useMessages, useI18n } from "@/locales";
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
import { RequestLogPanel } from "@/components/RequestLogPanel";
import {
  QuickSwitchModal,
  type QuickSwitchItem,
} from "@/components/QuickSwitchModal";
import styles from "./RunPage.module.css";

const NUM = new Intl.NumberFormat("en");

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function RunPage() {
  const messages = useMessages();
  const { run } = messages.glossary;
  const failedModalMessages = messages.failedSubtasksModal;
  const navigate = useTaskStore((state) => state.navigate);
  const profiles = useModelProfiles();
  const prompts = usePromptPresets("glossary");
  const promptSlice = prompts.glossary;
  const appSettings = useModuleSettings("app");
  const glossarySettings = useModuleSettings("glossary");
  const glossaryReviewSettings = useModuleSettings("glossary_review");
  const snapshot = useRunSnapshot("glossary");
  const reviewSnapshot = useRunSnapshot("glossary_review");
  const activeTaskId = useRuntimeStore((state) => state.glossary.activeTaskId);
  usePollRunSnapshot("glossary");
  usePollRunSnapshot("glossary_review");

  // Refresh active-task state on mount so re-entering the page after
  // navigating away picks up the live backend status (poll only ticks
  // every 2s, and snapshot in store can be stale right after remount).
  useEffect(() => {
    void useRuntimeStore.getState().refreshActiveTask("glossary");
    void useRuntimeStore.getState().refreshActiveTask("glossary_review");
  }, []);

  const [failedModalOpen, setFailedModalOpen] = useState(false);
  const [completionPromptOpen, setCompletionPromptOpen] = useState(false);
  const [hasReviewableArtifacts, setHasReviewableArtifacts] = useState(false);
  const [sendingToReview, setSendingToReview] = useState(false);

  // Same as translation: fire even when every chunk failed so the
  // user is told that Continue can retry remaining chunks.
  useEffect(() => {
    if (!activeTaskId) return;
    if (snapshot.status !== "completed" && snapshot.status !== "failed") {
      return;
    }
    if (snapshot.progress.failed <= 0) return;
    if (hasDismissedCompletionWithFailures(activeTaskId)) return;
    setCompletionPromptOpen(true);
  }, [
    activeTaskId,
    snapshot.status,
    snapshot.progress.failed,
    snapshot.progress.completed,
  ]);

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
    if (!activeTaskId || snapshot.status !== "completed") {
      setHasReviewableArtifacts(false);
      return;
    }
    let cancelled = false;
    setHasReviewableArtifacts(false);
    glossaryBridge
      .readArtifacts(activeTaskId)
      .then((artifacts) => {
        if (cancelled) return;
        setHasReviewableArtifacts(
          Boolean(artifacts.combined_artifact?.xlsx_path) ||
            artifacts.per_novel_artifacts.some((item) =>
              Boolean(item.xlsx_path),
            ),
        );
      })
      .catch(() => {
        if (!cancelled) setHasReviewableArtifacts(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTaskId, snapshot.status]);

  const handleAcceptCompletion = () => {
    if (activeTaskId) markCompletionWithFailuresDismissed(activeTaskId);
    setCompletionPromptOpen(false);
  };

  const activeModelId = appSettings.draft?.active_glossary_model_id ?? null;
  const activeModel = activeModelId
    ? profiles.profiles.find((p) => p.id === activeModelId)
    : undefined;
  const locale = useI18n((state) => state.locale);
  const localeDefaultPromptId = `default-glossary-${locale}`;
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
    .filter(
      (preset) => !preset.is_system || preset.id === localeDefaultPromptId,
    )
    .map((preset) => ({
      id: preset.id,
      name: preset.name,
      description: preset.description,
    }));

  const handleSelectModel = async (id: string) => {
    await useModelProfilesStore.getState().selectActive("glossary", id);
  };
  const handleSelectPrompt = async (id: string) => {
    await prompts.selectActive("glossary", id);
  };

  const total = snapshot.progress.total;
  const completed = snapshot.progress.completed;
  const failed = snapshot.progress.failed;
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  // Floor (not round) so a near-finished run like 400/402 renders 99%,
  // not a misleading 100%, until every subtask actually completes.
  // SKIPPED split parents are diagnostics; progress tracks the real
  // work units that remain after split children are created.
  const settled = completed;
  const percent =
    total > 0
      ? Math.floor((settled / total) * 100)
      : snapshot.status === "completed"
        ? 100
        : 0;
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const elapsedSeconds = Math.floor(snapshot.progress.elapsed_seconds);
  const showFailures =
    snapshot.failures.length > 0 &&
    snapshot.status !== "running" &&
    snapshot.status !== "pending";
  const showStartupNotice =
    Boolean(activeTaskId) &&
    (snapshot.status === "pending" || snapshot.status === "running") &&
    snapshot.progress.total === 0;
  const glossaryOutputFolder = glossarySettings.draft?.output_folder.trim() ?? "";
  const glossaryNovelBackground =
    glossarySettings.draft?.novel_background ?? "";
  const reviewBusy =
    !reviewSnapshot.isIdle &&
    (reviewSnapshot.status === "pending" ||
      reviewSnapshot.status === "running" ||
      reviewSnapshot.status === "stopping" ||
      reviewSnapshot.status === "paused");
  const showSendToReview =
    snapshot.status === "completed" &&
    hasReviewableArtifacts &&
    glossaryOutputFolder.length > 0;

  const handleSendToReview = async () => {
    if (!glossaryOutputFolder || !hasReviewableArtifacts || reviewBusy) return;
    setSendingToReview(true);
    try {
      glossaryReviewSettings.update("input_folder", glossaryOutputFolder);
      glossaryReviewSettings.update(
        "novel_background",
        glossaryNovelBackground,
      );
      glossaryReviewSettings.update("selected_xlsx_path", "");
      glossaryReviewSettings.update("selected_reference_paths", []);
      await glossaryReviewSettings.saveNow();
      navigate({ module: "glossary-review", page: "settings" });
    } finally {
      setSendingToReview(false);
    }
  };

  return (
    <>
      <Panel title={run.title} subtitle={run.sub} />

      <RunErrorBanner kind="glossary" />

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
          onManage={() => navigate({ module: "glossary", page: "prompt" })}
        />
      ) : null}

      {showFailures ? (
        <div className={styles.failuresPillRow}>
          <Pill
            variant="ghost"
            onClick={() => setFailedModalOpen(true)}
          >
            {`${failedModalMessages.triggerPrefix}${snapshot.failures.length}${failedModalMessages.triggerSuffix}`}
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
          runtimeConfig={
            activeModel && glossarySettings.draft
              ? {
                  concurrencyLimit: activeModel.concurrency_limit,
                  rpmLimit: activeModel.rpm_limit,
                  timeoutSeconds: activeModel.timeout_seconds,
                  retryAttempts: glossarySettings.draft.request_retry_attempts,
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
        {showStartupNotice ? (
          <p className={styles.startupNotice}>{run.startupNotice}</p>
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

      {showSendToReview ? (
        <div className={styles.followupActions}>
          <Pill
            type="button"
            onClick={() => void handleSendToReview()}
            disabled={reviewBusy || sendingToReview}
          >
            {run.sendToReview}
          </Pill>
        </div>
      ) : null}

      <RunControls kind="glossary">
        <RequestLogPanel
          kind="glossary"
          taskId={activeTaskId}
          taskStatus={snapshot.status}
        />
      </RunControls>
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
