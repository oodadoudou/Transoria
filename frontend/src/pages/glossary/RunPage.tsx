import { useEffect, useState } from "react";
import { BridgeError, glossaryBridge, glossaryReviewBridge } from "@/bridge";
import { useMessages } from "@/locales";
import { useTaskStore } from "@/store/useTaskStore";
import { usePollRunSnapshot, useRunSnapshot, useRuntimeStore } from "@/store/useRuntimeStore";
import { useModelProfiles } from "@/store/useModelProfilesStore";
import { applyRunSelection } from "@/store/runSelection";
import { useModuleSettings } from "@/store/useSettingsStore";
import { useWorkflowPresets, useWorkflowPresetsStore } from "@/store/useWorkflowPresetsStore";
import {
  useGlossarySource,
  useGlossaryStageKind,
  useReviewLinkedToExtraction,
  type GlossarySource,
} from "@/store/glossaryStage";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/Button";
import { Segmented } from "@/components/Segmented";
import { RunErrorBanner } from "@/components/RunErrorBanner";
import { RunControls } from "@/components/RunControls";
import { RequestLogPanel } from "@/components/RequestLogPanel";
import { RunConfigBar, type RunConfigBarItem } from "@/components/RunConfigBar";
import { QuickSwitchModal, type QuickSwitchItem } from "@/components/QuickSwitchModal";
import { ReviewInputs } from "../glossary-review/ReviewInputs";
import { ReviewProgress } from "../glossary-review/ReviewProgress";
import { ExtractionProgress } from "./ExtractionProgress";
import styles from "./RunPage.module.css";

const AUTO_REVIEW_POLL_MS = 2000;
const REVIEW_ENDED_EARLY = new Set(["stopped", "failed"]);
const REVIEW_IN_FLIGHT = new Set(["pending", "running", "pausing", "paused", "stopping"]);

export function RunPage() {
  const messages = useMessages();
  const { run } = messages.glossary;
  const navigate = useTaskStore((state) => state.navigate);
  const profiles = useModelProfiles();
  const workflow = useWorkflowPresets("glossary");
  const workflowSlice = workflow.glossary;
  const glossarySettings = useModuleSettings("glossary");
  const reviewSettings = useModuleSettings("glossary_review");
  const snapshot = useRunSnapshot("glossary");
  const reviewSnapshot = useRunSnapshot("glossary_review");
  const activeTaskId = useRuntimeStore((state) => state.glossary.activeTaskId);
  const reviewTaskId = useRuntimeStore((state) => state.glossary_review.activeTaskId);
  const source = useGlossarySource((state) => state.source);
  const setSource = useGlossarySource((state) => state.setSource);
  const stageKind = useGlossaryStageKind();
  const linked = useReviewLinkedToExtraction();
  const reviewStartError = useRuntimeStore((state) => {
    const value = state.glossary.snapshot?.metadata?.review_error;
    return typeof value === "string" ? value : "";
  });
  usePollRunSnapshot("glossary");
  usePollRunSnapshot("glossary_review");

  // Re-entering the page picks up live backend status before the next poll tick.
  useEffect(() => {
    void useRuntimeStore.getState().refreshActiveTask("glossary");
    void useRuntimeStore.getState().refreshActiveTask("glossary_review");
  }, []);

  const [presetSwitchOpen, setPresetSwitchOpen] = useState(false);
  const [reviewPrefill, setReviewPrefill] = useState<{ xlsx: string; references: string[] } | null>(null);
  const [hasReviewableArtifacts, setHasReviewableArtifacts] = useState(false);
  const [startingReview, setStartingReview] = useState(false);

  useEffect(() => {
    setHasReviewableArtifacts(false);
    setReviewPrefill(null);
    if (!activeTaskId || snapshot.status !== "completed") return;
    let cancelled = false;
    glossaryBridge
      .readArtifacts(activeTaskId)
      .then((artifacts) => {
        if (cancelled) return;
        const novels = artifacts.per_novel_artifacts.filter((item) => item.xlsx_path);
        setHasReviewableArtifacts(Boolean(artifacts.combined_artifact?.xlsx_path) || novels.length > 0);
        // Several novels without a combined table still need the user to pick one.
        const single = artifacts.combined_artifact?.xlsx_path
          ? artifacts.combined_artifact
          : novels.length === 1
            ? novels[0]
            : null;
        setReviewPrefill(
          single
            ? { xlsx: single.xlsx_path, references: single.references_path ? [single.references_path] : [] }
            : null,
        );
      })
      .catch(() => {
        if (!cancelled) setHasReviewableArtifacts(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeTaskId, snapshot.status]);

  const reviewEnabled = glossarySettings.draft?.review_after_extraction ?? true;
  const reviewInFlight = !reviewSnapshot.isIdle && REVIEW_IN_FLIGHT.has(reviewSnapshot.status);
  const extractionCompleted = source === "extract" && snapshot.status === "completed";
  const awaitingAutoReview = extractionCompleted && reviewEnabled && !reviewStartError && !linked;
  const showReviewStage = source === "existing" || linked;
  const linkedReviewEnded = linked && REVIEW_ENDED_EARLY.has(reviewSnapshot.status);
  const showStartReview =
    extractionCompleted &&
    hasReviewableArtifacts &&
    !reviewInFlight &&
    (linkedReviewEnded || (!linked && (!reviewEnabled || Boolean(reviewStartError))));

  // The backend starts review right after extraction completes; pick that task up without a reload.
  useEffect(() => {
    if (!awaitingAutoReview) return;
    const timer = window.setInterval(() => {
      void useRuntimeStore.getState().refreshActiveTask("glossary_review");
    }, AUTO_REVIEW_POLL_MS);
    void useRuntimeStore.getState().refreshActiveTask("glossary_review");
    return () => window.clearInterval(timer);
  }, [awaitingAutoReview]);

  const reportError = (kind: "glossary" | "glossary_review") => (error: BridgeError | null) =>
    useRuntimeStore.getState().setLastError(kind, error);

  const handleStartReview = async () => {
    if (!activeTaskId) return;
    setStartingReview(true);
    try {
      const draft = glossarySettings.draft;
      reviewSettings.update("input_folder", draft?.output_folder.trim() || draft?.input_folder.trim() || "");
      reviewSettings.update("selected_xlsx_path", reviewPrefill?.xlsx ?? "");
      reviewSettings.update("selected_reference_paths", reviewPrefill?.references ?? []);
      await reviewSettings.saveNow();
      if (!reviewPrefill) {
        setSource("existing");
        return;
      }
      await glossaryReviewBridge.startTask(`start-${Date.now().toString(36)}`, activeTaskId);
      await useRuntimeStore.getState().refreshActiveTask("glossary_review");
    } catch (error) {
      reportError("glossary")(BridgeError.isBridgeError(error) ? error : null);
    } finally {
      setStartingReview(false);
    }
  };

  const sourceLanguage = glossarySettings.draft?.source_language ?? "kr";
  const targetLanguage = glossarySettings.draft?.target_language ?? "zh";
  const activePreset = workflowSlice.matchedId
    ? workflowSlice.presets.find((preset) => preset.id === workflowSlice.matchedId)
    : undefined;

  const presetItem: RunConfigBarItem = {
    id: "preset",
    label: messages.runConfig.preset,
    primary:
      workflowSlice.presets.length === 0
        ? messages.runConfig.noPreset
        : activePreset?.name ?? messages.runConfig.customPreset,
    secondary: `${messages.language.options[sourceLanguage]} → ${messages.language.options[targetLanguage]}`,
    actionLabel: messages.runConfig.switchAction,
    onClick: () => setPresetSwitchOpen(true),
  };

  const presetItems: QuickSwitchItem[] = workflowSlice.presets.map((preset) => ({
    id: preset.id,
    name: preset.name,
    description: [
      `${messages.language.options[preset.source_language]} → ${messages.language.options[preset.target_language]}`,
      profiles.profiles.find((item) => item.id === preset.model_profile_id)?.display_name ?? preset.model_profile_id,
      profiles.profiles.find((item) => item.id === preset.review?.model_profile_id)?.display_name ?? "",
      preset.review && !preset.review.enabled ? run.noReview : "",
    ]
      .filter(Boolean)
      .join(" · "),
  }));

  const selectPreset = async (id: string) => {
    await applyRunSelection(
      () => workflow.applyPreset("glossary", id),
      () => useWorkflowPresetsStore.getState().mutationError,
      reportError("glossary"),
    );
  };

  return (
    <>
      <Panel
        title={run.title}
        subtitle={run.sub}
        titleExtra={
          <Segmented<GlossarySource>
            ariaLabel={run.sourceLabel}
            options={[
              { id: "extract", label: run.sourceExtract },
              { id: "existing", label: run.sourceExisting },
            ]}
            value={source}
            onChange={setSource}
          />
        }
      />

      {source === "extract" ? <RunErrorBanner kind="glossary" /> : null}
      {source === "existing" || linked ? <RunErrorBanner kind="glossary_review" /> : null}

      {source === "existing" ? (
        <ReviewInputs label={run.activeConfig} presetItem={presetItem} />
      ) : (
        <Panel label={run.activeConfig}>
          <RunConfigBar items={[presetItem]} />
        </Panel>
      )}

      {presetSwitchOpen ? (
        <QuickSwitchModal
          title={messages.quickSwitch.titlePreset}
          items={presetItems}
          activeId={workflowSlice.matchedId}
          emptyMessage={messages.quickSwitch.emptyPreset}
          onSelect={selectPreset}
          onClose={() => setPresetSwitchOpen(false)}
          onManage={() => navigate({ module: "glossary", page: "presets" })}
        />
      ) : null}

      {source === "extract" ? <ExtractionProgress label={run.extractStage} /> : null}

      {showStartReview ? (
        <div className={styles.followupActions}>
          <Button type="button" onClick={() => void handleStartReview()} disabled={startingReview}>
            {run.startReview}
          </Button>
        </div>
      ) : null}

      {showReviewStage ? <ReviewProgress label={run.reviewStage} /> : null}

      <RunControls key={stageKind} kind={stageKind}>
        <RequestLogPanel
          kind={stageKind}
          taskId={stageKind === "glossary" ? activeTaskId : reviewTaskId}
          taskStatus={stageKind === "glossary" ? snapshot.status : reviewSnapshot.status}
          launcherVariant="bare"
        />
      </RunControls>
    </>
  );
}
