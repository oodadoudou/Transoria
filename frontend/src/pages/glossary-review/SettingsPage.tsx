import { useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { NumberField } from "@/components/NumberField";
import { Segmented } from "@/components/Segmented";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { TextField } from "@/components/TextField";
import styles from "../glossary/SettingsPage.module.css";

type Toggle = "on" | "off";

export function SettingsPage() {
  const messages = useMessages();
  const { settings } = messages.glossaryReview;
  const moduleSettings = useModuleSettings("glossary_review");
  const draft = moduleSettings.draft;

  if (!draft) {
    return <Panel title={settings.title} subtitle={settings.sub} />;
  }

  return (
    <>
      <Panel title={settings.title} subtitle={settings.sub}>
        <div className={styles.fieldGrid}>
          <FolderPickerRow
            label={settings.inputFolder}
            value={draft.input_folder}
            variant="input"
            onChange={(path) => moduleSettings.update("input_folder", path)}
          />
          <TextField
            label={settings.outputFilename}
            value={draft.output_filename}
            onChange={(value) => moduleSettings.update("output_filename", value)}
            help={settings.outputFilenameHelp}
          />
        </div>
      </Panel>

      <Panel>
        <TextField
          label={settings.novelBackground}
          value={draft.novel_background}
          onChange={(value) => moduleSettings.update("novel_background", value)}
          help={settings.novelBackgroundHelp}
          multiline
        />
      </Panel>

      <Panel>
        <ToggleRow label="" hint="">
          <NumberField
            label={settings.reviewRounds}
            value={draft.review_rounds}
            onChange={(v) => moduleSettings.update("review_rounds", v)}
            help={settings.reviewRoundsHelp}
            min={1}
            max={10}
          />
        </ToggleRow>
        <ToggleRow label="" hint="">
          <NumberField
            label={settings.maxWorkers}
            value={draft.max_workers}
            onChange={(v) => moduleSettings.update("max_workers", v)}
            help={settings.maxWorkersHelp}
            min={1}
            max={20}
          />
        </ToggleRow>
        <ToggleRow label="" hint="">
          <NumberField
            label={settings.batchSize}
            value={draft.batch_size}
            onChange={(v) => moduleSettings.update("batch_size", v)}
            help={settings.batchSizeHelp}
            min={1}
            max={200}
          />
        </ToggleRow>
        <ToggleRow label="" hint="">
          <NumberField
            label={settings.timeoutSeconds}
            value={draft.timeout_seconds}
            onChange={(v) => moduleSettings.update("timeout_seconds", v)}
            help={settings.timeoutSecondsHelp}
            min={5}
          />
        </ToggleRow>
        <ToggleRow
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
        </ToggleRow>
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

interface ToggleRowProps {
  label: string;
  hint: string;
  children: React.ReactNode;
}

function ToggleRow({ label, hint, children }: ToggleRowProps) {
  if (!label && !hint) {
    return <div className={styles.fieldRow ?? ""}>{children}</div>;
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
