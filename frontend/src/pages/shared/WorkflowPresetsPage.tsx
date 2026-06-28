import { useEffect, useMemo, useRef, useState } from "react";

import type { Language, PromptKind, WorkflowPreset } from "@/bridge";
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
  });

  const presetToForm = (preset: WorkflowPreset): FormState => ({
    id: preset.id,
    name: preset.name,
    model_profile_id: preset.model_profile_id,
    prompt_preset_id: preset.prompt_preset_id,
    source_language: preset.source_language,
    target_language: preset.target_language,
  });

  const activePreset = workflowSlice.matchedId
    ? workflowSlice.presets.find((preset) => preset.id === workflowSlice.matchedId)
    : undefined;
  const activeModel = modelById(String(activeModelId));
  const activePrompt = promptById(String(activePromptId));
  const activeSummary = [
    `${languageLabels[activeSourceLanguage]} → ${languageLabels[activeTargetLanguage]}`,
    activeModel?.display_name ?? labels.missingSelection,
    activePrompt?.name ?? labels.missingSelection,
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
    const draft = {
      name: form.name.trim(),
      model_profile_id: form.model_profile_id,
      prompt_preset_id: form.prompt_preset_id,
      source_language: form.source_language,
      target_language: form.target_language,
      enabled: true,
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
                model?.display_name ?? preset.model_profile_id,
                prompt?.name ?? preset.prompt_preset_id,
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
          seed={modal.seed}
          modelOptions={models.profiles.map((profile) => ({
            id: profile.id,
            label: `${profile.display_name} · ${profile.model_id}`,
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
}

interface WorkflowPresetModalProps {
  mode: ModalMode;
  seed: FormState;
  modelOptions: Option[];
  promptOptions: Option[];
  onSave: (form: FormState) => void;
  onCancel: () => void;
}

function WorkflowPresetModal({
  mode,
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
    form.model_profile_id.trim().length > 0 &&
    form.prompt_preset_id.trim().length > 0;

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
      <div className={styles.modal} onClick={(event) => event.stopPropagation()}>
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
  return (
    a.id === b.id &&
    a.name === b.name &&
    a.model_profile_id === b.model_profile_id &&
    a.prompt_preset_id === b.prompt_preset_id &&
    a.source_language === b.source_language &&
    a.target_language === b.target_language
  );
}
