import { type ReactNode, useState } from "react";

import {
  BridgeError,
  dialogsBridge,
  epubRepairBridge,
  type EpubRepairPreview,
  type EpubRepairResult,
} from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { CompactPath } from "@/components/CompactPath";
import { useMessages } from "@/locales";
import { useLocalState } from "@/utils/localState";
import styles from "./EpubMetadataPage.module.css";

const INPUT_LOCAL_KEY = "transoria.generalTools.epubRepair.inputPath";
const OUTPUT_FOLDER_LOCAL_KEY = "transoria.generalTools.epubRepair.outputFolder";

function splitPath(path: string): { dir: string; name: string } {
  const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (index < 0) return { dir: "", name: path };
  return { dir: path.slice(0, index + 1), name: path.slice(index + 1) };
}

function joinPath(dir: string, name: string): string {
  const folder = dir.trim();
  if (!folder) return name;
  const separator = folder.includes("\\") && !folder.includes("/") ? "\\" : "/";
  return `${folder.replace(/[\\/]+$/, "")}${separator}${name}`;
}

function defaultOutputFolder(inputPath: string): string {
  return splitPath(inputPath.trim()).dir.replace(/[\\/]$/, "");
}

function repairedFilename(inputPath: string): string {
  const fallback = splitPath(inputPath.trim()).name.replace(/\.epub$/i, "");
  return fallback ? `${fallback}-repaired.epub` : "repaired.epub";
}

export function EpubRepairPage({ embedded = false }: { embedded?: boolean } = {}) {
  const messages = useMessages();
  const text = messages.epubRepairTool;
  const [inputPath, setInputPath] = useLocalState(INPUT_LOCAL_KEY, "");
  const [outputFolderPath, setOutputFolderPath] = useLocalState(
    OUTPUT_FOLDER_LOCAL_KEY,
    "",
  );
  const [result, setResult] = useState<EpubRepairResult | null>(null);
  const [preview, setPreview] = useState<EpubRepairPreview | null>(null);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const resolvedOutputFolder =
    outputFolderPath.trim() || defaultOutputFolder(inputPath);
  const resolvedOutputPath = joinPath(
    resolvedOutputFolder,
    repairedFilename(inputPath),
  );

  const handleChooseInput = async () => {
    try {
      const selected = await dialogsBridge.chooseEpubFile(inputPath || undefined);
      if (selected.path) {
        setInputPath(selected.path);
        setPreview(null);
        setResult(null);
        if (!outputFolderPath.trim()) {
          setOutputFolderPath(defaultOutputFolder(selected.path));
        }
      }
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleChooseOutputFolder = async () => {
    try {
      const selected = await dialogsBridge.chooseOutputDirectory(
        resolvedOutputFolder || undefined,
      );
      if (selected.path) {
        setOutputFolderPath(selected.path);
        setPreview(null);
        setResult(null);
      }
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handlePreview = async () => {
    if (!inputPath.trim() || !resolvedOutputPath.trim()) return;
    setActionError(null);
    setFeedback(null);
    setResult(null);
    setLoading(true);
    try {
      setPreview(await epubRepairBridge.preview(inputPath, resolvedOutputPath));
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
      else throw error;
    } finally {
      setLoading(false);
    }
  };

  const handleRepair = async () => {
    if (!inputPath.trim() || !resolvedOutputPath.trim()) return;
    setActionError(null);
    setFeedback(null);
    setLoading(true);
    try {
      const next = await epubRepairBridge.apply(
        inputPath,
        resolvedOutputPath,
        false,
      );
      setResult(next);
      setFeedback(text.repaired);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    } finally {
      setLoading(false);
    }
  };

  const handleRevealOutput = async () => {
    const path = result?.output_path || resolvedOutputPath;
    if (!path.trim()) return;
    try {
      await dialogsBridge.revealFile(path);
      setFeedback(null);
    } catch {
      const folder = splitPath(path).dir || resolvedOutputFolder;
      if (folder) await dialogsBridge.openDirectory(folder);
    }
  };

  return (
    <>
      <Panel
        title={embedded ? undefined : text.title}
        subtitle={embedded ? undefined : text.sub}
      >
        <div className={styles.pathGrid}>
          <label className={styles.field}>
            <span>{text.inputFile}</span>
            <div className={styles.fileRow}>
              <input
                value={inputPath}
                onChange={(event) => {
                  setInputPath(event.target.value);
                  setPreview(null);
                  setResult(null);
                }}
                placeholder={text.inputPlaceholder}
              />
              <Pill variant="ghost" onClick={handleChooseInput} disabled={loading}>
                {text.chooseEpub}
              </Pill>
            </div>
          </label>
          <label className={styles.field}>
            <span>{text.outputFolder}</span>
            <div className={styles.fileRow}>
              <input
                value={outputFolderPath}
                onChange={(event) => {
                  setOutputFolderPath(event.target.value);
                  setPreview(null);
                  setResult(null);
                }}
                placeholder={defaultOutputFolder(inputPath)}
              />
              <Pill
                variant="ghost"
                onClick={handleChooseOutputFolder}
                disabled={loading}
              >
                {text.chooseOutputFolder}
              </Pill>
            </div>
          </label>
        </div>

        <div className={styles.generatedOutput}>
          <span>{text.generatedOutput}</span>
          <CompactPath
            value={resolvedOutputPath}
            copyLabel={messages.common.copyPath}
            className={styles.generatedPath}
            emptyLabel="-"
          />
        </div>

        <div className={styles.actionRow}>
          <Pill onClick={handlePreview} disabled={!inputPath || loading}>
            {text.preview}
          </Pill>
          <Pill
            onClick={handleRepair}
            disabled={!preview || preview.output_path !== resolvedOutputPath || loading}
          >
            {text.repair}
          </Pill>
          <Pill
            variant="ghost"
            onClick={handleRevealOutput}
            disabled={!result}
          >
            {text.openOutput}
          </Pill>
          {feedback ? <span className={styles.feedback}>{feedback}</span> : null}
          {actionError ? (
            <span className={styles.actionError}>
              <code>{actionError.code}</code> {actionError.message}
            </span>
          ) : null}
        </div>
      </Panel>

      <Panel label={text.previewLabel}>
        {preview ? (
          <div className={styles.summaryGrid}>
            <Stat label={text.scanned} value={String(preview.documents_scanned)} />
            <Stat label={text.toRepair} value={String(preview.documents_to_repair)} />
            <Stat
              label={text.structure}
              value={outcomeLabel(preview.structure_check.status, text)}
            />
            <Stat
              label={text.currentOutput}
              value={<CompactPath value={preview.output_path} copyLabel={messages.common.copyPath} />}
            />
          </div>
        ) : (
          <div className={styles.empty}>{text.noResult}</div>
        )}
      </Panel>

      <Panel label={text.currentLabel}>
        {result ? (
          <div className={styles.summaryGrid}>
            <Stat label={text.currentLabel} value={outcomeLabel(result.outcome, text)} />
            <Stat label={text.scanned} value={String(result.documents_scanned)} />
            <Stat label={text.repairedFiles} value={String(result.documents_repaired)} />
            <Stat label={text.htmlScanned} value={String(result.html_files_scanned)} />
            <Stat label={text.xmlRepaired} value={String(result.xml_files_repaired)} />
            <Stat
              label={text.voidContainers}
              value={String(result.void_containers_repaired)}
            />
            <Stat
              label={text.wrappersAdded}
              value={String(result.document_wrappers_added)}
            />
            <Stat
              label={text.currentOutput}
              value={
                <CompactPath
                  value={result.output_path}
                  copyLabel={messages.common.copyPath}
                />
              }
            />
          </div>
        ) : (
          <div className={styles.empty}>{text.noResult}</div>
        )}
      </Panel>
    </>
  );
}

function outcomeLabel(
  status: string,
  text: { success: string; successWithWarnings: string; failed: string },
): string {
  if (status === "ok" || status === "success") return text.success;
  if (status === "warning" || status === "success_with_warnings") {
    return text.successWithWarnings;
  }
  return text.failed;
}

interface StatProps {
  label: string;
  value: ReactNode;
}

function Stat({ label, value }: StatProps) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue}>{value}</span>
    </div>
  );
}
