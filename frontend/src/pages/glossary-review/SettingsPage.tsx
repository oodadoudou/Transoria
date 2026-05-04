import { useEffect, useState } from "react";
import { BridgeError, glossaryReviewBridge, type GlossaryReviewInputCandidates } from "@/bridge";
import { format, useMessages } from "@/locales";
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
  const [candidates, setCandidates] =
    useState<GlossaryReviewInputCandidates | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);

  useEffect(() => {
    if (!draft?.input_folder.trim()) {
      setCandidates(null);
      setScanError(null);
      return;
    }
    let cancelled = false;
    setScanLoading(true);
    setScanError(null);
    glossaryReviewBridge
      .discoverInputs(draft.input_folder, draft.output_filename)
      .then((next) => {
        if (cancelled) return;
        setCandidates(next);
        const xlsxPaths = next.xlsx_candidates.map((item) => item.path);
        if (!xlsxPaths.includes(draft.selected_xlsx_path)) {
          moduleSettings.update(
            "selected_xlsx_path",
            xlsxPaths.length === 1 ? xlsxPaths[0] : "",
          );
        }
        const referencePaths = next.reference_candidates.map((item) => item.path);
        const validSelectedRefs = draft.selected_reference_paths.filter((path) =>
          referencePaths.includes(path),
        );
        if (draft.selected_reference_paths.length === 0 && referencePaths.length > 0) {
          moduleSettings.update("selected_reference_paths", referencePaths);
        } else if (validSelectedRefs.length !== draft.selected_reference_paths.length) {
          moduleSettings.update("selected_reference_paths", validSelectedRefs);
        }
      })
      .catch((error) => {
        if (cancelled) return;
        const reason = BridgeError.isBridgeError(error)
          ? `${error.code}: ${error.message}`
          : String(error);
        setCandidates(null);
        setScanError(format(settings.inputScanError, { reason }));
      })
      .finally(() => {
        if (!cancelled) setScanLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [draft?.input_folder, draft?.output_filename]);

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
            onChange={(path) => {
              moduleSettings.update("input_folder", path);
              moduleSettings.update("selected_xlsx_path", "");
              moduleSettings.update("selected_reference_paths", []);
            }}
          />
          <TextField
            label={settings.outputFilename}
            value={draft.output_filename}
            onChange={(value) => moduleSettings.update("output_filename", value)}
            help={settings.outputFilenameHelp}
          />
        </div>
        <div className={styles.fileSelectGrid}>
          <FileSelect
            label={settings.glossaryFile}
            help={settings.glossaryFileHelp}
            placeholder={settings.glossaryFilePlaceholder}
            value={draft.selected_xlsx_path}
            options={candidates?.xlsx_candidates ?? []}
            disabled={scanLoading || !candidates}
            onChange={(value) => moduleSettings.update("selected_xlsx_path", value)}
          />
          <ReferencePicker
            label={settings.referenceFiles}
            help={settings.referenceFilesHelp}
            empty={settings.referenceFilesEmpty}
            selected={draft.selected_reference_paths}
            options={candidates?.reference_candidates ?? []}
            disabled={scanLoading || !candidates}
            onChange={(value) =>
              moduleSettings.update("selected_reference_paths", value)
            }
          />
        </div>
        {scanLoading ? (
          <div className={styles.scanHint}>{settings.inputScanLoading}</div>
        ) : null}
        {scanError ? <div className={styles.scanError}>{scanError}</div> : null}
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

interface FileOption {
  path: string;
  name: string;
}

interface FileSelectProps {
  label: string;
  help: string;
  placeholder: string;
  value: string;
  options: FileOption[];
  disabled: boolean;
  onChange: (value: string) => void;
}

function FileSelect({
  label,
  help,
  placeholder,
  value,
  options,
  disabled,
  onChange,
}: FileSelectProps) {
  const normalizedValue = options.some((option) => option.path === value)
    ? value
    : "";
  return (
    <div className={styles.fileField}>
      <div className={styles.rowLabel}>{label}</div>
      <select
        className={styles.selectInput}
        value={normalizedValue}
        disabled={disabled || options.length === 0}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{placeholder}</option>
        {options.map((option) => (
          <option key={option.path} value={option.path}>
            {option.name}
          </option>
        ))}
      </select>
      <div className={styles.rowHint}>{help}</div>
    </div>
  );
}

interface ReferencePickerProps {
  label: string;
  help: string;
  empty: string;
  selected: string[];
  options: FileOption[];
  disabled: boolean;
  onChange: (value: string[]) => void;
}

function ReferencePicker({
  label,
  help,
  empty,
  selected,
  options,
  disabled,
  onChange,
}: ReferencePickerProps) {
  const selectedSet = new Set(selected);
  const toggle = (path: string) => {
    if (selectedSet.has(path)) {
      onChange(selected.filter((item) => item !== path));
      return;
    }
    onChange([...selected, path]);
  };
  return (
    <div className={styles.fileField}>
      <div className={styles.rowLabel}>{label}</div>
      {options.length === 0 ? (
        <div className={styles.referenceEmpty}>{empty}</div>
      ) : (
        <div className={styles.referenceList}>
          {options.map((option) => (
            <label key={option.path} className={styles.referenceItem}>
              <input
                type="checkbox"
                checked={selectedSet.has(option.path)}
                disabled={disabled}
                onChange={() => toggle(option.path)}
              />
              <span>{option.name}</span>
            </label>
          ))}
        </div>
      )}
      <div className={styles.rowHint}>{help}</div>
    </div>
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
