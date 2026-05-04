import { useEffect, useState } from "react";
import { useMessages, useI18n } from "@/locales";
import {
  usePromptPresets,
  usePromptPresetsStore,
} from "@/store/usePromptPresetsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { OverflowMenu } from "@/components/OverflowMenu";
import { PromptPresetModal } from "@/components/PromptPresetModal";
import type { PromptKind, PromptPresetBody } from "@/bridge";
import styles from "./PromptConfigPage.module.css";

interface PromptConfigPageProps {
  owner: PromptKind;
}

function ownerToKind(owner: PromptKind): PromptKind {
  return owner;
}

function defaultPromptId(kind: PromptKind, locale: string): string {
  if (kind === "glossary_review") return `default-glossary-review-${locale}`;
  return `default-${kind}-${locale}`;
}

type ModalState =
  | { mode: "create"; seed: PromptPresetBody | null }
  | { mode: "edit"; presetId: string }
  | null;

export function PromptConfigPage({ owner }: PromptConfigPageProps) {
  const messages = useMessages();
  const { prompt: p } = messages;
  const kind = ownerToKind(owner);
  const store = usePromptPresets(kind);
  const slice = store[kind];
  const [modalState, setModalState] = useState<ModalState>(null);
  const [editBody, setEditBody] = useState<PromptPresetBody | null>(null);

  const locale = useI18n((state) => state.locale);
  const localeDefaultId = defaultPromptId(kind, locale);
  // Only the locale-matching system preset is shown — users picked
  // their UI language, so we surface the system prompt phrased in
  // that language. The non-matching system preset is still resolved
  // by id behind the scenes (e.g. if the user selected it before
  // switching locale).
  const visiblePresets = slice.presets.filter((preset) => {
    if (!preset.is_system) return true;
    return preset.id === localeDefaultId;
  });
  const fallbackSummary =
    visiblePresets.find((preset) => preset.id === localeDefaultId) ??
    visiblePresets[0];
  const displayedActiveId = slice.activeId ?? fallbackSummary?.id ?? null;
  const activeSummary =
    slice.presets.find((preset) => preset.id === displayedActiveId) ??
    fallbackSummary;

  // Load the preset body when the modal opens in edit mode. Use the
  // raw store getter so the effect identity does not depend on the
  // ``usePromptPresets`` snapshot (which mutates on every store
  // change and would otherwise reset ``editBody`` to null on each
  // render, defeating the load).
  useEffect(() => {
    if (modalState?.mode !== "edit") {
      setEditBody(null);
      return;
    }
    let cancelled = false;
    void usePromptPresetsStore
      .getState()
      .read(modalState.presetId)
      .then((preset) => {
        if (!cancelled) setEditBody(preset);
      });
    return () => {
      cancelled = true;
    };
  }, [modalState]);

  const handleAdd = () => {
    setModalState({ mode: "create", seed: null });
  };

  const handleDuplicateActive = async () => {
    if (!activeSummary) return;
    const body = await store.read(activeSummary.id);
    if (!body) return;
    setModalState({ mode: "create", seed: body });
  };

  return (
    <>
      <Panel title={p.pageTitle} subtitle={p.pageSub} />

      {slice.loadError && slice.loadError.code !== "bridge.io_error" ? (
        <Panel label={messages.errors.loadFailureTitle}>
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
                {activeSummary.is_default ? p.badgeDefault : p.badgeCustom}
              </span>
            </div>
          </div>
        ) : (
          <span className={styles.empty}>
            {messages.inspector.noActivePrompt}
          </span>
        )}
      </Panel>

      <Panel
        label={p.available}
        labelExtra={
          <div className={styles.headerActions}>
            <span>{p.availableHint}</span>
            <Pill variant="ghost" onClick={handleAdd}>
              {p.actions.add}
            </Pill>
            {activeSummary ? (
              <Pill
                variant="ghost"
                onClick={() => void handleDuplicateActive()}
              >
                {p.actions.duplicate}
              </Pill>
            ) : null}
          </div>
        }
      >
        <div className={styles.list}>
          {visiblePresets.map((preset) => {
            const open = () =>
              setModalState({ mode: "edit", presetId: preset.id });
            const duplicate = async () => {
              const body = await store.read(preset.id);
              if (body) setModalState({ mode: "create", seed: body });
            };
            const items = preset.is_system
              ? [
                  {
                    key: "view",
                    label: messages.rowMenu.view,
                    onSelect: open,
                  },
                  {
                    key: "duplicate",
                    label: messages.rowMenu.duplicate,
                    onSelect: () => void duplicate(),
                  },
                ]
              : [
                  {
                    key: "edit",
                    label: messages.rowMenu.edit,
                    onSelect: open,
                  },
                  {
                    key: "duplicate",
                    label: messages.rowMenu.duplicate,
                    onSelect: () => void duplicate(),
                  },
                  {
                    key: "delete",
                    label: messages.rowMenu.delete,
                    onSelect: () => void store.deletePreset(preset.id),
                    variant: "danger" as const,
                  },
                ];
            return (
              <div
                key={preset.id}
                className={`${styles.row} ${preset.id === displayedActiveId ? styles.rowActive : ""}`.trim()}
              >
                <button
                  type="button"
                  role="radio"
                  aria-checked={preset.id === displayedActiveId}
                  className={styles.radioBtn}
                  onClick={() => {
                    void store.selectActive(kind, preset.id);
                  }}
                >
                  <span
                    className={`${styles.radio} ${preset.id === displayedActiveId ? styles.radioActive : ""}`.trim()}
                    aria-hidden
                  />
                  <span className={styles.rowText}>
                    <span className={styles.rowName}>{preset.name}</span>
                    <span
                      className={styles.rowMeta}
                      title={preset.system_prompt}
                    >
                      {preset.system_prompt.replace(/\s+/g, " ").trim()}
                    </span>
                  </span>
                  <span className={styles.rowBadge}>
                    {preset.is_system
                      ? messages.rowMenu.systemBadge
                      : p.badgeCustom}
                  </span>
                </button>
                <OverflowMenu
                  ariaLabel={messages.rowMenu.triggerLabel}
                  items={items}
                />
              </div>
            );
          })}
        </div>
      </Panel>

      {modalState ? (
        <PromptPresetModal
          mode={modalState.mode}
          kind={kind}
          seed={modalState.mode === "edit" ? editBody : modalState.seed}
          onSaved={async () => {
            await store.refresh(kind);
            setModalState(null);
          }}
          onCancel={() => setModalState(null)}
        />
      ) : null}
    </>
  );
}
