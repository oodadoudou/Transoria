import { useEffect, useState } from "react";
import {
  BridgeError,
  glossaryReviewBridge,
  type GlossaryReviewInputCandidates,
} from "@/bridge";
import { format, useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { HelpTip } from "@/components/HelpTip";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import styles from "../glossary/SettingsPage.module.css";

export function ReviewInputs() {
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

  if (!draft) return null;

  const xlsxOptions = candidates?.xlsx_candidates ?? [];
  const referenceOptions = candidates?.reference_candidates ?? [];
  const missingGlossary = !scanLoading && candidates !== null && xlsxOptions.length === 0;

  return (
    <Panel>
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
          historyKey="glossary_review:input_folder"
        />
      </div>
      {xlsxOptions.length > 0 ? (
        <div className={styles.fileSelectGrid}>
          <FileSelect
            label={settings.glossaryFile}
            placeholder={settings.glossaryFilePlaceholder}
            value={draft.selected_xlsx_path}
            options={xlsxOptions}
            disabled={scanLoading}
            onChange={(value) => moduleSettings.update("selected_xlsx_path", value)}
          />
          {referenceOptions.length > 0 ? (
            <ReferencePicker
              label={settings.referenceFiles}
              help={settings.referenceFilesHelp}
              selected={draft.selected_reference_paths}
              options={referenceOptions}
              disabled={scanLoading}
              onChange={(value) => moduleSettings.update("selected_reference_paths", value)}
            />
          ) : null}
        </div>
      ) : null}
      {missingGlossary ? <div className={styles.scanError}>{settings.noGlossaryFile}</div> : null}
      {scanError ? <div className={styles.scanError}>{scanError}</div> : null}
    </Panel>
  );
}

interface FileOption {
  path: string;
  name: string;
}

interface FileSelectProps {
  label: string;
  placeholder: string;
  value: string;
  options: FileOption[];
  disabled: boolean;
  onChange: (value: string) => void;
}

function FileSelect({
  label,
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
    </div>
  );
}

interface ReferencePickerProps {
  label: string;
  help: string;
  selected: string[];
  options: FileOption[];
  disabled: boolean;
  onChange: (value: string[]) => void;
}

function ReferencePicker({
  label,
  help,
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
      <div className={styles.rowLabel}>{label}<HelpTip>{help}</HelpTip></div>
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
    </div>
  );
}
