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
    ],
    advancedPattern: "",
    styleId: "basic:classic",
    customCss: "",
  };
}

export function TxtToEpubPage() {
  const messages = useMessages();
  const text = messages.generalTools.txtToEpub;
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
    const cssError = validateCss(draft.styleId === "custom" ? draft.customCss : "");
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
        overwrite = window.confirm(`输出文件已存在：\n${plan.output_path}\n\n是否覆盖？`);
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
      <Panel title={text.title} subtitle={text.sub}>
        <div className={styles.grid2}>
          <div className={styles.fileRow}>
            <span className={styles.label}>输入 TXT</span>
            <input
              className={styles.input}
              value={inputPath}
              onChange={(event) => setInputPath(event.target.value)}
              placeholder="选择或粘贴 .txt 文件路径"
            />
            <Pill variant="ghost" onClick={handleChooseTxt}>选择 TXT</Pill>
          </div>
          <FolderPickerRow
            label="输出目录"
            value={outputDir}
            variant="output"
            onChange={setOutputDir}
            onError={setActionError}
            compact
          />
        </div>
        <div className={styles.metaGrid}>
          <label className={styles.field}>
            <span>书名</span>
            <input
              value={draft.title}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, title: event.target.value }))
              }
              placeholder="留空则使用 TXT 文件名"
            />
          </label>
          <label className={styles.field}>
            <span>作者</span>
            <input
              value={draft.author}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, author: event.target.value }))
              }
              placeholder="可选"
            />
          </label>
          <div className={styles.fileRow}>
            <span className={styles.label}>封面</span>
            <input
              className={styles.input}
              value={coverPath}
              onChange={(event) => setCoverPath(event.target.value)}
              placeholder="可选：jpg / png / webp"
            />
            <Pill variant="ghost" onClick={handleChooseCover}>选择封面</Pill>
          </div>
        </div>
      </Panel>

      <Panel label="章节识别">
        <div className={styles.modeRow}>
          {(["preset", "simple", "advanced"] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              className={draft.regexMode === mode ? styles.modeActive : styles.modeButton}
              onClick={() => setDraft((prev) => ({ ...prev, regexMode: mode }))}
            >
              {mode === "preset" ? "预设" : mode === "simple" ? "简单正则" : "高级正则"}
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
                <strong>{preset.label}</strong>
                <span>{preset.description}</span>
              </button>
            ))}
          </div>
        ) : draft.regexMode === "simple" ? (
          <div className={styles.ruleGrid}>
            {[1, 2, 3].map((level) => (
              <label key={level} className={styles.field}>
                <span>{level} 级标题正则</span>
                <input
                  value={draft.customRules.find((rule) => rule.level === level)?.pattern ?? ""}
                  onChange={(event) => patchRule(level, event.target.value)}
                  placeholder={`例如 ^#{${level}}\\s*(?P<title>.+)$`}
                />
              </label>
            ))}
          </div>
        ) : (
          <label className={styles.field}>
            <span>高级正则</span>
            <input
              value={draft.advancedPattern}
              onChange={(event) =>
                setDraft((prev) => ({ ...prev, advancedPattern: event.target.value }))
              }
              placeholder="例如 ^第\\d+章\\s*(?P<title>.*)$"
            />
          </label>
        )}
        <div className={styles.actionRow}>
          <Pill onClick={handleScan}>扫描目录</Pill>
          <span className={styles.hint}>
            {scanInfo
              ? `${NUM.format(tocEntries.length)} 个候选 · ${NUM.format(selectedCount)} 个启用 · ${NUM.format(scanInfo.lineCount)} 行`
              : "扫描后可编辑标题、层级和是否进入目录。"}
          </span>
        </div>
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>启用</th>
                <th>层级</th>
                <th>标题</th>
                <th>行号</th>
                <th>原文预览</th>
              </tr>
            </thead>
            <tbody>
              {tocEntries.length === 0 ? (
                <tr>
                  <td colSpan={5} className={styles.empty}>尚未扫描目录。</td>
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

      <Panel label="EPUB 样式">
        <div className={styles.styleGrid}>
          {styleOptions.map((style) => (
            <button
              key={style.id}
              type="button"
              className={draft.styleId === style.id ? styles.styleActive : styles.styleCard}
              onClick={() => setDraft((prev) => ({ ...prev, styleId: style.id }))}
            >
              <span>{style.label}</span>
              <small>{style.groupLabel}</small>
            </button>
          ))}
          <button
            type="button"
            className={draft.styleId === "custom" ? styles.styleActive : styles.styleCard}
            onClick={() => setDraft((prev) => ({ ...prev, styleId: "custom" }))}
          >
            <span>自定义 CSS</span>
            <small>基于模板修改</small>
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
                复制模板
              </Pill>
              <Pill variant="ghost" onClick={handleDownloadTemplate}>下载模板</Pill>
              <Pill
                variant="ghost"
                onClick={() => cssFileInputRef.current?.click()}
              >
                导入 CSS
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
                重置模板
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
            {validateCss(draft.styleId === "custom" ? draft.customCss : "") ? (
              <div className={styles.actionError}>
                {validateCss(draft.styleId === "custom" ? draft.customCss : "")}
              </div>
            ) : null}
          </div>
          <iframe
            className={styles.stylePreview}
            title="EPUB 样式预览"
            srcDoc={previewDocument(activeCss)}
          />
        </div>
      </Panel>

      <Panel label="生成">
        {actionError ? <div className={styles.actionError}>{actionError.message}</div> : null}
        <div className={styles.actionRow}>
          <Pill onClick={handleExecute} disabled={isRunning}>
            {isRunning ? "生成中" : "生成 EPUB"}
          </Pill>
          <Pill variant="ghost" onClick={handleStop} disabled={!isRunning}>
            停止
          </Pill>
          <span className={styles.hint}>
            输出文件名默认使用书名；输出目录为空时使用输入 TXT 同目录。
          </span>
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
            <strong>输出</strong>
            <span>{artifacts.output_files.join("\n") || "暂无输出文件"}</span>
            <div className={styles.actionRow}>
              <Pill
                variant="ghost"
                onClick={() => {
                  void dialogsBridge.openDirectory(artifacts.output_folder);
                }}
              >
                打开输出目录
              </Pill>
              <Pill
                variant="ghost"
                onClick={() => {
                  void navigator.clipboard.writeText(artifacts.output_files.join("\n"));
                  setArtifactFeedback("已复制输出路径");
                }}
              >
                复制路径
              </Pill>
              {artifactFeedback ? <span className={styles.hint}>{artifactFeedback}</span> : null}
            </div>
          </div>
        ) : null}
        {report ? (
          <div className={styles.report}>
            写入章节 {NUM.format(report.totals.chapters_written)} · 目录项{" "}
            {NUM.format(report.totals.toc_entries)} · 字符{" "}
            {NUM.format(report.totals.characters_written)}
          </div>
        ) : null}
      </Panel>
    </>
  );
}

function validateCss(css: string): string {
  const lowered = css.toLowerCase();
  if (!css.trim()) return "";
  if (lowered.includes("@import")) return "自定义 CSS 不能使用 @import。";
  if (/url\(\s*['"]?\s*(https?:|file:|\/)/.test(lowered)) {
    return "自定义 CSS 不能引用远程 URL 或本地绝对路径。";
  }
  return "";
}

function previewDocument(css: string): string {
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>${css}</style>
<style>body{padding:24px;max-width:520px;margin:0 auto;background:#fffaf3;color:#2a2620}</style>
</head>
<body>
  <h1>第一卷 春日</h1>
  <h2>第一章 重逢</h2>
  <p>这是 EPUB 样式预览。正文会保持段落缩进、行距和章节标题层级。</p>
  <p>用户选择样式后，生成的章节 XHTML 会引用同一份 CSS。</p>
</body>
</html>`;
}
