import { useEffect, useState } from "react";
import { BridgeError, glossaryBridge, glossaryReviewBridge, type PromptKind } from "@/bridge";
import { useI18n, useMessages } from "@/locales";
import { useTaskStore } from "@/store/useTaskStore";
import { usePollRunSnapshot, useRunSnapshot, useRuntimeStore } from "@/store/useRuntimeStore";
import { useModelProfiles, useModelProfilesStore } from "@/store/useModelProfilesStore";
import { usePromptPresets, usePromptPresetsStore } from "@/store/usePromptPresetsStore";
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
import { ToggleSwitch } from "@/components/ToggleSwitch";
import { RunErrorBanner } from "@/components/RunErrorBanner";
import { RunControls } from "@/components/RunControls";
import { RequestLogPanel } from "@/components/RequestLogPanel";
import { RunConfigBar, type RunConfigBarItem } from "@/components/RunConfigBar";
import { QuickSwitchModal, type QuickSwitchItem } from "@/components/QuickSwitchModal";
import { ReviewInputs } from "../glossary-review/ReviewInputs";
import { ReviewProgress } from "../glossary-review/ReviewProgress";
import { ExtractionProgress } from "./ExtractionProgress";
import styles from "./RunPage.module.css";

type Switcher = "preset" | "model" | "prompt" | "reviewModel" | "reviewPrompt";

const REVIEW_IN_FLIGHT = new Set(["pending", "running", "pausing", "paused", "stopping"]);

export function RunPage() {
  const messages = useMessages();
  const { run } = messages.glossary;
  const navigate = useTaskStore((state) => state.navigate);
  const locale = useI18n((state) => state.locale);
  const profiles = useModelProfiles();
  const extractPrompts = usePromptPresets("glossary").glossary;
  const reviewPrompts = usePromptPresets("glossary_review").glossary_review;
  const workflow = useWorkflowPresets("glossary");
  const workflowSlice = workflow.glossary;
  const appSettings = useModuleSettings("app");
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
  usePollRunSnapshot("glossary");
  usePollRunSnapshot("glossary_review");

  // Re-entering the page picks up live backend status before the next poll tick.
  useEffect(() => {
    void useRuntimeStore.getState().refreshActiveTask("glossary");
    void useRuntimeStore.getState().refreshActiveTask("glossary_review");
  }, []);

  const [switchOpen, setSwitchOpen] = useState<Switcher | null>(null);
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
  const showReviewStage = source === "existing" || (reviewEnabled && linked);
  const showStartReview =
    source === "extract" &&
    reviewEnabled &&
    snapshot.status === "completed" &&
    hasReviewableArtifacts &&
    !linked &&
    !reviewInFlight;

  const reportError = (kind: PromptKind) => (error: BridgeError | null) =>
    useRuntimeStore.getState().setLastError(kind === "glossary_review" ? "glossary_review" : "glossary", error);

  const handleStartReview = async () => {
    if (!activeTaskId) return;
    setStartingReview(true);
    try {
      reviewSettings.update("input_folder", glossarySettings.draft?.output_folder.trim() ?? "");
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
      reportError("glossary_review")(BridgeError.isBridgeError(error) ? error : null);
    } finally {
      setStartingReview(false);
    }
  };

  const modelName = (id: string | null | undefined) => {
    const profile = id ? profiles.profiles.find((item) => item.id === id) : undefined;
    return { primary: profile?.display_name ?? messages.runConfig.missingModel, secondary: profile?.model_id ?? "" };
  };
  const shownPrompt = (kind: PromptKind) => {
    const slice = kind === "glossary" ? extractPrompts : reviewPrompts;
    const fallback = `default-${kind === "glossary" ? "glossary" : "glossary-review"}-${locale}`;
    const id = slice.activeId ?? (slice.presets.some((item) => item.id === fallback) ? fallback : null);
    return { id, preset: id ? slice.presets.find((item) => item.id === id) : undefined, fallback };
  };
  const extractPrompt = shownPrompt("glossary");
  const reviewPrompt = shownPrompt("glossary_review");
  const extractModelId = appSettings.draft?.active_glossary_model_id ?? null;
  const reviewModelId = appSettings.draft?.active_glossary_review_model_id ?? null;
  const sourceLanguage = glossarySettings.draft?.source_language ?? "kr";
  const targetLanguage = glossarySettings.draft?.target_language ?? "zh";
  const activePreset = workflowSlice.matchedId
    ? workflowSlice.presets.find((preset) => preset.id === workflowSlice.matchedId)
    : undefined;

  const modelItems: QuickSwitchItem[] = profiles.profiles
    .filter((profile) => profile.api_key_status !== "missing")
    .map((profile) => ({ id: profile.id, name: profile.display_name, description: profile.model_id }));
  const promptItems = (kind: PromptKind): QuickSwitchItem[] => {
    const slice = kind === "glossary" ? extractPrompts : reviewPrompts;
    const fallback = shownPrompt(kind).fallback;
    return slice.presets
      .filter((preset) => !preset.is_system || preset.id === fallback)
      .map((preset) => ({ id: preset.id, name: preset.name, description: preset.description }));
  };
  const presetItems: QuickSwitchItem[] = workflowSlice.presets.map((preset) => ({
    id: preset.id,
    name: preset.name,
    description: [
      `${messages.language.options[preset.source_language]} → ${messages.language.options[preset.target_language]}`,
      profiles.profiles.find((item) => item.id === preset.model_profile_id)?.display_name ?? preset.model_profile_id,
      preset.review === null || preset.review.enabled
        ? profiles.profiles.find((item) => item.id === preset.review?.model_profile_id)?.display_name ?? ""
        : run.noReview,
    ]
      .filter(Boolean)
      .join(" · "),
  }));

  const selectModel = (kind: PromptKind) => async (id: string) => {
    await applyRunSelection(
      () => useModelProfilesStore.getState().selectActive(kind, id),
      () => useModelProfilesStore.getState().mutationError,
      reportError(kind),
    );
    await useWorkflowPresetsStore.getState().refresh("glossary");
  };
  const selectPrompt = (kind: PromptKind) => async (id: string) => {
    await applyRunSelection(
      () => usePromptPresetsStore.getState().selectActive(kind, id),
      () => usePromptPresetsStore.getState().mutationError,
      reportError(kind),
    );
    await useWorkflowPresetsStore.getState().refresh("glossary");
  };
  const selectPreset = async (id: string) => {
    await applyRunSelection(
      () => workflow.applyPreset("glossary", id),
      () => useWorkflowPresetsStore.getState().mutationError,
      reportError("glossary"),
    );
  };

  const reviewItems: RunConfigBarItem[] = [
    {
      id: "reviewModel",
      label: run.reviewModel,
      ...modelName(reviewModelId),
      actionLabel: messages.runConfig.switchAction,
      onClick: () => setSwitchOpen("reviewModel"),
    },
    {
      id: "reviewPrompt",
      label: run.reviewPrompt,
      primary: reviewPrompt.preset?.name ?? messages.runConfig.missingPrompt,
      secondary: reviewPrompt.preset?.description ?? "",
      actionLabel: messages.runConfig.switchAction,
      onClick: () => setSwitchOpen("reviewPrompt"),
    },
  ];
  const configItems: RunConfigBarItem[] =
    source === "existing"
      ? reviewItems
      : [
          {
            id: "preset",
            label: messages.runConfig.preset,
            primary:
              workflowSlice.presets.length === 0
                ? messages.runConfig.noPreset
                : activePreset?.name ?? messages.runConfig.customPreset,
            secondary: `${messages.language.options[sourceLanguage]} → ${messages.language.options[targetLanguage]}`,
            actionLabel: messages.runConfig.switchAction,
            onClick: () => setSwitchOpen("preset"),
          },
          {
            id: "model",
            label: run.extractModel,
            ...modelName(extractModelId),
            actionLabel: messages.runConfig.switchAction,
            onClick: () => setSwitchOpen("model"),
          },
          {
            id: "prompt",
            label: run.extractPrompt,
            primary: extractPrompt.preset?.name ?? messages.runConfig.missingPrompt,
            secondary: extractPrompt.preset?.description ?? "",
            actionLabel: messages.runConfig.switchAction,
            onClick: () => setSwitchOpen("prompt"),
          },
          ...(reviewEnabled ? reviewItems : []),
        ];

  return (
    <>
      <Panel title={run.title} subtitle={run.sub} />

      <RunErrorBanner kind="glossary" />
      <RunErrorBanner kind="glossary_review" />

      <div className={styles.stageSwitch}>
        <Segmented<GlossarySource>
          ariaLabel={run.sourceLabel}
          options={[
            { id: "extract", label: run.sourceExtract },
            { id: "existing", label: run.sourceExisting },
          ]}
          value={source}
          onChange={setSource}
        />
      </div>

      {source === "existing" ? <ReviewInputs label={run.reviewInputs} /> : null}

      <Panel label={run.activeConfig}>
        <RunConfigBar items={configItems} />
        {source === "extract" && glossarySettings.draft ? (
          <ToggleSwitch
            label={run.reviewAfterExtraction}
            checked={reviewEnabled}
            onChange={(checked) => {
              glossarySettings.update("review_after_extraction", checked);
              void glossarySettings.saveNow().then(() => useWorkflowPresetsStore.getState().refresh("glossary"));
            }}
          />
        ) : null}
      </Panel>

      {switchOpen === "preset" ? (
        <QuickSwitchModal
          title={messages.quickSwitch.titlePreset}
          items={presetItems}
          activeId={workflowSlice.matchedId}
          emptyMessage={messages.quickSwitch.emptyPreset}
          onSelect={selectPreset}
          onClose={() => setSwitchOpen(null)}
          onManage={() => navigate({ module: "glossary", page: "presets" })}
        />
      ) : null}
      {switchOpen === "model" || switchOpen === "reviewModel" ? (
        <QuickSwitchModal
          title={messages.quickSwitch.titleModel}
          items={modelItems}
          activeId={switchOpen === "model" ? extractModelId : reviewModelId}
          emptyMessage={messages.quickSwitch.emptyModel}
          onSelect={selectModel(switchOpen === "model" ? "glossary" : "glossary_review")}
          onClose={() => setSwitchOpen(null)}
          onManage={() => navigate({ module: "model", page: "general" })}
        />
      ) : null}
      {switchOpen === "prompt" || switchOpen === "reviewPrompt" ? (
        <QuickSwitchModal
          title={messages.quickSwitch.titlePrompt}
          items={promptItems(switchOpen === "prompt" ? "glossary" : "glossary_review")}
          activeId={switchOpen === "prompt" ? extractPrompt.id : reviewPrompt.id}
          emptyMessage={messages.quickSwitch.emptyPrompt}
          onSelect={selectPrompt(switchOpen === "prompt" ? "glossary" : "glossary_review")}
          onClose={() => setSwitchOpen(null)}
          onManage={() => navigate({ module: "glossary", page: "prompt" })}
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
