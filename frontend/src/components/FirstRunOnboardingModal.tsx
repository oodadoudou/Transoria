import { useEffect, useMemo, useState } from "react";

import {
  BridgeError,
  modelTemplatesBridge,
  settingsBridge,
  type AppSettings,
  type ModelProfile,
  type ModelProfileDraft,
  type ProviderTemplate,
} from "@/bridge";
import { useMessages } from "@/locales";
import { useModelProfilesStore } from "@/store/useModelProfilesStore";
import { useSettingsStore } from "@/store/useSettingsStore";
import { useToastStore } from "@/store/useToastStore";
import { Pill } from "./Pill";
import styles from "./FirstRunOnboardingModal.module.css";

const API_KEY_STATUSES_READY = new Set(["present", "from_env"]);
const ONBOARDING_TEMPLATE_ORDER = [
  "deepseek",
  "anthropic",
  "google",
  "openai",
  "custom",
];

type ActiveModelAppSettings = Pick<
  AppSettings,
  | "active_translation_model_id"
  | "active_glossary_model_id"
  | "active_glossary_review_model_id"
>;

interface FirstRunOnboardingModalProps {
  onDone: () => void;
  onSkip: () => void;
}

export function needsFirstRunOnboarding(
  profiles: Array<Pick<ModelProfile, "api_key_status">>,
  app: ActiveModelAppSettings | null | undefined,
): boolean {
  if (!app) return false;
  const hasConfiguredKey = profiles.some((profile) =>
    API_KEY_STATUSES_READY.has(profile.api_key_status),
  );
  const hasActiveModel = Boolean(
    app.active_translation_model_id ||
      app.active_glossary_model_id ||
      app.active_glossary_review_model_id,
  );
  return !hasConfiguredKey || !hasActiveModel;
}

export function buildOnboardingProfileDraft(
  template: ProviderTemplate,
  apiKey: string,
  custom: { displayName?: string; baseUrl?: string; modelId?: string } = {},
): ModelProfileDraft {
  const defaults = template.recommended_defaults;
  const displayName =
    custom.displayName?.trim() ||
    (template.id === "custom" ? "Custom Provider" : template.display_name);
  const baseUrl = custom.baseUrl?.trim() || template.default_base_url;
  const modelId = custom.modelId?.trim() || template.hint_models[0] || "";
  return {
    display_name: displayName,
    provider_format: template.provider_format,
    base_url: baseUrl,
    model_id: modelId,
    api_keys: [apiKey.trim()],
    rotate_keys: true,
    thinking_level: defaults.thinking_level,
    timeout_seconds: defaults.timeout_seconds,
    concurrency_limit: defaults.concurrency_limit,
    rpm_limit: defaults.rpm_limit,
    tpm_limit: defaults.tpm_limit,
    max_output_tokens: defaults.max_output_tokens,
    thinking_budget_tokens: defaults.max_output_tokens,
    input_token_limit: 0,
    top_p: defaults.top_p < 1 ? defaults.top_p : null,
    temperature: defaults.temperature,
    presence_penalty: null,
    frequency_penalty: null,
    custom_headers: [],
    force_thinking_enable: false,
  };
}

function asBridgeError(error: unknown): BridgeError {
  if (BridgeError.isBridgeError(error)) return error;
  return new BridgeError({
    code: "bridge.io_error",
    message: error instanceof Error ? error.message : String(error),
    retryable: true,
  });
}

function providerTemplatesForOnboarding(
  templates: ProviderTemplate[],
): ProviderTemplate[] {
  return ONBOARDING_TEMPLATE_ORDER.flatMap((id) => {
    const template = templates.find((candidate) => candidate.id === id);
    return template ? [template] : [];
  });
}

export function FirstRunOnboardingModal({
  onDone,
  onSkip,
}: FirstRunOnboardingModalProps) {
  const messages = useMessages();
  const m = messages.firstRun;
  const [templates, setTemplates] = useState<ProviderTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [customName, setCustomName] = useState(m.customDefaultName);
  const [customBaseUrl, setCustomBaseUrl] = useState("");
  const [customModelId, setCustomModelId] = useState("");
  const [loadingTemplates, setLoadingTemplates] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<BridgeError | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadingTemplates(true);
    modelTemplatesBridge
      .list()
      .then(({ templates: loaded }) => {
        if (cancelled) return;
        const filtered = providerTemplatesForOnboarding(loaded);
        setTemplates(filtered);
        setSelectedTemplateId((current) => current || filtered[0]?.id || "");
      })
      .catch((err) => {
        if (!cancelled) setError(asBridgeError(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingTemplates(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedTemplate = useMemo(
    () =>
      templates.find((template) => template.id === selectedTemplateId) ?? null,
    [selectedTemplateId, templates],
  );
  const isCustom = selectedTemplate?.id === "custom";
  const canSave = Boolean(
    selectedTemplate &&
      apiKey.trim() &&
      (!isCustom || (customBaseUrl.trim() && customModelId.trim())),
  );

  const handleFinish = async () => {
    if (!selectedTemplate || !canSave) return;
    setSaving(true);
    setError(null);
    try {
      const draft = buildOnboardingProfileDraft(selectedTemplate, apiKey, {
        displayName: isCustom ? customName : undefined,
        baseUrl: isCustom ? customBaseUrl : undefined,
        modelId: isCustom ? customModelId : undefined,
      });
      const created =
        await useModelProfilesStore.getState().createProfile(draft);
      if (!created) {
        throw (
          useModelProfilesStore.getState().mutationError ??
          new Error(messages.toast.profileSaveFailed)
        );
      }
      const patch: ActiveModelAppSettings = {
        active_translation_model_id: created.id,
        active_glossary_model_id: created.id,
        active_glossary_review_model_id: created.id,
      };
      const { rejected_fields } = await settingsBridge.savePartial(
        "app",
        patch,
      );
      if (rejected_fields?.length) {
        throw new Error(
          `Settings rejected fields: ${rejected_fields
            .map((field) => field.field)
            .join(", ")}`,
        );
      }
      const app = useSettingsStore.getState().app.draft;
      if (app) {
        useSettingsStore.getState().applyAppFromBridge({ ...app, ...patch });
      }
      useToastStore.getState().push({
        variant: "success",
        title: m.savedTitle,
        detail: draft.display_name,
      });
      onDone();
    } catch (err) {
      const bridgeError = asBridgeError(err);
      setError(bridgeError);
      useToastStore.getState().push({
        variant: "error",
        title: messages.toast.profileSaveFailed,
        detail: bridgeError.message,
        durationMs: 5000,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.overlay} role="dialog" aria-modal="true">
      <div className={styles.modal}>
        <header className={styles.header}>
          <div>
            <h2 className={styles.title}>{m.title}</h2>
            <p className={styles.subtitle}>{m.subtitle}</p>
          </div>
        </header>

        {error ? (
          <div className={styles.errorBanner}>
            <code>{error.code}</code>
            <span>{error.message}</span>
          </div>
        ) : null}

        <section className={styles.body}>
          <div className={styles.section}>
            <div className={styles.sectionHeader}>
              <span>{m.providerLabel}</span>
              {loadingTemplates ? <small>{m.loadingProviders}</small> : null}
            </div>
            {templates.length ? (
              <div className={styles.providerGrid}>
                {templates.map((template) => {
                  const selected = template.id === selectedTemplateId;
                  return (
                    <button
                      key={template.id}
                      type="button"
                      className={`${styles.providerCard} ${
                        selected ? styles.providerCardSelected : ""
                      }`.trim()}
                      onClick={() => {
                        setSelectedTemplateId(template.id);
                        setError(null);
                      }}
                    >
                      <span className={styles.providerName}>
                        {template.display_name}
                      </span>
                      <span className={styles.providerMeta}>
                        {template.id === "custom"
                          ? m.customProviderMeta
                          : template.hint_models[0]}
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : !loadingTemplates ? (
              <div className={styles.empty}>{m.noProviders}</div>
            ) : null}
          </div>

          <div className={styles.section}>
            <label className={styles.field}>
              <span className={styles.label}>{m.apiKeyLabel}</span>
              <input
                className={styles.input}
                type="password"
                value={apiKey}
                placeholder={m.apiKeyPlaceholder}
                autoComplete="off"
                spellCheck={false}
                onChange={(event) => setApiKey(event.target.value)}
              />
            </label>
            {selectedTemplate && !isCustom ? (
              <div className={styles.summaryLine}>
                <span>{selectedTemplate.default_base_url}</span>
                <strong>{selectedTemplate.hint_models[0]}</strong>
              </div>
            ) : null}
          </div>

          {isCustom ? (
            <div className={styles.customGrid}>
              <label className={styles.field}>
                <span className={styles.label}>{m.customNameLabel}</span>
                <input
                  className={styles.input}
                  type="text"
                  value={customName}
                  onChange={(event) => setCustomName(event.target.value)}
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>{m.customBaseUrlLabel}</span>
                <input
                  className={styles.input}
                  type="text"
                  value={customBaseUrl}
                  placeholder="https://api.example.com/v1"
                  spellCheck={false}
                  onChange={(event) => setCustomBaseUrl(event.target.value)}
                />
              </label>
              <label className={styles.field}>
                <span className={styles.label}>{m.customModelIdLabel}</span>
                <input
                  className={styles.input}
                  type="text"
                  value={customModelId}
                  placeholder="model-id"
                  spellCheck={false}
                  onChange={(event) => setCustomModelId(event.target.value)}
                />
              </label>
            </div>
          ) : null}
        </section>

        <footer className={styles.footer}>
          <Pill variant="ghost" onClick={onSkip} disabled={saving}>
            {m.skipAction}
          </Pill>
          <Pill onClick={handleFinish} disabled={!canSave || saving}>
            {saving ? m.savingAction : m.finishAction}
          </Pill>
        </footer>
      </div>
    </div>
  );
}
