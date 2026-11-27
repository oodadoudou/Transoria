import { useEffect, useState } from "react";
import {
  BridgeError,
  dialogsBridge,
  glossaryReviewBridge,
  type GlossaryReviewInputCandidates,
} from "@/bridge";
import { format, useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { RunConfigBar, type RunConfigBarItem } from "@/components/RunConfigBar";
import { QuickSwitchModal } from "@/components/QuickSwitchModal";
import styles from "../glossary/RunPage.module.css";

type Picker = "xlsx" | "references";

function splitPath(path: string): { name: string; parent: string } {
  const trimmed = path.replace(/[\\/]+$/, "");
  const index = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  return index < 0
    ? { name: trimmed, parent: "" }
    : { name: trimmed.slice(index + 1), parent: trimmed.slice(0, index) };
}

export function ReviewInputs({ label, presetItem }: { label: string; presetItem: RunConfigBarItem }) {
  const messages = useMessages();
  const { settings } = messages.glossaryReview;
  const moduleSettings = useModuleSettings("glossary_review");
  const draft = moduleSettings.draft;
  const [candidates, setCandidates] = useState<GlossaryReviewInputCandidates | null>(null);
  const [scanLoading, setScanLoading] = useState(false);
  const [scanError, setScanError] = useState<string | null>(null);
  const [picker, setPicker] = useState<Picker | null>(null);

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
          moduleSettings.update("selected_xlsx_path", xlsxPaths.length === 1 ? xlsxPaths[0] : "");
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
        const reason = BridgeError.isBridgeError(error) ? `${error.code}: ${error.message}` : String(error);
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
  const folder = draft.input_folder.trim();
  const folderParts = splitPath(folder);
  const selectedXlsx = xlsxOptions.find((option) => option.path === draft.selected_xlsx_path);
  const selectedRefs = referenceOptions.filter((option) =>
    draft.selected_reference_paths.includes(option.path),
  );
  const missingGlossary = !scanLoading && candidates !== null && xlsxOptions.length === 0;

  const chooseFolder = async () => {
    try {
      const result = await dialogsBridge.chooseInputDirectory(folder || undefined);
      if (!result.path) return;
      moduleSettings.update("input_folder", result.path);
      moduleSettings.update("selected_xlsx_path", "");
      moduleSettings.update("selected_reference_paths", []);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setScanError(error.message);
    }
  };

  const toggleReference = (path: string) => {
    const current = draft.selected_reference_paths;
    moduleSettings.update(
      "selected_reference_paths",
      current.includes(path) ? current.filter((item) => item !== path) : [...current, path],
    );
  };

  const items: RunConfigBarItem[] = [
    {
      id: "folder",
      label: settings.inputFolder,
      primary: folder ? folderParts.name : settings.noSelection,
      secondary: folderParts.parent,
      actionLabel: messages.runConfig.switchAction,
      onClick: () => void chooseFolder(),
    },
    {
      id: "xlsx",
      label: settings.glossaryFile,
      primary: selectedXlsx?.name ?? (missingGlossary ? settings.noGlossaryFile : settings.noSelection),
      actionLabel: messages.runConfig.switchAction,
      active: missingGlossary ? false : undefined,
      onClick: () => setPicker("xlsx"),
    },
    {
      id: "references",
      label: settings.referenceFiles,
      primary:
        selectedRefs.length === 1
          ? selectedRefs[0].name
          : selectedRefs.length > 1
            ? format(settings.fileCount, { count: selectedRefs.length })
            : settings.noSelection,
      actionLabel: messages.runConfig.switchAction,
      active: referenceOptions.length === 0 ? false : undefined,
      onClick: () => setPicker("references"),
    },
    presetItem,
  ];

  return (
    <Panel label={label}>
      <RunConfigBar items={items} />
      {scanError ? <div className={styles.inputError}>{scanError}</div> : null}
      {picker === "xlsx" ? (
        <QuickSwitchModal
          title={settings.glossaryFile}
          items={xlsxOptions.map((option) => ({ id: option.path, name: option.name }))}
          activeId={draft.selected_xlsx_path || null}
          emptyMessage={settings.noGlossaryFile}
          onSelect={(path) => moduleSettings.update("selected_xlsx_path", path)}
          onClose={() => setPicker(null)}
        />
      ) : null}
      {picker === "references" ? (
        <QuickSwitchModal
          title={settings.referenceFiles}
          items={referenceOptions.map((option) => ({ id: option.path, name: option.name }))}
          activeId={null}
          selectedIds={draft.selected_reference_paths}
          emptyMessage={settings.noSelection}
          onSelect={toggleReference}
          onClose={() => setPicker(null)}
        />
      ) : null}
    </Panel>
  );
}
