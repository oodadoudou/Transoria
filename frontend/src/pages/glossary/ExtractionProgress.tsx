import { useEffect, useState } from "react";
import { useMessages } from "@/locales";
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
import { ProgressRing } from "@/components/ProgressRing";
import { ChunkStatusGrid } from "@/components/ChunkStatusGrid";
import { LiveRequestCounter } from "@/components/LiveRequestCounter";
import { FailedSubtasksModal } from "@/components/FailedSubtasksModal";
import { CompletionWithFailuresDialog } from "@/components/CompletionWithFailuresDialog";
import { formatDuration, NUM, Stat } from "./RunStats";
import styles from "./RunPage.module.css";

export function ExtractionProgress({ label }: { label?: string }) {
  const messages = useMessages();
  const { run } = messages.glossary;
  const failedModalMessages = messages.failedSubtasksModal;
  const profiles = useModelProfiles();
  const appSettings = useModuleSettings("app");
  const glossarySettings = useModuleSettings("glossary");
  const snapshot = useRunSnapshot("glossary");
  const activeTaskId = useRuntimeStore((state) => state.glossary.activeTaskId);
  const [failedModalOpen, setFailedModalOpen] = useState(false);
  const [completionPromptOpen, setCompletionPromptOpen] = useState(false);

  // Fire even when every chunk failed so the user is told that Continue can retry them.
  useEffect(() => {
    if (!activeTaskId) return;
    if (snapshot.status !== "completed" && snapshot.status !== "failed") return;
    if (snapshot.progress.failed <= 0) return;
    if (hasDismissedCompletionWithFailures(activeTaskId)) return;
    setCompletionPromptOpen(true);
  }, [activeTaskId, snapshot.status, snapshot.progress.failed, snapshot.progress.completed]);

  useEffect(() => {
    if (!activeTaskId) return;
    if (snapshot.status !== "completed") return;
    if (snapshot.progress.failed > 0) return;
    if (snapshot.progress.completed <= 0) return;
    if (hasShownCleanCompletionToast(activeTaskId)) return;
    markCleanCompletionToastShown(activeTaskId);
    useToastStore.getState().push({ variant: "success", title: messages.runCompleted.title });
  }, [
    activeTaskId,
    snapshot.status,
    snapshot.progress.failed,
    snapshot.progress.completed,
    messages.runCompleted.title,
  ]);

  const activeModelId = appSettings.draft?.active_glossary_model_id ?? null;
  const activeModel = activeModelId
    ? profiles.profiles.find((profile) => profile.id === activeModelId)
    : undefined;
  const total = snapshot.progress.total;
  const completed = snapshot.progress.completed;
  const failed = snapshot.progress.failed;
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  // Floor, not round: 400/402 reads 99% until every subtask actually completes.
  const percent =
    total > 0
      ? Math.floor((completed / total) * 100)
      : snapshot.status === "completed"
        ? 100
        : 0;
  const showFailures =
    snapshot.failures.length > 0 && snapshot.status !== "running" && snapshot.status !== "pending";
  const showStartupNotice =
    Boolean(activeTaskId) &&
    (snapshot.status === "pending" || snapshot.status === "running") &&
    snapshot.progress.total === 0;

  return (
    <>
      {showFailures ? (
        <div className={styles.failuresPillRow}>
          <Button variant="ghost" onClick={() => setFailedModalOpen(true)}>
            {`${failedModalMessages.triggerPrefix}${snapshot.failures.length}${failedModalMessages.triggerSuffix}`}
          </Button>
          {snapshot.status === "failed" ||
          snapshot.status === "stopped" ||
          snapshot.status === "paused" ||
          (snapshot.status === "completed" && snapshot.progress.failed > 0) ? (
            <span className={styles.failuresHint}>{failedModalMessages.continueHint}</span>
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
          onAccept={() => {
            if (activeTaskId) markCompletionWithFailuresDismissed(activeTaskId);
            setCompletionPromptOpen(false);
          }}
        />
      ) : null}

      <Panel label={label} labelExtra={label ? activeTaskId : undefined}>
        <div className={styles.progressCard}>
          <ProgressRing percent={percent} completed={completed} total={total} />
          <div className={styles.statGrid}>
            <Stat label={run.stats.completed} value={NUM.format(completed)} />
            <Stat label={run.stats.failed} value={NUM.format(failed)} />
            <Stat label={run.stats.remaining} value={NUM.format(remaining)} />
            <Stat
              label={run.stats.elapsed}
              value={snapshot.isIdle ? "—" : formatDuration(Math.floor(snapshot.progress.elapsed_seconds))}
            />
            <Stat
              label={run.stats.avgSpeed}
              value={NUM.format(Math.round(snapshot.progress.rate_per_second * 60))}
              delta="/min"
            />
          </div>
        </div>
        {showStartupNotice ? <p className={styles.startupNotice}>{run.startupNotice}</p> : null}
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
    </>
  );
}
