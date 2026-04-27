import { useEffect, useState } from 'react';
import { useMessages } from '@/locales';
import { usePromptPresets } from '@/store/usePromptPresetsStore';
import { Panel } from '@/components/Panel';
import { Pill } from '@/components/Pill';
import { TextField } from '@/components/TextField';
import type { PromptKind, PromptPresetBody } from '@/bridge';
import styles from './PromptConfigPage.module.css';

interface PromptConfigPageProps {
  owner: 'translation' | 'glossary';
}

type PreviewTab = 'system' | 'suffix' | 'thinking';

function ownerToKind(owner: 'translation' | 'glossary'): PromptKind {
  return owner;
}

export function PromptConfigPage({ owner }: PromptConfigPageProps) {
  const messages = useMessages();
  const { prompt: p } = messages;
  const kind = ownerToKind(owner);
  const store = usePromptPresets(kind);
  const slice = store[kind];
  const [tab, setTab] = useState<PreviewTab>('system');
  const [body, setBody] = useState<PromptPresetBody | null>(null);

  const activeSummary =
    slice.presets.find((preset) => preset.id === slice.activeId) ??
    slice.presets[0];

  useEffect(() => {
    if (!activeSummary) {
      setBody(null);
      return;
    }
    let cancelled = false;
    void store.read(activeSummary.id).then((preset) => {
      if (!cancelled) setBody(preset);
    });
    return () => {
      cancelled = true;
    };
  }, [activeSummary?.id, store]);

  return (
    <>
      <Panel title={p.pageTitle} subtitle={p.pageSub} />

      {slice.loadError ? (
        <Panel label={messages.errors.runFailureTitle}>
          <pre className={styles.empty}>{slice.loadError.message}</pre>
        </Panel>
      ) : null}

      <Panel label={p.active} labelExtra={<span>{p.activeHint}</span>}>
        {activeSummary ? (
          <div className={styles.activeRow}>
            <div className={styles.av} aria-hidden />
            <div className={styles.activeText}>
              <b>{activeSummary.name}</b>
              <span className={styles.activeMeta}>
                {activeSummary.is_default
                  ? p.badgeDefault
                  : p.badgeCustom}
              </span>
            </div>
          </div>
        ) : (
          <span className={styles.empty}>{messages.inspector.noActivePrompt}</span>
        )}
      </Panel>

      <Panel label={p.available} labelExtra={<span>{p.availableHint}</span>}>
        <div className={styles.list}>
          {slice.presets.map((preset) => (
            <button
              key={preset.id}
              type="button"
              role="radio"
              aria-checked={preset.id === slice.activeId}
              className={`${styles.row} ${preset.id === slice.activeId ? styles.rowActive : ''}`.trim()}
              onClick={() => {
                void store.selectActive(kind, preset.id);
              }}
            >
              <span
                className={`${styles.radio} ${preset.id === slice.activeId ? styles.radioActive : ''}`.trim()}
                aria-hidden
              />
              <span className={styles.rowText}>
                <span className={styles.rowName}>{preset.name}</span>
                <span className={styles.rowMeta}>{preset.description}</span>
              </span>
              <span className={styles.rowBadge}>
                {preset.is_default ? p.badgeDefault : p.badgeCustom}
              </span>
            </button>
          ))}
        </div>
        <div className={styles.actions}>
          <Pill
            variant="ghost"
            onClick={() => {
              void store.createPreset(kind, {
                name: `New preset`,
                kind,
                description: '',
                enabled: true,
                system_prompt: '',
                suffix_prompt: '',
                thinking_prompt: '',
              });
            }}
          >
            {p.actions.add}
          </Pill>
          {activeSummary ? (
            <Pill
              variant="ghost"
              onClick={() => {
                void store.duplicatePreset(activeSummary.id);
              }}
            >
              {p.actions.duplicate}
            </Pill>
          ) : null}
          {activeSummary && !activeSummary.is_default ? (
            <Pill
              variant="ghost"
              onClick={() => {
                void store.deletePreset(activeSummary.id);
              }}
            >
              {p.actions.delete}
            </Pill>
          ) : null}
        </div>
      </Panel>

      {body ? (
        <Panel label={p.preview}>
          <div className={styles.tabs} role="tablist">
            <TabButton active={tab === 'system'} onClick={() => setTab('system')}>
              {p.previewSystem}
            </TabButton>
            <TabButton active={tab === 'suffix'} onClick={() => setTab('suffix')}>
              {p.previewSuffix}
            </TabButton>
            <TabButton
              active={tab === 'thinking'}
              onClick={() => setTab('thinking')}
            >
              {p.previewThinking}
            </TabButton>
          </div>
          <PromptEditor
            body={body}
            tab={tab}
            onChange={(field, value) => {
              setBody({ ...body, [field]: value });
              void store.updatePreset(body.id, { [field]: value });
            }}
            emptyText={p.noThinkingPrompt}
          />
        </Panel>
      ) : null}
    </>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={`${styles.tab} ${active ? styles.tabActive : ''}`.trim()}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

interface PromptEditorProps {
  body: PromptPresetBody;
  tab: PreviewTab;
  onChange: (
    field: 'system_prompt' | 'suffix_prompt' | 'thinking_prompt',
    value: string,
  ) => void;
  emptyText: string;
}

function PromptEditor({ body, tab, onChange, emptyText }: PromptEditorProps) {
  const field =
    tab === 'system'
      ? 'system_prompt'
      : tab === 'suffix'
      ? 'suffix_prompt'
      : 'thinking_prompt';
  const value = body[field];
  if (body.is_default && tab !== 'system' && !value) {
    return <div className={styles.empty}>{emptyText}</div>;
  }
  return (
    <TextField
      label=""
      value={value}
      onChange={(v) => onChange(field, v)}
      multiline
      rows={10}
      mono
    />
  );
}
