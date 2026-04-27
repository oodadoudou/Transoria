import { useState } from "react";
import { useMessages } from "@/locales";
import {
  BridgeError,
  dialogsBridge,
  replacementBridge,
  type ReplacementRule,
  type ReplacementValidationIssue,
} from "@/bridge";
import { useModuleSettings } from "@/store/useSettingsStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import styles from "./BatchReplacementPage.module.css";

export function BatchReplacementPage() {
  const messages = useMessages();
  const moduleSettings = useModuleSettings("replacement");
  const draft = moduleSettings.draft;
  const [rules, setRules] = useState<ReplacementRule[]>([]);
  const [warnings, setWarnings] = useState<
    Array<{ line_number: number; message: string }>
  >([]);
  const [issues, setIssues] = useState<ReplacementValidationIssue[]>([]);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [running, setRunning] = useState(false);

  if (!draft) {
    return (
      <Panel
        title={messages.batchReplacement.title}
        subtitle={messages.batchReplacement.sub}
      />
    );
  }

  const handleImport = async () => {
    setActionError(null);
    try {
      const dialogResult = await dialogsBridge.chooseReplacementRulesFile();
      if (!dialogResult.path) return;
      const parsed = await replacementBridge.importRules(dialogResult.path);
      setRules(parsed.rules);
      setWarnings(parsed.parse_warnings);
      const validation = await replacementBridge.validateRules(parsed.rules);
      setIssues(validation.issues);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const handleExecute = async () => {
    setActionError(null);
    setRunning(true);
    try {
      const requestId = `replace-${Date.now().toString(36)}`;
      await replacementBridge.startTask(requestId, rules);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    } finally {
      setRunning(false);
    }
  };

  return (
    <>
      <Panel
        title={messages.batchReplacement.title}
        subtitle={messages.batchReplacement.sub}
      >
        <div className={styles.pickerStack}>
          <FolderPickerRow
            label={messages.batchReplacement.inputFolder}
            value={draft.input_folder}
            variant="input"
            onChange={(p) => moduleSettings.update("input_folder", p)}
          />
          <FolderPickerRow
            label={messages.batchReplacement.outputFolder}
            value={draft.output_folder}
            variant="output"
            onChange={(p) => moduleSettings.update("output_folder", p)}
          />
        </div>
      </Panel>

      <Panel
        label={messages.batchReplacement.rulesLabel}
        labelExtra={
          <Pill variant="ghost" onClick={handleImport}>
            {messages.batchReplacement.importRules}
          </Pill>
        }
      >
        {rules.length === 0 ? (
          <div className={styles.empty}>
            {messages.batchReplacement.noRules}
          </div>
        ) : (
          <table className={styles.rulesTable}>
            <thead>
              <tr>
                <th>{messages.batchReplacementHeaders.src}</th>
                <th>{messages.batchReplacementHeaders.dst}</th>
                <th>{messages.batchReplacementHeaders.regex}</th>
                <th>{messages.batchReplacementHeaders.caseSensitive}</th>
              </tr>
            </thead>
            <tbody>
              {rules.slice(0, 50).map((rule, i) => (
                <tr key={i}>
                  <td>{rule.src}</td>
                  <td>{rule.dst}</td>
                  <td>{rule.regex ? "yes" : "no"}</td>
                  <td>{rule.case_sensitive ? "yes" : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {warnings.length > 0 ? (
          <div className={styles.warnings}>
            {warnings.map((w, i) => (
              <div key={i}>
                line {w.line_number}: {w.message}
              </div>
            ))}
          </div>
        ) : null}
        {issues.length > 0 ? (
          <div className={styles.issues}>
            {issues.map((issue, i) => (
              <div key={i}>
                <code>{issue.code}</code> · rule #{issue.rule_index}:{" "}
                {issue.message}
              </div>
            ))}
          </div>
        ) : null}
      </Panel>

      <Panel>
        <div className={styles.actionRow}>
          <Pill
            onClick={handleExecute}
            disabled={
              running ||
              rules.length === 0 ||
              !draft.input_folder ||
              !draft.output_folder
            }
          >
            {messages.batchReplacement.execute}
          </Pill>
          {actionError ? (
            <span className={styles.actionError}>
              <code>{actionError.code}</code> {actionError.message}
            </span>
          ) : null}
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
