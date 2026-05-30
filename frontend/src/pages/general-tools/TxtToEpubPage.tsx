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
const EN_PRESET_TEXT: Record<string, { label: string; description: string }> = {
  markdown: {
    label: "Markdown headings",
    description: "#, ##, ###, and #### headings",
  },
  zh_webnovel: {
    label: "Chinese web novel",
    description: "Volume/chapter formats common in Chinese fiction",
  },
  zh_published: {
    label: "Chinese published",
    description: "Preface, prologue, sections, chapters, epilogue",
  },
  extra: {
    label: "Extras and side stories",
    description: "Bonus chapters, side stories, and special episodes",
  },
  ko_novel: {
    label: "Korean fiction",
    description: "Korean prologue, episode, volume, side story, epilogue",
  },
  en_chapter: {
    label: "English chapters",
    description: "Chapter, Volume, Prologue, and Epilogue",
  },
  numeric: {
    label: "Numeric headings",
    description: "1., 1.1, 01, 001",
  },
};
const EN_STYLE_LABELS: Record<string, string> = {
  classic: "Classic",
  clean: "Clean",
  contrast: "High contrast",
  elegant: "Elegant",
  eyecare: "Eye care",
  fantasy: "Fantasy",
  geometric: "Geometric",
  geometric_frame: "Geometric frame",
  grayscale: "Grayscale",
  line_hierarchy: "Line hierarchy",
  linear: "Linear",
  minimal: "Minimal",
  minimal_grid: "Minimal grid",
  minimal_linear: "Minimal linear",
  minimal_modern: "Modern minimal",
  modern: "Modern",
  monochrome: "Monochrome",
  soft: "Soft",
  structured_minimal: "Structured minimal",
  warm: "Warm",
};

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

export function TxtToEpubPage() {
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

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [styleResult, presetResult] = await Promise.all([
          txtToEpubBridge.listStyles(),
          txtToEpubBridge.listPresets(),
        ]);
        if (cancelled) return;
        setStyleOptions(styleResult.styles);
        setStyleTemplate(styleResult.template);
        setPresets(presetResult.presets);
        setDraft((prev) => ({
          ...prev,
          customCss: prev.customCss || styleResult.template,
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
  const activeCss = draft.styleId === "custom"
    ? draft.customCss
    : selectedStyle?.css ?? styleTemplate;
  const selectedCount = tocEntries.filter((entry) => entry.enabled).length;
  const isRunning =
    activeTaskId !== null &&
    (snapshot.status === "running" || snapshot.status === "pending");
  const settled = snapshot.progress.completed + snapshot.progress.skipped;
  const percent =
    snapshot.progress.total > 0
      ? Math.floor((settled / snapshot.progress.total) * 100)
      : 0;
  const englishUi = messages.generalTools.crumb === "General Tools";

  const handleChooseTxt = async () => {
    try {
      const result = await dialogsBridge.chooseTxtFile(inputPath || undefined);
      if (result.path) setInputPath(result.path);
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
      const result = await txtToEpubBridge.scanToc(
        inputPath,
        draft.regexMode === "preset" ? draft.presetId : "",
        draft.regexMode === "simple" ? draft.customRules : [],
        draft.regexMode === "advanced" ? draft.advancedPattern : "",
      );
      setTocEntries(result.entries);
      setScanInfo({
        lineCount: result.line_count,
        characterCount: result.character_count,
      });
      if (!draft.title.trim()) {
        setDraft((prev) => ({ ...prev, title: result.title }));
      }
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
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

  return (
    <>
      <Panel title={pageText.title} subtitle={pageText.sub}>
        <div className={styles.grid2}>
          <div className={styles.fileRow}>
            <span className={styles.label}>{text.inputTxt}</span>
            <input
              className={styles.input}
              value={inputPath}
              onChange={(event) => setInputPath(event.target.value)}
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
          <div className={styles.fileRow}>
            <span className={styles.label}>{text.cover}</span>
            <input
              className={styles.input}
              value={coverPath}
              onChange={(event) => setCoverPath(event.target.value)}
              placeholder={text.coverPlaceholder}
            />
            <Pill variant="ghost" onClick={handleChooseCover}>
              {text.chooseCover}
            </Pill>
          </div>
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
          <div className={styles.presetGrid}>
            {presets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                className={draft.presetId === preset.id ? styles.presetActive : styles.preset}
                onClick={() => setDraft((prev) => ({ ...prev, presetId: preset.id }))}
              >
                <strong>{displayPreset(preset, englishUi).label}</strong>
                <span>{displayPreset(preset, englishUi).description}</span>
              </button>
            ))}
          </div>
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
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>{text.enabled}</th>
                <th>{text.level}</th>
                <th>{text.headingTitle}</th>
                <th>{text.lineNumber}</th>
                <th>{text.sourcePreview}</th>
              </tr>
            </thead>
            <tbody>
              {tocEntries.length === 0 ? (
                <tr>
                  <td colSpan={5} className={styles.empty}>{text.noToc}</td>
                </tr>
              ) : (
                tocEntries.map((entry) => (
                  <tr key={entry.id}>
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
                    <td className={styles.previewText}>{entry.sourcePreview}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel label={text.epubStyle}>
        <div className={styles.styleGrid}>
          {styleOptions.map((style) => (
            <button
              key={style.id}
              type="button"
              className={draft.styleId === style.id ? styles.styleActive : styles.styleCard}
              onClick={() => setDraft((prev) => ({ ...prev, styleId: style.id }))}
            >
              <span>{displayStyle(style, englishUi).label}</span>
              <small>{displayStyle(style, englishUi).groupLabel}</small>
            </button>
          ))}
          <button
            type="button"
            className={draft.styleId === "custom" ? styles.styleActive : styles.styleCard}
            onClick={() => setDraft((prev) => ({ ...prev, styleId: "custom" }))}
          >
            <span>{text.customCss}</span>
            <small>{text.customCssHint}</small>
          </button>
        </div>
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
            <span>{artifacts.output_files.join("\n") || text.noOutput}</span>
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

function displayPreset(
  preset: TxtToEpubPreset,
  englishUi: boolean,
): { label: string; description: string } {
  if (!englishUi) return { label: preset.label, description: preset.description };
  return EN_PRESET_TEXT[preset.id] ?? { label: preset.label, description: preset.description };
}

function displayStyle(
  style: TxtToEpubStyle,
  englishUi: boolean,
): { label: string; groupLabel: string } {
  if (!englishUi) return { label: style.label, groupLabel: style.groupLabel };
  const [, key = style.id] = style.id.split(":");
  return {
    label: EN_STYLE_LABELS[key] ?? style.label,
    groupLabel: style.id.startsWith("enhanced:")
      ? "Enhanced style"
      : "Compatible style",
  };
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
