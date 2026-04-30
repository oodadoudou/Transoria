import { useCallback, useState } from "react";
import { format, useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { PersistedTextPreserveRule } from "@/bridge";
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

const EMPTY_RULES: PersistedTextPreserveRule[] = [];

function emptyRule(): PersistedTextPreserveRule {
  return { pattern: "", note: "", enabled: true };
}

export function TextPreservePage() {
  const messages = useMessages();
  const m = messages.translation.textPreservePage;
  const moduleSettings = useModuleSettings("translation");
  const draft = moduleSettings.draft;
  const [selection, setSelection] =
    useState<RuleTableSelection>(EMPTY_SELECTION);
  const selectedIndex = selection.last;

  const rules = draft?.text_preserve_rules ?? EMPTY_RULES;

  const setRules = useCallback(
    (next: PersistedTextPreserveRule[]) => {
      moduleSettings.update("text_preserve_rules", next);
    },
    [moduleSettings],
  );

  const addRule = () => {
    const next = [...rules, emptyRule()];
    setRules(next);
    const newIndex = next.length - 1;
    setSelection({ indices: [newIndex], last: newIndex });
  };
  const updateRule = (
    index: number,
    patch: Partial<PersistedTextPreserveRule>,
  ) =>
    setRules(
      rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)),
    );
  const deleteRule = (index: number) => {
    setRules(rules.filter((_, i) => i !== index));
    setSelection(EMPTY_SELECTION);
  };
  const handleBulkDelete = (indices: number[]) => {
    const drop = new Set(indices);
    setRules(rules.filter((_, i) => !drop.has(i)));
    setSelection(EMPTY_SELECTION);
  };

  const enabledCount = rules.filter((r) => r.enabled).length;

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
        label={format(m.stats.total, { n: rules.length })}
        labelExtra={<span>{format(m.stats.enabled, { n: enabledCount })}</span>}
      >
        <RuleTable
          rules={rules}
          selection={selection}
          onSelectionChange={setSelection}
          onBulkDelete={handleBulkDelete}
          contextMenuLabels={{
            deleteSelected: (n) =>
              format(messages.ruleTable.deleteSelected, { n }),
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
            { label: m.actions.import, onClick: () => undefined },
            { label: m.actions.export, onClick: () => undefined },
            { label: m.actions.search, onClick: () => undefined },
            { label: m.actions.statistics, onClick: () => undefined },
            { label: m.actions.preset, onClick: () => undefined },
          ]}
        />
      </Panel>
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
