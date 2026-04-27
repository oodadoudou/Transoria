import { useState } from 'react';
import { useMessages } from '@/locales';
import { useModelProfiles } from '@/store/useModelProfilesStore';
import { useModuleSettings, useSettingsStore } from '@/store/useSettingsStore';
import { Panel } from '@/components/Panel';
import { Pill } from '@/components/Pill';
import { NumberField } from '@/components/NumberField';
import { TextField } from '@/components/TextField';
import { DebouncedTextField } from '@/components/DebouncedTextField';
import { Segmented } from '@/components/Segmented';
import { ChevronDownIcon } from '@/components/Icon';
import type { ModelProfile, ProviderFormat, ThinkingLevel } from '@/bridge';
import styles from './ModelConfigPage.module.css';

interface ModelConfigPageProps {
  owner: 'translation' | 'glossary';
}

const PROVIDER_OPTIONS: Array<{ id: ProviderFormat; label: string }> = [
  { id: 'openai', label: 'OpenAI' },
  { id: 'anthropic', label: 'Anthropic' },
  { id: 'google', label: 'Google' },
  { id: 'sakura', label: 'Sakura' },
  { id: 'custom', label: 'Custom' },
];

const THINKING_OPTIONS: Array<{ id: ThinkingLevel; label: string }> = [
  { id: 'off', label: 'Off' },
  { id: 'low', label: 'Low' },
  { id: 'medium', label: 'Medium' },
  { id: 'high', label: 'High' },
];

export function ModelConfigPage({ owner }: ModelConfigPageProps) {
  const messages = useMessages();
  const { model: m, modelExtra } = messages;
  const store = useModelProfiles();
  const appSettings = useModuleSettings('app');

  const activeKey =
    owner === 'translation'
      ? 'active_translation_model_id'
      : 'active_glossary_model_id';
  const activeId = appSettings.draft?.[activeKey] ?? null;
  const [editingId, setEditingId] = useState<string | null>(null);
  const selectedId = editingId ?? activeId ?? store.profiles[0]?.id ?? null;
  const selected = store.profiles.find((p) => p.id === selectedId) ?? null;

  const handleSetActive = async () => {
    if (!selected) return;
    await store.selectActive(owner, selected.id);
    void useSettingsStore.getState().hydrate();
  };

  return (
    <>
      <Panel title={m.pageTitle} subtitle={m.pageSub} />

      {store.loadError ? (
        <Panel label={messages.errors.runFailureTitle}>
          <pre className={styles.errorText}>{store.loadError.message}</pre>
        </Panel>
      ) : null}

      {store.mutationError ? (
        <Panel label={messages.errors.runFailureTitle}>
          <div className={styles.errorRow}>
            <code className={styles.errorCode}>{store.mutationError.code}</code>
            <span className={styles.errorMessage}>
              {store.mutationError.message}
            </span>
            <button
              type="button"
              className={styles.errorDismiss}
              onClick={() => store.clearMutationError()}
            >
              {messages.errors.dismiss}
            </button>
          </div>
        </Panel>
      ) : null}

      <Panel
        label={m.sections.preset.title}
        labelExtra={<span>{m.sections.preset.sub}</span>}
      >
        <div className={styles.chipGrid}>
          {store.profiles.map((profile) => (
            <ModelChip
              key={profile.id}
              profile={profile}
              active={profile.id === activeId}
              editing={profile.id === selectedId}
              activeBadge={modelExtra.activeBadge}
              onEdit={() => setEditingId(profile.id)}
            />
          ))}
        </div>
      </Panel>

      {selected ? (
        <EditPanel
          profile={selected}
          isActive={selected.id === activeId}
          onUpdate={(patch) => store.updateProfile(selected.id, patch)}
          onDelete={() => {
            void store.deleteProfile(selected.id).then((ok) => {
              if (ok) setEditingId(null);
            });
          }}
          onSetApiKey={(keys) => store.setApiKey(selected.id, keys)}
          onSetActive={handleSetActive}
        />
      ) : null}
    </>
  );
}

interface ModelChipProps {
  profile: ModelProfile;
  active: boolean;
  editing: boolean;
  activeBadge: string;
  onEdit: () => void;
}

function ModelChip({
  profile,
  active,
  editing,
  activeBadge,
  onEdit,
}: ModelChipProps) {
  const className = [
    styles.chip,
    active ? styles.chipActive : '',
    editing && !active ? styles.chipEditing : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <button type="button" className={className} onClick={onEdit}>
      <span className={styles.chipName}>{profile.display_name}</span>
      {active ? (
        <span className={styles.chipBadge}>{activeBadge}</span>
      ) : (
        <ChevronDownIcon size={14} />
      )}
    </button>
  );
}

interface EditPanelProps {
  profile: ModelProfile;
  isActive: boolean;
  onUpdate: (patch: Partial<ModelProfile>) => void;
  onDelete: () => void;
  onSetApiKey: (keys: string[]) => void;
  onSetActive: () => void;
}

function EditPanel({
  profile,
  isActive,
  onUpdate,
  onDelete,
  onSetApiKey,
  onSetActive,
}: EditPanelProps) {
  const messages = useMessages();
  const { model: m, modelExtra } = messages;
  const [apiKeyDraft, setApiKeyDraft] = useState('');

  return (
    <>
      <Panel
        label={`${m.editTitle} · ${profile.display_name}`}
        labelExtra={
          isActive ? (
            <span>{modelExtra.activeBadge}</span>
          ) : (
            <Pill variant="ghost" onClick={onSetActive}>
              {modelExtra.setActive}
            </Pill>
          )
        }
      >
        <div className={styles.fields}>
          <DebouncedTextField
            label={m.displayName}
            value={profile.display_name}
            onCommit={(v) => onUpdate({ display_name: v })}
            help={m.displayNameHelp}
          />
          <DebouncedTextField
            label={m.baseUrl}
            value={profile.base_url}
            onCommit={(v) => onUpdate({ base_url: v })}
            help={m.baseUrlHelp}
            mono
          />
          <DebouncedTextField
            label={m.modelId}
            value={profile.model_id}
            onCommit={(v) => onUpdate({ model_id: v })}
            help={m.modelIdHelp}
            mono
          />
          <div className={styles.formatRow}>
            <span className={styles.formatLabel}>{m.apiFormatLabel}</span>
            <Segmented<ProviderFormat>
              ariaLabel={m.apiFormatLabel}
              options={PROVIDER_OPTIONS}
              value={profile.provider_format}
              onChange={(v) => onUpdate({ provider_format: v })}
            />
          </div>
          <div className={styles.apiKeyRow}>
            <TextField
              label={m.apiKeys}
              value={apiKeyDraft}
              onChange={setApiKeyDraft}
              placeholder={
                profile.api_key_status === 'present'
                  ? profile.api_key_masked
                  : m.apiKeysPlaceholder
              }
              help={m.apiKeysHelp}
              mono
            />
            <button
              type="button"
              className={styles.apiKeySave}
              onClick={() => {
                if (apiKeyDraft.trim()) {
                  onSetApiKey([apiKeyDraft.trim()]);
                  setApiKeyDraft('');
                }
              }}
            >
              {messages.settingsToolbar.save}
            </button>
          </div>
        </div>
      </Panel>

      <Panel label={m.limits} labelExtra={<span>{m.limitsHint}</span>}>
        <div className={styles.numberGrid}>
          <NumberField
            label={m.inputTokenLimit}
            value={profile.input_token_limit}
            onChange={(v) => onUpdate({ input_token_limit: v })}
            help={m.inputTokenLimitHelp}
            min={0}
          />
          <NumberField
            label={m.outputTokenLimit}
            value={profile.max_output_tokens}
            onChange={(v) => onUpdate({ max_output_tokens: v })}
            help={m.outputTokenLimitHelp}
            min={0}
          />
          <NumberField
            label={m.concurrency}
            value={profile.concurrency_limit}
            onChange={(v) => onUpdate({ concurrency_limit: v })}
            help={m.concurrencyHelp}
            min={1}
          />
          <NumberField
            label={m.rpm}
            value={profile.rpm_limit}
            onChange={(v) => onUpdate({ rpm_limit: v })}
            help={m.rpmHelp}
            min={0}
          />
          <NumberField
            label={m.tpm}
            value={profile.tpm_limit}
            onChange={(v) => onUpdate({ tpm_limit: v })}
            help={m.tpmHelp}
            min={0}
          />
          <NumberField
            label={m.retryAttempts}
            value={profile.retry_attempts}
            onChange={(v) => onUpdate({ retry_attempts: v })}
            help={m.retryAttemptsHelp}
            min={0}
            max={10}
          />
          <NumberField
            label={modelExtra.timeoutSeconds}
            value={Math.round(profile.timeout_seconds)}
            onChange={(v) => onUpdate({ timeout_seconds: v })}
            min={5}
            max={1800}
          />
        </div>
      </Panel>

      <Panel label={m.reasoning} labelExtra={<span>{m.reasoningHint}</span>}>
        <div className={styles.formatRow}>
          <span className={styles.formatLabel}>{m.thinkingLevel}</span>
          <Segmented<ThinkingLevel>
            ariaLabel={m.thinkingLevel}
            options={THINKING_OPTIONS}
            value={profile.thinking_level}
            onChange={(v) => onUpdate({ thinking_level: v })}
          />
        </div>
      </Panel>

      <Panel>
        <div className={styles.dangerRow}>
          <button
            type="button"
            className={styles.deleteButton}
            onClick={onDelete}
          >
            {modelExtra.deleteProfile}
          </button>
        </div>
      </Panel>
    </>
  );
}
