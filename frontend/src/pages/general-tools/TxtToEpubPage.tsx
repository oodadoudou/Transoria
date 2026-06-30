import { useEffect, useMemo, useRef, useState } from "react";

import {
  BridgeError,
  dialogsBridge,
  txtToEpubBridge,
  type TxtToEpubArtifacts,
  type TxtToEpubOptions,
  type TxtToEpubPreset,
  type TxtToEpubReport,
  type TxtToEpubRule,
  type TxtToEpubStyle,
  type TxtToEpubTocEntry,
} from "@/bridge";
import { CompactPath } from "@/components/CompactPath";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { useMessages } from "@/locales";
import {
  hasShownCleanCompletionToast,
  markCleanCompletionToastShown,
  usePollRunSnapshot,
  useRunSnapshot,
  useRuntimeStore,
} from "@/store/useRuntimeStore";
import { useToastStore } from "@/store/useToastStore";
import { useLocalState } from "@/utils/localState";
import { useSessionState } from "@/utils/sessionState";
import {
  displayPreset,
  displayStyle,
  mergeChinesePresets,
} from "./TxtToEpubPage.logic";
import styles from "./TxtToEpubPage.module.css";

const NUM = new Intl.NumberFormat("en");
const INPUT_LOCAL_KEY = "transoria.generalTools.txtToEpub.inputPath";
const OUTPUT_LOCAL_KEY = "transoria.generalTools.txtToEpub.outputDir";
const COVER_LOCAL_KEY = "transoria.generalTools.txtToEpub.coverPath";
const DRAFT_SESSION_KEY = "transoria.generalTools.txtToEpub.draft";
const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);
const SIMPLE_RULE_LEVELS = [1, 2, 3, 4] as const;
const VISIBLE_STYLE_IDS = new Set([
  "basic:classic",
  "basic:clean",
  "basic:eyecare",
  "basic:modern",
  "basic:minimal",
  "basic:literary",
  "basic:compact",
  "basic:spacious",
  "basic:double_line",
  "basic:sans_clean",
  "basic:framed",
  "basic:sidebar",
  "basic:structure_lines",
  "basic:reader_modern",
  "enhanced:soft_structure",
]);
interface DraftState {
  title: string;
  author: string;
  presetId: string;
  regexMode: "preset" | "simple" | "advanced";
  customRules: TxtToEpubRule[];
  advancedPattern: string;
  styleId: string;
  customCss: string;
}

function defaultDraft(): DraftState {
  return {
    title: "",
    author: "",
    presetId: "markdown",
    regexMode: "preset",
    customRules: [
      { level: 1, pattern: "" },
      { level: 2, pattern: "" },
      { level: 3, pattern: "" },
      { level: 4, pattern: "" },
    ],
    advancedPattern: "",
    styleId: "basic:classic",
    customCss: "",
  };
}

export function TxtToEpubPage({ embedded = false }: { embedded?: boolean } = {}) {
  const messages = useMessages();
  const pageText = messages.generalTools.txtToEpub;
  const text = messages.txtToEpubTool;
  const [inputPath, setInputPath] = useLocalState(INPUT_LOCAL_KEY, "");
  const [outputDir, setOutputDir] = useLocalState(OUTPUT_LOCAL_KEY, "");
  const [coverPath, setCoverPath] = useLocalState(COVER_LOCAL_KEY, "");
  const [draft, setDraft] = useSessionState<DraftState>(
    DRAFT_SESSION_KEY,
    defaultDraft(),
  );
  const [presets, setPresets] = useState<TxtToEpubPreset[]>([]);
  const [styleTemplate, setStyleTemplate] = useState("");
  const [styleOptions, setStyleOptions] = useState<TxtToEpubStyle[]>([]);
  const [tocEntries, setTocEntries] = useState<TxtToEpubTocEntry[]>([]);
  const [selectedTocIds, setSelectedTocIds] = useState<string[]>([]);
  const [manualTocText, setManualTocText] = useState("");
  const [manualTocLevel, setManualTocLevel] = useState(1);
  const [batchLevel, setBatchLevel] = useState(1);
  const [scanInfo, setScanInfo] = useState<{
    lineCount: number;
    characterCount: number;
  } | null>(null);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [artifacts, setArtifacts] = useState<TxtToEpubArtifacts | null>(null);
  const [report, setReport] = useState<TxtToEpubReport | null>(null);
  const [artifactFeedback, setArtifactFeedback] = useState<string | null>(null);
  const cssFileInputRef = useRef<HTMLInputElement | null>(null);
  const snapshot = useRunSnapshot("txt_to_epub");
  usePollRunSnapshot("txt_to_epub");
  const setActiveTaskId = useRuntimeStore((state) => state.setActiveTaskId);
  const activeTaskId = useRuntimeStore(
    (state) => state.txt_to_epub.activeTaskId,
  );
  const tocPresets = useMemo(() => mergeChinesePresets(presets, text), [presets, text]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [styleResult, presetResult] = await Promise.all([
          txtToEpubBridge.listStyles(),
          txtToEpubBridge.listPresets(),
        ]);
        if (cancelled) return;
        const visibleStyles = styleResult.styles.filter((style) =>
          VISIBLE_STYLE_IDS.has(style.id),
        );
        setStyleOptions(visibleStyles);
        setStyleTemplate(styleResult.template);
        setPresets(presetResult.presets);
        setDraft((prev) => ({
          ...prev,
          customCss: prev.customCss || styleResult.template,
          styleId:
            prev.styleId === "custom" || VISIBLE_STYLE_IDS.has(prev.styleId)
              ? prev.styleId
              : "basic:classic",
        }));
      } catch (error) {
        if (BridgeError.isBridgeError(error) && !cancelled) {
          setActionError(error);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [setDraft]);

  useEffect(() => {
    if (tocPresets.length === 0) return;
    if (["zh_webnovel", "zh_published", "extra"].includes(draft.presetId)) {
      setDraft((prev) => ({ ...prev, presetId: "zh_novel" }));
      return;
    }
    if (!tocPresets.some((preset) => preset.id === draft.presetId)) {
      setDraft((prev) => ({ ...prev, presetId: tocPresets[0].id }));
    }
  }, [draft.presetId, tocPresets, setDraft]);

  useEffect(() => {
    if (!activeTaskId) return;
    if (snapshot.status !== "completed") return;
    if (snapshot.progress.failed > 0) return;
    if (snapshot.progress.completed <= 0) return;
    if (hasShownCleanCompletionToast(activeTaskId)) return;
    markCleanCompletionToastShown(activeTaskId);
    useToastStore.getState().push({
      variant: "success",
      title: messages.runCompleted.title,
    });
  }, [
    activeTaskId,
    snapshot.status,
    snapshot.progress.failed,
    snapshot.progress.completed,
    messages.runCompleted.title,
  ]);

  useEffect(() => {
    if (!activeTaskId || !TERMINAL.has(snapshot.status)) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await txtToEpubBridge.readArtifacts(activeTaskId);
        if (!cancelled) setArtifacts(result);
      } catch (error) {
        if (BridgeError.isBridgeError(error) && !cancelled) setActionError(error);
      }
      try {
        const result = await txtToEpubBridge.readReport(activeTaskId);
        if (!cancelled) setReport(result);
      } catch (error) {
        if (
          BridgeError.isBridgeError(error) &&
          error.code !== "bridge.not_found" &&
          !cancelled
        ) {
          setActionError(error);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTaskId, snapshot.status]);

  const selectedStyle = useMemo(
    () => styleOptions.find((style) => style.id === draft.styleId) ?? null,
    [draft.styleId, styleOptions],
  );
  const selectedPreset = useMemo(
    () => tocPresets.find((preset) => preset.id === draft.presetId) ?? null,
    [draft.presetId, tocPresets],
  );
  const activeCss = draft.styleId === "custom"
    ? draft.customCss
    : selectedStyle?.css ?? styleTemplate;
  const selectedCount = tocEntries.filter((entry) => entry.enabled).length;
  const selectedTocIdSet = useMemo(() => new Set(selectedTocIds), [selectedTocIds]);
  const selectedTocCount = selectedTocIds.length;
  const allTocSelected =
    tocEntries.length > 0 && selectedTocCount === tocEntries.length;
  const isRunning =
    activeTaskId !== null &&
    (snapshot.status === "running" || snapshot.status === "pending");
  const settled = snapshot.progress.completed;
  const percent =
    snapshot.progress.total > 0
      ? Math.floor((settled / snapshot.progress.total) * 100)
      : 0;

  const updateInputPath = (nextPath: string) => {
    if (nextPath === inputPath) return;
    setInputPath(nextPath);
    setCoverPath("");
    setDraft((prev) => ({ ...prev, title: "", author: "" }));
    setTocEntries([]);
    setSelectedTocIds([]);
    setScanInfo(null);
    setManualTocText("");
    setActionError(null);
    setArtifacts(null);
    setReport(null);
  };

  const handleChooseTxt = async () => {
    try {
      const result = await dialogsBridge.chooseTxtFile(inputPath || undefined);
      if (result.path) updateInputPath(result.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleChooseCover = async () => {
    try {
      const result = await dialogsBridge.chooseImageFile(coverPath || undefined);
      if (result.path) setCoverPath(result.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleScan = async () => {
    setActionError(null);
    setArtifacts(null);
    setReport(null);
    try {
      const presetRules =
        draft.regexMode === "preset" && draft.presetId === "zh_novel"
          ? selectedPreset?.rules ?? []
          : [];
      const result = await txtToEpubBridge.scanToc(
        inputPath,
        draft.regexMode === "preset" && presetRules.length === 0 ? draft.presetId : "",
        draft.regexMode === "simple" ? draft.customRules : presetRules,
        draft.regexMode === "advanced" ? draft.advancedPattern : "",
      );
      setTocEntries(result.entries);
      setSelectedTocIds([]);
      setScanInfo({
        lineCount: result.line_count,
        characterCount: result.character_count,
      });
      if (!draft.title.trim()) {
        setDraft((prev) => ({ ...prev, title: result.title }));
      }
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        if (error.message.includes("cannot find TOC text")) {
          setActionError(
            new BridgeError({
              code: error.code,
              message: text.addTocNotFound,
              retryable: false,
            }),
          );
        } else {
          setActionError(error);
        }
      } else {
        throw error;
      }
    }
  };

  const optionsForRun = (overwrite: boolean): TxtToEpubOptions => ({
    source_path: inputPath,
    output_dir: outputDir,
    title: draft.title,
    author: draft.author,
    language: "zh",
    cover_path: coverPath,
    style_id: draft.styleId,
    custom_css: draft.styleId === "custom" ? draft.customCss : "",
    overwrite,
    toc_entries: tocEntries,
  });

  const handleExecute = async () => {
    setActionError(null);
    setArtifacts(null);
    setReport(null);
    setArtifactFeedback(null);
    const cssError = validateCss(
      draft.styleId === "custom" ? draft.customCss : "",
      text,
    );
    if (cssError) {
      setActionError(
        new BridgeError({
          code: "bridge.invalid_argument",
          message: cssError,
          retryable: false,
        }),
      );
      return;
    }
    try {
      let overwrite = false;
      const plan = await txtToEpubBridge.preview(optionsForRun(false));
      if (plan.output_exists) {
        overwrite = window.confirm(
          text.overwriteConfirm.replace("{path}", plan.output_path),
        );
        if (!overwrite) return;
      }
      const requestId = `txt-to-epub-${Date.now().toString(36)}`;
      const { task_id } = await txtToEpubBridge.startTask(
        requestId,
        optionsForRun(overwrite),
      );
      setActiveTaskId("txt_to_epub", task_id);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const handleStop = async () => {
    if (!activeTaskId) return;
    setActionError(null);
    try {
      await txtToEpubBridge.stopTask(activeTaskId);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const patchEntry = (id: string, patch: Partial<TxtToEpubTocEntry>) => {
    setTocEntries((prev) =>
      prev.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)),
    );
  };

  const toggleEntrySelection = (id: string, selected: boolean) => {
    setSelectedTocIds((prev) => {
      if (selected) return prev.includes(id) ? prev : [...prev, id];
      return prev.filter((selectedId) => selectedId !== id);
    });
  };

  const toggleAllEntries = (selected: boolean) => {
    setSelectedTocIds(selected ? tocEntries.map((entry) => entry.id) : []);
  };

  const patchSelectedEntries = (patch: Partial<TxtToEpubTocEntry>) => {
    if (selectedTocIds.length === 0) return;
    setTocEntries((prev) =>
      prev.map((entry) =>
        selectedTocIdSet.has(entry.id) ? { ...entry, ...patch } : entry,
      ),
    );
  };

  const deleteEntries = (ids: string[]) => {
    if (ids.length === 0) return;
    const idSet = new Set(ids);
    setTocEntries((prev) => prev.filter((entry) => !idSet.has(entry.id)));
    setSelectedTocIds((prev) => prev.filter((id) => !idSet.has(id)));
  };

  const handleAddTocEntry = async () => {
    const query = manualTocText.trim();
    if (!query) return;
    setActionError(null);
    try {
      const entry = await txtToEpubBridge.locateTocEntry(
        inputPath,
        query,
        manualTocLevel,
        tocEntries.map((item) => item.startLine),
      );
      setTocEntries((prev) =>
        [...prev, { ...entry, id: `toc-manual-${Date.now().toString(36)}` }]
          .sort((left, right) => left.startLine - right.startLine),
      );
      setManualTocText("");
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const patchRule = (level: number, pattern: string) => {
    setDraft((prev) => ({
      ...prev,
      customRules: prev.customRules.map((rule) =>
        rule.level === level ? { ...rule, pattern } : rule,
      ),
    }));
  };

  const handleCssImport = async (file: File | undefined) => {
    if (!file) return;
    const text = await file.text();
    setDraft((prev) => ({ ...prev, styleId: "custom", customCss: text }));
  };

  const handleDownloadTemplate = () => {
    const blob = new Blob([styleTemplate || draft.customCss], {
      type: "text/css;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "transoria-epub-style-template.css";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const confidenceLabel = (confidence?: number) => {
    const value = typeof confidence === "number" ? confidence : 1;
    if (value >= 0.85) return text.confidenceHigh;
    if (value >= 0.65) return text.confidenceMedium;
    return text.confidenceLow;
  };

  const confidenceClass = (confidence?: number) => {
    const value = typeof confidence === "number" ? confidence : 1;
    if (value >= 0.85) return styles.confidenceHigh;
    if (value >= 0.65) return styles.confidenceMedium;
    return styles.confidenceLow;
  };

  return (
    <>
      <Panel
        title={embedded ? undefined : pageText.title}
        subtitle={embedded ? undefined : pageText.sub}
      >
        <div className={styles.grid2}>
          <div className={styles.fileRow}>
            <span className={styles.label}>{text.inputTxt}</span>
            <input
              className={styles.input}
              value={inputPath}
              onChange={(event) => updateInputPath(event.target.value)}
              placeholder={text.inputPlaceholder}
            />
            <Pill variant="ghost" onClick={handleChooseTxt}>
              {text.chooseTxt}
            </Pill>
          </div>
          <FolderPickerRow
            label={text.outputDir}
            value={outputDir}
            variant="output"
            onChange={setOutputDir}
            onError={setActionError}
            historyKey="general_tools:txt_to_epub:output_folder"
            compact
          />
        </div>
        <div className={styles.metaGrid}>
          <label className={styles.field}>
            <span>{text.titleLabel}</span>
            <input
              value={draft.title}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, title: event.target.value }))
              }
              placeholder={text.titlePlaceholder}
            />
          </label>
          <label className={styles.field}>
            <span>{text.authorLabel}</span>
            <input
              value={draft.author}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, author: event.target.value }))
              }
              placeholder={text.optionalPlaceholder}
            />
          </label>
          <label className={styles.field}>
            <span>{text.cover}</span>
            <div className={styles.inlinePicker}>
              <input
                value={coverPath}
                onChange={(event) => setCoverPath(event.target.value)}
                placeholder={text.coverPlaceholder}
              />
              <Pill variant="ghost" onClick={handleChooseCover}>
                {text.chooseCover}
              </Pill>
            </div>
          </label>
        </div>
      </Panel>

      <Panel label={text.chapterRecognition}>
        <div className={styles.modeRow}>
          {(["preset", "simple", "advanced"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              className={draft.regexMode === mode ? styles.modeActive : styles.modeButton}
              onClick={() => setDraft((prev) => ({ ...prev, regexMode: mode }))}
            >
              {mode === "preset"
                ? text.modePreset
                : mode === "simple"
                  ? text.modeSimple
                  : text.modeAdvanced}
            </button>
          ))}
        </div>
        {draft.regexMode === "preset" ? (
          <label className={styles.selectBlock}>
            <span>{text.modePreset}</span>
            <select
              value={draft.presetId}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, presetId: event.target.value }))
              }
            >
              {tocPresets.map((preset) => {
                const display = displayPreset(preset, text);
                return (
                  <option key={preset.id} value={preset.id}>
                    {display.label} - {display.description}
                  </option>
                );
              })}
            </select>
            {tocPresets.length > 0 ? (
              <small className={styles.selectedSummary}>
                {displayPreset(
                  selectedPreset ?? tocPresets[0],
                  text,
                ).description}
              </small>
            ) : null}
          </label>
        ) : draft.regexMode === "simple" ? (
          <div className={styles.ruleGrid}>
            {SIMPLE_RULE_LEVELS.map((level) => (
              <label key={level} className={styles.field}>
                <span>{text.levelRule.replace("{level}", String(level))}</span>
                <input
                  value={draft.customRules.find((rule) => rule.level === level)?.pattern ?? ""}
                  onChange={(event) => patchRule(level, event.target.value)}
                  placeholder={text.levelRulePlaceholder.replace(
                    "{level}",
                    `{${level}}`,
                  )}
                />
              </label>
            ))}
          </div>
        ) : (
          <label className={styles.field}>
            <span>{text.advancedRegex}</span>
            <input
              value={draft.advancedPattern}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, advancedPattern: event.target.value }))
              }
              placeholder={text.advancedRegexPlaceholder}
            />
          </label>
        )}
        <div className={styles.actionRow}>
          <Pill onClick={handleScan}>{text.scanToc}</Pill>
          <span className={styles.hint}>
            {scanInfo
              ? text.scanSummary
                  .replace("{candidates}", NUM.format(tocEntries.length))
                  .replace("{selected}", NUM.format(selectedCount))
                  .replace("{lines}", NUM.format(scanInfo.lineCount))
              : text.scanHint}
          </span>
        </div>
        <div className={styles.tocEditorBar}>
          <label className={styles.addTocField}>
            <span>{text.addTocText}</span>
            <input
              value={manualTocText}
              onChange={(event) => setManualTocText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void handleAddTocEntry();
                }
              }}
              placeholder={text.addTocPlaceholder}
            />
          </label>
          <label className={styles.compactSelect}>
            <span>{text.level}</span>
            <select
              value={manualTocLevel}
              onChange={(event) => setManualTocLevel(Number(event.target.value))}
            >
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
              <option value={4}>4</option>
            </select>
          </label>
          <Pill variant="ghost" onClick={() => void handleAddTocEntry()}>
            {text.addToc}
          </Pill>
        </div>
        <div className={styles.batchBar}>
          <span className={styles.hint}>
            {text.selectedRows.replace("{count}", NUM.format(selectedTocCount))}
          </span>
          <label className={styles.compactSelect}>
            <span>{text.batchSetLevel}</span>
            <select
              value={batchLevel}
              onChange={(event) => setBatchLevel(Number(event.target.value))}
            >
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
              <option value={4}>4</option>
            </select>
          </label>
          <Pill
            variant="ghost"
            onClick={() => patchSelectedEntries({ level: batchLevel })}
            disabled={selectedTocCount === 0}
          >
            {text.applyLevel}
          </Pill>
          <Pill
            variant="ghost"
            onClick={() => patchSelectedEntries({ enabled: true })}
            disabled={selectedTocCount === 0}
          >
            {text.enableSelected}
          </Pill>
          <Pill
            variant="ghost"
            onClick={() => patchSelectedEntries({ enabled: false })}
            disabled={selectedTocCount === 0}
          >
            {text.disableSelected}
          </Pill>
          <Pill
            variant="ghost"
            onClick={() => deleteEntries(selectedTocIds)}
            disabled={selectedTocCount === 0}
          >
            {text.deleteSelected}
          </Pill>
        </div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    checked={allTocSelected}
                    onChange={(event) => toggleAllEntries(event.target.checked)}
                    aria-label={text.selectAll}
                  />
                </th>
                <th>{text.enabled}</th>
                <th>{text.level}</th>
                <th>{text.headingTitle}</th>
                <th>{text.lineNumber}</th>
                <th>{text.confidence}</th>
                <th>{text.sourcePreview}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {tocEntries.length === 0 ? (
                <tr>
                  <td colSpan={8} className={styles.empty}>{text.noToc}</td>
                </tr>
              ) : (
                tocEntries.map((entry) => (
                  <tr key={entry.id}>
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedTocIdSet.has(entry.id)}
                        onChange={(event) =>
                          toggleEntrySelection(entry.id, event.target.checked)
                        }
                        aria-label={text.selectAll}
                      />
                    </td>
                    <td>
                      <input
                        type="checkbox"
                        checked={entry.enabled}
                        onChange={(event) =>
                          patchEntry(entry.id, { enabled: event.target.checked })
                        }
                      />
                    </td>
                    <td>
                      <select
                        value={entry.level}
                        onChange={(event) =>
                          patchEntry(entry.id, { level: Number(event.target.value) })
                        }
                      >
                        <option value={1}>1</option>
                        <option value={2}>2</option>
                        <option value={3}>3</option>
                        <option value={4}>4</option>
                      </select>
                    </td>
                    <td>
                      <input
                        className={styles.tableInput}
                        value={entry.title}
                        onChange={(event) =>
                          patchEntry(entry.id, { title: event.target.value })
                        }
                      />
                    </td>
                    <td>{entry.startLine}</td>
                    <td>
                      <span
                        className={`${styles.confidenceBadge} ${confidenceClass(entry.confidence)}`}
                      >
                        {confidenceLabel(entry.confidence)}
                      </span>
                    </td>
                    <td className={styles.previewText}>{entry.sourcePreview}</td>
                    <td>
                      <button
                        type="button"
                        className={styles.tableAction}
                        onClick={() => deleteEntries([entry.id])}
                      >
                        {text.deleteRow}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel label={text.epubStyle}>
        <label className={styles.selectBlock}>
          <span>{text.epubStyle}</span>
          <select
            value={draft.styleId}
            onChange={(event) =>
              setDraft((prev) => ({ ...prev, styleId: event.target.value }))
            }
          >
            {styleOptions.map((style) => {
              const display = displayStyle(style, text);
              return (
                <option key={style.id} value={style.id}>
                  {display.label} - {display.groupLabel}
                </option>
              );
            })}
            <option value="custom">
              {text.customCss} - {text.customCssHint}
            </option>
          </select>
          <small className={styles.selectedSummary}>
            {draft.styleId === "custom"
                ? text.customCssHint
                : selectedStyle
                ? displayStyle(selectedStyle, text).groupLabel
                : ""}
          </small>
        </label>
        <div className={styles.cssGrid}>
          <div>
            <div className={styles.actionRow}>
              <Pill
                variant="ghost"
                onClick={() => {
                  void navigator.clipboard.writeText(styleTemplate || draft.customCss);
                }}
              >
                {text.copyTemplate}
              </Pill>
              <Pill variant="ghost" onClick={handleDownloadTemplate}>
                {text.downloadTemplate}
              </Pill>
              <Pill
                variant="ghost"
                onClick={() => cssFileInputRef.current?.click()}
              >
                {text.importCss}
              </Pill>
              <Pill
                variant="ghost"
                onClick={() =>
                  setDraft((prev) => ({
                    ...prev,
                    styleId: "custom",
                    customCss: styleTemplate,
                  }))
                }
              >
                {text.resetTemplate}
              </Pill>
            </div>
            <input
              ref={cssFileInputRef}
              type="file"
              accept=".css,text/css"
              hidden
              onChange={(event) => {
                void handleCssImport(event.target.files?.[0]);
                event.currentTarget.value = "";
              }}
            />
            <textarea
              className={styles.cssEditor}
              value={draft.customCss}
              onFocus={() => setDraft((prev) => ({ ...prev, styleId: "custom" }))}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, customCss: event.target.value }))
              }
              spellCheck={false}
            />
            {validateCss(draft.styleId === "custom" ? draft.customCss : "", text) ? (
              <div className={styles.actionError}>
                {validateCss(
                  draft.styleId === "custom" ? draft.customCss : "",
                  text,
                )}
              </div>
            ) : null}
          </div>
          <iframe
            className={styles.stylePreview}
            title={text.stylePreviewTitle}
            srcDoc={previewDocument(activeCss, text)}
          />
        </div>
      </Panel>

      <Panel label={text.generate}>
        {actionError ? <div className={styles.actionError}>{actionError.message}</div> : null}
        <div className={styles.actionRow}>
          <Pill onClick={handleExecute} disabled={isRunning}>
            {isRunning ? text.generating : text.generate}
          </Pill>
          <Pill variant="ghost" onClick={handleStop} disabled={!isRunning}>
            {text.stop}
          </Pill>
          <span className={styles.hint}>{text.outputHint}</span>
        </div>
        {activeTaskId ? (
          <div className={styles.progressBlock}>
            <div className={styles.progressTrack}>
              <div className={styles.progressFill} style={{ width: `${percent}%` }} />
            </div>
            <span className={styles.hint}>
              {snapshot.status} · {settled}/{snapshot.progress.total || 1}
            </span>
          </div>
        ) : null}
        {artifacts ? (
          <div className={styles.artifacts}>
            <strong>{text.output}</strong>
            {artifacts.output_files.length > 0 ? (
              <div className={styles.artifactList}>
                {artifacts.output_files.map((path) => (
                  <CompactPath
                    key={path}
                    value={path}
                    copyLabel={messages.common.copyPath}
                  />
                ))}
              </div>
            ) : (
              <span className={styles.hint}>{text.noOutput}</span>
            )}
            <div className={styles.actionRow}>
              <Pill
                variant="ghost"
                onClick={() => {
                  void dialogsBridge.openDirectory(artifacts.output_folder);
                }}
              >
                {text.openOutputFolder}
              </Pill>
              <Pill
                variant="ghost"
                onClick={() => {
                  void navigator.clipboard.writeText(artifacts.output_files.join("\n"));
                  setArtifactFeedback(text.copiedPath);
                }}
              >
                {text.copyPath}
              </Pill>
              {artifactFeedback ? <span className={styles.hint}>{artifactFeedback}</span> : null}
            </div>
          </div>
        ) : null}
        {report ? (
          <div className={styles.report}>
            {text.reportSummary
              .replace("{chapters}", NUM.format(report.totals.chapters_written))
              .replace("{toc}", NUM.format(report.totals.toc_entries))
              .replace("{characters}", NUM.format(report.totals.characters_written))}
          </div>
        ) : null}
      </Panel>
    </>
  );
}

function validateCss(css: string, text: ReturnType<typeof useMessages>["txtToEpubTool"]): string {
  const lowered = css.toLowerCase();
  if (!css.trim()) return "";
  if (lowered.includes("@import")) return text.cssImportBlocked;
  if (/url\(\s*['"]?\s*(https?:|file:|\/)/.test(lowered)) {
    return text.cssUrlBlocked;
  }
  return "";
}

function previewDocument(
  css: string,
  text: ReturnType<typeof useMessages>["txtToEpubTool"],
): string {
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>${css}</style>
<style>body{padding:24px;max-width:520px;margin:0 auto;background:#fffaf3;color:#2a2620}</style>
</head>
<body>
  <h1>${escapeHtml(text.previewHeading1)}</h1>
  <h2>${escapeHtml(text.previewHeading2)}</h2>
  <h3>${escapeHtml(text.previewHeading3)}</h3>
  <h4>${escapeHtml(text.previewHeading4)}</h4>
  <p>${escapeHtml(text.previewParagraph1)}</p>
  <p>${escapeHtml(text.previewParagraph2)}</p>
</body>
</html>`;
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
