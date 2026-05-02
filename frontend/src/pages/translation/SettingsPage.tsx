import { useEffect } from "react";
import { useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { Language } from "@/bridge";
import { Panel } from "@/components/Panel";
import { NumberField } from "@/components/NumberField";
import { Segmented } from "@/components/Segmented";
import { LanguageSelect } from "@/components/LanguageSelect";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import styles from "./SettingsPage.module.css";

type Toggle = "on" | "off";

export function SettingsPage() {
  const messages = useMessages();
  const { settings } = messages.translation;
  const moduleSettings = useModuleSettings("translation");
  const draft = moduleSettings.draft;

  const localizedSubfolder = messages.bilingual.subfolderDefault;
  useEffect(() => {
    if (!draft) return;
    if (draft.bilingual_subfolder_name !== localizedSubfolder) {
      moduleSettings.update("bilingual_subfolder_name", localizedSubfolder);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localizedSubfolder, draft?.bilingual_subfolder_name]);

  if (!draft) {
    return (
      <Panel
        title={settings.title}
        subtitle={settings.sub}
        subtitleSingleLine
      />
    );
  }

  return (
    <>
      <Panel title={settings.title} subtitle={settings.sub} subtitleSingleLine>
        <div className={styles.fieldGrid}>
          <FolderPickerRow
            label={settings.inputFolder}
            value={draft.input_folder}
            variant="input"
            onChange={(path) => moduleSettings.update("input_folder", path)}
          />
          <FolderPickerRow
            label={settings.outputFolder}
            value={draft.output_folder}
            variant="output"
            onChange={(path) => moduleSettings.update("output_folder", path)}
          />
        </div>
      </Panel>

      <Panel>
        <Row label={messages.language.sourceLabel} hint="">
          <LanguageSelect
            ariaLabel={messages.language.sourceLabel}
            value={draft.source_language as Language}
            onChange={(v) => moduleSettings.update("source_language", v)}
          />
        </Row>
        <Row label={messages.language.targetLabel} hint="">
          <LanguageSelect
            ariaLabel={messages.language.targetLabel}
            value={draft.target_language as Language}
            onChange={(v) => {
              moduleSettings.update("target_language", v);
              moduleSettings.update(
                "chinese_output_form",
                v === "zh-Hant" ? "traditional" : "simplified",
              );
            }}
          />
        </Row>
      </Panel>

      <Panel>
        <Row
          label={settings.openOutputOnComplete}
          hint={settings.openOutputOnCompleteHint}
        >
          <Segmented<Toggle>
            ariaLabel={settings.openOutputOnComplete}
            options={[
              { id: "on", label: settings.on },
              { id: "off", label: settings.off },
            ]}
            value={draft.auto_open_output_folder ? "on" : "off"}
            onChange={(v) =>
              moduleSettings.update("auto_open_output_folder", v === "on")
            }
          />
        </Row>
        <Row label={messages.bilingual.label} hint={messages.bilingual.hint}>
          <Segmented<Toggle>
            ariaLabel={messages.bilingual.label}
            options={[
              { id: "on", label: settings.on },
              { id: "off", label: settings.off },
            ]}
            value={draft.bilingual_enabled ? "on" : "off"}
            onChange={(v) =>
              moduleSettings.update("bilingual_enabled", v === "on")
            }
          />
        </Row>
        {draft.bilingual_enabled ? (
          <Row
            label={messages.bilingual.dedupeLabel}
            hint={messages.bilingual.dedupeHint}
          >
            <Segmented<Toggle>
              ariaLabel={messages.bilingual.dedupeLabel}
              options={[
                { id: "on", label: settings.on },
                { id: "off", label: settings.off },
              ]}
              value={draft.bilingual_dedupe_identical ? "on" : "off"}
              onChange={(v) =>
                moduleSettings.update("bilingual_dedupe_identical", v === "on")
              }
            />
          </Row>
        ) : null}
        <Row label="" hint="">
          <NumberField
            label={settings.precedingLines}
            value={draft.context_lines}
            onChange={(v) => moduleSettings.update("context_lines", v)}
            help={settings.precedingLinesHelp}
            min={0}
            max={200}
          />
        </Row>
        <Row label="" hint="">
          <NumberField
            label={settings.lowConfidenceMaxRetries}
            value={draft.low_confidence_max_retries}
            onChange={(v) =>
              moduleSettings.update("low_confidence_max_retries", v)
            }
            help={settings.lowConfidenceMaxRetriesHelp}
            min={0}
            max={10}
          />
        </Row>
        <Row label="" hint="">
          <NumberField
            label={settings.timeoutSeconds}
            value={draft.timeout_seconds}
            onChange={(v) => moduleSettings.update("timeout_seconds", v)}
            help={settings.timeoutSecondsHelp}
            min={5}
            max={600}
          />
        </Row>
      </Panel>

      <SettingsToolbar
        saveState={moduleSettings.saveState}
        lastError={moduleSettings.lastError}
        onSave={() => {
          void moduleSettings.saveNow({ explicit: true });
        }}
        onReset={() => {
          void moduleSettings.reset();
        }}
      />
    </>
  );
}

interface RowProps {
  label: string;
  hint: string;
  children: React.ReactNode;
}

function Row({ label, hint, children }: RowProps) {
  if (!label && !hint) {
    return <div className={styles.fieldRow}>{children}</div>;
  }
  return (
    <div className={styles.row}>
      <div className={styles.rowText}>
        <div className={styles.rowLabel}>{label}</div>
        {hint ? <div className={styles.rowHint}>{hint}</div> : null}
      </div>
      <div className={styles.rowControl}>{children}</div>
    </div>
  );
}
