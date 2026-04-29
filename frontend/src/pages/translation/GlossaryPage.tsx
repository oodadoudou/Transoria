import { useEffect, useRef } from "react";
import { format, useMessages } from "@/locales";
import { useTaskStore, type GlossaryEntry } from "@/store/useTaskStore";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { TextField } from "@/components/TextField";
import { Segmented } from "@/components/Segmented";
import { ToggleSwitch } from "@/components/ToggleSwitch";
import styles from "./GlossaryPage.module.css";

type Toggle = "on" | "off";

interface PersistedEntry {
  src: string;
  dst: string;
  info: string;
  regex: boolean;
  case_sensitive: boolean;
  enabled: boolean;
}

function entryToPersisted(entry: GlossaryEntry): PersistedEntry {
  return {
    src: entry.source,
    dst: entry.translation,
    info: entry.description,
    regex: false,
    case_sensitive: entry.caseSensitive,
    enabled: entry.enabled,
  };
}

function persistedToEntry(raw: unknown, index: number): GlossaryEntry | null {
  if (!raw || typeof raw !== "object") return null;
  const obj = raw as Record<string, unknown>;
  const src = typeof obj.src === "string" ? obj.src : "";
  const dst = typeof obj.dst === "string" ? obj.dst : "";
  return {
    id: `g-${index}`,
    source: src,
    translation: dst,
    description: typeof obj.info === "string" ? obj.info : "",
    caseSensitive: obj.case_sensitive === true,
    enabled: obj.enabled !== false,
  };
}

export function GlossaryPage() {
  const messages = useMessages();
  const { glossaryPage: g } = messages.translation;
  const state = useTaskStore((s) => s.translationGlossary);
  const setEnabled = useTaskStore((s) => s.setTranslationGlossaryEnabled);
  const setSelectedId = useTaskStore((s) => s.setTranslationGlossarySelectedId);
  const addEntry = useTaskStore((s) => s.addTranslationGlossaryEntry);
  const updateEntry = useTaskStore((s) => s.updateTranslationGlossaryEntry);
  const deleteEntry = useTaskStore((s) => s.deleteTranslationGlossaryEntry);
  const importEntries = useTaskStore((s) => s.importTranslationGlossaryEntries);
  const moduleSettings = useModuleSettings("translation");
  const draft = moduleSettings.draft;
  const isHydrated = moduleSettings.isHydrated;

  // One-time hydration from settings → in-memory store on first load.
  // The local store is the edit buffer; settings is the persistence
  // layer threaded into TranslationConfig at run start.
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (hydratedRef.current) return;
    if (!isHydrated || !draft) return;
    hydratedRef.current = true;
    const persisted = (draft.translation_glossary ?? []) as unknown[];
    if (persisted.length === 0) return;
    const entries = persisted
      .map((raw, idx) => persistedToEntry(raw, idx))
      .filter((entry): entry is GlossaryEntry => entry !== null);
    if (entries.length > 0) {
      importEntries(entries);
    }
  }, [draft, isHydrated, importEntries]);

  // Sync edits from the in-memory store back to settings (debounced
  // by ``useModuleSettings.update``). Skipped until hydration completes
  // so we don't blank the persisted entries with the empty initial
  // store on first mount.
  useEffect(() => {
    if (!hydratedRef.current) return;
    moduleSettings.update(
      "translation_glossary",
      state.entries.map(entryToPersisted),
    );
    // moduleSettings.update is referentially stable across renders;
    // the linter wants it in deps but adding it would re-fire the
    // effect unnecessarily.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.entries]);

  const selected = state.entries.find((e) => e.id === state.selectedId) ?? null;
  const enabledCount = state.entries.filter((e) => e.enabled).length;

  return (
    <>
      <Panel
        title={g.title}
        subtitle={g.sub}
        labelExtra={
          <Segmented<Toggle>
            ariaLabel={g.enabled}
            options={[
              { id: "on", label: g.enabled },
              { id: "off", label: g.disabled },
            ]}
            value={state.enabled ? "on" : "off"}
            onChange={(v) => setEnabled(v === "on")}
          />
        }
      />

      <Panel
        label={format(g.stats.total, { n: state.entries.length })}
        labelExtra={<span>{format(g.stats.enabled, { n: enabledCount })}</span>}
      >
        <div className={styles.editorGrid}>
          <div className={styles.tableWrap}>
            <div className={styles.tableHeader}>
              <span className={styles.colIndex}>#</span>
              <span>{g.columns.source}</span>
              <span>{g.columns.translation}</span>
              <span>{g.columns.description}</span>
              <span className={styles.colRule}>{g.columns.rule}</span>
            </div>
            {state.entries.length === 0 ? (
              <div className={styles.empty}>{g.empty}</div>
            ) : (
              state.entries.map((entry, index) => (
                <button
                  key={entry.id}
                  type="button"
                  className={`${styles.row} ${
                    state.selectedId === entry.id ? styles.rowActive : ""
                  } ${entry.enabled ? "" : styles.rowDisabled}`.trim()}
                  onClick={() => setSelectedId(entry.id)}
                >
                  <span className={`${styles.colIndex} tnum`}>{index + 1}</span>
                  <span className={styles.cell}>{entry.source}</span>
                  <span className={styles.cell}>{entry.translation}</span>
                  <span className={`${styles.cell} ${styles.cellMuted}`}>
                    {entry.description}
                  </span>
                  <span className={styles.colRule}>
                    <span
                      className={`${styles.ruleChip} ${
                        entry.caseSensitive ? styles.ruleChipOn : ""
                      }`.trim()}
                      title={entry.caseSensitive ? "Aa" : "Aa (insensitive)"}
                    >
                      Aa
                    </span>
                  </span>
                </button>
              ))
            )}
          </div>

          <aside className={styles.sidebar}>
            {selected ? (
              <EntryEditor
                entry={selected}
                onChange={(updates) => updateEntry(selected.id, updates)}
                onDelete={() => deleteEntry(selected.id)}
              />
            ) : (
              <div className={styles.editorEmpty}>{g.editor.empty}</div>
            )}
          </aside>
        </div>

        <div className={styles.toolbar}>
          <ToolbarBtn label={g.actions.add} onClick={addEntry} primary />
          <ToolbarBtn label={g.actions.import} />
          <ToolbarBtn label={g.actions.export} />
          <ToolbarBtn label={g.actions.search} />
          <ToolbarBtn label={g.actions.statistics} />
          <ToolbarBtn label={g.actions.preset} />
        </div>
      </Panel>
    </>
  );
}

function EntryEditor({
  entry,
  onChange,
  onDelete,
}: {
  entry: GlossaryEntry;
  onChange: (updates: Partial<GlossaryEntry>) => void;
  onDelete: () => void;
}) {
  const messages = useMessages();
  const { glossaryPage: g } = messages.translation;

  return (
    <div className={styles.editor}>
      <TextField
        label={g.editor.source}
        value={entry.source}
        onChange={(v) => onChange({ source: v })}
        placeholder={g.editor.sourcePlaceholder}
      />
      <TextField
        label={g.editor.translation}
        value={entry.translation}
        onChange={(v) => onChange({ translation: v })}
        placeholder={g.editor.translationPlaceholder}
      />
      <TextField
        label={g.editor.description}
        value={entry.description}
        onChange={(v) => onChange({ description: v })}
        placeholder={g.editor.descriptionPlaceholder}
      />
      <ToggleSwitch
        label={g.editor.caseSensitive}
        checked={entry.caseSensitive}
        onChange={(next) => onChange({ caseSensitive: next })}
        help={g.editor.caseSensitiveHelp}
      />
      <ToggleSwitch
        label={g.editor.active}
        checked={entry.enabled}
        onChange={(next) => onChange({ enabled: next })}
      />
      <div className={styles.editorActions}>
        <Pill variant="ghost" onClick={onDelete}>
          {g.actions.delete}
        </Pill>
      </div>
    </div>
  );
}

function ToolbarBtn({
  label,
  onClick,
  primary,
}: {
  label: string;
  onClick?: () => void;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      className={`${styles.toolbarBtn} ${primary ? styles.toolbarBtnPrimary : ""}`.trim()}
      onClick={onClick}
    >
      {primary ? "+ " : ""}
      {label}
    </button>
  );
}
