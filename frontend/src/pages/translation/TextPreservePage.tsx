import { useCallback, useState } from "react";
import { format, useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import {
  BridgeError,
  dialogsBridge,
  rulesBridge,
  type PersistedTextPreserveRule,
  type TextPreserveRulePayload,
} from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { TextField } from "@/components/TextField";
import { ToggleSwitch } from "@/components/ToggleSwitch";
import {
  EMPTY_SELECTION,
  RuleTable,
  type RuleTableColumn,
  type RuleTableSelection,
  ruleTableStyles,
} from "@/components/RuleTable";
import { useSearchShortcut } from "@/components/useSearchShortcut";
import {
  RuleExportModal,
  type RuleExportFormat,
} from "@/components/RuleExportModal";
import { RuleStatsModal } from "@/components/RuleStatsModal";
import { appendUniqueRows, tableRowKey } from "@/utils/tableDedupe";

const EMPTY_RULES: PersistedTextPreserveRule[] = [];

function emptyRule(): PersistedTextPreserveRule {
  return { pattern: "", note: "", enabled: true };
}

function textPreserveRuleKey(rule: PersistedTextPreserveRule): string {
  return tableRowKey([rule.pattern, rule.note, rule.enabled]);
}

export function TextPreservePage() {
  const messages = useMessages();
  const m = messages.translation.textPreservePage;
  const moduleSettings = useModuleSettings("translation");
  const draft = moduleSettings.draft;
  const [selection, setSelection] =
    useState<RuleTableSelection>(EMPTY_SELECTION);
  const selectedIndex = selection.last;
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [exportOpen, setExportOpen] = useState(false);
  const [statsOpen, setStatsOpen] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  useSearchShortcut(() => setSearchOpen(true));

  const allRules = draft?.text_preserve_rules ?? EMPTY_RULES;
  const rules = (() => {
    const q = searchQuery.trim().toLowerCase();
    if (!searchOpen || !q) return allRules;
    return allRules.filter((r) =>
      [r.pattern, r.note].some((field) => field.toLowerCase().includes(q)),
    );
  })();

  const setRules = useCallback(
    (next: PersistedTextPreserveRule[]) => {
      moduleSettings.update("text_preserve_rules", next);
    },
    [moduleSettings],
  );

  // Filter view → master operations: we look up the actual item by
  // reference in ``allRules`` rather than by index in the filtered
  // ``rules`` view, so bulk delete / duplicate stay correct while a
  // search is active. ``rules.filter`` preserves object identity, so
  // a Set of refs is enough.
  const addRule = () => {
    const next = [...allRules, emptyRule()];
    setRules(next);
    const newIndex = next.length - 1;
    setSelection({ indices: [newIndex], last: newIndex });
  };
  const updateRule = (
    filteredIndex: number,
    patch: Partial<PersistedTextPreserveRule>,
  ) => {
    const item = rules[filteredIndex];
    if (!item) return;
    setRules(allRules.map((r) => (r === item ? { ...r, ...patch } : r)));
  };
  const deleteRule = (filteredIndex: number) => {
    const item = rules[filteredIndex];
    if (!item) return;
    setRules(allRules.filter((r) => r !== item));
    setSelection(EMPTY_SELECTION);
  };
  const handleBulkDelete = (filteredIndices: number[]) => {
    const dropRefs = new Set(
      filteredIndices.map((i) => rules[i]).filter(Boolean),
    );
    setRules(allRules.filter((r) => !dropRefs.has(r)));
    setSelection(EMPTY_SELECTION);
  };
  const handleBulkDuplicate = (filteredIndices: number[]) => {
    const sources = filteredIndices
      .map((i) => rules[i])
      .filter((r): r is PersistedTextPreserveRule => Boolean(r));
    if (sources.length === 0) return;
    const copies = sources.map((rule) => ({ ...rule }));
    setRules([...allRules, ...copies]);
    const startIndex = allRules.length;
    const newIndices = copies.map((_, i) => startIndex + i);
    setSelection({
      indices: newIndices,
      last: newIndices[newIndices.length - 1] ?? null,
    });
  };

  const enabledCount = allRules.filter((r) => r.enabled).length;

  const handleImport = async () => {
    setImportError(null);
    try {
      const choice = await dialogsBridge.chooseGlossaryFile({
        allowJson: true,
        allowXlsx: true,
      });
      if (!choice.path) return;
      const result = await rulesBridge.importRules(
        "text_preserve",
        choice.path,
      );
      const incoming = result.rules as TextPreserveRulePayload[];
      if (incoming.length === 0) {
        setImportError(m.importEmpty);
        return;
      }
      setRules(appendUniqueRows(allRules, incoming, textPreserveRuleKey).rows);
    } catch (error) {
      setImportError(
        BridgeError.isBridgeError(error)
          ? `${error.code}: ${error.message}`
          : String(error),
      );
    }
  };

  const handleExport = async (fmt: RuleExportFormat) => {
    setImportError(null);
    try {
      const choice = await dialogsBridge.chooseSavePath(
        `text-preserve.${fmt}`,
        [fmt],
      );
      if (!choice.path) return;
      await rulesBridge.exportRules("text_preserve", choice.path, allRules);
    } catch (error) {
      setImportError(
        BridgeError.isBridgeError(error)
          ? `${error.code}: ${error.message}`
          : String(error),
      );
    }
  };

  if (!draft) {
    return <Panel title={m.title} subtitle={m.sub} />;
  }

  const columns: RuleTableColumn<PersistedTextPreserveRule>[] = [
    {
      key: "pattern",
      label: m.columns.pattern,
      width: "1.6fr",
      render: (rule) => (
        <span style={{ fontFamily: "var(--font-mono)" }}>{rule.pattern}</span>
      ),
      edit: {
        getValue: (rule) => rule.pattern,
        onCommit: (idx, value) => updateRule(idx, { pattern: value }),
      },
    },
    {
      key: "note",
      label: m.columns.note,
      width: "1.4fr",
      render: (rule) => (
        <span className={ruleTableStyles.cellMuted}>{rule.note}</span>
      ),
      edit: {
        getValue: (rule) => rule.note,
        onCommit: (idx, value) => updateRule(idx, { note: value }),
      },
    },
  ];

  const selected =
    selectedIndex !== null && selectedIndex >= 0 && selectedIndex < rules.length
      ? rules[selectedIndex]
      : null;

  return (
    <>
      <Panel title={m.title} subtitle={m.sub} />

      <Panel
        label={format(m.stats.total, { n: allRules.length })}
        labelExtra={<span>{format(m.stats.enabled, { n: enabledCount })}</span>}
      >
        {searchOpen ? (
          <input
            type="text"
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
          rules={rules}
          selection={selection}
          onSelectionChange={setSelection}
          onBulkDelete={handleBulkDelete}
          onBulkDuplicate={handleBulkDuplicate}
          contextMenuLabels={{
            deleteSelected: (n) =>
              format(messages.ruleTable.deleteSelected, { n }),
            duplicateSelected: (n) =>
              format(messages.ruleTable.duplicateSelected, { n }),
          }}
          isEnabled={(rule) => rule.enabled}
          columns={columns}
          emptyMessage={m.empty}
          editor={
            selected !== null && selectedIndex !== null ? (
              <RuleEditor
                rule={selected}
                labels={m}
                onChange={(patch) => updateRule(selectedIndex, patch)}
                onDelete={() => deleteRule(selectedIndex)}
              />
            ) : (
              <div className={ruleTableStyles.editorEmpty}>{m.editorEmpty}</div>
            )
          }
          toolbar={[
            { label: m.actions.add, onClick: addRule, primary: true },
            { label: m.actions.import, onClick: () => void handleImport() },
            { label: m.actions.export, onClick: () => setExportOpen(true) },
            {
              label: m.actions.search,
              onClick: () => setSearchOpen((v) => !v),
            },
            { label: m.actions.statistics, onClick: () => setStatsOpen(true) },
          ]}
        />
        {importError ? (
          <div style={{ marginTop: 12, fontSize: 12, color: "#b04038" }}>
            {importError}
          </div>
        ) : null}
      </Panel>
      {exportOpen ? (
        <RuleExportModal
          onPick={(fmt) => {
            setExportOpen(false);
            void handleExport(fmt);
          }}
          onClose={() => setExportOpen(false)}
        />
      ) : null}
      {statsOpen ? (
        <RuleStatsModal
          kind="text_preserve"
          rules={allRules}
          onClose={() => setStatsOpen(false)}
        />
      ) : null}
    </>
  );
}

function RuleEditor({
  rule,
  labels,
  onChange,
  onDelete,
}: {
  rule: PersistedTextPreserveRule;
  labels: ReturnType<typeof useMessages>["translation"]["textPreservePage"];
  onChange: (patch: Partial<PersistedTextPreserveRule>) => void;
  onDelete: () => void;
}) {
  return (
    <div className={ruleTableStyles.editor}>
      <TextField
        label={labels.patternLabel}
        value={rule.pattern}
        onChange={(v) => onChange({ pattern: v })}
        placeholder={labels.patternPlaceholder}
        mono
      />
      <TextField
        label={labels.noteLabel}
        value={rule.note}
        onChange={(v) => onChange({ note: v })}
        placeholder={labels.notePlaceholder}
      />
      <ToggleSwitch
        label={labels.enabledLabel}
        checked={rule.enabled}
        onChange={(next) => onChange({ enabled: next })}
      />
      <div className={ruleTableStyles.editorActions}>
        <Pill variant="ghost" onClick={onDelete}>
          {labels.deleteAction}
        </Pill>
      </div>
    </div>
  );
}
