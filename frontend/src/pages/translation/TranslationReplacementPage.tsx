import { useCallback } from "react";
import { useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { PersistedTranslationReplacementRule } from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { TextField } from "@/components/TextField";
import { ToggleSwitch } from "@/components/ToggleSwitch";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import styles from "./TranslationReplacementPage.module.css";

type RuleField = "pre_replacements" | "post_replacements";

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

  const updateGroup = useCallback(
    (
      field: RuleField,
      next: PersistedTranslationReplacementRule[],
    ) => {
      moduleSettings.update(field, next);
    },
    [moduleSettings],
  );

  if (!draft) {
    return <Panel title={m.title} subtitle={m.sub} />;
  }

  return (
    <>
      <Panel title={m.title} subtitle={m.sub} />

      <RuleGroup
        labels={m}
        groupLabel={m.preLabel}
        groupHint={m.preHint}
        rules={draft.pre_replacements ?? EMPTY_RULES}
        onChange={(next) => updateGroup("pre_replacements", next)}
      />
      <RuleGroup
        labels={m}
        groupLabel={m.postLabel}
        groupHint={m.postHint}
        rules={draft.post_replacements ?? EMPTY_RULES}
        onChange={(next) => updateGroup("post_replacements", next)}
      />

      <SettingsToolbar
        saveState={moduleSettings.saveState}
        lastError={moduleSettings.lastError}
        onSave={() => {
          void moduleSettings.saveNow();
        }}
        onReset={() => {
          void moduleSettings.reset();
        }}
      />
    </>
  );
}

interface RuleGroupProps {
  labels: ReturnType<typeof useMessages>["translation"]["replacementPage"];
  groupLabel: string;
  groupHint: string;
  rules: PersistedTranslationReplacementRule[];
  onChange: (next: PersistedTranslationReplacementRule[]) => void;
}

function RuleGroup({
  labels,
  groupLabel,
  groupHint,
  rules,
  onChange,
}: RuleGroupProps) {
  const addRule = () => onChange([...rules, emptyRule()]);
  const updateRule = (
    index: number,
    patch: Partial<PersistedTranslationReplacementRule>,
  ) =>
    onChange(
      rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)),
    );
  const deleteRule = (index: number) =>
    onChange(rules.filter((_, i) => i !== index));

  return (
    <Panel label={groupLabel} labelExtra={<span>{groupHint}</span>}>
      {rules.length === 0 ? (
        <div className={styles.empty}>{labels.empty}</div>
      ) : (
        <div className={styles.ruleList}>
          {rules.map((rule, index) => (
            <div key={index} className={styles.ruleCard}>
              <div className={styles.ruleHeader}>
                <span className={styles.ruleIndex}>#{index + 1}</span>
                <ToggleSwitch
                  label={labels.regexLabel}
                  checked={rule.regex}
                  onChange={(next) => updateRule(index, { regex: next })}
                />
                <ToggleSwitch
                  label={labels.caseSensitiveLabel}
                  checked={rule.case_sensitive}
                  onChange={(next) =>
                    updateRule(index, { case_sensitive: next })
                  }
                />
                <ToggleSwitch
                  label={labels.enabledLabel}
                  checked={rule.enabled}
                  onChange={(next) => updateRule(index, { enabled: next })}
                />
                <Pill variant="ghost" onClick={() => deleteRule(index)}>
                  {labels.deleteAction}
                </Pill>
              </div>
              <TextField
                label={labels.srcLabel}
                value={rule.src}
                onChange={(v) => updateRule(index, { src: v })}
                placeholder={labels.srcPlaceholder}
                mono
              />
              <TextField
                label={labels.dstLabel}
                value={rule.dst}
                onChange={(v) => updateRule(index, { dst: v })}
                placeholder={labels.dstPlaceholder}
                mono
              />
              <TextField
                label={labels.noteLabel}
                value={rule.note}
                onChange={(v) => updateRule(index, { note: v })}
              />
            </div>
          ))}
        </div>
      )}
      <div className={styles.toolbar}>
        <Pill onClick={addRule}>{labels.addRule}</Pill>
      </div>
    </Panel>
  );
}
