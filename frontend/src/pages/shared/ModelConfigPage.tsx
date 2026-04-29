import { useState } from "react";
import { useMessages } from "@/locales";
import { useModelProfiles } from "@/store/useModelProfilesStore";
import { useModuleSettings, useSettingsStore } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { ChevronDownIcon } from "@/components/Icon";
import { ModelProfileModal } from "@/components/ModelProfileModal";
import type { ModelProfile } from "@/bridge";
import styles from "./ModelConfigPage.module.css";

interface ModelConfigPageProps {
  owner: "translation" | "glossary";
}

export function ModelConfigPage({ owner }: ModelConfigPageProps) {
  const messages = useMessages();
  const { model: m, modelExtra } = messages;
  const store = useModelProfiles();
  const appSettings = useModuleSettings("app");

  const activeKey =
    owner === "translation"
      ? "active_translation_model_id"
      : "active_glossary_model_id";
  const activeId = appSettings.draft?.[activeKey] ?? null;
  type ModalState =
    | { mode: "create" }
    | { mode: "edit"; profileId: string }
    | null;
  const [modalState, setModalState] = useState<ModalState>(null);
  const editingProfile =
    modalState?.mode === "edit"
      ? (store.profiles.find((p) => p.id === modalState.profileId) ?? null)
      : null;

  const handleSetActive = async (profileId: string) => {
    await store.selectActive(owner, profileId);
    void useSettingsStore.getState().hydrate();
  };

  return (
    <>
      <Panel title={m.pageTitle} subtitle={m.pageSub} />

      {store.loadError ? (
        <Panel label={messages.errors.loadFailureTitle}>
          <div className={styles.errorRow}>
            <code className={styles.errorCode}>{store.loadError.code}</code>
            <span className={styles.errorMessage}>
              {store.loadError.message}
            </span>
            <button
              type="button"
              className={styles.errorDismiss}
              onClick={() => void store.refresh()}
            >
              {messages.errors.retry}
            </button>
          </div>
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

      {(() => {
        const seeded = store.profiles.filter((p) => p.id.startsWith("preset-"));
        const configured = store.profiles.filter(
          (p) => !p.id.startsWith("preset-"),
        );
        const cfg = m.sections.configured;
        return (
          <>
            <Panel
              label={m.sections.preset.title}
              labelExtra={
                <div className={styles.presetActions}>
                  <span>{m.sections.preset.sub}</span>
                </div>
              }
            >
              <div className={styles.chipGrid}>
                {seeded.map((profile) => (
                  <ModelChip
                    key={profile.id}
                    profile={profile}
                    active={profile.id === activeId}
                    editing={
                      modalState?.mode === "edit" &&
                      modalState.profileId === profile.id
                    }
                    activeBadge={modelExtra.activeBadge}
                    onEdit={() =>
                      setModalState({ mode: "edit", profileId: profile.id })
                    }
                  />
                ))}
              </div>
            </Panel>

            <Panel
              label={cfg.title}
              labelExtra={
                <div className={styles.presetActions}>
                  <span>{cfg.sub}</span>
                  <Pill
                    variant="ghost"
                    onClick={() => setModalState({ mode: "create" })}
                  >
                    + {modelExtra.addCustom}
                  </Pill>
                </div>
              }
            >
              {configured.length === 0 ? (
                <div className={styles.empty}>{cfg.empty}</div>
              ) : (
                <div className={styles.configuredList}>
                  {configured.map((profile) => (
                    <ConfiguredModelRow
                      key={profile.id}
                      profile={profile}
                      active={profile.id === activeId}
                      labels={cfg}
                      onApply={() => void handleSetActive(profile.id)}
                      onEdit={() =>
                        setModalState({ mode: "edit", profileId: profile.id })
                      }
                      onDelete={() => void store.deleteProfile(profile.id)}
                    />
                  ))}
                </div>
              )}
            </Panel>
          </>
        );
      })()}

      {modalState ? (
        <ModelProfileModal
          mode={modalState.mode}
          profile={editingProfile ?? undefined}
          isActive={editingProfile !== null && editingProfile.id === activeId}
          onSaved={async (id) => {
            await store.refresh();
            setModalState(null);
            // For brand-new profiles created via the modal, surface
            // them in the active selection if nothing else is active.
            if (modalState.mode === "create" && activeId === null) {
              await handleSetActive(id);
            }
          }}
          onCancel={() => setModalState(null)}
          onDelete={
            modalState.mode === "edit" && editingProfile
              ? async () => {
                  const ok = await store.deleteProfile(editingProfile.id);
                  if (ok) setModalState(null);
                }
              : undefined
          }
          onSetActive={
            modalState.mode === "edit" && editingProfile
              ? () => handleSetActive(editingProfile.id)
              : undefined
          }
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
    active ? styles.chipActive : "",
    editing && !active ? styles.chipEditing : "",
  ]
    .filter(Boolean)
    .join(" ");
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

interface ConfiguredModelRowProps {
  profile: ModelProfile;
  active: boolean;
  labels: {
    applyAction: string;
    appliedBadge: string;
    editAction: string;
    deleteAction: string;
  };
  onApply: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

function ConfiguredModelRow({
  profile,
  active,
  labels,
  onApply,
  onEdit,
  onDelete,
}: ConfiguredModelRowProps) {
  return (
    <div
      className={`${styles.configuredRow} ${active ? styles.configuredRowActive : ""}`}
    >
      <div className={styles.configuredText}>
        <span className={styles.configuredName}>{profile.display_name}</span>
        <span className={styles.configuredMeta}>
          {profile.provider_format} · {profile.model_id}
        </span>
      </div>
      <div className={styles.configuredActions}>
        {active ? (
          <span className={styles.configuredBadge}>{labels.appliedBadge}</span>
        ) : (
          <Pill variant="ghost" onClick={onApply}>
            {labels.applyAction}
          </Pill>
        )}
        <Pill variant="ghost" onClick={onEdit}>
          {labels.editAction}
        </Pill>
        <Pill variant="ghost" onClick={onDelete}>
          {labels.deleteAction}
        </Pill>
      </div>
    </div>
  );
}
