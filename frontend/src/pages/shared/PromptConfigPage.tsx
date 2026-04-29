import { useEffect, useState } from "react";
import { useMessages } from "@/locales";
import { usePromptPresets } from "@/store/usePromptPresetsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { PromptPresetModal } from "@/components/PromptPresetModal";
import type { PromptKind, PromptPresetBody } from "@/bridge";
import styles from "./PromptConfigPage.module.css";

interface PromptConfigPageProps {
  owner: "translation" | "glossary";
}

function ownerToKind(owner: "translation" | "glossary"): PromptKind {
  return owner;
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

  const activeSummary =
    slice.presets.find((preset) => preset.id === slice.activeId) ??
    slice.presets[0];

  // Load the preset body when the modal opens in edit mode.
  useEffect(() => {
    if (modalState?.mode !== "edit") {
      setEditBody(null);
      return;
    }
    let cancelled = false;
    void store.read(modalState.presetId).then((preset) => {
      if (!cancelled) setEditBody(preset);
    });
    return () => {
      cancelled = true;
    };
  }, [modalState, store]);

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
              <Pill variant="ghost" onClick={() => void handleDuplicateActive()}>
                {p.actions.duplicate}
              </Pill>
            ) : null}
          </div>
        }
      >
        <div className={styles.list}>
          {slice.presets.map((preset) => (
            <div
              key={preset.id}
              className={`${styles.row} ${preset.id === slice.activeId ? styles.rowActive : ""}`.trim()}
            >
              <button
                type="button"
                role="radio"
                aria-checked={preset.id === slice.activeId}
                className={styles.radioBtn}
                onClick={() => {
                  void store.selectActive(kind, preset.id);
                }}
              >
                <span
                  className={`${styles.radio} ${preset.id === slice.activeId ? styles.radioActive : ""}`.trim()}
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
              <Pill
                variant="ghost"
                onClick={() =>
                  setModalState({ mode: "edit", presetId: preset.id })
                }
              >
                {messages.promptModal.titleEdit}
              </Pill>
            </div>
          ))}
        </div>
      </Panel>

      {modalState ? (
        <PromptPresetModal
          mode={modalState.mode}
          kind={kind}
          seed={
            modalState.mode === "edit"
              ? editBody
              : modalState.seed
          }
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
