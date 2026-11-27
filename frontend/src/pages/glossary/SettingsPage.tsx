import { useEffect, useRef, useState } from "react";
import { glossaryBridge } from "@/bridge";
import { useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { Language } from "@/bridge";
import { Panel } from "@/components/Panel";
import { NumberField } from "@/components/NumberField";
import { Segmented } from "@/components/Segmented";
import { LanguageSelect } from "@/components/LanguageSelect";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { TextField } from "@/components/TextField";
import {
  SettingsField,
  SettingsFieldFrame,
  SettingsFieldStack,
} from "@/components/SettingsField";
import styles from "./SettingsPage.module.css";

type Toggle = "on" | "off";

const BACKGROUND_READ_DELAY_MS = 500;

export function SettingsPage() {
  const messages = useMessages();
  const { settings } = messages.glossary;
  const reviewLabels = messages.glossaryReview.settings;
  const moduleSettings = useModuleSettings("glossary");
  const reviewSettings = useModuleSettings("glossary_review");
  const draft = moduleSettings.draft;
  const review = reviewSettings.draft;
  const [sameFolderChoice, setSameFolderChoice] = useState<boolean | null>(null);
  const latestFolder = useRef("");
  const backgroundTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (backgroundTimer.current !== null) window.clearTimeout(backgroundTimer.current);
    },
    [],
  );

  // Only a folder the user just switched to refills the background; typed paths settle first.
  const changeInputFolder = (path: string) => {
    moduleSettings.update("input_folder", path);
    latestFolder.current = path.trim();
    if (backgroundTimer.current !== null) window.clearTimeout(backgroundTimer.current);
    if (!latestFolder.current) return;
    backgroundTimer.current = window.setTimeout(() => {
      const folder = latestFolder.current;
      glossaryBridge
        .readFolderBackground(folder)
        .then(({ text }) => {
          if (text && folder === latestFolder.current) moduleSettings.update("novel_background", text);
        })
        .catch(() => undefined);
    }, BACKGROUND_READ_DELAY_MS);
  };

  if (!draft) {
    return <Panel title={settings.title} subtitle={settings.sub} />;
  }

  const outputFolder = draft.output_folder.trim();
  const sameFolder =
    sameFolderChoice ?? (!outputFolder || outputFolder === draft.input_folder.trim());

  return (
    <>
      <Panel title={settings.title} subtitle={settings.sub}>
        <div className={styles.fieldGrid}>
          <FolderPickerRow
            label={settings.inputFolder}
            value={draft.input_folder}
            variant="input"
            onChange={changeInputFolder}
            historyKey="glossary:input_folder"
          />
          {sameFolder ? null : (
            <FolderPickerRow
              label={settings.outputFolder}
              value={draft.output_folder}
              variant="output"
              onChange={(path) => moduleSettings.update("output_folder", path)}
              historyKey="glossary:output_folder"
            />
          )}
        </div>
        <label className={styles.checkboxRow}>
          <input
            type="checkbox"
            checked={sameFolder}
            onChange={(event) => {
              setSameFolderChoice(event.target.checked);
              // A blank output folder means "write next to the input" on the backend.
              if (event.target.checked) moduleSettings.update("output_folder", "");
            }}
          />
          <span>{settings.sameAsInput}</span>
        </label>
      </Panel>

      <Panel>
        <SettingsFieldStack>
          <SettingsField label={messages.language.sourceLabel}>
            <LanguageSelect
              ariaLabel={messages.language.sourceLabel}
              value={draft.source_language as Language}
              onChange={(v) => moduleSettings.update("source_language", v)}
            />
          </SettingsField>
          <SettingsField label={messages.language.targetLabel}>
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
          </SettingsField>
        </SettingsFieldStack>
      </Panel>

      <Panel>
        <SettingsFieldStack>
          <SettingsFieldFrame>
            <TextField
              label={settings.novelBackground}
              value={draft.novel_background}
              onChange={(value) =>
                moduleSettings.update("novel_background", value)
              }
              help={settings.novelBackgroundHelp}
              multiline
            />
          </SettingsFieldFrame>
        </SettingsFieldStack>
      </Panel>

      <Panel>
        <SettingsFieldStack>
          <SettingsField
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
          </SettingsField>
          <SettingsField
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
          </SettingsField>
          <SettingsField
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
              onChange={(v) => {
                moduleSettings.update("auto_open_output_folder", v === "on");
                reviewSettings.update("auto_open_output_folder", v === "on");
              }}
            />
          </SettingsField>
          <SettingsField
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
          </SettingsField>
          <SettingsFieldFrame>
            <NumberField
              label={settings.referenceExamplesPerTerm}
              value={draft.reference_examples_per_term}
              onChange={(v) =>
                moduleSettings.update("reference_examples_per_term", v)
              }
              help={settings.referenceExamplesPerTermHelp}
              min={0}
              max={200}
            />
          </SettingsFieldFrame>
          <SettingsFieldFrame>
            <NumberField
              label={settings.minimumFrequency}
              value={draft.minimum_frequency}
              onChange={(v) => moduleSettings.update("minimum_frequency", v)}
              help={settings.minimumFrequencyHelp}
              min={1}
            />
          </SettingsFieldFrame>
          <SettingsFieldFrame>
            <NumberField
              label={settings.maxTermDisplayLength}
              value={draft.max_term_display_length}
              onChange={(v) =>
                moduleSettings.update("max_term_display_length", v)
              }
              help={settings.maxTermDisplayLengthHelp}
              min={4}
              max={128}
            />
          </SettingsFieldFrame>
          <SettingsFieldFrame>
            <NumberField
              label={settings.timeoutSeconds}
              value={draft.timeout_seconds}
              onChange={(v) => moduleSettings.update("timeout_seconds", v)}
              help={settings.timeoutSecondsHelp}
              min={5}
            />
          </SettingsFieldFrame>
          <SettingsFieldFrame>
            <NumberField
              label={settings.requestRetryAttempts}
              value={draft.request_retry_attempts}
              onChange={(v) =>
                moduleSettings.update("request_retry_attempts", v)
              }
              help={settings.requestRetryAttemptsHelp}
              min={0}
              max={20}
            />
          </SettingsFieldFrame>
        </SettingsFieldStack>
      </Panel>

      {review ? (
        <Panel label={settings.reviewSection}>
          <SettingsFieldStack>
            <SettingsFieldFrame>
              <TextField
                label={reviewLabels.outputFilename}
                value={review.output_filename}
                onChange={(value) => reviewSettings.update("output_filename", value)}
                help={reviewLabels.outputFilenameHelp}
              />
            </SettingsFieldFrame>
            <SettingsFieldFrame>
              <NumberField
                label={reviewLabels.reviewRounds}
                value={review.review_rounds}
                onChange={(v) => reviewSettings.update("review_rounds", v)}
                help={reviewLabels.reviewRoundsHelp}
                min={1}
                max={10}
              />
            </SettingsFieldFrame>
            <SettingsFieldFrame>
              <NumberField
                label={reviewLabels.batchSize}
                value={review.batch_size}
                onChange={(v) => reviewSettings.update("batch_size", v)}
                help={reviewLabels.batchSizeHelp}
                min={1}
                max={200}
              />
            </SettingsFieldFrame>
            <SettingsFieldFrame>
              <NumberField
                label={reviewLabels.timeoutSeconds}
                value={review.timeout_seconds}
                onChange={(v) => reviewSettings.update("timeout_seconds", v)}
                help={reviewLabels.timeoutSecondsHelp}
                min={5}
              />
            </SettingsFieldFrame>
            <SettingsFieldFrame>
              <NumberField
                label={reviewLabels.retryAttempts}
                value={review.retry_attempts}
                onChange={(v) => reviewSettings.update("retry_attempts", v)}
                help={reviewLabels.retryAttemptsHelp}
                min={0}
                max={20}
              />
            </SettingsFieldFrame>
          </SettingsFieldStack>
        </Panel>
      ) : null}

      <SettingsToolbar
        saveState={moduleSettings.saveState}
        lastError={moduleSettings.lastError}
        onSave={() => {
          void moduleSettings.saveNow({ explicit: true });
          void reviewSettings.saveNow({ explicit: true });
        }}
        onReset={() => {
          void moduleSettings.reset();
          void reviewSettings.reset();
        }}
      />
    </>
  );
}
