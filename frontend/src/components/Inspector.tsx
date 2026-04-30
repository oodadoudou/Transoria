import { useMessages, useI18n } from "@/locales";
import { useTaskStore } from "@/store/useTaskStore";
import { useRunSnapshot } from "@/store/useRuntimeStore";
import { useModelProfiles } from "@/store/useModelProfilesStore";
import { usePromptPresets } from "@/store/usePromptPresetsStore";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { PromptKind } from "@/bridge";
import styles from "./Inspector.module.css";

const NUM = new Intl.NumberFormat("en");

function moduleToRunKind(module: string): PromptKind {
  return module === "glossary" ? "glossary" : "translation";
}

export function Inspector() {
  const messages = useMessages();
  const route = useTaskStore((state) => state.route);
  const kind = moduleToRunKind(route.module);
  const snapshot = useRunSnapshot(kind);
  const profilesStore = useModelProfiles();
  const promptPresets = usePromptPresets(kind);
  const appSettings = useModuleSettings("app");

  const activeModelId =
    appSettings.draft?.[
      kind === "translation"
        ? "active_translation_model_id"
        : "active_glossary_model_id"
    ] ?? null;
  const activeProfile = profilesStore.profiles.find(
    (p) => p.id === activeModelId,
  );

  const locale = useI18n((state) => state.locale);
  const localeDefaultPromptId = `default-${kind}-${locale}`;
  const activePresetId =
    promptPresets[kind].activeId ??
    appSettings.draft?.[
      kind === "translation"
        ? "active_translation_prompt_id"
        : "active_glossary_prompt_id"
    ] ??
    (promptPresets[kind].presets.some((p) => p.id === localeDefaultPromptId)
      ? localeDefaultPromptId
      : null);
  const activePreset =
    promptPresets[kind].presets.find((p) => p.id === activePresetId) ?? null;

  return (
    <aside className={styles.inspector}>
      <Block
        title={messages.inspector.activeModel}
        subtitle={
          activeProfile?.display_name ?? messages.inspector.noActiveModel
        }
      >
        <ModelCard
          gradient="warm"
          title={activeProfile?.model_id || messages.inspector.noModelId}
          subtitle={
            activeProfile?.provider_format ?? messages.inspector.noActiveModel
          }
        />
      </Block>

      <Block
        title={messages.inspector.tokensThisRun}
        subtitle={messages.inspector.tokensSubtitle}
      >
        <div className={styles.tokens}>
          <TokenCell
            label={messages.inspector.tokensInput}
            value={snapshot.usage.input_tokens}
          />
          <TokenCell
            label={messages.inspector.tokensOutput}
            value={snapshot.usage.output_tokens}
          />
          <TokenCell
            label={messages.inspector.tokensTotal}
            value={snapshot.usage.total_tokens}
            full
          />
        </div>
      </Block>

      <Block
        title={messages.inspector.activePrompt}
        subtitle={messages.inspector.activePromptSubtitle}
      >
        <ModelCard
          gradient="cool"
          title={activePreset?.name ?? messages.inspector.noActivePrompt}
          subtitle={
            activePreset
              ? activePreset.is_default
                ? messages.prompt.badgeDefault
                : messages.prompt.badgeCustom
              : messages.inspector.noActivePrompt
          }
        />
      </Block>
    </aside>
  );
}

interface BlockProps {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}

function Block({ title, subtitle, children }: BlockProps) {
  return (
    <section className={styles.block}>
      <header className={styles.header}>
        <span>{title}</span>
        {subtitle ? <small>{subtitle}</small> : null}
      </header>
      {children}
    </section>
  );
}

interface ModelCardProps {
  gradient: "warm" | "cool";
  title: string;
  subtitle: string;
}

function ModelCard({ gradient, title, subtitle }: ModelCardProps) {
  const avClass = gradient === "warm" ? styles.avWarm : styles.avCool;
  return (
    <div className={styles.modelCard}>
      <div className={`${styles.av} ${avClass}`} />
      <div className={styles.modelText}>
        <b>{title}</b>
        <span>{subtitle}</span>
      </div>
    </div>
  );
}

interface TokenCellProps {
  label: string;
  value: number;
  full?: boolean;
}

function TokenCell({ label, value, full }: TokenCellProps) {
  return (
    <div className={`${styles.cell} ${full ? styles.full : ""}`.trim()}>
      <div className={styles.cellLabel}>{label}</div>
      <b className="tnum">{NUM.format(value)}</b>
    </div>
  );
}
