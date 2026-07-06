import { useState } from "react";
import { useMessages } from "@/locales";
import { useModelProfiles } from "@/store/useModelProfilesStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { ChevronDownIcon } from "@/components/Icon";
import { ModelProfileModal } from "@/components/ModelProfileModal";
import { OverflowMenu } from "@/components/OverflowMenu";
import type { ModelProfile } from "@/bridge";
import styles from "./ModelConfigPage.module.css";

type ModalState =
  | { mode: "create" }
  | { mode: "edit"; profileId: string }
  | null;

export function ModelConfigPage() {
  const messages = useMessages();
  const { model: m, modelExtra } = messages;
  const store = useModelProfiles();

  const [modalState, setModalState] = useState<ModalState>(null);
  const editingProfile =
    modalState?.mode === "edit"
      ? (store.profiles.find((p) => p.id === modalState.profileId) ?? null)
      : null;

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
                    editing={
                      modalState?.mode === "edit" &&
                      modalState.profileId === profile.id
                    }
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
                      menu={messages.rowMenu}
                      onEdit={() =>
                        setModalState({ mode: "edit", profileId: profile.id })
                      }
                      onDuplicate={async () => {
                        const copy = await store.duplicateProfile(profile.id);
                        if (copy) {
                          setModalState({ mode: "edit", profileId: copy.id });
                        }
                      }}
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
          onSaved={async () => {
            await store.refresh();
            setModalState(null);
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
        />
      ) : null}
    </>
  );
}

interface ModelChipProps {
  profile: ModelProfile;
  editing: boolean;
  onEdit: () => void;
}

function ModelChip({ profile, editing, onEdit }: ModelChipProps) {
  const className = [styles.chip, editing ? styles.chipEditing : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <button type="button" className={className} onClick={onEdit}>
      <span className={styles.chipName}>{profile.display_name}</span>
      <ChevronDownIcon size={14} />
    </button>
  );
}

interface ConfiguredModelRowProps {
  profile: ModelProfile;
  menu: {
    triggerLabel: string;
    edit: string;
    duplicate: string;
    delete: string;
  };
  onEdit: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

function ConfiguredModelRow({
  profile,
  menu,
  onEdit,
  onDuplicate,
  onDelete,
}: ConfiguredModelRowProps) {
  const profileMeta = `${profile.provider_format} · ${profile.model_id}`;
  return (
    <div className={styles.configuredRow} onDoubleClick={onEdit}>
      <div className={styles.configuredText}>
        <span className={styles.configuredName} title={profile.display_name}>
          {profile.display_name}
        </span>
        <span className={styles.configuredMeta} title={profileMeta}>
          {profileMeta}
        </span>
      </div>
      <div className={styles.configuredActions}>
        <OverflowMenu
          ariaLabel={menu.triggerLabel}
          items={[
            { key: "edit", label: menu.edit, onSelect: onEdit },
            { key: "duplicate", label: menu.duplicate, onSelect: onDuplicate },
            {
              key: "delete",
              label: menu.delete,
              onSelect: onDelete,
              variant: "danger",
            },
          ]}
        />
      </div>
    </div>
  );
}
