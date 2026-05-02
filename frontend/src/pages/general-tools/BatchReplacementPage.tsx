import { useEffect, useState } from "react";
import { useMessages } from "@/locales";
import {
  BridgeError,
  dialogsBridge,
  replacementBridge,
  type ReplacementArtifacts,
  type ReplacementReport,
  type ReplacementRule,
  type ReplacementValidationIssue,
} from "@/bridge";
import { BatchReplacementReportModal } from "@/components/BatchReplacementReportModal";
import { useModuleSettings } from "@/store/useSettingsStore";
import {
  useRunSnapshot,
  usePollRunSnapshot,
  useRuntimeStore,
} from "@/store/useRuntimeStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import { FailedSubtaskList } from "@/components/FailedSubtaskList";
import styles from "./BatchReplacementPage.module.css";

const NUM = new Intl.NumberFormat("en");

const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

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
  const [artifacts, setArtifacts] = useState<ReplacementArtifacts | null>(null);
  // Loaded once after a task settles into a terminal state and held in
  // memory so the user can re-open the modal without another fetch.
  // Cleared on Execute (next run) and on app restart (component unmount).
  const [report, setReport] = useState<ReplacementReport | null>(null);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportError, setReportError] = useState<BridgeError | null>(null);

  const snapshot = useRunSnapshot("replacement");
  usePollRunSnapshot("replacement");
  const setActiveTaskId = useRuntimeStore((state) => state.setActiveTaskId);
  const activeTaskId = useRuntimeStore(
    (state) => state.replacement.activeTaskId,
  );

  // Pull artifacts as soon as the task settles into a terminal state.
  useEffect(() => {
    if (!activeTaskId) {
      setArtifacts(null);
      return;
    }
    if (!TERMINAL.has(snapshot.status)) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await replacementBridge.readArtifacts(activeTaskId);
        if (!cancelled) setArtifacts(result);
      } catch (error) {
        if (
          BridgeError.isBridgeError(error) &&
          error.code !== "bridge.not_found"
        ) {
          if (!cancelled) setActionError(error);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTaskId, snapshot.status]);

  // Pull the per-rule report once the task is terminal so the user can
  // open the modal without an extra round-trip. The fetched payload
  // stays in component state — closing the modal does NOT discard it,
  // matching the spec ("user can reopen freely until next replacement
  // or app restart").
  useEffect(() => {
    if (!activeTaskId) {
      setReport(null);
      setReportError(null);
      return;
    }
    if (!TERMINAL.has(snapshot.status)) return;
    let cancelled = false;
    void (async () => {
      try {
        const fetched =
          await replacementBridge.readReplacementReport(activeTaskId);
        if (!cancelled) {
          setReport(fetched);
          setReportError(null);
        }
      } catch (error) {
        if (
          BridgeError.isBridgeError(error) &&
          error.code !== "bridge.not_found"
        ) {
          if (!cancelled) setReportError(error);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeTaskId, snapshot.status]);

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
    setArtifacts(null);
    // Drop the previous report so the modal trigger disappears until
    // the new run completes — matches the spec ("cleared on next
    // replacement").
    setReport(null);
    setReportError(null);
    setReportOpen(false);
    try {
      const requestId = `replace-${Date.now().toString(36)}`;
      const { task_id } = await replacementBridge.startTask(requestId, rules);
      setActiveTaskId("replacement", task_id);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const handleStop = async () => {
    if (!activeTaskId) return;
    setActionError(null);
    try {
      await replacementBridge.stopTask(activeTaskId);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  // ``snapshot.status`` falls back to ``"pending"`` when no task has
  // ever run for this kind (the snapshot store has no record yet).
  // Treating that as in-flight would lock the Execute button on cold
  // start. Anchor the predicate on ``activeTaskId`` first: no active
  // id means nothing is in flight regardless of the placeholder
  // status, and only an active id with a non-terminal status counts
  // as actually running.
  const isRunning =
    activeTaskId !== null &&
    (snapshot.status === "running" || snapshot.status === "pending");
  const total = snapshot.progress.total;
  const completed = snapshot.progress.completed;
  const failed = snapshot.progress.failed;
  // Floor (not round) so near-finished runs like 400/402 stay at 99%
  // instead of misleadingly showing 100% before all subtasks settle.
  const percent = total > 0 ? Math.floor((completed / total) * 100) : 0;

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
              isRunning ||
              rules.length === 0 ||
              !draft.input_folder ||
              !draft.output_folder
            }
          >
            {messages.batchReplacement.execute}
          </Pill>
          <Pill
            variant="ghost"
            onClick={handleStop}
            disabled={!isRunning || !activeTaskId}
          >
            {messages.batchReplacement.stop}
          </Pill>
          {actionError ? (
            <span className={styles.actionError}>
              <code>{actionError.code}</code> {actionError.message}
            </span>
          ) : null}
        </div>
      </Panel>

      {activeTaskId ? (
        <Panel label={messages.batchReplacement.progressLabel}>
          <div className={styles.progressGrid}>
            <Stat
              label={messages.batchReplacement.statusLabel}
              value={snapshot.status}
            />
            <Stat
              label={messages.batchReplacement.processedFiles}
              value={`${NUM.format(completed)} / ${NUM.format(total)} (${percent}%)`}
            />
            <Stat
              label={messages.batchReplacement.failedFiles}
              value={NUM.format(failed)}
            />
          </div>
          {snapshot.failures.length > 0 ? (
            <div className={styles.failures}>
              <FailedSubtaskList failures={snapshot.failures} />
            </div>
          ) : null}
        </Panel>
      ) : null}

      {artifacts ? (
        <Panel label={messages.batchReplacement.artifactsLabel}>
          <div className={styles.artifactSummary}>
            <Stat
              label={messages.batchReplacement.totalReplacements}
              value={NUM.format(artifacts.total_replacements)}
            />
            <Stat
              label={messages.batchReplacement.outputFiles}
              value={NUM.format(artifacts.output_files.length)}
            />
          </div>
          {artifacts.output_files.length > 0 ? (
            <ul className={styles.artifactList}>
              {artifacts.output_files.map((path) => (
                <li key={path}>
                  <code>{path}</code>
                </li>
              ))}
            </ul>
          ) : (
            <div className={styles.empty}>
              {messages.batchReplacement.noArtifacts}
            </div>
          )}
          {artifacts.statistics_json_path ? (
            <div className={styles.statsLine}>
              <span>{messages.batchReplacement.statisticsFile}:</span>{" "}
              <code>{artifacts.statistics_json_path}</code>
            </div>
          ) : null}
          {report ? (
            <div className={styles.statsLine}>
              <Pill variant="ghost" onClick={() => setReportOpen(true)}>
                {messages.batchReplacement.viewReport}
              </Pill>
            </div>
          ) : null}
          {reportError ? (
            <div className={styles.statsLine}>
              <code>{reportError.code}</code> {reportError.message}
            </div>
          ) : null}
        </Panel>
      ) : null}

      {reportOpen && report ? (
        <BatchReplacementReportModal
          report={report}
          onClose={() => setReportOpen(false)}
        />
      ) : null}

      <SettingsToolbar
        saveState={moduleSettings.saveState}
        lastError={moduleSettings.lastError}
        onSave={() => {
          void moduleSettings.saveNow({ explicit: true });
        }}
        onReset={() => {
          void moduleSettings.reset();
        }}
      />
    </>
  );
}

interface StatProps {
  label: string;
  value: string;
}

function Stat({ label, value }: StatProps) {
  return (
    <div className={styles.stat}>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue}>{value}</span>
    </div>
  );
}
