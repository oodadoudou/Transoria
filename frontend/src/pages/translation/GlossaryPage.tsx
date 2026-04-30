import { useEffect, useRef, useState } from "react";
import { format, useMessages } from "@/locales";
import { BridgeError, dialogsBridge, glossaryBridge } from "@/bridge";
import { useTaskStore, type GlossaryEntry } from "@/store/useTaskStore";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { TextField } from "@/components/TextField";
import { Segmented } from "@/components/Segmented";
import { ToggleSwitch } from "@/components/ToggleSwitch";
import {
  EMPTY_SELECTION,
  RuleTable,
  type RuleTableColumn,
  type RuleTableSelection,
  ruleTableStyles,
} from "@/components/RuleTable";
import { GlossaryStatsModal } from "@/components/GlossaryStatsModal";
import { GlossaryPresetModal } from "@/components/GlossaryPresetModal";

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
  const addEntry = useTaskStore((s) => s.addTranslationGlossaryEntry);
  const updateEntry = useTaskStore((s) => s.updateTranslationGlossaryEntry);
  const deleteEntry = useTaskStore((s) => s.deleteTranslationGlossaryEntry);
  const importEntries = useTaskStore((s) => s.importTranslationGlossaryEntries);
  const [selection, setSelection] =
    useState<RuleTableSelection>(EMPTY_SELECTION);
  const moduleSettings = useModuleSettings("translation");
  const draft = moduleSettings.draft;
  const isHydrated = moduleSettings.isHydrated;
  const [importError, setImportError] = useState<string | null>(null);

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

  useEffect(() => {
    if (!hydratedRef.current) return;
    moduleSettings.update(
      "translation_glossary",
      state.entries.map(entryToPersisted),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.entries]);

  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [statsOpen, setStatsOpen] = useState(false);
  const [presetOpen, setPresetOpen] = useState(false);

  const filteredEntries = (() => {
    const query = searchQuery.trim().toLowerCase();
    if (!searchOpen || !query) return state.entries;
    return state.entries.filter((e) =>
      [e.source, e.translation, e.description].some((field) =>
        field.toLowerCase().includes(query),
      ),
    );
  })();

  const selectedIndex = selection.last;
  const selected =
    selectedIndex !== null &&
    selectedIndex >= 0 &&
    selectedIndex < filteredEntries.length
      ? filteredEntries[selectedIndex]
      : null;
  const enabledCount = state.entries.filter((e) => e.enabled).length;

  const handleImport = async () => {
    setImportError(null);
    try {
      const choice = await dialogsBridge.chooseGlossaryFile({
        allowJson: true,
        allowXlsx: true,
      });
      if (!choice.path) return;
      const result = await glossaryBridge.importRules(choice.path);
      const incoming: GlossaryEntry[] = result.entries.map((entry, idx) => ({
        id: `g-imp-${Date.now()}-${idx}`,
        source: entry.src,
        translation: entry.dst,
        description: entry.info,
        caseSensitive: entry.case_sensitive,
        enabled: entry.enabled,
      }));
      if (incoming.length === 0) {
        setImportError(g.importEmpty);
        return;
      }
      importEntries([...state.entries, ...incoming]);
    } catch (error) {
      setImportError(
        BridgeError.isBridgeError(error)
          ? `${error.code}: ${error.message}`
          : String(error),
      );
    }
  };

  const handleExport = async () => {
    setImportError(null);
    try {
      const choice = await dialogsBridge.chooseSavePath("glossary.json", [
        "json",
        "xlsx",
      ]);
      if (!choice.path) return;
      await glossaryBridge.exportRules(
        choice.path,
        state.entries.map((entry) => ({
          src: entry.source,
          dst: entry.translation,
          info: entry.description,
          regex: false,
          case_sensitive: entry.caseSensitive,
          enabled: entry.enabled,
        })),
      );
    } catch (error) {
      setImportError(
        BridgeError.isBridgeError(error)
          ? `${error.code}: ${error.message}`
          : String(error),
      );
    }
  };

  const commitFromIndex = (idx: number, patch: Partial<GlossaryEntry>) => {
    const target = filteredEntries[idx];
    if (target) updateEntry(target.id, patch);
  };

  const columns: RuleTableColumn<GlossaryEntry>[] = [
    {
      key: "source",
      label: g.columns.source,
      width: "1.4fr",
      render: (entry) => entry.source,
      edit: {
        getValue: (entry) => entry.source,
        onCommit: (idx, value) => commitFromIndex(idx, { source: value }),
      },
    },
    {
      key: "translation",
      label: g.columns.translation,
      width: "1.4fr",
      render: (entry) => entry.translation,
      edit: {
        getValue: (entry) => entry.translation,
        onCommit: (idx, value) => commitFromIndex(idx, { translation: value }),
      },
    },
    {
      key: "description",
      label: g.columns.description,
      width: "1.6fr",
      render: (entry) => (
        <span className={ruleTableStyles.cellMuted}>{entry.description}</span>
      ),
      edit: {
        getValue: (entry) => entry.description,
        onCommit: (idx, value) => commitFromIndex(idx, { description: value }),
      },
    },
    {
      key: "rule",
      label: g.columns.rule,
      width: "56px",
      align: "right",
      render: (entry) => (
        <span
          className={`${ruleTableStyles.ruleChip} ${entry.caseSensitive ? ruleTableStyles.ruleChipOn : ""}`.trim()}
          title="Aa"
        >
          Aa
        </span>
      ),
    },
  ];

  const handleAdd = () => {
    addEntry();
    // The new entry is appended at the end of state.entries; once the
    // search filter is cleared this will also be the last filtered
    // index. We optimistically point selection at the new tail.
    const newIndex = state.entries.length;
    setSelection({ indices: [newIndex], last: newIndex });
  };

  const handleBulkDelete = (indices: number[]) => {
    // Translate indices in the filtered view to entry ids before
    // mutating, so the array shifts during deletion don't bite us.
    const ids = indices
      .map((i) => filteredEntries[i]?.id)
      .filter((id): id is string => Boolean(id));
    ids.forEach(deleteEntry);
    setSelection(EMPTY_SELECTION);
  };

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
        {searchOpen ? (
          <input
            type="text"
            placeholder={g.searchPlaceholder}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            autoFocus
            style={{
              width: "100%",
              marginTop: 8,
              padding: "8px 12px",
              border: "1px solid var(--hairline-strong)",
              borderRadius: 8,
              font: "inherit",
              fontSize: 13,
              background: "var(--panel)",
            }}
          />
        ) : null}
        <RuleTable
          rules={filteredEntries}
          selection={selection}
          onSelectionChange={setSelection}
          onBulkDelete={handleBulkDelete}
          contextMenuLabels={{
            deleteSelected: (n) =>
              format(messages.ruleTable.deleteSelected, { n }),
          }}
          isEnabled={(entry) => entry.enabled}
          columns={columns}
          emptyMessage={g.empty}
          editor={
            selected ? (
              <EntryEditor
                entry={selected}
                onChange={(updates) => updateEntry(selected.id, updates)}
                onDelete={() => deleteEntry(selected.id)}
              />
            ) : (
              <div className={ruleTableStyles.editorEmpty}>
                {g.editor.empty}
              </div>
            )
          }
          toolbar={[
            { label: g.actions.add, onClick: handleAdd, primary: true },
            { label: g.actions.import, onClick: () => void handleImport() },
            { label: g.actions.export, onClick: () => void handleExport() },
            {
              label: g.actions.search,
              onClick: () => {
                setSearchOpen((prev) => {
                  if (prev) setSearchQuery("");
                  return !prev;
                });
              },
            },
            {
              label: g.actions.statistics,
              onClick: () => setStatsOpen(true),
            },
            {
              label: g.actions.preset,
              onClick: () => setPresetOpen(true),
            },
          ]}
        />

        {importError ? (
          <div style={{ marginTop: 12, fontSize: 12, color: "#b04038" }}>
            {importError}
          </div>
        ) : null}
      </Panel>

      {statsOpen ? (
        <GlossaryStatsModal
          entries={state.entries}
          onClose={() => setStatsOpen(false)}
        />
      ) : null}
      {presetOpen ? (
        <GlossaryPresetModal
          onPick={(rawEntries) => {
            const incoming: GlossaryEntry[] = rawEntries.map((entry, idx) => ({
              id: `g-pre-${Date.now()}-${idx}`,
              source: entry.src,
              translation: entry.dst,
              description: entry.info,
              caseSensitive: entry.case_sensitive,
              enabled: entry.enabled,
            }));
            importEntries([...state.entries, ...incoming]);
          }}
          onClose={() => setPresetOpen(false)}
        />
      ) : null}
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
    <div className={ruleTableStyles.editor}>
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
      <div className={ruleTableStyles.editorActions}>
        <Pill variant="ghost" onClick={onDelete}>
          {g.actions.delete}
        </Pill>
      </div>
    </div>
  );
}
