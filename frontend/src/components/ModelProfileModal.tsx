import * as React from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMessages } from "@/locales";
import {
  BridgeError,
  modelProfilesBridge,
  modelTemplatesBridge,
  type InlineProbeCredentials,
  type ModelListEntry,
  type ModelProfile,
  type ModelProfileDraft,
  type ModelTestResult,
  type ProviderFormat,
  type ProviderTemplate,
  type ProviderTemplateFieldHint,
  type ThinkingLevel,
} from "@/bridge";
import { useToastStore } from "@/store/useToastStore";
import { Pill } from "./Pill";
import { TextField } from "./TextField";
import { NumberField } from "./NumberField";
import { ToggleSwitch } from "./ToggleSwitch";
import { Segmented } from "./Segmented";
import { FieldHint } from "./FieldHint";
import styles from "./ModelProfileModal.module.css";

type Mode = "create" | "edit";

interface ModelProfileModalProps {
  mode: Mode;
  /** Existing profile snapshot when ``mode === "edit"``. */
  profile?: ModelProfile;
  /** Called after a successful save. ``profileId`` is the saved id
   *  so the caller can mark it active / scroll to its chip. */
  onSaved: (profileId: string) => void;
  onCancel: () => void;
  /** Edit mode only — invoked when user clicks Delete. */
  onDelete?: () => Promise<void>;
  /** Edit mode only — invoked when user clicks "Set as active".
   *  ``undefined`` hides the button (e.g. when profile is already
   *  the active selection). */
  onSetActive?: () => Promise<void>;
  /** Whether this profile is currently the active selection for
   *  the kind shown by the page that opened the modal. */
  isActive?: boolean;
}

const PROVIDER_OPTIONS: Array<{ id: ProviderFormat; label: string }> = [
  { id: "openai", label: "OpenAI" },
  { id: "anthropic", label: "Anthropic" },
  { id: "google", label: "Google" },
  { id: "sakura", label: "Sakura" },
  { id: "custom", label: "Custom" },
];

const THINKING_OPTIONS: Array<{ id: ThinkingLevel; label: string }> = [
  { id: "off", label: "Off" },
  { id: "low", label: "Low" },
  { id: "medium", label: "Medium" },
  { id: "high", label: "High" },
];

interface Draft {
  display_name: string;
  provider_format: ProviderFormat;
  base_url: string;
  model_id: string;
  api_keys: string;
  rotate_keys: boolean;
  thinking_level: ThinkingLevel;
  timeout_seconds: number;
  concurrency_limit: number;
  rpm_limit: number;
  tpm_limit: number;
  retry_attempts: number;
  retry_initial_backoff_seconds: number;
  retry_max_backoff_seconds: number;
  max_output_tokens: number;
  thinking_budget_tokens: number;
  input_token_limit: number;
  temperature: number | null;
  top_p: number | null;
  presence_penalty: number | null;
  frequency_penalty: number | null;
  custom_headers: Array<[string, string]>;
  force_thinking_enable: boolean;
}

const EMPTY_DRAFT: Draft = {
  display_name: "",
  provider_format: "custom",
  base_url: "",
  model_id: "",
  api_keys: "",
  rotate_keys: true,
  thinking_level: "off",
  timeout_seconds: 60,
  concurrency_limit: 2,
  rpm_limit: 60,
  tpm_limit: 0,
  retry_attempts: 2,
  retry_initial_backoff_seconds: 1,
  retry_max_backoff_seconds: 30,
  max_output_tokens: 4096,
  thinking_budget_tokens: 4096,
  input_token_limit: 0,
  temperature: null,
  top_p: null,
  presence_penalty: null,
  frequency_penalty: null,
  custom_headers: [],
  force_thinking_enable: false,
};

function templateToDraft(t: ProviderTemplate): Draft {
  const r = t.recommended_defaults;
  return {
    ...EMPTY_DRAFT,
    display_name: t.id === "custom" ? "" : t.display_name,
    provider_format: t.provider_format,
    base_url: t.default_base_url,
    model_id: t.hint_models[0] ?? "",
    rotate_keys: true,
    thinking_level: r.thinking_level,
    timeout_seconds: r.timeout_seconds,
    concurrency_limit: r.concurrency_limit,
    rpm_limit: r.rpm_limit,
    tpm_limit: r.tpm_limit,
    retry_attempts: r.retry_attempts,
    max_output_tokens: r.max_output_tokens,
    thinking_budget_tokens: r.max_output_tokens,
    temperature: r.temperature,
    top_p: r.top_p < 1 ? r.top_p : null,
  };
}

function profileToDraft(p: ModelProfile): Draft {
  return {
    display_name: p.display_name,
    provider_format: p.provider_format,
    base_url: p.base_url,
    model_id: p.model_id,
    api_keys: "",
    rotate_keys: p.rotate_keys,
    thinking_level: p.thinking_level,
    timeout_seconds: p.timeout_seconds,
    concurrency_limit: p.concurrency_limit,
    rpm_limit: p.rpm_limit,
    tpm_limit: p.tpm_limit,
    retry_attempts: p.retry_attempts,
    retry_initial_backoff_seconds: p.retry_initial_backoff_seconds,
    retry_max_backoff_seconds: p.retry_max_backoff_seconds,
    max_output_tokens: p.max_output_tokens,
    thinking_budget_tokens: p.thinking_budget_tokens,
    input_token_limit: p.input_token_limit,
    temperature: p.temperature,
    top_p: p.top_p,
    presence_penalty: p.presence_penalty,
    frequency_penalty: p.frequency_penalty,
    custom_headers: p.custom_headers,
    force_thinking_enable: p.force_thinking_enable,
  };
}

function normalizeDraft(d: Draft): Draft {
  return {
    ...d,
    display_name: d.display_name.trim(),
    base_url: d.base_url.trim(),
    model_id: d.model_id.trim(),
  };
}

function draftToCreatePayload(d: Draft): ModelProfileDraft {
  const normalized = normalizeDraft(d);
  return {
    display_name: normalized.display_name,
    provider_format: normalized.provider_format,
    base_url: normalized.base_url,
    model_id: normalized.model_id,
    rotate_keys: normalized.rotate_keys,
    thinking_level: normalized.thinking_level,
    timeout_seconds: normalized.timeout_seconds,
    concurrency_limit: normalized.concurrency_limit,
    rpm_limit: normalized.rpm_limit,
    tpm_limit: normalized.tpm_limit,
    retry_attempts: normalized.retry_attempts,
    retry_initial_backoff_seconds: normalized.retry_initial_backoff_seconds,
    retry_max_backoff_seconds: normalized.retry_max_backoff_seconds,
    max_output_tokens: normalized.max_output_tokens,
    thinking_budget_tokens: normalized.thinking_budget_tokens,
    input_token_limit: normalized.input_token_limit,
    top_p: normalized.top_p,
    temperature: normalized.temperature,
    presence_penalty: normalized.presence_penalty,
    frequency_penalty: normalized.frequency_penalty,
    custom_headers: normalized.custom_headers,
    force_thinking_enable: normalized.force_thinking_enable,
    api_keys: parseApiKeys(normalized.api_keys),
  };
}

function parseApiKeys(buffer: string): string[] {
  return buffer
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
}

function asBridgeError(error: unknown): BridgeError {
  if (BridgeError.isBridgeError(error)) return error;
  return new BridgeError({
    code: "bridge.io_error",
    message: error instanceof Error ? error.message : String(error),
    retryable: true,
  });
}

export function ModelProfileModal({
  mode,
  profile,
  onSaved,
  onCancel,
  onDelete,
  onSetActive,
  isActive,
}: ModelProfileModalProps) {
  const messages = useMessages();
  const m = messages.modelModal;

  const [templates, setTemplates] = useState<ProviderTemplate[]>([]);
  const [selectedTemplate, setSelectedTemplate] =
    useState<ProviderTemplate | null>(null);
  const [draft, setDraft] = useState<Draft>(() =>
    mode === "edit" && profile ? profileToDraft(profile) : EMPTY_DRAFT,
  );
  const [error, setError] = useState<BridgeError | null>(null);
  const [saving, setSaving] = useState(false);
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null);
  const [fetchedModels, setFetchedModels] = useState<ModelListEntry[] | null>(
    null,
  );
  const [probeBusy, setProbeBusy] = useState<"test" | "fetch" | null>(null);
  const requestSeq = useRef(0);
  // Snapshot of the initial draft for the unsaved-changes guard.
  const baselineRef = useRef<Draft>(draft);

  const handleCancel = () => {
    if (saving) return;
    if (JSON.stringify(draft) !== JSON.stringify(baselineRef.current)) {
      const confirmed = window.confirm(m.unsavedChangesConfirm);
      if (!confirmed) return;
    }
    onCancel();
  };

  // Load templates once on mount.
  useEffect(() => {
    void modelTemplatesBridge
      .list()
      .then((r) => setTemplates(r.templates))
      .catch((err) => setError(asBridgeError(err)));
  }, []);

  // In edit mode, fetch the full profile to populate api_keys (the list
  // endpoint returns only masked status; the modal needs the actual keys
  // so the user can see what was saved instead of staring at an empty box).
  useEffect(() => {
    if (mode !== "edit" || !profile) return;
    let cancelled = false;
    void modelProfilesBridge
      .readFull(profile.id)
      .then((r) => {
        if (cancelled) return;
        const joined = r.api_keys.join("\n");
        setDraft((prev) => ({ ...prev, api_keys: joined }));
      })
      .catch((err) => {
        if (!cancelled) setError(asBridgeError(err));
      });
    return () => {
      cancelled = true;
    };
  }, [mode, profile?.id]);

  // For edit-mode hints, look up a matching template by
  // provider_format. Falls back to "custom" (description-only) so the
  // user still gets the generic field explanations.
  const hintTemplate = useMemo<ProviderTemplate | null>(() => {
    if (mode === "create") return selectedTemplate;
    return (
      templates.find((t) => t.provider_format === draft.provider_format) ??
      templates.find((t) => t.id === "custom") ??
      null
    );
  }, [mode, selectedTemplate, templates, draft.provider_format]);

  const hintMode: "provider" | "custom" =
    hintTemplate && hintTemplate.id !== "custom" ? "provider" : "custom";

  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const handlePickTemplate = (template: ProviderTemplate) => {
    setSelectedTemplate(template);
    setDraft(templateToDraft(template));
  };

  const handleBackToPicker = () => {
    setSelectedTemplate(null);
    setDraft(EMPTY_DRAFT);
    setTestResult(null);
    setFetchedModels(null);
    setError(null);
  };

  const inlineCreds = (): InlineProbeCredentials | null => {
    const normalized = normalizeDraft(draft);
    const apiKeys = parseApiKeys(normalized.api_keys);
    if (apiKeys.length === 0) return null;
    if (!normalized.base_url || !normalized.provider_format) return null;
    const creds: InlineProbeCredentials = {
      provider_format: normalized.provider_format,
      base_url: normalized.base_url,
      api_key: apiKeys[0],
    };
    if (normalized.model_id) creds.model_id = normalized.model_id;
    if (normalized.custom_headers.length)
      creds.custom_headers = normalized.custom_headers;
    return creds;
  };

  const handleTest = async () => {
    const creds = inlineCreds();
    if (!creds || !creds.model_id) return;
    const seq = ++requestSeq.current;
    setProbeBusy("test");
    setTestResult(null);
    setError(null);
    try {
      const result = await modelProfilesBridge.testConnectionInline(
        creds,
        `inline-test-${Date.now().toString(36)}`,
      );
      if (seq === requestSeq.current) setTestResult(result);
    } catch (err) {
      if (seq === requestSeq.current) setError(asBridgeError(err));
    } finally {
      if (seq === requestSeq.current) setProbeBusy(null);
    }
  };

  const handleFetch = async () => {
    const creds = inlineCreds();
    if (!creds) return;
    const seq = ++requestSeq.current;
    setProbeBusy("fetch");
    setFetchedModels(null);
    setError(null);
    try {
      const result = await modelProfilesBridge.fetchModelListInline(
        creds,
        `inline-fetch-${Date.now().toString(36)}`,
      );
      if (seq === requestSeq.current) setFetchedModels(result.models);
    } catch (err) {
      if (seq === requestSeq.current) setError(asBridgeError(err));
    } finally {
      if (seq === requestSeq.current) setProbeBusy(null);
    }
  };

  const handleSave = async () => {
    const normalized = normalizeDraft(draft);
    if (!normalized.display_name) {
      setError(
        new BridgeError({
          code: "bridge.invalid_argument",
          message: "Display name is required.",
          retryable: false,
          details: { field: "display_name" },
        }),
      );
      return;
    }
    setSaving(true);
    setError(null);
    const pushToast = useToastStore.getState().push;
    try {
      if (mode === "edit" && profile) {
        // Update profile fields, then api keys separately if user typed any.
        const payload = draftToCreatePayload(normalized);
        const { api_keys: keys, ...patch } = payload;
        await modelProfilesBridge.update(
          profile.id,
          patch as Partial<ModelProfile>,
        );
        if (keys && keys.length > 0) {
          await modelProfilesBridge.setApiKey(profile.id, keys);
        }
        pushToast({
          variant: "success",
          title: messages.toast.profileSaved,
          detail: normalized.display_name,
        });
        onSaved(profile.id);
      } else {
        const { profile: saved } = await modelProfilesBridge.create(
          draftToCreatePayload(normalized),
        );
        pushToast({
          variant: "success",
          title: messages.toast.profileSaved,
          detail: normalized.display_name,
        });
        onSaved(saved.id);
      }
    } catch (err) {
      const bridgeError = asBridgeError(err);
      setError(bridgeError);
      pushToast({
        variant: "error",
        title: messages.toast.profileSaveFailed,
        detail: bridgeError.message,
        durationMs: 5000,
      });
    } finally {
      setSaving(false);
    }
  };

  const showStep1 = mode === "create" && selectedTemplate === null;

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={handleCancel}
    >
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.header}>
          <h2 className={styles.title}>
            {mode === "edit" ? m.titleEdit : m.titleAdd}
          </h2>
          <button
            type="button"
            className={styles.closeButton}
            onClick={handleCancel}
            aria-label={m.cancelAction}
          >
            ×
          </button>
        </div>

        {error ? (
          <div className={styles.errorBanner}>
            <code>{error.code}</code>
            <span>{error.message}</span>
          </div>
        ) : null}

        {showStep1 ? (
          <TemplatePickerStep
            templates={templates}
            onPick={handlePickTemplate}
          />
        ) : (
          <FormStep
            draft={draft}
            update={update}
            hintTemplate={hintTemplate}
            hintMode={hintMode}
            providerLocked={
              mode === "create" &&
              selectedTemplate !== null &&
              selectedTemplate.id !== "custom"
            }
            onTest={handleTest}
            onFetch={handleFetch}
            probeBusy={probeBusy}
            testResult={testResult}
            fetchedModels={fetchedModels}
          />
        )}

        <div className={styles.footer}>
          {mode === "create" && selectedTemplate !== null ? (
            <Pill variant="ghost" onClick={handleBackToPicker}>
              {m.pickerBack}
            </Pill>
          ) : null}
          {mode === "edit" && onDelete ? (
            <Pill variant="ghost" onClick={() => void onDelete()}>
              {messages.modelExtra.deleteProfile}
            </Pill>
          ) : null}
          {mode === "edit" && onSetActive && !isActive ? (
            <Pill variant="ghost" onClick={() => void onSetActive()}>
              {messages.modelExtra.setActive}
            </Pill>
          ) : null}
          <div className={styles.footerRight}>
            <Pill variant="ghost" onClick={handleCancel}>
              {m.cancelAction}
            </Pill>
            {!showStep1 ? (
              <Pill onClick={handleSave} disabled={saving}>
                {m.saveAction}
              </Pill>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

interface TemplatePickerStepProps {
  templates: ProviderTemplate[];
  onPick: (template: ProviderTemplate) => void;
}

function TemplatePickerStep({ templates, onPick }: TemplatePickerStepProps) {
  const messages = useMessages();
  const m = messages.modelModal;
  return (
    <div className={styles.step}>
      <p className={styles.stepTitle}>{m.step1Title}</p>
      <p className={styles.stepSub}>{m.step1Sub}</p>
      <div className={styles.templateGrid}>
        {templates.map((template) => (
          <button
            type="button"
            key={template.id}
            className={styles.templateCard}
            onClick={() => onPick(template)}
          >
            <span className={styles.templateName}>{template.display_name}</span>
            <span className={styles.templateMeta}>
              {template.id === "custom" ? "" : template.provider_format}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

interface FormStepProps {
  draft: Draft;
  update: <K extends keyof Draft>(key: K, value: Draft[K]) => void;
  hintTemplate: ProviderTemplate | null;
  hintMode: "provider" | "custom";
  providerLocked: boolean;
  onTest: () => void;
  onFetch: () => void;
  probeBusy: "test" | "fetch" | null;
  testResult: ModelTestResult | null;
  fetchedModels: ModelListEntry[] | null;
}

function FormStep({
  draft,
  update,
  hintTemplate,
  hintMode,
  providerLocked,
  onTest,
  onFetch,
  probeBusy,
  testResult,
  fetchedModels,
}: FormStepProps) {
  const messages = useMessages();
  const m = messages.modelModal;
  const me = messages.modelExtra;
  const model = messages.model;

  const hint = (key: string): ProviderTemplateFieldHint | null => {
    if (!hintTemplate) return null;
    return hintTemplate.field_hints[key] ?? null;
  };

  const renderHint = (key: string) => {
    const h = hint(key);
    if (!h) return null;
    return (
      <FieldHint
        mode={hintMode}
        providerName={hintTemplate?.display_name}
        hint={h}
      />
    );
  };

  return (
    <div className={styles.step}>
      <p className={styles.stepTitle}>{m.step2Title}</p>

      <section className={styles.section}>
        <TextField
          label={model.displayName}
          value={draft.display_name}
          onChange={(v) => update("display_name", v)}
          placeholder={model.displayName}
        />
        <div className={styles.formatRow}>
          <span className={styles.formatLabel}>{model.apiFormatLabel}</span>
          <Segmented<ProviderFormat>
            ariaLabel={model.apiFormatLabel}
            options={PROVIDER_OPTIONS}
            value={draft.provider_format}
            onChange={(v) => {
              if (providerLocked) return;
              update("provider_format", v);
            }}
          />
          {providerLocked ? (
            <span className={styles.formatLockedHint}>
              ({hintTemplate?.display_name})
            </span>
          ) : null}
        </div>
        <TextField
          label={model.baseUrl}
          value={draft.base_url}
          onChange={(v) => update("base_url", v)}
          mono
        />
        <div className={styles.modelIdRow}>
          <TextField
            label={model.modelId}
            value={draft.model_id}
            onChange={(v) => update("model_id", v)}
            mono
          />
          {(() => {
            const fetchUnsupported =
              draft.provider_format === "anthropic" ||
              !hintTemplate?.supports_fetch_model_list;
            const fetchLabel = fetchUnsupported
              ? me.fetchUnsupported
              : probeBusy === "fetch"
                ? me.fetchRunning
                : me.fetchModels;
            return (
              <div className={styles.fetchButtonRow}>
                <Pill
                  onClick={onFetch}
                  disabled={probeBusy !== null || fetchUnsupported}
                >
                  {fetchLabel}
                </Pill>
              </div>
            );
          })()}
          {fetchedModels && fetchedModels.length > 0 ? (
            <select
              className={styles.modelPicker}
              value={
                fetchedModels.some((entry) => entry.id === draft.model_id)
                  ? draft.model_id
                  : ""
              }
              onChange={(e) => update("model_id", e.target.value)}
            >
              <option value="">—</option>
              {fetchedModels.map((entry) => (
                <option key={entry.id} value={entry.id}>
                  {entry.display_name
                    ? `${entry.display_name} (${entry.id})`
                    : entry.id}
                </option>
              ))}
            </select>
          ) : null}
        </div>
        <TextField
          label={model.apiKeys}
          value={draft.api_keys}
          onChange={(v) => update("api_keys", v)}
          placeholder={model.apiKeysPlaceholder}
          multiline
          rows={3}
          mono
        />
        <ToggleSwitch
          label={m.rotateKeysLabel}
          checked={draft.rotate_keys}
          onChange={(v) => update("rotate_keys", v)}
          help={m.rotateKeysHelp}
        />
      </section>

      <section className={styles.section}>
        <div className={styles.actionRow}>
          <Pill onClick={onTest} disabled={probeBusy !== null}>
            {probeBusy === "test" ? me.testRunning : me.testConnection}
          </Pill>
        </div>
        {testResult ? (
          <div className={testResult.ok ? styles.statusOk : styles.statusFail}>
            <strong>{testResult.ok ? me.testOk : me.testFailed}</strong> ·{" "}
            {me.testLatency}: {testResult.latency_ms}ms ·{" "}
            {testResult.provider_response.detail}
          </div>
        ) : null}
      </section>

      <details className={styles.details}>
        <summary>{m.runtimeTuningLabel}</summary>
        <div className={styles.gridTwo}>
          <FieldRow hint={renderHint("concurrency_limit")}>
            <NumberField
              label={model.concurrency}
              value={draft.concurrency_limit}
              onChange={(v) => update("concurrency_limit", v)}
            />
          </FieldRow>
          <FieldRow hint={renderHint("rpm_limit")}>
            <NumberField
              label={model.rpm}
              value={draft.rpm_limit}
              onChange={(v) => update("rpm_limit", v)}
            />
          </FieldRow>
          <FieldRow hint={renderHint("tpm_limit")}>
            <NumberField
              label={model.tpm}
              value={draft.tpm_limit}
              onChange={(v) => update("tpm_limit", v)}
            />
          </FieldRow>
          <FieldRow hint={renderHint("retry_attempts")}>
            <NumberField
              label={model.retryAttempts}
              value={draft.retry_attempts}
              onChange={(v) => update("retry_attempts", v)}
            />
          </FieldRow>
        </div>
      </details>

      <details className={styles.details}>
        <summary>{m.samplingLabel}</summary>
        <div className={styles.gridTwo}>
          <FieldRow hint={renderHint("input_token_limit")}>
            <NumberField
              label={model.inputTokenLimit}
              value={draft.input_token_limit}
              onChange={(v) => update("input_token_limit", v)}
            />
          </FieldRow>
          <FieldRow hint={renderHint("max_output_tokens")}>
            <NumberField
              label={model.outputTokenLimit}
              value={draft.max_output_tokens}
              onChange={(v) => update("max_output_tokens", v)}
            />
          </FieldRow>
          <div className={styles.formatRow}>
            <span className={styles.formatLabel}>{model.thinkingLevel}</span>
            <Segmented<ThinkingLevel>
              ariaLabel={model.thinkingLevel}
              options={THINKING_OPTIONS}
              value={draft.thinking_level}
              onChange={(v) => update("thinking_level", v)}
            />
          </div>
          {draft.thinking_level === "off" ? (
            <ToggleSwitch
              label={m.forceThinkingLabel}
              checked={draft.force_thinking_enable}
              onChange={(v) => update("force_thinking_enable", v)}
              help={m.forceThinkingHelp}
            />
          ) : null}
        </div>

        <OptionalNumberRow
          label={model.temperature}
          help={model.temperatureHelp}
          value={draft.temperature}
          onChange={(v) => update("temperature", v)}
          step={0.1}
          defaultValue={1}
        />
        <OptionalNumberRow
          label={model.topP}
          help={model.topPHelp}
          value={draft.top_p}
          onChange={(v) => update("top_p", v)}
          step={0.05}
          defaultValue={0.95}
        />
        <OptionalNumberRow
          label={model.presencePenalty}
          help={model.presencePenaltyHelp}
          value={draft.presence_penalty}
          onChange={(v) => update("presence_penalty", v)}
          step={0.1}
          defaultValue={0}
        />
        <OptionalNumberRow
          label={model.frequencyPenalty}
          help={model.frequencyPenaltyHelp}
          value={draft.frequency_penalty}
          onChange={(v) => update("frequency_penalty", v)}
          step={0.1}
          defaultValue={0}
        />
      </details>

      <details className={styles.details}>
        <summary>{model.customHeaders}</summary>
        <CustomHeadersEditor
          value={draft.custom_headers}
          onChange={(v) => update("custom_headers", v)}
          placeholder={model.customHeadersPlaceholder}
          help={model.customHeadersHelp}
        />
      </details>
    </div>
  );
}

interface OptionalNumberRowProps {
  label: string;
  help: string;
  value: number | null;
  onChange: (next: number | null) => void;
  step: number;
  defaultValue: number;
}

function OptionalNumberRow({
  label,
  help,
  value,
  onChange,
  step,
  defaultValue,
}: OptionalNumberRowProps) {
  const enabled = value !== null;
  // Local draft mirrors the NumberField pattern: lets the user clear /
  // pause mid-edit without the controlled input snapping back to 0 on
  // every keystroke.
  const [draft, setDraft] = useState<string | null>(null);
  const display = draft ?? (value ?? "").toString();
  return (
    <div className={styles.optionalRow}>
      <div className={styles.optionalText}>
        <div className={styles.optionalLabel}>{label}</div>
        <div className={styles.optionalHelp}>{help}</div>
      </div>
      <div className={styles.optionalControls}>
        <ToggleSwitch
          label=""
          checked={enabled}
          onChange={(next) => {
            setDraft(null);
            onChange(next ? defaultValue : null);
          }}
        />
        {enabled ? (
          <input
            type="number"
            className={styles.optionalInput}
            value={display}
            step={step}
            onChange={(e) => {
              const raw = e.target.value;
              setDraft(raw);
              if (raw === "" || raw === "-" || raw === ".") return;
              const parsed = Number(raw);
              if (Number.isFinite(parsed)) onChange(parsed);
            }}
            onBlur={() => setDraft(null)}
          />
        ) : null}
      </div>
    </div>
  );
}

interface CustomHeadersEditorProps {
  value: Array<[string, string]>;
  onChange: (next: Array<[string, string]>) => void;
  placeholder: string;
  help: string;
}

function CustomHeadersEditor({
  value,
  onChange,
  placeholder,
  help,
}: CustomHeadersEditorProps) {
  const propJson =
    value.length > 0 ? JSON.stringify(Object.fromEntries(value)) : "";
  const [text, setText] = useState(propJson);
  const [parseError, setParseError] = useState<string | null>(null);
  // Track our last-emitted JSON so we can distinguish "parent state
  // is just echoing what we already typed" (skip) from "parent state
  // changed externally — e.g. profile switch or template apply"
  // (re-seed the textarea so it doesn't show stale content).
  const lastEmittedRef = useRef<string>(propJson);

  useEffect(() => {
    if (propJson === lastEmittedRef.current) return;
    lastEmittedRef.current = propJson;
    setText(propJson);
    setParseError(null);
  }, [propJson]);

  const apply = (raw: string) => {
    setText(raw);
    if (raw.trim() === "") {
      setParseError(null);
      lastEmittedRef.current = "";
      onChange([]);
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      if (
        parsed === null ||
        typeof parsed !== "object" ||
        Array.isArray(parsed)
      ) {
        setParseError("expected a JSON object");
        return;
      }
      const pairs: Array<[string, string]> = Object.entries(parsed)
        .filter(([k]) => typeof k === "string" && k.length > 0)
        .map(([k, v]) => [k, String(v)]);
      setParseError(null);
      lastEmittedRef.current =
        pairs.length > 0 ? JSON.stringify(Object.fromEntries(pairs)) : "";
      onChange(pairs);
    } catch (error) {
      setParseError(error instanceof Error ? error.message : "invalid JSON");
    }
  };

  return (
    <div className={styles.customHeadersBlock}>
      <div className={styles.customHeadersHelp}>{help}</div>
      <textarea
        className={styles.customHeadersInput}
        value={text}
        onChange={(e) => apply(e.target.value)}
        placeholder={placeholder}
        rows={3}
        spellCheck={false}
      />
      {parseError ? (
        <div className={styles.customHeadersError}>{parseError}</div>
      ) : null}
    </div>
  );
}

interface FieldRowProps {
  hint: React.ReactNode;
  children: React.ReactNode;
}

/** Row that places a `FieldHint` popover next to a `NumberField`. The
 * NumberField doesn't expose a slot for trailing content, so we lay
 * them out in a flex container instead. */
function FieldRow({ hint, children }: FieldRowProps) {
  return (
    <div className={styles.fieldRow}>
      <div className={styles.fieldRowField}>{children}</div>
      {hint ? <div className={styles.fieldRowHint}>{hint}</div> : null}
    </div>
  );
}
