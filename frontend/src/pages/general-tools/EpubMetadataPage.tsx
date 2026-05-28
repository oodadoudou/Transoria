import { useState } from "react";

import {
  BridgeError,
  dialogsBridge,
  epubMetadataBridge,
  type EpubMetadataApplyResult,
  type EpubMetadataInfo,
} from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { useMessages } from "@/locales";
import { useLocalState } from "@/utils/localState";
import styles from "./EpubMetadataPage.module.css";

const INPUT_LOCAL_KEY = "transoria.generalTools.epubMetadata.inputPath";
const OUTPUT_LOCAL_KEY = "transoria.generalTools.epubMetadata.outputPath";
const COVER_LOCAL_KEY = "transoria.generalTools.epubMetadata.coverPath";

function splitPath(path: string): { dir: string; name: string } {
  const index = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  if (index < 0) return { dir: "", name: path };
  return { dir: path.slice(0, index + 1), name: path.slice(index + 1) };
}

function defaultOutputPath(inputPath: string): string {
  if (!inputPath.trim()) return "";
  const { dir, name } = splitPath(inputPath.trim());
  const base = name.replace(/\.epub$/i, "");
  return `${dir}${base}_metadata.epub`;
}

function defaultSaveName(inputPath: string): string {
  const output = defaultOutputPath(inputPath);
  return splitPath(output).name || "metadata.epub";
}

function outputFolder(path: string): string {
  const { dir } = splitPath(path);
  return dir.replace(/[\\/]$/, "");
}

export function EpubMetadataPage() {
  const text = useMessages().epubMetadataTool;
  const [inputPath, setInputPath] = useLocalState(INPUT_LOCAL_KEY, "");
  const [outputPath, setOutputPath] = useLocalState(OUTPUT_LOCAL_KEY, "");
  const [coverPath, setCoverPath] = useLocalState(COVER_LOCAL_KEY, "");
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState("");
  const [info, setInfo] = useState<EpubMetadataInfo | null>(null);
  const [result, setResult] = useState<EpubMetadataApplyResult | null>(null);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleChooseInput = async () => {
    try {
      const selected = await dialogsBridge.chooseEpubFile(inputPath || undefined);
      if (selected.path) {
        setInputPath(selected.path);
        if (!outputPath.trim()) setOutputPath(defaultOutputPath(selected.path));
      }
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleChooseOutput = async () => {
    try {
      const selected = await dialogsBridge.chooseSavePath(
        defaultSaveName(inputPath),
        ["epub"],
      );
      if (selected.path) setOutputPath(selected.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleChooseCover = async () => {
    try {
      const selected = await dialogsBridge.chooseImageFile(coverPath || undefined);
      if (selected.path) setCoverPath(selected.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) setActionError(error);
    }
  };

  const handleRead = async () => {
    setActionError(null);
    setFeedback(null);
    setResult(null);
    setLoading(true);
    try {
      const next = await epubMetadataBridge.read(inputPath);
      setInfo(next);
      setTitle(next.title);
      setAuthor(next.authors.join(", "));
      if (!outputPath.trim()) setOutputPath(defaultOutputPath(inputPath));
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

  const handleApply = async () => {
    setActionError(null);
    setFeedback(null);
    setLoading(true);
    try {
      const next = await epubMetadataBridge.apply(
        inputPath,
        outputPath,
        title,
        author,
        coverPath,
      );
      setResult(next);
      setFeedback(text.saved);
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
    const path = result?.output_path || outputPath;
    if (!path.trim()) return;
    try {
      await dialogsBridge.revealFile(path);
      setFeedback(null);
    } catch {
      const folder = outputFolder(path);
      if (!folder) return;
      await dialogsBridge.openDirectory(folder);
    }
  };

  return (
    <>
      <Panel title={text.title} subtitle={text.sub}>
        <div className={styles.pathGrid}>
          <label className={styles.field}>
            <span>{text.inputFile}</span>
            <div className={styles.fileRow}>
              <input
                value={inputPath}
                onChange={(event) => setInputPath(event.target.value)}
                placeholder={text.inputPlaceholder}
              />
              <Pill variant="ghost" onClick={handleChooseInput}>
                {text.chooseEpub}
              </Pill>
            </div>
          </label>
          <label className={styles.field}>
            <span>{text.outputFile}</span>
            <div className={styles.fileRow}>
              <input
                value={outputPath}
                onChange={(event) => setOutputPath(event.target.value)}
                placeholder={defaultOutputPath(inputPath)}
              />
              <Pill variant="ghost" onClick={handleChooseOutput}>
                {text.chooseOutput}
              </Pill>
            </div>
          </label>
        </div>

        <div className={styles.editGrid}>
          <label className={styles.field}>
            <span>{text.titleLabel}</span>
            <input value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label className={styles.field}>
            <span>{text.authorLabel}</span>
            <input value={author} onChange={(event) => setAuthor(event.target.value)} />
          </label>
          <label className={styles.field}>
            <span>{text.coverFile}</span>
            <div className={styles.fileRow}>
              <input
                value={coverPath}
                onChange={(event) => setCoverPath(event.target.value)}
              />
              <Pill variant="ghost" onClick={handleChooseCover}>
                {text.chooseCover}
              </Pill>
            </div>
          </label>
        </div>

        <div className={styles.actionRow}>
          <Pill onClick={handleRead} disabled={!inputPath || loading}>
            {text.read}
          </Pill>
          <Pill onClick={handleApply} disabled={!inputPath || !outputPath || loading}>
            {text.apply}
          </Pill>
          <Pill
            variant="ghost"
            onClick={handleRevealOutput}
            disabled={!result && !outputPath}
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

      <Panel label={text.currentLabel}>
        {info ? (
          <div className={styles.summaryGrid}>
            <Stat label={text.currentTitle} value={info.title || "-"} />
            <Stat
              label={text.currentAuthors}
              value={info.authors.length ? info.authors.join(", ") : "-"}
            />
            <Stat
              label={text.currentCover}
              value={info.has_cover ? text.coverPresent : text.coverMissing}
            />
          </div>
        ) : (
          <div className={styles.empty}>{text.noMetadata}</div>
        )}
      </Panel>
    </>
  );
}

interface StatProps {
  label: string;
  value: string;
}

function Stat({ label, value }: StatProps) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue}>{value}</span>
    </div>
  );
}
