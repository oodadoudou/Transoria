import { useEffect, useRef, useState } from "react";
import { useMessages } from "@/locales";
import {
  BridgeError,
  type PromptKind,
  type PromptPresetBody,
  type PromptPreviewResult,
} from "@/bridge";
import { usePromptPresets } from "@/store/usePromptPresetsStore";
import { useToastStore } from "@/store/useToastStore";
import { Pill } from "./Pill";
import { TextField } from "./TextField";
import { ToggleSwitch } from "./ToggleSwitch";
import styles from "./PromptPresetModal.module.css";

type Mode = "create" | "edit";
type Tab = "system" | "thinking";

interface PromptPresetModalProps {
  mode: Mode;
  kind: PromptKind;
  /** Existing preset body in edit mode; ``"duplicate"`` source when
   *  the caller wants the create modal pre-filled (e.g. Duplicate
   *  active). `null` for a blank create. */
  seed?: PromptPresetBody | null;
  onSaved: (presetId: string) => void;
  onCancel: () => void;
}

interface Draft {
  name: string;
  description: string;
  enabled: boolean;
  system_prompt: string;
  thinking_prompt: string;
}

function bodyToDraft(body: PromptPresetBody): Draft {
  return {
    name: body.name,
    description: body.description,
    enabled: body.enabled,
    system_prompt: body.system_prompt,
    thinking_prompt: body.thinking_prompt,
  };
}

function blankDraft(): Draft {
  return {
    name: "",
    description: "",
    enabled: true,
    system_prompt: "",
    thinking_prompt: "",
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

export function PromptPresetModal({
  mode,
  kind,
  seed,
  onSaved,
  onCancel,
}: PromptPresetModalProps) {
  const messages = useMessages();
  const m = messages.promptModal;
  const store = usePromptPresets(kind);

  const [draft, setDraft] = useState<Draft>(() =>
    seed ? bodyToDraft(seed) : blankDraft(),
  );
  const [tab, setTab] = useState<Tab>("system");
  const [error, setError] = useState<BridgeError | null>(null);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<PromptPreviewResult | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const requestSeq = useRef(0);
  // Snapshot of the initial draft so the unsaved-changes guard can
  // diff against "what the user opened with" rather than the latest
  // keystroke. Refreshed whenever the seed changes (e.g. user picks a
  // different preset from the list while the modal is open).
  const baselineRef = useRef<Draft>(seed ? bodyToDraft(seed) : blankDraft());

  // For edit mode, when the seed prop changes (e.g. user opens a
  // different preset), refresh the local draft.
  useEffect(() => {
    const next = seed ? bodyToDraft(seed) : blankDraft();
    setDraft(next);
    baselineRef.current = next;
    setPreview(null);
    setError(null);
  }, [seed]);

  // Block accidental dismissal when the user has typed changes that
  // haven't been saved. The default close paths (overlay click, Cancel
  // button, ESC if any) all run through ``handleCancel`` so the prompt
  // is consistent.
  const isDirty = !readOnlyDraftEquals(draft, baselineRef.current);

  const handleCancel = () => {
    if (saving) return;
    if (isDirty) {
      // ``window.confirm`` is intentionally synchronous and native —
      // the modal already blocks app interaction, and a custom dialog-
      // inside-dialog would visually clash. Returning false drops
      // out and keeps the modal open with the user's edits intact.
      const confirmed = window.confirm(m.unsavedChangesConfirm);
      if (!confirmed) return;
    }
    onCancel();
  };

  const update = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
  };

  const isDefault = mode === "edit" && seed?.is_default === true;
  const isSystem = mode === "edit" && seed?.is_system === true;
  const readOnly = isSystem;

  const handleSave = async () => {
    if (!draft.name.trim()) {
      setError(
        new BridgeError({
          code: "bridge.invalid_argument",
          message: "Name is required.",
          retryable: false,
          details: { field: "name" },
        }),
      );
      return;
    }
    setSaving(true);
    setError(null);
    const pushToast = useToastStore.getState().push;
    try {
      if (mode === "edit" && seed) {
        await store.updatePreset(seed.id, {
          name: draft.name,
          description: draft.description,
          enabled: draft.enabled,
          system_prompt: draft.system_prompt,
          thinking_prompt: draft.thinking_prompt,
        });
        pushToast({
          variant: "success",
          title: messages.toast.presetSaved,
          detail: draft.name,
        });
        onSaved(seed.id);
      } else {
        const created = await store.createPreset(kind, {
          name: draft.name,
          kind,
          description: draft.description,
          enabled: draft.enabled,
          is_system: false,
          system_prompt: draft.system_prompt,
          thinking_prompt: draft.thinking_prompt,
        });
        if (created) {
          pushToast({
            variant: "success",
            title: messages.toast.presetSaved,
            detail: draft.name,
          });
          onSaved(created.id);
        } else {
          const failureError =
            store.mutationError ?? makeUnknownError("create returned null");
          setError(failureError);
          pushToast({
            variant: "error",
            title: messages.toast.presetSaveFailed,
            detail: failureError.message,
            durationMs: 5000,
          });
        }
      }
    } catch (err) {
      const bridgeError = asBridgeError(err);
      setError(bridgeError);
      pushToast({
        variant: "error",
        title: messages.toast.presetSaveFailed,
        detail: bridgeError.message,
        durationMs: 5000,
      });
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async () => {
    if (!isDefault || !seed) return;
    setSaving(true);
    setError(null);
    try {
      const restored = await store.resetToDefault(seed.id);
      if (restored) {
        setDraft(bodyToDraft(restored));
        onSaved(seed.id);
      }
    } catch (err) {
      setError(asBridgeError(err));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (mode !== "edit" || !seed || isDefault) return;
    setSaving(true);
    try {
      const ok = await store.deletePreset(seed.id);
      if (ok) onCancel();
    } catch (err) {
      setError(asBridgeError(err));
    } finally {
      setSaving(false);
    }
  };

  const handlePreview = async () => {
    if (mode !== "edit" || !seed) return;
    const seq = ++requestSeq.current;
    setPreviewBusy(true);
    setPreview(null);
    setError(null);
    try {
      const result = await store.preview(
        seed.id,
        {
          source_language: m.sampleSourceLanguage,
          target_language: m.sampleTargetLanguage,
          input: m.sampleInput,
        },
        true,
      );
      if (seq === requestSeq.current && result) setPreview(result);
    } catch (err) {
      if (seq === requestSeq.current) setError(asBridgeError(err));
    } finally {
      if (seq === requestSeq.current) setPreviewBusy(false);
    }
  };

  const tabValue =
    tab === "system" ? draft.system_prompt : draft.thinking_prompt;

  const setTabValue = (value: string) => {
    if (tab === "system") update("system_prompt", value);
    else update("thinking_prompt", value);
  };

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
            {readOnly
              ? m.titleView
              : mode === "edit"
                ? m.titleEdit
                : m.titleAdd}
          </h2>
          <button
            type="button"
            className={styles.closeButton}
            onClick={handleCancel}
            aria-label={readOnly ? m.closeAction : m.cancelAction}
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

        {readOnly ? (
          <div className={styles.systemNotice}>{m.systemReadOnlyNotice}</div>
        ) : null}

        <div className={styles.body}>
          <TextField
            label={m.nameLabel}
            value={draft.name}
            onChange={readOnly ? undefined : (v) => update("name", v)}
            placeholder={m.namePlaceholder}
          />
          <TextField
            label={m.descriptionLabel}
            value={draft.description}
            onChange={readOnly ? undefined : (v) => update("description", v)}
            placeholder={m.descriptionPlaceholder}
          />
          {readOnly ? null : (
            <ToggleSwitch
              label={m.enabledLabel}
              checked={draft.enabled}
              onChange={(v) => update("enabled", v)}
            />
          )}

          <div className={styles.tabs} role="tablist">
            <TabButton
              active={tab === "system"}
              onClick={() => setTab("system")}
            >
              {m.systemTab}
            </TabButton>
            <TabButton
              active={tab === "thinking"}
              onClick={() => setTab("thinking")}
            >
              {m.thinkingTab}
            </TabButton>
          </div>
          <div className={styles.tabHelp}>
            {tab === "system" ? m.systemTabHelp : m.thinkingTabHelp}
          </div>
          <TextField
            label=""
            value={tabValue}
            onChange={readOnly ? undefined : setTabValue}
            multiline
            rows={10}
            mono
          />

          {mode === "edit" && seed ? (
            <div className={styles.previewBlock}>
              <div className={styles.previewActions}>
                <Pill
                  variant="ghost"
                  onClick={handlePreview}
                  disabled={previewBusy}
                >
                  {previewBusy ? m.previewRunning : m.previewAction}
                </Pill>
                <span className={styles.previewSampleNote}>
                  {m.previewSampleContext}: {m.sampleSourceLanguage} →{" "}
                  {m.sampleTargetLanguage}
                </span>
              </div>
              {preview ? (
                <div className={styles.previewPanel}>
                  {preview.clamped ? (
                    <div className={styles.clampedNotice}>
                      {m.previewClampedNotice}
                    </div>
                  ) : null}
                  <pre className={styles.previewOutput}>{preview.prompt}</pre>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className={styles.footer}>
          {readOnly ? null : (
            <>
              {mode === "edit" && isDefault ? (
                <Pill variant="ghost" onClick={() => void handleReset()}>
                  {m.resetAction}
                </Pill>
              ) : null}
              {mode === "edit" && !isDefault && seed ? (
                <Pill variant="ghost" onClick={() => void handleDelete()}>
                  {m.deleteAction}
                </Pill>
              ) : null}
            </>
          )}
          <div className={styles.footerRight}>
            <Pill variant="ghost" onClick={handleCancel}>
              {readOnly ? m.closeAction : m.cancelAction}
            </Pill>
            {readOnly ? null : (
              <Pill onClick={() => void handleSave()} disabled={saving}>
                {m.saveAction}
              </Pill>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function readOnlyDraftEquals(a: Draft, b: Draft): boolean {
  // Shallow string-by-string equality across every editable field.
  return (
    a.name === b.name &&
    a.description === b.description &&
    a.enabled === b.enabled &&
    a.system_prompt === b.system_prompt &&
    a.thinking_prompt === b.thinking_prompt
  );
}

function makeUnknownError(message: string): BridgeError {
  return new BridgeError({
    code: "bridge.io_error",
    message,
    retryable: true,
  });
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
      className={`${styles.tab} ${active ? styles.tabActive : ""}`.trim()}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
