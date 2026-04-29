import { useCallback } from "react";
import { format, useMessages } from "@/locales";
import { useModuleSettings } from "@/store/useSettingsStore";
import type { PersistedTextPreserveRule } from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { TextField } from "@/components/TextField";
import { ToggleSwitch } from "@/components/ToggleSwitch";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import styles from "./TextPreservePage.module.css";

const EMPTY_RULES: PersistedTextPreserveRule[] = [];

function emptyRule(): PersistedTextPreserveRule {
  return { pattern: "", note: "", enabled: true };
}

export function TextPreservePage() {
  const messages = useMessages();
  const m = messages.translation.textPreservePage;
  const moduleSettings = useModuleSettings("translation");
  const draft = moduleSettings.draft;

  const rules = draft?.text_preserve_rules ?? EMPTY_RULES;

  const setRules = useCallback(
    (next: PersistedTextPreserveRule[]) => {
      moduleSettings.update("text_preserve_rules", next);
    },
    [moduleSettings],
  );

  const addRule = () => setRules([...rules, emptyRule()]);
  const updateRule = (
    index: number,
    patch: Partial<PersistedTextPreserveRule>,
  ) =>
    setRules(rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));
  const deleteRule = (index: number) =>
    setRules(rules.filter((_, i) => i !== index));

  const enabledCount = rules.filter((r) => r.enabled).length;

  if (!draft) {
    return <Panel title={m.title} subtitle={m.sub} />;
  }

  return (
    <>
      <Panel title={m.title} subtitle={m.sub} />

      <Panel
        label={format(m.stats.total, { n: rules.length })}
        labelExtra={
          <span>{format(m.stats.enabled, { n: enabledCount })}</span>
        }
      >
        {rules.length === 0 ? (
          <div className={styles.empty}>{m.empty}</div>
        ) : (
          <div className={styles.ruleList}>
            {rules.map((rule, index) => (
              <div key={index} className={styles.ruleCard}>
                <div className={styles.ruleHeader}>
                  <span className={styles.ruleIndex}>#{index + 1}</span>
                  <ToggleSwitch
                    label={m.enabledLabel}
                    checked={rule.enabled}
                    onChange={(next) => updateRule(index, { enabled: next })}
                  />
                  <Pill variant="ghost" onClick={() => deleteRule(index)}>
                    {m.deleteAction}
                  </Pill>
                </div>
                <TextField
                  label={m.patternLabel}
                  value={rule.pattern}
                  onChange={(v) => updateRule(index, { pattern: v })}
                  placeholder={m.patternPlaceholder}
                  mono
                />
                <TextField
                  label={m.noteLabel}
                  value={rule.note}
                  onChange={(v) => updateRule(index, { note: v })}
                  placeholder={m.notePlaceholder}
                />
              </div>
            ))}
          </div>
        )}
        <div className={styles.toolbar}>
          <Pill onClick={addRule}>{m.addRule}</Pill>
        </div>
      </Panel>

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
