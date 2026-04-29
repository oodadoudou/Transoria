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
  RuleTable,
  type RuleTableColumn,
  ruleTableStyles,
} from "@/components/RuleTable";

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
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const fieldName: "pre_replacements" | "post_replacements" =
    group === "pre" ? "pre_replacements" : "post_replacements";
  const rules = (draft?.[fieldName] ?? EMPTY_RULES) as PersistedTranslationReplacementRule[];

  const setRules = useCallback(
    (next: PersistedTranslationReplacementRule[]) => {
      moduleSettings.update(fieldName, next);
    },
    [moduleSettings, fieldName],
  );

  const addRule = () => {
    const next = [...rules, emptyRule()];
    setRules(next);
    setSelectedIndex(next.length - 1);
  };
  const updateRule = (
    index: number,
    patch: Partial<PersistedTranslationReplacementRule>,
  ) =>
    setRules(rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));
  const deleteRule = (index: number) => {
    setRules(rules.filter((_, i) => i !== index));
    if (selectedIndex !== null && selectedIndex >= index) {
      setSelectedIndex(null);
    }
  };

  const enabledCount = rules.filter((r) => r.enabled).length;

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
    },
    {
      key: "dst",
      label: m.columns.dst,
      width: "1.4fr",
      render: (rule) => (
        <span style={{ fontFamily: "var(--font-mono)" }}>{rule.dst}</span>
      ),
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
    {
      key: "status",
      label: m.columns.status,
      width: "48px",
      align: "right",
      render: (rule) => (
        <span
          className={`${ruleTableStyles.statusDot} ${rule.enabled ? ruleTableStyles.statusDotOn : ""}`.trim()}
        />
      ),
    },
  ];

  const selected = selectedIndex !== null ? rules[selectedIndex] : null;

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
              setSelectedIndex(null);
            }}
          />
        }
      />

      <Panel
        label={format(m.stats.total, { n: rules.length })}
        labelExtra={
          <span>
            {group === "pre" ? m.preHint : m.postHint} ·{" "}
            {format(m.stats.enabled, { n: enabledCount })}
          </span>
        }
      >
        <RuleTable
          rules={rules}
          selectedIndex={selectedIndex}
          onSelectIndex={setSelectedIndex}
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
              <div className={ruleTableStyles.editorEmpty}>
                {m.editorEmpty}
              </div>
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
