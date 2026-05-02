import { useCallback, useState } from "react";
import { format, useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { PersistedTranslationReplacementRule } from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { Segmented } from "@/components/Segmented";
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

type Group = "pre" | "post";

const EMPTY_RULES: PersistedTranslationReplacementRule[] = [];

function emptyRule(): PersistedTranslationReplacementRule {
  return {
    src: "",
    dst: "",
    regex: false,
    case_sensitive: false,
    note: "",
    enabled: true,
  };
}

export function TranslationReplacementPage() {
  const messages = useMessages();
  const m = messages.translation.replacementPage;
  const moduleSettings = useModuleSettings("translation");
  const draft = moduleSettings.draft;
  const [group, setGroup] = useState<Group>("pre");
  const [selection, setSelection] =
    useState<RuleTableSelection>(EMPTY_SELECTION);
  const selectedIndex = selection.last;
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  useSearchShortcut(() => setSearchOpen(true));

  const fieldName: "pre_replacements" | "post_replacements" =
    group === "pre" ? "pre_replacements" : "post_replacements";
  const allRules = (draft?.[fieldName] ??
    EMPTY_RULES) as PersistedTranslationReplacementRule[];
  const rules = (() => {
    const q = searchQuery.trim().toLowerCase();
    if (!searchOpen || !q) return allRules;
    return allRules.filter((r) =>
      [r.src, r.dst, r.note].some((field) => field.toLowerCase().includes(q)),
    );
  })();

  const setRules = useCallback(
    (next: PersistedTranslationReplacementRule[]) => {
      moduleSettings.update(fieldName, next);
    },
    [moduleSettings, fieldName],
  );

  // Operations resolve the filtered index to the actual rule by
  // reference so search-active state doesn't misalign bulk actions.
  const addRule = () => {
    const next = [...allRules, emptyRule()];
    setRules(next);
    const newIndex = next.length - 1;
    setSelection({ indices: [newIndex], last: newIndex });
  };
  const updateRule = (
    filteredIndex: number,
    patch: Partial<PersistedTranslationReplacementRule>,
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
      .filter((r): r is PersistedTranslationReplacementRule => Boolean(r));
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

  if (!draft) {
    return <Panel title={m.title} subtitle={m.sub} />;
  }

  const columns: RuleTableColumn<PersistedTranslationReplacementRule>[] = [
    {
      key: "src",
      label: m.columns.src,
      width: "1.4fr",
      render: (rule) => (
        <span style={{ fontFamily: "var(--font-mono)" }}>{rule.src}</span>
      ),
      edit: {
        getValue: (rule) => rule.src,
        onCommit: (idx, value) => updateRule(idx, { src: value }),
      },
    },
    {
      key: "dst",
      label: m.columns.dst,
      width: "1.4fr",
      render: (rule) => (
        <span style={{ fontFamily: "var(--font-mono)" }}>{rule.dst}</span>
      ),
      edit: {
        getValue: (rule) => rule.dst,
        onCommit: (idx, value) => updateRule(idx, { dst: value }),
      },
    },
    {
      key: "rule",
      label: m.columns.rule,
      width: "84px",
      align: "right",
      render: (rule) => (
        <span className={ruleTableStyles.ruleChipGroup}>
          <span
            className={`${ruleTableStyles.ruleChip} ${rule.regex ? ruleTableStyles.ruleChipOn : ""}`.trim()}
            title={m.regexLabel}
          >
            .*
          </span>
          <span
            className={`${ruleTableStyles.ruleChip} ${rule.case_sensitive ? ruleTableStyles.ruleChipOn : ""}`.trim()}
            title={m.caseSensitiveLabel}
          >
            Aa
          </span>
        </span>
      ),
    },
  ];

  const selected =
    selectedIndex !== null && selectedIndex >= 0 && selectedIndex < rules.length
      ? rules[selectedIndex]
      : null;

  return (
    <>
      <Panel
        title={m.title}
        subtitle={m.sub}
        labelExtra={
          <Segmented<Group>
            ariaLabel={`${m.preLabel} / ${m.postLabel}`}
            options={[
              { id: "pre", label: m.preLabel },
              { id: "post", label: m.postLabel },
            ]}
            value={group}
            onChange={(v) => {
              setGroup(v);
              setSelection(EMPTY_SELECTION);
            }}
          />
        }
      />

      <Panel
        label={format(m.stats.total, { n: allRules.length })}
        labelExtra={
          <span>
            {group === "pre" ? m.preHint : m.postHint} ·{" "}
            {format(m.stats.enabled, { n: enabledCount })}
          </span>
        }
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
            { label: m.actions.import, onClick: () => undefined },
            { label: m.actions.export, onClick: () => undefined },
            {
              label: m.actions.search,
              onClick: () => setSearchOpen((v) => !v),
            },
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
  rule: PersistedTranslationReplacementRule;
  labels: ReturnType<typeof useMessages>["translation"]["replacementPage"];
  onChange: (patch: Partial<PersistedTranslationReplacementRule>) => void;
  onDelete: () => void;
}) {
  return (
    <div className={ruleTableStyles.editor}>
      <TextField
        label={labels.srcLabel}
        value={rule.src}
        onChange={(v) => onChange({ src: v })}
        placeholder={labels.srcPlaceholder}
        mono
      />
      <TextField
        label={labels.dstLabel}
        value={rule.dst}
        onChange={(v) => onChange({ dst: v })}
        placeholder={labels.dstPlaceholder}
        mono
      />
      <ToggleSwitch
        label={labels.regexLabel}
        checked={rule.regex}
        onChange={(next) => onChange({ regex: next })}
      />
      <ToggleSwitch
        label={labels.caseSensitiveLabel}
        checked={rule.case_sensitive}
        onChange={(next) => onChange({ case_sensitive: next })}
      />
      <TextField
        label={labels.noteLabel}
        value={rule.note}
        onChange={(v) => onChange({ note: v })}
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
