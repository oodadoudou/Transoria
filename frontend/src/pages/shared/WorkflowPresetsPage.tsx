import { useEffect, useMemo, useRef, useState } from "react";

import type { Language, PresetRoute, PromptKind, WorkflowPreset } from "@/bridge";
import { useMessages, useI18n } from "@/locales";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useModelProfiles } from "@/store/useModelProfilesStore";
import { usePromptPresets } from "@/store/usePromptPresetsStore";
import { useModuleSettings } from "@/store/useSettingsStore";
import { useWorkflowPresets } from "@/store/useWorkflowPresetsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { TextField } from "@/components/TextField";
import { LanguageSelect } from "@/components/LanguageSelect";
import { OverflowMenu } from "@/components/OverflowMenu";
import styles from "./WorkflowPresetsPage.module.css";

interface WorkflowPresetsPageProps {
  owner: PromptKind;
}

type ModalMode = "create" | "edit";

interface FormState {
  id: string | null;
  name: string;
  model_profile_id: string;
  prompt_preset_id: string;
  source_language: Language;
  target_language: Language;
  advanced: boolean;
  routes: PresetRoute[];
  fallback_route: PresetRoute | null;
  group_concurrency: number;
  retry_failed: boolean;
}

function defaultPromptId(kind: PromptKind, locale: string): string {
  if (kind === "glossary_review") return `default-glossary-review-${locale}`;
  return `default-${kind}-${locale}`;
}

function activeModelField(kind: PromptKind) {
  if (kind === "translation") return "active_translation_model_id";
  if (kind === "glossary") return "active_glossary_model_id";
  return "active_glossary_review_model_id";
}

function activePromptField(kind: PromptKind) {
  if (kind === "translation") return "active_translation_prompt_id";
  if (kind === "glossary") return "active_glossary_prompt_id";
  return "active_glossary_review_prompt_id";
}

export function WorkflowPresetsPage({ owner }: WorkflowPresetsPageProps) {
  const messages = useMessages();
  const labels = messages.workflowPresets;
  const languageLabels = messages.language.options;
  const locale = useI18n((state) => state.locale);
  const models = useModelProfiles();
  const prompts = usePromptPresets(owner);
  const promptSlice = prompts[owner];
  const workflow = useWorkflowPresets(owner);
  const workflowSlice = workflow[owner];
  const appSettings = useModuleSettings("app");
  const moduleSettings = useModuleSettings(owner);
  const [modal, setModal] = useState<{ mode: ModalMode; seed: FormState } | null>(
    null,
  );

  const visiblePrompts = useMemo(() => {
    const localeDefaultId = defaultPromptId(owner, locale);
    return promptSlice.presets.filter(
      (preset) => !preset.is_system || preset.id === localeDefaultId,
    );
  }, [locale, owner, promptSlice.presets]);

  const activeModelId =
    appSettings.draft?.[
      activeModelField(owner) as keyof typeof appSettings.draft
    ] ?? "";
  const fallbackPromptId = defaultPromptId(owner, locale);
  const activePromptId =
    appSettings.draft?.[
      activePromptField(owner) as keyof typeof appSettings.draft
    ] ??
    (visiblePrompts.some((preset) => preset.id === fallbackPromptId)
      ? fallbackPromptId
      : visiblePrompts[0]?.id ?? "");
  const activeSourceLanguage =
    (moduleSettings.draft?.source_language as Language | undefined) ?? "kr";
  const activeTargetLanguage =
    (moduleSettings.draft?.target_language as Language | undefined) ?? "zh";

  const modelById = (id: string) =>
    models.profiles.find((profile) => profile.id === id);
  const promptById = (id: string) =>
    promptSlice.presets.find((preset) => preset.id === id);

  const makeCurrentForm = (): FormState => ({
    id: null,
    name: "",
    model_profile_id: String(activeModelId ?? ""),
    prompt_preset_id: String(activePromptId ?? ""),
    source_language: "kr",
    target_language: "zh",
    advanced: false,
    routes: [],
    fallback_route: null,
    group_concurrency: 2,
    retry_failed: false,
  });

  const presetToForm = (preset: WorkflowPreset): FormState => ({
    id: preset.id,
    name: preset.name,
    model_profile_id: preset.model_profile_id,
    prompt_preset_id: preset.prompt_preset_id,
    source_language: preset.source_language,
    target_language: preset.target_language,
    advanced: preset.advanced,
    routes: preset.routes.map((route) => ({
      ...route,
      rpm_limit: route.rpm_limit ?? modelById(route.model_profile_id)?.rpm_limit ?? 0,
    })),
    fallback_route: preset.fallback_route
      ? {
          ...preset.fallback_route,
          rpm_limit:
            preset.fallback_route.rpm_limit ??
            modelById(preset.fallback_route.model_profile_id)?.rpm_limit ??
            0,
        }
      : null,
    group_concurrency: preset.group_concurrency || 2,
    retry_failed: preset.retry_failed,
  });

  const activePreset = workflowSlice.matchedId
    ? workflowSlice.presets.find((preset) => preset.id === workflowSlice.matchedId)
    : undefined;
  const activeModel = modelById(String(activeModelId));
  const activePrompt = promptById(String(activePromptId));
  const activeSummary = [
    `${languageLabels[activeSourceLanguage]} → ${languageLabels[activeTargetLanguage]}`,
    activePreset?.advanced
      ? `${activePreset.routes.length} ${labels.routeCount}`
      : activeModel?.display_name ?? labels.missingSelection,
    activePreset?.advanced
      ? `${labels.groupConcurrency}: ${activePreset.group_concurrency}`
      : activePrompt?.name ?? labels.missingSelection,
  ].join(" · ");

  const beginCreate = () => {
    const seed = makeCurrentForm();
    setModal({
      mode: "create",
      seed: { ...seed, name: labels.defaultName },
    });
  };

  const beginEdit = (preset: WorkflowPreset) => {
    setModal({ mode: "edit", seed: presetToForm(preset) });
  };

  const duplicateForEdit = async (preset: WorkflowPreset) => {
    const copied = await workflow.duplicatePreset(preset.id);
    if (copied) beginEdit(copied);
  };

  const save = async (form: FormState) => {
    const primary = form.advanced ? form.routes[0] : null;
    const draft = {
      name: form.name.trim(),
      model_profile_id: primary?.model_profile_id ?? form.model_profile_id,
      prompt_preset_id: primary?.prompt_preset_id ?? form.prompt_preset_id,
      source_language: form.source_language,
      target_language: form.target_language,
      enabled: true,
      advanced: owner === "translation" && form.advanced,
      routes: owner === "translation" && form.advanced ? form.routes : [],
      fallback_route:
        owner === "translation" && form.advanced ? form.fallback_route : null,
      group_concurrency:
        owner === "translation" && form.advanced ? form.group_concurrency : 0,
      retry_failed: owner === "translation" && form.advanced && form.retry_failed,
    };
    let saved: WorkflowPreset | null;
    if (form.id) {
      saved = await workflow.updatePreset(form.id, draft);
    } else {
      saved = await workflow.createPreset(owner, draft);
    }
    if (saved) {
      setModal(null);
    }
  };

  return (
    <>
      <Panel title={labels.pageTitle} subtitle={labels.pageSub} />

      {workflowSlice.loadError ? (
        <Panel label={messages.errors.loadFailureTitle}>
          <div className={styles.errorRow}>
            <code className={styles.errorCode}>{workflowSlice.loadError.code}</code>
            <span className={styles.errorMessage}>
              {workflowSlice.loadError.message}
            </span>
            <button
              type="button"
              className={styles.errorDismiss}
              onClick={() => void workflow.refresh(owner)}
            >
              {messages.errors.retry}
            </button>
          </div>
        </Panel>
      ) : null}

      {workflow.mutationError ? (
        <Panel label={messages.errors.runFailureTitle}>
          <div className={styles.errorRow}>
            <code className={styles.errorCode}>{workflow.mutationError.code}</code>
            <span className={styles.errorMessage}>
              {workflow.mutationError.message}
            </span>
            <button
              type="button"
              className={styles.errorDismiss}
              onClick={() => workflow.clearMutationError()}
            >
              {messages.errors.dismiss}
            </button>
          </div>
        </Panel>
      ) : null}

      <Panel
        label={labels.currentConfig}
        labelExtra={<span>{labels.currentHint}</span>}
      >
        <div className={styles.activeRow}>
          <div className={styles.av} aria-hidden />
          <div className={styles.activeText}>
            <b>{activePreset?.name ?? labels.customConfig}</b>
            <span className={styles.activeMeta}>{activeSummary}</span>
          </div>
        </div>
      </Panel>

      <Panel
        label={labels.available}
        labelExtra={
          <div className={styles.headerActions}>
            <span>{labels.availableHint}</span>
            <Pill variant="ghost" onClick={beginCreate}>
              {labels.addAction}
            </Pill>
          </div>
        }
      >
        {workflowSlice.presets.length === 0 ? (
          <div className={styles.empty}>
            <b>{labels.emptyTitle}</b>
            <span>{labels.emptyBody}</span>
          </div>
        ) : (
          <div className={styles.list}>
            {workflowSlice.presets.map((preset) => {
              const model = modelById(preset.model_profile_id);
              const prompt = promptById(preset.prompt_preset_id);
              const isActive = preset.id === workflowSlice.matchedId;
              const meta = [
                `${languageLabels[preset.source_language]} → ${
                  languageLabels[preset.target_language]
                }`,
                preset.advanced
                  ? `${preset.routes.length} ${labels.routeCount}`
                  : model?.display_name ?? preset.model_profile_id,
                preset.advanced
                  ? `${labels.groupConcurrency}: ${preset.group_concurrency}`
                  : prompt?.name ?? preset.prompt_preset_id,
              ].join(" · ");
              return (
                <div
                  key={preset.id}
                  className={`${styles.row} ${isActive ? styles.rowActive : ""}`.trim()}
                  onDoubleClick={() => beginEdit(preset)}
                >
                  <button
                    type="button"
                    role="radio"
                    aria-checked={isActive}
                    className={styles.radioBtn}
                    onClick={() => void workflow.applyPreset(owner, preset.id)}
                  >
                    <span
                      className={`${styles.radio} ${isActive ? styles.radioActive : ""}`.trim()}
                      aria-hidden
                    />
                    <span className={styles.rowText}>
                      <span className={styles.rowName}>{preset.name}</span>
                      <span className={styles.rowMeta} title={meta}>
                        {meta}
                      </span>
                    </span>
                    <span className={styles.rowBadge}>
                      {isActive ? labels.activeBadge : labels.badgeCustom}
                    </span>
                  </button>
                  <OverflowMenu
                    ariaLabel={messages.rowMenu.triggerLabel}
                    items={[
                      {
                        key: "edit",
                        label: labels.editAction,
                        onSelect: () => beginEdit(preset),
                      },
                      {
                        key: "duplicate",
                        label: labels.duplicateAction,
                        onSelect: () => void duplicateForEdit(preset),
                      },
                      {
                        key: "delete",
                        label: labels.deleteAction,
                        onSelect: () => void workflow.deletePreset(preset.id),
                        variant: "danger",
                      },
                    ]}
                  />
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      {modal ? (
        <WorkflowPresetModal
          mode={modal.mode}
          owner={owner}
          seed={modal.seed}
          modelOptions={models.profiles.map((profile) => ({
            id: profile.id,
            label: `${profile.display_name} · ${profile.model_id} · ${profile.provider_format} · ${profile.rpm_limit} RPM`,
            modelId: profile.model_id,
            thinkingLevel: profile.thinking_level,
            rpmLimit: profile.rpm_limit,
          }))}
          promptOptions={visiblePrompts.map((preset) => ({
            id: preset.id,
            label: preset.name,
          }))}
          onSave={(form) => void save(form)}
          onCancel={() => setModal(null)}
        />
      ) : null}
    </>
  );
}

interface Option {
  id: string;
  label: string;
  modelId?: string;
  thinkingLevel?: string;
  rpmLimit?: number;
}

function modelRpm(options: Option[], profileId: string): number {
  return options.find((option) => option.id === profileId)?.rpmLimit ?? 0;
}

function suggestedRouteModel(options: Option[], routes: PresetRoute[]): string {
  const primaryModelId = options.find(
    (option) => option.id === routes[0]?.model_profile_id,
  )?.modelId;
  const available = options.filter(
    (option) => !routes.some((route) => route.model_profile_id === option.id),
  );
  return (
    available.find((option) => option.modelId === primaryModelId)?.id ||
    available[0]?.id ||
    routes[0]?.model_profile_id ||
    ""
  );
}

interface WorkflowPresetModalProps {
  mode: ModalMode;
  owner: PromptKind;
  seed: FormState;
  modelOptions: Option[];
  promptOptions: Option[];
  onSave: (form: FormState) => void;
  onCancel: () => void;
}

function WorkflowPresetModal({
  mode,
  owner,
  seed,
  modelOptions,
  promptOptions,
  onSave,
  onCancel,
}: WorkflowPresetModalProps) {
  const messages = useMessages();
  const labels = messages.workflowPresets;
  const [form, setForm] = useState<FormState>(seed);
  const baselineRef = useRef<FormState>(seed);
  const isDirty = !formEquals(form, baselineRef.current);
  const canSave =
    form.name.trim().length > 0 &&
    (form.advanced
      ? form.routes.length > 0 &&
        form.group_concurrency > 0 &&
        form.routes.every(
          (route) =>
            route.model_profile_id && route.prompt_preset_id &&
            route.rpm_limit !== null && route.rpm_limit >= 0,
        ) &&
        new Set(form.routes.map((route) => route.model_profile_id)).size ===
          form.routes.length &&
        (!form.fallback_route ||
          (form.fallback_route.model_profile_id &&
            form.fallback_route.prompt_preset_id &&
            form.fallback_route.rpm_limit !== null &&
            form.fallback_route.rpm_limit >= 0))
      : form.model_profile_id.trim().length > 0 &&
        form.prompt_preset_id.trim().length > 0);
  const routeModelIds = form.routes.map(
    (route) => modelOptions.find((model) => model.id === route.model_profile_id)?.modelId,
  );
  const routeThinkingLevels = form.routes.map(
    (route) => modelOptions.find((model) => model.id === route.model_profile_id)?.thinkingLevel,
  );
  const routeMismatch =
    new Set(routeModelIds).size > 1 ||
    new Set(routeThinkingLevels).size > 1 ||
    new Set(form.routes.map((route) => route.prompt_preset_id)).size > 1;

  useEffect(() => {
    setForm(seed);
    baselineRef.current = seed;
  }, [seed]);

  const handleCancel = () => {
    if (isDirty && !window.confirm(messages.promptModal.unsavedChangesConfirm)) {
      return;
    }
    onCancel();
  };

  useEscapeKey(handleCancel);

  return (
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={handleCancel}
    >
      <div
        className={`${styles.modal} ${form.advanced ? styles.modalWide : ""}`.trim()}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h2 className={styles.modalTitle}>
            {mode === "edit" ? labels.formTitleEdit : labels.formTitleCreate}
          </h2>
          <button
            type="button"
            className={styles.closeButton}
            onClick={handleCancel}
            aria-label={labels.cancelAction}
          >
            ×
          </button>
        </div>
        <div className={styles.modalBody}>
          <TextField
            label={labels.nameLabel}
            value={form.name}
            placeholder={labels.namePlaceholder}
            onChange={(name) => setForm((current) => ({ ...current, name }))}
          />
          {owner === "translation" ? (
            <label className={styles.toggleRow}>
              <span>{labels.advanced}</span>
              <input
                type="checkbox"
                checked={form.advanced}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    advanced: event.target.checked,
                    routes:
                      event.target.checked && current.routes.length === 0
                        ? [{
                            model_profile_id:
                              current.model_profile_id || modelOptions[0]?.id || "",
                            prompt_preset_id:
                              current.prompt_preset_id || promptOptions[0]?.id || "",
                            rpm_limit: modelRpm(
                              modelOptions,
                              current.model_profile_id || modelOptions[0]?.id || "",
                            ),
                          }]
                        : current.routes,
                  }))
                }
              />
            </label>
          ) : null}
          {form.advanced && owner === "translation" ? (
            <>
              <div className={styles.advancedHeading}>
                <strong>{labels.normalRoutes}</strong>
                <button
                  type="button"
                  className={styles.textAction}
                  onClick={() => setForm((current) => {
                    const modelId = suggestedRouteModel(modelOptions, current.routes);
                    return {
                      ...current,
                      routes: [
                        ...current.routes,
                        {
                          model_profile_id: modelId,
                          prompt_preset_id:
                            current.routes[0]?.prompt_preset_id || promptOptions[0]?.id || "",
                          rpm_limit: modelRpm(modelOptions, modelId),
                        },
                      ],
                    };
                  })}
                  disabled={form.routes.length >= modelOptions.length}
                >
                  {labels.addRoute}
                </button>
              </div>
              {form.routes.map((route, index) => (
                <div className={styles.routeRow} key={index}>
                  <SelectField
                    label={`${labels.modelLabel} ${index + 1}`}
                    value={route.model_profile_id}
                    options={modelOptions}
                    emptyLabel={labels.missingSelection}
                    onChange={(model_profile_id) =>
                      setForm((current) => ({
                        ...current,
                        routes: current.routes.map((item, position) =>
                          position === index
                            ? { ...item, model_profile_id, rpm_limit: modelRpm(modelOptions, model_profile_id) }
                            : item,
                        ),
                      }))
                    }
                  />
                  <SelectField
                    label={labels.promptLabel}
                    value={route.prompt_preset_id}
                    options={promptOptions}
                    emptyLabel={labels.missingSelection}
                    onChange={(prompt_preset_id) =>
                      setForm((current) => ({
                        ...current,
                        routes: current.routes.map((item, position) =>
                          position === index ? { ...item, prompt_preset_id } : item,
                        ),
                      }))
                    }
                  />
                  <NumberInput
                    label={labels.routeRpm}
                    value={route.rpm_limit ?? 0}
                    min={0}
                    title={labels.routeRpmHint}
                    onChange={(rpm_limit) =>
                      setForm((current) => ({
                        ...current,
                        routes: current.routes.map((item, position) =>
                          position === index ? { ...item, rpm_limit } : item,
                        ),
                      }))
                    }
                  />
                  {form.routes.length > 1 ? (
                    <div className={styles.routeActions}>
                      {index === 0 ? (
                        <span className={styles.routePrimary}>{labels.primaryRoute}</span>
                      ) : (
                        <button
                          type="button"
                          className={styles.textAction}
                          onClick={() =>
                            setForm((current) => {
                              const routes = [...current.routes];
                              const [primary] = routes.splice(index, 1);
                              routes.unshift(primary);
                              return { ...current, routes };
                            })
                          }
                        >
                          {labels.setPrimary}
                        </button>
                      )}
                      <button
                        type="button"
                        className={styles.textAction}
                        onClick={() =>
                          setForm((current) => ({
                            ...current,
                            routes: current.routes.filter((_, position) => position !== index),
                          }))
                        }
                      >
                        {labels.removeRoute}
                      </button>
                    </div>
                  ) : null}
                </div>
              ))}
              {routeMismatch ? (
                <p className={styles.warning}>{labels.routeMismatchWarning}</p>
              ) : null}
              <NumberInput
                label={labels.groupConcurrency}
                value={form.group_concurrency}
                onChange={(group_concurrency) =>
                  setForm((current) => ({ ...current, group_concurrency }))
                }
              />
              <label className={styles.toggleRow}>
                <span>{labels.failedRetry}</span>
                <input
                  type="checkbox"
                  checked={form.retry_failed}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, retry_failed: event.target.checked }))
                  }
                />
              </label>
              {form.retry_failed ? (
                <>
                  <label className={styles.toggleRow}>
                    <span>{labels.fallbackRoute}</span>
                    <input
                      type="checkbox"
                      checked={form.fallback_route !== null}
                      onChange={(event) => setForm((current) => {
                        const modelId = suggestedRouteModel(modelOptions, current.routes);
                        return {
                          ...current,
                          fallback_route: event.target.checked
                            ? {
                                model_profile_id: modelId,
                                prompt_preset_id:
                                  current.routes[0]?.prompt_preset_id || promptOptions[0]?.id || "",
                                rpm_limit: modelRpm(modelOptions, modelId),
                              }
                            : null,
                        };
                      })}
                    />
                  </label>
                  {form.fallback_route ? (
                    <div className={styles.routeRow}>
                      <SelectField
                        label={labels.modelLabel}
                        value={form.fallback_route.model_profile_id}
                        options={modelOptions}
                        emptyLabel={labels.missingSelection}
                        onChange={(model_profile_id) =>
                          setForm((current) => ({
                            ...current,
                            fallback_route: current.fallback_route
                              ? {
                                  ...current.fallback_route,
                                  model_profile_id,
                                  rpm_limit: modelRpm(modelOptions, model_profile_id),
                                }
                              : null,
                          }))
                        }
                      />
                      <SelectField
                        label={labels.promptLabel}
                        value={form.fallback_route.prompt_preset_id}
                        options={promptOptions}
                        emptyLabel={labels.missingSelection}
                        onChange={(prompt_preset_id) =>
                          setForm((current) => ({
                            ...current,
                            fallback_route: current.fallback_route
                              ? { ...current.fallback_route, prompt_preset_id }
                              : null,
                          }))
                        }
                      />
                      <NumberInput
                        label={labels.routeRpm}
                        value={form.fallback_route.rpm_limit ?? 0}
                        min={0}
                        title={labels.routeRpmHint}
                        onChange={(rpm_limit) =>
                          setForm((current) => ({
                            ...current,
                            fallback_route: current.fallback_route
                              ? { ...current.fallback_route, rpm_limit }
                              : null,
                          }))
                        }
                      />
                    </div>
                  ) : null}
                </>
              ) : null}
            </>
          ) : (
            <>
              <SelectField
                label={labels.modelLabel}
                value={form.model_profile_id}
                options={modelOptions}
                emptyLabel={labels.missingSelection}
                onChange={(model_profile_id) =>
                  setForm((current) => ({ ...current, model_profile_id }))
                }
              />
              <SelectField
                label={labels.promptLabel}
                value={form.prompt_preset_id}
                options={promptOptions}
                emptyLabel={labels.missingSelection}
                onChange={(prompt_preset_id) =>
                  setForm((current) => ({ ...current, prompt_preset_id }))
                }
              />
            </>
          )}
          <div className={styles.languageGrid}>
            <div className={styles.field}>
              <label className={styles.fieldLabel}>
                {labels.sourceLanguageLabel}
              </label>
              <LanguageSelect
                ariaLabel={labels.sourceLanguageLabel}
                value={form.source_language}
                onChange={(source_language) =>
                  setForm((current) => ({ ...current, source_language }))
                }
              />
            </div>
            <div className={styles.field}>
              <label className={styles.fieldLabel}>
                {labels.targetLanguageLabel}
              </label>
              <LanguageSelect
                ariaLabel={labels.targetLanguageLabel}
                value={form.target_language}
                onChange={(target_language) =>
                  setForm((current) => ({ ...current, target_language }))
                }
              />
            </div>
          </div>
        </div>
        <div className={styles.modalFooter}>
          <Pill variant="ghost" onClick={handleCancel}>
            {labels.cancelAction}
          </Pill>
          <Pill onClick={() => onSave(form)} disabled={!canSave}>
            {labels.saveAction}
          </Pill>
        </div>
      </div>
    </div>
  );
}

interface SelectFieldProps {
  label: string;
  value: string;
  options: Option[];
  emptyLabel: string;
  onChange: (value: string) => void;
}

function SelectField({
  label,
  value,
  options,
  emptyLabel,
  onChange,
}: SelectFieldProps) {
  return (
    <div className={styles.field}>
      <label className={styles.fieldLabel}>{label}</label>
      <select
        className={styles.select}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{emptyLabel}</option>
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function formEquals(a: FormState, b: FormState): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function NumberInput({
  label,
  value,
  min = 1,
  title,
  onChange,
}: {
  label: string;
  value: number;
  min?: number;
  title?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className={styles.field}>
      <span className={styles.fieldLabel}>{label}</span>
      <input
        className={styles.select}
        type="number"
        min={min}
        step={1}
        value={value}
        title={title}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
