import { useEffect, useState } from "react";
import { useMessages, useI18n } from "@/locales";
import { useTaskStore } from "@/store/useTaskStore";
import {
  useRunSnapshot,
  usePollRunSnapshot,
  useRuntimeStore,
} from "@/store/useRuntimeStore";
import {
  useModelProfiles,
  useModelProfilesStore,
} from "@/store/useModelProfilesStore";
import { usePromptPresets } from "@/store/usePromptPresetsStore";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { ProgressRing } from "@/components/ProgressRing";
import { RunErrorBanner } from "@/components/RunErrorBanner";
import { FailedSubtaskList } from "@/components/FailedSubtaskList";
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
  const navigate = useTaskStore((state) => state.navigate);
  const profiles = useModelProfiles();
  const prompts = usePromptPresets("translation");
  const promptSlice = prompts.translation;
  const appSettings = useModuleSettings("app");
  const snapshot = useRunSnapshot("translation");
  usePollRunSnapshot("translation");

  // Refresh active-task state on mount so re-entering the page after
  // navigating away picks up the live backend status without waiting
  // for the next 2-second poll tick.
  useEffect(() => {
    void useRuntimeStore.getState().refreshActiveTask("translation");
  }, []);

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
  const remaining = snapshot.progress.pending + snapshot.progress.running;
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  const ratePerMinute = Math.round(snapshot.progress.rate_per_second * 60);
  const etaSeconds =
    snapshot.progress.eta_seconds > 0 ? snapshot.progress.eta_seconds : null;

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
