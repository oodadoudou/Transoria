import { useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { Language } from "@/bridge";
import { Panel } from "@/components/Panel";
import { NumberField } from "@/components/NumberField";
import { TextField } from "@/components/TextField";
import { Segmented } from "@/components/Segmented";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import styles from "./SettingsPage.module.css";

type Toggle = "on" | "off";

const LANGUAGE_IDS: Language[] = ["kr", "zh", "zh-Hant", "en", "ja"];

export function SettingsPage() {
  const messages = useMessages();
  const { settings } = messages.translation;
  const moduleSettings = useModuleSettings("translation");
  const draft = moduleSettings.draft;

  if (!draft) {
    return <Panel title={settings.title} subtitle={settings.sub} />;
  }

  const languageOptions = LANGUAGE_IDS.map((id) => ({
    id,
    label: messages.language.options[id],
  }));
  const targetIsChinese =
    draft.target_language === "zh" || draft.target_language === "zh-Hant";

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
          <Segmented<Language>
            ariaLabel={messages.language.sourceLabel}
            options={languageOptions}
            value={draft.source_language as Language}
            onChange={(v) => moduleSettings.update("source_language", v)}
          />
        </Row>
        <Row label={messages.language.targetLabel} hint="">
          <Segmented<Language>
            ariaLabel={messages.language.targetLabel}
            options={languageOptions}
            value={draft.target_language as Language}
            onChange={(v) => moduleSettings.update("target_language", v)}
          />
        </Row>
        {targetIsChinese ? (
          <Row label="" hint="">
            <Segmented<"simplified" | "traditional">
              ariaLabel={messages.language.chineseFormSimplified}
              options={[
                {
                  id: "simplified",
                  label: messages.language.chineseFormSimplified,
                },
                {
                  id: "traditional",
                  label: messages.language.chineseFormTraditional,
                },
              ]}
              value={draft.chinese_output_form}
              onChange={(v) => moduleSettings.update("chinese_output_form", v)}
            />
          </Row>
        ) : null}
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
          <>
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
                  moduleSettings.update(
                    "bilingual_dedupe_identical",
                    v === "on",
                  )
                }
              />
            </Row>
            <Row label="" hint="">
              <TextField
                label={messages.bilingual.subfolderLabel}
                value={draft.bilingual_subfolder_name}
                onChange={(v) =>
                  moduleSettings.update("bilingual_subfolder_name", v)
                }
              />
            </Row>
          </>
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
      </Panel>

      <SettingsToolbar
        saveState={moduleSettings.saveState}
        lastError={moduleSettings.lastError}
        onSave={() => {
          void moduleSettings.saveNow();
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
