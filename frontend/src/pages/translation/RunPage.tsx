import { useEffect, useState } from "react";
import { useMessages, useI18n } from "@/locales";
import { translationBridge, BridgeError } from "@/bridge";
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
import styles from "./RunPage.module.css";

const NUM = new Intl.NumberFormat("en");

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function RunPage() {
  const messages = useMessages();
  const { run } = messages.translation;
  const failedModalMessages = messages.failedSubtasksModal;
  const navigate = useTaskStore((state) => state.navigate);
  const profiles = useModelProfiles();
  const prompts = usePromptPresets("translation");
  const promptSlice = prompts.translation;
  const appSettings = useModuleSettings("app");
  const snapshot = useRunSnapshot("translation");
  const activeTaskId = useRuntimeStore(
    (state) => state.translation.activeTaskId,
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
  const [rerunPending, setRerunPending] = useState(false);

  // Auto-open the completion-with-failures dialog the first time we
  // see a terminal status with failures for this task. Fires even when
  // every chunk failed (progress.completed == 0) so the user is always
  // offered a "continue rerun" path — they were getting silent dead
  // ends previously. Dismissal is tracked module-level so navigating
  // away and back doesn't re-open.
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

  const handleAcceptCompletion = () => {
    if (activeTaskId) markCompletionWithFailuresDismissed(activeTaskId);
    setCompletionPromptOpen(false);
  };

  const handleRerunFailed = async () => {
    if (!activeTaskId || rerunPending) return;
    setRerunPending(true);
    try {
      await translationBridge.continueTask(activeTaskId);
      // Continue resets the dismissal: if the rerun produces new failures,
      // the dialog should fire again so the user can re-decide.
      setCompletionPromptOpen(false);
      await useRuntimeStore.getState().refreshActiveTask("translation");
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        useRuntimeStore.getState().setLastError("translation", error);
      }
    } finally {
      setRerunPending(false);
    }
  };

  const activeModelId = appSettings.draft?.active_translation_model_id ?? null;
  const activeModel = activeModelId
    ? profiles.profiles.find((p) => p.id === activeModelId)
    : undefined;
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
    await useModelProfilesStore.getState().selectActive("translation", id);
  };
  const handleSelectPrompt = async (id: string) => {
    await prompts.selectActive("translation", id);
  };

  const total = snapshot.progress.total;
  const completed = snapshot.progress.completed;
  const failed = snapshot.progress.failed;
  const skipped = snapshot.progress.skipped;
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  // Floor (not round) so a near-finished run like 400/402 renders 99%,
  // not a misleading 100%, until every subtask actually completes.
  // SKIPPED counts as "settled" for percent purposes — the failed-chunk
  // split path turns the original failed parent into SKIPPED and runs
  // the children, so a clean COMPLETED run with splits would otherwise
  // display 91% instead of 100%.
  const settled = completed + skipped;
  const percent = total > 0 ? Math.floor((settled / total) * 100) : 0;
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const elapsedSeconds = Math.floor(snapshot.progress.elapsed_seconds);

  return (
    <>
      <Panel title={run.title} subtitle={run.sub} />

      <RunErrorBanner kind="translation" />

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
          onManage={() => navigate({ module: "translation", page: "prompt" })}
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

      <RunControls kind="translation" />
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
