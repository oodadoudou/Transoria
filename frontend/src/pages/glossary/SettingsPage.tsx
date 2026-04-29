import { useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { Language } from "@/bridge";
import { Panel } from "@/components/Panel";
import { NumberField } from "@/components/NumberField";
import { Segmented } from "@/components/Segmented";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import styles from "./SettingsPage.module.css";

type Toggle = "on" | "off";

const LANGUAGE_IDS: Language[] = ["kr", "zh", "zh-Hant", "en", "ja"];

export function SettingsPage() {
  const messages = useMessages();
  const { settings } = messages.glossary;
  const moduleSettings = useModuleSettings("glossary");
  const draft = moduleSettings.draft;

  if (!draft) {
    return <Panel title={settings.title} subtitle={settings.sub} />;
  }

  const languageOptions = LANGUAGE_IDS.map((id) => ({
    id,
    label: messages.language.options[id],
  }));

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
        <ToggleRow label={messages.language.sourceLabel} hint="">
          <Segmented<Language>
            ariaLabel={messages.language.sourceLabel}
            options={languageOptions}
            value={draft.source_language as Language}
            onChange={(v) => moduleSettings.update("source_language", v)}
          />
        </ToggleRow>
        <ToggleRow label={messages.language.targetLabel} hint="">
          <Segmented<Language>
            ariaLabel={messages.language.targetLabel}
            options={languageOptions}
            value={draft.target_language as Language}
            onChange={(v) => {
              moduleSettings.update("target_language", v);
              moduleSettings.update(
                "chinese_output_form",
                v === "zh-Hant" ? "traditional" : "simplified",
              );
            }}
          />
        </ToggleRow>
      </Panel>

      <Panel>
        <ToggleRow
          label={settings.combineFolderGlossary}
          hint={settings.combineFolderGlossaryHint}
        >
          <Segmented<Toggle>
            ariaLabel={settings.combineFolderGlossary}
            options={[
              { id: "on", label: settings.on },
              { id: "off", label: settings.off },
            ]}
            value={draft.merge_folder_glossary ? "on" : "off"}
            onChange={(v) =>
              moduleSettings.update("merge_folder_glossary", v === "on")
            }
          />
        </ToggleRow>
        <ToggleRow
          label={settings.allowSrcEqDst}
          hint={settings.allowSrcEqDstHint}
        >
          <Segmented<Toggle>
            ariaLabel={settings.allowSrcEqDst}
            options={[
              { id: "on", label: settings.on },
              { id: "off", label: settings.off },
            ]}
            value={draft.keep_identical_src_dst ? "on" : "off"}
            onChange={(v) =>
              moduleSettings.update("keep_identical_src_dst", v === "on")
            }
          />
        </ToggleRow>
        <ToggleRow
          label={settings.normalizeWidths}
          hint={settings.normalizeWidthsHint}
        >
          <Segmented<Toggle>
            ariaLabel={settings.normalizeWidths}
            options={[
              { id: "on", label: settings.on },
              { id: "off", label: settings.off },
            ]}
            value={draft.normalize_widths ? "on" : "off"}
            onChange={(v) =>
              moduleSettings.update("normalize_widths", v === "on")
            }
          />
        </ToggleRow>
        <ToggleRow label="" hint="">
          <NumberField
            label="Reference examples per term"
            value={draft.reference_examples_per_term}
            onChange={(v) =>
              moduleSettings.update("reference_examples_per_term", v)
            }
            min={0}
            max={200}
          />
        </ToggleRow>
        <ToggleRow label="" hint="">
          <NumberField
            label="Minimum frequency"
            value={draft.minimum_frequency}
            onChange={(v) => moduleSettings.update("minimum_frequency", v)}
            min={1}
          />
        </ToggleRow>
        <ToggleRow label="" hint="">
          <NumberField
            label="Max term display length"
            value={draft.max_term_display_length}
            onChange={(v) =>
              moduleSettings.update("max_term_display_length", v)
            }
            min={4}
            max={128}
          />
        </ToggleRow>
        <ToggleRow label="" hint="">
          <NumberField
            label="Chunk token limit"
            value={draft.chunk_token_limit}
            onChange={(v) => moduleSettings.update("chunk_token_limit", v)}
            min={500}
            max={32000}
          />
        </ToggleRow>
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
