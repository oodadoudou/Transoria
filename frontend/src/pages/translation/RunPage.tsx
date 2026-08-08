import { useEffect, useState } from "react";
import { useMessages, useI18n } from "@/locales";
import {
  DEFAULT_PROOFREADING_FILTERS,
  useTaskStore,
} from "@/store/useTaskStore";
import {
  hasDismissedCompletionWithFailures,
  hasShownCleanCompletionToast,
  markCleanCompletionToastShown,
  hasShownLowConfToast,
  markLowConfToastShown,
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
import { useModuleSettings, useSettingsStore } from "@/store/useSettingsStore";
import {
  useWorkflowPresets,
  useWorkflowPresetsStore,
} from "@/store/useWorkflowPresetsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { ProgressRing } from "@/components/ProgressRing";
import { ChunkStatusGrid } from "@/components/ChunkStatusGrid";
import { LiveRequestCounter } from "@/components/LiveRequestCounter";
import { RunErrorBanner } from "@/components/RunErrorBanner";
import { FailedSubtasksModal } from "@/components/FailedSubtasksModal";
import { CompletionWithFailuresDialog } from "@/components/CompletionWithFailuresDialog";
import { RunControls } from "@/components/RunControls";
import { GuidedEmptyState } from "@/components/GuidedEmptyState";
import { RequestLogPanel } from "@/components/RequestLogPanel";
import { RunConfigBar } from "@/components/RunConfigBar";
import {
  QuickSwitchModal,
  type QuickSwitchItem,
} from "@/components/QuickSwitchModal";
import styles from "./RunPage.module.css";

const NUM = new Intl.NumberFormat("en");
const NEXT_STEP_DISMISSED_KEY = "transoria.translation.next-step.dismissed";
type NextStepKind = "model" | "start" | "proofreading";

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function loadNextStepDismissed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(NEXT_STEP_DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

function saveNextStepDismissed(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(NEXT_STEP_DISMISSED_KEY, "1");
  } catch {
    // Optional UI hint; storage failure should not block the Run page.
  }
}

export function RunPage() {
  const messages = useMessages();
  const { run } = messages.translation;
  const failedModalMessages = messages.failedSubtasksModal;
  const navigate = useTaskStore((state) => state.navigate);
  const openProofreadingTask = useTaskStore((state) => state.openProofreadingTask);
  const profiles = useModelProfiles();
  const prompts = usePromptPresets("translation");
  const workflow = useWorkflowPresets("translation");
  const workflowSlice = workflow.translation;
  const promptSlice = prompts.translation;
  const appSettings = useModuleSettings("app");
  const translationSettings = useModuleSettings("translation");
  const snapshot = useRunSnapshot("translation");
  const activeTaskId = useRuntimeStore(
    (state) => state.translation.activeTaskId,
  );
  const recentTranslationTask = useRuntimeStore(
    (state) => state.translation.header,
  );
  usePollRunSnapshot("translation");

  // Refresh active-task state on mount so re-entering the page after
  // navigating away picks up the live backend status without waiting
  // for the next 2-second poll tick.
  useEffect(() => {
    void useRuntimeStore.getState().refreshActiveTask("translation");
  }, []);

  const [failedModalOpen, setFailedModalOpen] = useState(false);
  const [completionPromptOpen, setCompletionPromptOpen] = useState(false);
  const [legacyNextStepDismissed, setLegacyNextStepDismissed] = useState(
    loadNextStepDismissed,
  );

  // Auto-open the completion-with-failures dialog the first time we
  // see a terminal status with failures for this task. Fires even when
  // every chunk failed (progress.completed == 0) so the user is always
  // told that Continue can retry remaining chunks. Dismissal is tracked
  // module-level so navigating away and back doesn't re-open.
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

  // Celebratory toast on truly clean completion (no failures, some
  // work done). Per-task-id dedupe so the toast doesn't re-fire on
  // tab switches; the cache-cleanup mirror keeps the snapshot at
  // status=completed indefinitely.
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

  // Reminder toast when a clean run still has low-confidence segments
  // the proofreading page should review. Fires once per task; respects
  // the same per-task dedupe pattern as the celebratory toast.
  useEffect(() => {
    if (!activeTaskId) return;
    if (snapshot.status !== "completed" && snapshot.status !== "failed") {
      return;
    }
    if (snapshot.progress.failed > 0) return;
    if (snapshot.progress.pending + snapshot.progress.running > 0) return;
    if (snapshot.lowConfidence.total <= 0) return;
    if (hasShownLowConfToast(activeTaskId)) return;
    markLowConfToastShown(activeTaskId);
    const detailParts: string[] = [];
    detailParts.push(
      messages.runLowConfReminder.totalLine.replace(
        "{n}",
        String(snapshot.lowConfidence.total),
      ),
    );
    if (snapshot.lowConfidence.sourceResidue > 0) {
      detailParts.push(
        messages.runLowConfReminder.residueLine.replace(
          "{n}",
          String(snapshot.lowConfidence.sourceResidue),
        ),
      );
    }
    useToastStore.getState().push({
      variant: "warning",
      title: messages.runLowConfReminder.title,
      detail: detailParts.join(" · "),
      durationMs: 8000,
    });
  }, [
    activeTaskId,
    snapshot.status,
    snapshot.progress.failed,
    snapshot.progress.pending,
    snapshot.progress.running,
    snapshot.lowConfidence.total,
    snapshot.lowConfidence.sourceResidue,
    messages.runLowConfReminder,
  ]);

  const handleAcceptCompletion = () => {
    if (activeTaskId) markCompletionWithFailuresDismissed(activeTaskId);
    setCompletionPromptOpen(false);
  };

  const activeModelId = appSettings.draft?.active_translation_model_id ?? null;
  const activeModel = activeModelId
    ? profiles.profiles.find((p) => p.id === activeModelId)
    : undefined;
  const hasUsableModel =
    activeModel?.api_key_status === "present" ||
    activeModel?.api_key_status === "from_env";
  const locale = useI18n((state) => state.locale);
  const localeDefaultPromptId = `default-translation-${locale}`;
  const displayedPromptId =
    promptSlice.activeId ??
    (promptSlice.presets.some((p) => p.id === localeDefaultPromptId)
      ? localeDefaultPromptId
      : null);
  const activePrompt = displayedPromptId
    ? promptSlice.presets.find((p) => p.id === displayedPromptId)
    : undefined;

  const [switchOpen, setSwitchOpen] = useState<"preset" | "model" | "prompt" | null>(null);

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
  const activePreset = workflowSlice.matchedId
    ? workflowSlice.presets.find((preset) => preset.id === workflowSlice.matchedId)
    : undefined;
  const sourceLanguage = translationSettings.draft?.source_language ?? "kr";
  const targetLanguage = translationSettings.draft?.target_language ?? "zh";
  const presetItems: QuickSwitchItem[] = workflowSlice.presets.map((preset) => {
    const model = profiles.profiles.find((item) => item.id === preset.model_profile_id);
    const prompt = promptSlice.presets.find((item) => item.id === preset.prompt_preset_id);
    return {
      id: preset.id,
      name: preset.name,
      description: [
        `${messages.language.options[preset.source_language]} → ${
          messages.language.options[preset.target_language]
        }`,
        model?.display_name ?? preset.model_profile_id,
        prompt?.name ?? preset.prompt_preset_id,
      ].join(" · "),
    };
  });

  const handleSelectModel = async (id: string) => {
    await useModelProfilesStore.getState().selectActive("translation", id);
    await useWorkflowPresetsStore.getState().refresh("translation");
  };
  const handleSelectPrompt = async (id: string) => {
    await prompts.selectActive("translation", id);
    await useWorkflowPresetsStore.getState().refresh("translation");
  };
  const handleSelectPreset = async (id: string) => {
    await workflow.applyPreset("translation", id);
  };
  const persistNextStepDismissed = () => {
    saveNextStepDismissed();
    setLegacyNextStepDismissed(true);
    if (appSettings.draft?.workflow_next_step_dismissed) return;
    const settingsStore = useSettingsStore.getState();
    settingsStore.updateField("app", "workflow_next_step_dismissed", true);
    void settingsStore.saveNow("app");
  };

  useEffect(() => {
    if (!legacyNextStepDismissed) return;
    if (!appSettings.isHydrated) return;
    if (appSettings.draft?.workflow_next_step_dismissed) return;
    const settingsStore = useSettingsStore.getState();
    settingsStore.updateField("app", "workflow_next_step_dismissed", true);
    void settingsStore.saveNow("app");
  }, [
    appSettings.draft?.workflow_next_step_dismissed,
    appSettings.isHydrated,
    legacyNextStepDismissed,
  ]);

  const nextStepDismissed =
    legacyNextStepDismissed ||
    Boolean(appSettings.draft?.workflow_next_step_dismissed);

  const dismissNextStep = () => {
    persistNextStepDismissed();
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
  const showReviewRequired =
    Boolean(activeTaskId) &&
    (snapshot.status === "completed" || snapshot.status === "failed") &&
    failed === 0 &&
    remaining === 0 &&
    snapshot.lowConfidence.total > 0;
  const showFailures =
    snapshot.failures.length > 0 &&
    snapshot.status !== "running" &&
    snapshot.status !== "pending";
  const showStartupNotice =
    Boolean(activeTaskId) &&
    (snapshot.status === "pending" || snapshot.status === "running") &&
    snapshot.progress.total === 0;
  const showProgressEmpty = !activeTaskId && snapshot.subtasks.length === 0;
  const nextStepReady = profiles.hydrated && appSettings.isHydrated;
  const nextStepKind: NextStepKind | null = !nextStepReady
    ? null
    : !hasUsableModel
      ? "model"
      : !recentTranslationTask
        ? "start"
        : null;
  return (
    <>
      <Panel title={run.title} subtitle={run.sub} />

      <RunErrorBanner kind="translation" />

      {!nextStepDismissed && nextStepKind ? (
        <NextStepCard
          kind={nextStepKind}
          riskCount={snapshot.lowConfidence.total}
          onDismiss={dismissNextStep}
          onConfigureModel={() => navigate({ module: "model", page: "general" })}
          onOpenSettings={() =>
            navigate({ module: "translation", page: "settings" })
          }
          onOpenProofreading={() => {
            if (!activeTaskId) return;
            openProofreadingTask(activeTaskId, DEFAULT_PROOFREADING_FILTERS);
          }}
        />
      ) : null}

      <Panel label={run.activeConfig}>
        <RunConfigBar
          items={[
            {
              id: "preset",
              label: messages.runConfig.preset,
              primary:
                workflowSlice.presets.length === 0
                  ? messages.runConfig.noPreset
                  : activePreset?.name ?? messages.runConfig.customPreset,
              secondary:
                workflowSlice.presets.length === 0
                  ? messages.runConfig.noPresetHint
                  : `${messages.language.options[sourceLanguage]} → ${
                      messages.language.options[targetLanguage]
                    }`,
              actionLabel: messages.runConfig.switchAction,
              onClick: () => setSwitchOpen("preset"),
            },
            {
              id: "model",
              label: messages.runConfig.model,
              primary: activeModel?.display_name ?? messages.runConfig.missingModel,
              secondary: activeModel?.model_id ?? "",
              actionLabel: messages.runConfig.switchAction,
              onClick: () => setSwitchOpen("model"),
            },
            {
              id: "prompt",
              label: messages.runConfig.prompt,
              primary: activePrompt?.name ?? messages.runConfig.missingPrompt,
              secondary: activePrompt?.description ?? "",
              actionLabel: messages.runConfig.switchAction,
              onClick: () => setSwitchOpen("prompt"),
            },
          ]}
        />
      </Panel>

      {switchOpen === "preset" ? (
        <QuickSwitchModal
          title={messages.quickSwitch.titlePreset}
          items={presetItems}
          activeId={workflowSlice.matchedId}
          emptyMessage={messages.quickSwitch.emptyPreset}
          onSelect={handleSelectPreset}
          onClose={() => setSwitchOpen(null)}
          onManage={() => navigate({ module: "translation", page: "presets" })}
        />
      ) : null}

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
          onManage={() => navigate({ module: "translation", page: "prompt" })}
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
            activeModel && translationSettings.draft
              ? {
                  concurrencyLimit: activeModel.concurrency_limit,
                  rpmLimit: activeModel.rpm_limit,
                  timeoutSeconds: activeModel.timeout_seconds,
                  retryAttempts:
                    translationSettings.draft.request_retry_attempts,
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
        {activeTaskId ? (
          <div className={styles.taskIdStrip}>
            <span>{run.taskId}</span>
            <code>{activeTaskId}</code>
          </div>
        ) : null}
        {showProgressEmpty ? (
          <GuidedEmptyState
            label={run.emptyState.label}
            title={run.emptyState.title}
            body={run.emptyState.body}
            actionLabel={run.emptyState.action}
            onAction={() =>
              navigate({ module: "translation", page: "settings" })
            }
          />
        ) : (
          <>
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
            {showReviewRequired && activeTaskId ? (
              <NextStepCard
                kind="proofreading"
                riskCount={snapshot.lowConfidence.total}
                persistent
                onDismiss={() => undefined}
                onConfigureModel={() =>
                  navigate({ module: "model", page: "general" })
                }
                onOpenSettings={() =>
                  navigate({ module: "translation", page: "settings" })
                }
                onOpenProofreading={() =>
                  openProofreadingTask(
                    activeTaskId,
                    DEFAULT_PROOFREADING_FILTERS,
                  )
                }
              />
            ) : null}
          </>
        )}
      </Panel>

      <RunControls kind="translation">
        <RequestLogPanel
          kind="translation"
          taskId={activeTaskId}
          taskStatus={snapshot.status}
          launcherVariant="bare"
        />
      </RunControls>
    </>
  );
}

interface NextStepCardProps {
  kind: NextStepKind;
  riskCount: number;
  persistent?: boolean;
  onDismiss: () => void;
  onConfigureModel: () => void;
  onOpenSettings: () => void;
  onOpenProofreading: () => void;
}

function NextStepCard({
  kind,
  riskCount,
  persistent = false,
  onDismiss,
  onConfigureModel,
  onOpenSettings,
  onOpenProofreading,
}: NextStepCardProps) {
  const messages = useMessages();
  const copy = messages.translation.run.nextStep;
  const content =
    kind === "model"
      ? {
          title: copy.modelTitle,
          body: copy.modelBody,
          action: copy.modelAction,
          onAction: onConfigureModel,
        }
      : kind === "proofreading"
        ? {
            title: copy.proofreadingTitle.replace("{n}", String(riskCount)),
            body: copy.proofreadingBody,
            action: copy.proofreadingAction,
            onAction: onOpenProofreading,
          }
        : {
            title: copy.startTitle,
            body: copy.startBody,
            action: copy.startAction,
            onAction: onOpenSettings,
          };

  if (persistent && kind === "proofreading") {
    return (
      <button
        type="button"
        className={styles.reviewRequiredButton}
        aria-label={content.action}
        onClick={content.onAction}
      >
        <span>{content.title}</span>
        <span className={styles.reviewRequiredAction}>{content.action}</span>
      </button>
    );
  }

  return (
    <section className={styles.nextStepCard} aria-label={copy.ariaLabel}>
      <div className={styles.nextStepCopy}>
        <div className={styles.nextStepLabel}>{copy.label}</div>
        <h3>{content.title}</h3>
        <p>{content.body}</p>
      </div>
      <div className={styles.nextStepActions}>
        <Pill variant="primary" onClick={content.onAction}>
          {content.action}
        </Pill>
        {!persistent ? (
          <button
            type="button"
            className={styles.nextStepDismiss}
            onClick={onDismiss}
          >
            {copy.dismiss}
          </button>
        ) : null}
      </div>
    </section>
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
