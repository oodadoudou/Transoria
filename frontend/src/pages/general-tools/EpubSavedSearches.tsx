import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Plus, Trash2, X } from "lucide-react";
import { useMessages } from "@/locales";
import { epubContentBridge } from "@/bridge/client";
import { parseSavedSearches, type SavedSearch, type SearchOptions } from "./epubSearchLibrary";
import styles from "./EpubContentPage.module.css";

export function EpubSavedSearches({ current, load, execute, close }: {
  current: SearchOptions; load: (value: SearchOptions) => void; close: () => void;
  execute: (rules: SavedSearch[], apply: boolean, fingerprint?: string) => Promise<Record<string, unknown>>;
}) {
  const t = useMessages().epubContentTool;
  const [entries, setEntries] = useState<SavedSearch[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [proposal, setProposal] = useState<{ rules: SavedSearch[]; result: Record<string, unknown> } | null>(null);
  useEffect(() => {
    let active = true;
    epubContentBridge.loadSearches().then(({ entries: saved }) => { if (active) setEntries(parseSavedSearches(JSON.stringify(saved))); })
      .catch((cause) => { if (active) setError(String(cause)); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);
  const update = (next: SavedSearch[]) => {
    setBusy(true);
    epubContentBridge.saveSearches(next).then(() => { setEntries(next); setProposal(null); setError(""); })
      .catch((cause) => setError(String(cause))).finally(() => setBusy(false));
  };
  const run = async (apply: boolean) => {
    if (busy || (apply && !proposal)) return;
    if (apply && !window.confirm(t.confirmReplace)) return;
    setBusy(true); setError("");
    try {
      const rules = apply ? proposal!.rules : entries.filter((entry) => selected.includes(entry.id));
      const result = await execute(rules, apply, apply ? String(proposal!.result.fingerprint) : undefined);
      setProposal(apply ? null : { rules, result });
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  return <div className={styles.modalBackdrop}><div className={`${styles.saveDialog} ${styles.tocPatternDialog}`} role="dialog" aria-modal="true" aria-label={t.savedSearches}>
    <div className={styles.toolHeading}><h3>{t.savedSearches}</h3><button type="button" aria-label={t.close} disabled={busy} onClick={close}><X size={18} /></button></div>
    <label htmlFor="saved-search-name">{t.label}</label><div className={styles.toolControls}><input id="saved-search-name" value={name} onChange={(event) => setName(event.target.value)} /><button type="button" disabled={!name.trim() || !current.query || entries.length >= 100 || busy} onClick={() => { update([...entries, { ...current, id: crypto.randomUUID(), name: name.trim() }]); setName(""); }}><Plus size={16} />{t.saveSearch}</button></div>
    {entries.map((entry, index) => <div key={entry.id} className={styles.savedSearchRow}>
      <input type="checkbox" aria-label={`${t.selectSearch}: ${entry.name}`} checked={selected.includes(entry.id)} disabled={busy} onChange={(event) => { setSelected(event.target.checked ? [...selected, entry.id] : selected.filter((id) => id !== entry.id)); setProposal(null); }} />
      <input aria-label={`${t.label}: ${entry.name}`} defaultValue={entry.name} disabled={busy} onBlur={(event) => { const value = event.target.value.trim(); if (value && value !== entry.name) update(entries.map((item) => item.id === entry.id ? { ...item, name: value } : item)); else if (!value) { event.target.value = entry.name; setError(t.label); } }} />
      <button type="button" disabled={busy} onClick={() => { load(entry); close(); }}>{t.loadSearch}</button>
      <button type="button" aria-label={`${t.up}: ${entry.name}`} disabled={!index || busy} onClick={() => { const next = [...entries]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; update(next); }}><ArrowUp size={16} /></button>
      <button type="button" aria-label={`${t.down}: ${entry.name}`} disabled={index === entries.length - 1 || busy} onClick={() => { const next = [...entries]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; update(next); }}><ArrowDown size={16} /></button>
      <button type="button" aria-label={`${t.removeEntry}: ${entry.name}`} disabled={busy} onClick={() => { update(entries.filter((item) => item.id !== entry.id)); setSelected(selected.filter((id) => id !== entry.id)); }}><Trash2 size={16} /></button>
    </div>)}
    {error ? <div className={styles.error} role="alert">{error}</div> : null}
    {proposal ? <div role="status">{String(proposal.result.replacements)} {t.matches} · {String(proposal.result.files_changed)} {t.filesChanged}</div> : null}
    <div className={styles.saveActions}><button type="button" disabled={!selected.length || busy} onClick={() => void run(false)}>{t.previewReplace}</button><button type="button" disabled={!proposal || !proposal.result.replacements || busy} onClick={() => void run(true)}>{t.applyTool}</button></div>
  </div></div>;
}
