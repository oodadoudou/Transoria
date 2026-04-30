import { useState } from "react";
import { useMessages } from "@/locales";
import { useTaskStore } from "@/store/useTaskStore";
import { useRunSnapshot, usePollRunSnapshot } from "@/store/useRuntimeStore";
import {
  useModelProfiles,
  useModelProfilesStore,
} from "@/store/useModelProfilesStore";
import { usePromptPresets } from "@/store/usePromptPresetsStore";
import { useModuleSettings, useSettingsStore } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { ProgressRing } from "@/components/ProgressRing";
import { RunErrorBanner } from "@/components/RunErrorBanner";
import { FailedSubtaskList } from "@/components/FailedSubtaskList";
import { RunControls } from "@/components/RunControls";
import {
  QuickSwitchModal,
  type QuickSwitchItem,
} from "@/components/QuickSwitchModal";
import { ModelListPicker } from "@/components/ModelListPicker";
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
  const navigate = useTaskStore((state) => state.navigate);
  const profiles = useModelProfiles();
  const prompts = usePromptPresets("glossary");
  const promptSlice = prompts.glossary;
  const appSettings = useModuleSettings("app");
  const snapshot = useRunSnapshot("glossary");
  usePollRunSnapshot("glossary");

  const profileIds = appSettings.draft?.glossary_model_ids ?? [];
  const activeModels = profileIds
    .map((id) => profiles.profiles.find((p) => p.id === id))
    .filter((p): p is NonNullable<typeof p> => p !== undefined);
  const primaryModel = activeModels[0];
  const activePrompt = promptSlice.activeId
    ? promptSlice.presets.find((p) => p.id === promptSlice.activeId)
    : undefined;

  const [switchOpen, setSwitchOpen] = useState<"model" | "prompt" | null>(null);

  const promptItems: QuickSwitchItem[] = promptSlice.presets.map((preset) => ({
    id: preset.id,
    name: preset.name,
    description: preset.description,
  }));

  const handleSubmitModelList = async (orderedIds: string[]) => {
    await useModelProfilesStore
      .getState()
      .setModuleProfiles("glossary", orderedIds);
    void useSettingsStore.getState().hydrate();
  };
  const handleSelectPrompt = async (id: string) => {
    await prompts.selectActive("glossary", id);
  };

  const total = snapshot.progress.total;
  const completed = snapshot.progress.completed;
  const failed = snapshot.progress.failed;
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const etaSeconds =
    snapshot.progress.eta_seconds > 0 ? snapshot.progress.eta_seconds : null;

  return (
    <>
      <Panel title={run.title} subtitle={run.sub} />

      <RunErrorBanner kind="glossary" />

      <Panel label={run.activeConfig}>
        <div className={styles.activeStrip}>
          <ActiveCard
            label={run.activeModel}
            primary={
              activeModels.length === 0
                ? "—"
                : activeModels.length === 1
                  ? (primaryModel?.display_name ?? "—")
                  : `${primaryModel?.display_name ?? ""} +${activeModels.length - 1}`
            }
            secondary={
              activeModels.length === 0
                ? ""
                : activeModels.map((m) => m.model_id).join(" → ")
            }
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
        <ModelListPicker
          title={messages.modelListPicker.title}
          available={profiles.profiles}
          selectedIds={profileIds}
          emptyMessage={messages.quickSwitch.emptyModel}
          onSubmit={handleSubmitModelList}
          onClose={() => setSwitchOpen(null)}
          onManage={() => navigate({ module: "model", page: "general" })}
        />
      ) : null}

      {switchOpen === "prompt" ? (
        <QuickSwitchModal
          title={messages.quickSwitch.titlePrompt}
          items={promptItems}
          activeId={promptSlice.activeId}
          emptyMessage={messages.quickSwitch.emptyPrompt}
          onSelect={handleSelectPrompt}
          onClose={() => setSwitchOpen(null)}
          onManage={() => navigate({ module: "glossary", page: "prompt" })}
        />
      ) : null}

      {snapshot.failures.length > 0 ? (
        <Panel label="Failed subtasks">
          <FailedSubtaskList failures={snapshot.failures} />
        </Panel>
      ) : null}

      <Panel label={run.progress}>
        <div className={styles.progressCard}>
          <ProgressRing percent={percent} completed={completed} total={total} />
          <div className={styles.statGrid}>
            <Stat label={run.stats.completed} value={NUM.format(completed)} />
            <Stat label={run.stats.failed} value={NUM.format(failed)} />
            <Stat label={run.stats.remaining} value={NUM.format(remaining)} />
            <Stat
              label={run.stats.elapsed}
              value={snapshot.isIdle ? "—" : formatDuration(0)}
            />
            <Stat
              label={run.stats.eta}
              value={etaSeconds === null ? "—" : formatDuration(etaSeconds)}
            />
            <Stat
              label={run.stats.avgSpeed}
              value={NUM.format(ratePerMinute)}
              delta="/min"
            />
          </div>
        </div>
      </Panel>

      <RunControls kind="glossary" />
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
