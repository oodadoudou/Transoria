import { useState } from "react";
import { FolderOpen, X } from "lucide-react";
import { dialogsBridge, epubContentBridge, type EpubContentSession } from "@/bridge";
import { useMessages } from "@/locales";
import styles from "./EpubContentPage.module.css";

type ReportRow = { path?: string; line?: number; kind?: string; message?: string; diff?: string; selectors?: string[]; family?: string; before_size?: number; after_size?: number; width?: number; height?: number; error?: string; reason?: string; skipped?: boolean; suggestions?: string[] };
type Report = { rows?: ReportRow[]; output?: string; count?: number; fingerprint?: string; dictionary_loaded?: boolean; truncated?: boolean; applied?: boolean; exit_code?: number; words?: Array<{ word: string; count: number }> };

export function EpubEditorTools({ session, commit, update, select, close }: {
  session: EpubContentSession; commit: () => Promise<unknown>; update: (session: EpubContentSession, resetHistory?: boolean) => Promise<void>;
  select: (path: string, line: number) => void; close: () => void;
}) {
  const t = useMessages().epubContentTool;
  const [tool, setTool] = useState("issues");
  const [report, setReport] = useState<Report | null>(null);
  const [proposal, setProposal] = useState<{ options: Record<string, unknown>; fingerprint: string } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [localPath, setLocalPath] = useState("");
  const [ignored, setIgnored] = useState("");
  const [quality, setQuality] = useState(85);
  const [dimension, setDimension] = useState(0);
  const [resource, setResource] = useState("");
  const [stylesheet, setStylesheet] = useState("");
  const [family, setFamily] = useState("");
  const [checkpoint, setCheckpoint] = useState("");
  const [name, setName] = useState("");
  const choices: Array<[string, string]> = [["issues", t.issues], ["diff", t.compare], ["text_report", t.textReport], ["cleanup_css", t.cleanCss], ["images", t.images], ["fonts", t.fonts], ["set_cover", t.coverMetadata], ["embed_font", t.embedFont], ["upgrade", t.upgrade], ["epubcheck", t.epubcheck]];
  const reversible = ["cleanup_css", "images", "fonts"].includes(tool);
  const mutation = ["set_cover", "embed_font", "upgrade"].includes(tool);
  const run = async (action: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setError("");
    try { await action(); } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  const options = () => ({ dictionary_path: localPath, ignored: ignored.split("\n").filter(Boolean), quality, max_dimension: dimension, path: resource, family, style_path: stylesheet, checkpoint, jar_path: localPath });
  const execute = async (apply = false) => run(async () => {
    if ((apply || mutation) && !window.confirm(t.confirmTool)) return;
    await commit();
    const frozen = apply ? proposal?.options : options();
    if (!frozen) return;
    const response = await epubContentBridge.tool(session.session_id, tool, { ...frozen, apply, fingerprint: apply ? proposal?.fingerprint : undefined });
    setReport(response.result as Report);
    setProposal(!apply && reversible && typeof response.result.fingerprint === "string" ? { options: frozen, fingerprint: response.result.fingerprint } : null);
    await update(response.session, apply || mutation);
  });
  const pick = async () => run(async () => { const chosen = await dialogsBridge.chooseAnyFile(localPath || undefined); if (chosen.path) setLocalPath(chosen.path); });
  const resources = session.files.filter((file) => tool === "set_cover" ? file.media_type.startsWith("image/") : /\.(ttf|otf|woff2?)$/i.test(file.path));
  return <div className={styles.modalBackdrop}><section className={`${styles.saveDialog} ${styles.toolsDialog}`} role="dialog" aria-modal="true" aria-label={t.tools}>
    <div className={styles.toolHeader}><h3>{t.tools}</h3><button type="button" title={t.close} aria-label={t.close} disabled={busy} onClick={close}><X size={18} /></button></div>
    <div className={styles.toolControls}><select aria-label={t.tools} value={tool} disabled={busy} onChange={(event) => { setTool(event.target.value); setReport(null); setProposal(null); setError(""); }}>{choices.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
      <button type="button" disabled={busy} onClick={() => void execute()}>{reversible ? t.previewTool : t.runTool}</button>
      {reversible ? <button type="button" disabled={busy || !proposal || !report?.count} onClick={() => void execute(true)}>{t.applyTool}</button> : null}
    </div>
    {tool === "diff" ? <div className={styles.toolControls}><select aria-label={t.compare} value={checkpoint} onChange={(event) => { setCheckpoint(event.target.value); setReport(null); }}><option value="">{t.sourceBook}</option>{session.checkpoints.map((item) => <option key={item} value={item}>{item}</option>)}</select><button type="button" disabled={busy || !checkpoint} onClick={() => void run(async () => { if (!window.confirm(t.confirmTool)) return; await commit(); const next = await epubContentBridge.restoreCheckpoint(session.session_id, checkpoint); await update(next, true); setReport(null); })}>{t.restoreCheckpoint}</button><input aria-label={t.checkpointName} placeholder={t.checkpointName} value={name} onChange={(event) => setName(event.target.value)} /><button type="button" disabled={busy || !name.trim()} onClick={() => void run(async () => { await commit(); await update(await epubContentBridge.namedCheckpoint(session.session_id, name.trim())); setName(""); })}>{t.newCheckpoint}</button></div> : null}
    {tool === "text_report" || tool === "epubcheck" ? <label>{tool === "text_report" ? t.dictionary : t.checkJar}<div className={styles.resourcePick}><input value={localPath} onChange={(event) => { setLocalPath(event.target.value); setProposal(null); }} /><button type="button" title={t.chooseFile} disabled={busy} onClick={() => void pick()}><FolderOpen size={16} /></button></div></label> : null}
    {tool === "text_report" ? <label>{t.ignoredWords}<textarea rows={2} value={ignored} onChange={(event) => setIgnored(event.target.value)} /></label> : null}
    {tool === "images" ? <div className={styles.toolControls}><label>{t.quality}<input type="number" min={10} max={100} value={quality} onChange={(event) => { setQuality(Number(event.target.value)); setProposal(null); }} /></label><label>{t.maxDimension}<input type="number" min={0} max={10000} value={dimension} onChange={(event) => { setDimension(Number(event.target.value)); setProposal(null); }} /></label></div> : null}
    {tool === "set_cover" || tool === "embed_font" ? <label>{tool === "set_cover" ? t.imageResource : t.fonts}<select value={resource} onChange={(event) => setResource(event.target.value)}><option value="">{t.chooseFile}</option>{resources.map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}</select></label> : null}
    {tool === "embed_font" ? <div className={styles.toolControls}><label>{t.fontFamily}<input value={family} onChange={(event) => setFamily(event.target.value)} /></label><select aria-label={t.styleFiles} value={stylesheet} onChange={(event) => setStylesheet(event.target.value)}><option value="">{t.styleFiles}</option>{session.files.filter((file) => file.media_type === "text/css").map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}</select></div> : null}
    {error ? <div className={styles.error} role="alert">{error}</div> : null}
    {busy ? <p role="status">{t.loading}</p> : null}
    <div className={styles.toolResults}>
      {report?.applied ? <p role="status">{t.toolApplied}</p> : null}
      {report?.dictionary_loaded === false ? <p role="status">{t.dictionaryMissing}</p> : null}
      {report?.truncated ? <p role="status">{t.reportTruncated}</p> : null}
      {report?.exit_code !== undefined ? <p role="status">{t.checkExit}: {report.exit_code}</p> : null}
      {report?.rows?.map((row, index) => <article key={`${row.path}-${index}`} className={styles.toolResult}>
        <div>{row.path ? session.files.some((file) => file.path === row.path && file.editable) ? <button type="button" disabled={busy} title={row.path} onClick={() => { select(row.path!, row.line ?? 1); close(); }}>{row.path}{row.line ? `:${row.line}` : ""}</button> : <strong>{row.path}</strong> : null}<span>{row.kind ?? row.family ?? ""}</span></div>
        {row.message || row.error || row.reason ? <p>{row.message ?? row.error ?? row.reason}</p> : null}
        {row.suggestions?.length ? <p>{row.suggestions.join(" · ")}</p> : null}
        {row.selectors ? <code>{row.selectors.join(", ")}</code> : null}
        {typeof row.before_size === "number" ? <p>{t.before}: {row.before_size.toLocaleString()} B · {t.after}: {row.after_size?.toLocaleString()} B{row.width ? ` · ${row.width} × ${row.height}` : ""}</p> : null}
        {row.diff ? <pre>{row.diff}</pre> : null}
      </article>)}
      {report && (!report.rows || !report.rows.length) && !report.output && !report.applied ? <p>{t.toolEmpty}</p> : null}
      {report?.words?.length ? <details><summary>{t.textReport}</summary><table><tbody>{report.words.map((word) => <tr key={word.word}><td>{word.word}</td><td>{word.count.toLocaleString()}</td></tr>)}</tbody></table></details> : null}
      {report?.output ? <pre>{report.output}</pre> : null}
    </div>
  </section></div>;
}
