import { useEffect, useState } from "react";
import { format, useMessages } from "@/locales";
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
  hasShownCleanCompletionToast,
  markCleanCompletionToastShown,
  useRunSnapshot,
  usePollRunSnapshot,
  useRuntimeStore,
} from "@/store/useRuntimeStore";
import { useToastStore } from "@/store/useToastStore";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { CompactPath } from "@/components/CompactPath";
import { FolderPickerRow } from "@/components/FolderPickerRow";
import { FailedSubtasksModal } from "@/components/FailedSubtasksModal";
import { TextField } from "@/components/TextField";
import {
  EMPTY_SELECTION,
  RuleTable,
  type RuleTableColumn,
  type RuleTableSelection,
} from "@/components/RuleTable";
import { useSearchShortcut } from "@/components/useSearchShortcut";
import { tableRowKey, uniqueRows } from "@/utils/tableDedupe";
import { useLocalState } from "@/utils/localState";
import { useSessionState } from "@/utils/sessionState";
import styles from "./BatchReplacementPage.module.css";

const NUM = new Intl.NumberFormat("en");
const INPUT_LOCAL_KEY = "transoria.generalTools.batchReplacement.inputFolder";
const OUTPUT_LOCAL_KEY = "transoria.generalTools.batchReplacement.outputFolder";
const RULE_FILE_LOCAL_KEY = "transoria.generalTools.batchReplacement.ruleFile";
const RULES_LOCAL_KEY = "transoria.generalTools.batchReplacement.rules";

const TERMINAL: ReadonlySet<string> = new Set([
  "completed",
  "failed",
  "stopped",
]);

function replacementRuleKey(rule: ReplacementRule): string {
  return tableRowKey([
    rule.src,
    rule.dst,
    rule.regex,
    rule.case_sensitive,
    rule.enabled,
  ]);
}

function normalizeRuleFilePath(path: string): string {
  const cleaned = path.trim().replace(/^["']|["']$/g, "").replace(/^file:\/\//, "");
  try {
    return decodeURI(cleaned);
  } catch {
    return cleaned;
  }
}

export function BatchReplacementPage() {
  const messages = useMessages();
  const moduleSettings = useModuleSettings("replacement");
  const draft = moduleSettings.draft;
  const [inputFolder, setInputFolder] = useSessionState(INPUT_LOCAL_KEY, "");
  const [outputFolder, setOutputFolder] = useSessionState(OUTPUT_LOCAL_KEY, "");
  const [ruleFilePath, setRuleFilePath] = useSessionState(RULE_FILE_LOCAL_KEY, "");
  const [pathsHydrated, setPathsHydrated] = useState(false);
  const [rules, setRules] = useLocalState<ReplacementRule[]>(
    RULES_LOCAL_KEY,
    [],
  );
  const [ruleSelection, setRuleSelection] =
    useState<RuleTableSelection>(EMPTY_SELECTION);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  useSearchShortcut(() => setSearchOpen(true));
  const visibleRules = (() => {
    const q = searchQuery.trim().toLowerCase();
    if (!searchOpen || !q) return rules;
    return rules.filter((r) =>
      [r.src, r.dst].some((field) => field.toLowerCase().includes(q)),
    );
  })();
  const updateRule = (filteredIndex: number, patch: Partial<ReplacementRule>) =>
    setRules((prev) => {
      const item = visibleRules[filteredIndex];
      if (!item) return prev;
      return prev.map((rule) => (rule === item ? { ...rule, ...patch } : rule));
    });
  const deleteRules = (filteredIndices: number[]) => {
    const targets = new Set(
      filteredIndices.map((i) => visibleRules[i]).filter(Boolean),
    );
    if (targets.size === 0) return;
    setRules((prev) => prev.filter((rule) => !targets.has(rule)));
    setRuleSelection(EMPTY_SELECTION);
  };
  const [warnings, setWarnings] = useState<
    Array<{ line_number: number; message: string }>
  >([]);
  const [issues, setIssues] = useState<ReplacementValidationIssue[]>([]);
  const [actionError, setActionError] = useState<BridgeError | null>(null);
  const [artifacts, setArtifacts] = useState<ReplacementArtifacts | null>(null);
  const [artifactFeedback, setArtifactFeedback] = useState<string | null>(null);
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
  const [failedModalOpen, setFailedModalOpen] = useState(false);
  const failedModalMessages = messages.failedSubtasksModal;

  useEffect(() => {
    if (!draft || pathsHydrated) return;
    setInputFolder((current) => current || draft.input_folder);
    setOutputFolder((current) => current || draft.output_folder);
    setPathsHydrated(true);
  }, [draft, pathsHydrated, setInputFolder, setOutputFolder]);

  // Celebratory toast on truly clean completion. Replacement is
  // single-pass (no continue/rerun), so the failure dialog isn't
  // applicable here — only the modal-based failure surface and this
  // success toast.
  useEffect(() => {
    if (!activeTaskId) return;
    if (snapshot.status !== "completed") return;
    if (snapshot.progress.failed > 0) return;
    if (snapshot.progress.completed <= 0) return;
    if (hasShownCleanCompletionToast(activeTaskId)) return;
    markCleanCompletionToastShown(activeTaskId);
    useToastStore.getState().push({
      variant: "success",
      title: messages.runCompleted.title,
    });
  }, [
    activeTaskId,
    snapshot.status,
    snapshot.progress.failed,
    snapshot.progress.completed,
    messages.runCompleted.title,
  ]);

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

  const importRulesFromPath = async (rawPath: string) => {
    const path = normalizeRuleFilePath(rawPath);
    if (!path) return;
    setActionError(null);
    try {
      setRuleFilePath(path);
      const parsed = await replacementBridge.importRules(path);
      const unique = uniqueRows(parsed.rules, replacementRuleKey);
      setRules(unique);
      setWarnings(parsed.parse_warnings);
      const validation = await replacementBridge.validateRules(unique);
      setIssues(validation.issues);
    } catch (error) {
      if (BridgeError.isBridgeError(error)) {
        setActionError(error);
      } else {
        throw error;
      }
    }
  };

  const handleChooseRuleFile = async () => {
    setActionError(null);
    try {
      const dialogResult = await dialogsBridge.chooseReplacementRulesFile(
        ruleFilePath || undefined,
      );
      if (!dialogResult.path) return;
      await importRulesFromPath(dialogResult.path);
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
    setArtifactFeedback(null);
    // Drop the previous report so the modal trigger disappears until
    // the new run completes — matches the spec ("cleared on next
    // replacement").
    setReport(null);
    setReportError(null);
    setReportOpen(false);
    try {
      const requestId = `replace-${Date.now().toString(36)}`;
      const { task_id } = await replacementBridge.startTask(
        requestId,
        rules,
        inputFolder,
        outputFolder,
      );
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

  const copyArtifactPath = (path: string): void => {
    void copyOutputPaths(
      [path],
      messages.common.copyPathDone,
      setArtifactFeedback,
    );
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
  const settled = completed;
  const percent = total > 0 ? Math.floor((settled / total) * 100) : 0;

  return (
    <>
      <Panel
        title={messages.batchReplacement.title}
        subtitle={messages.batchReplacement.sub}
      >
        <div className={styles.pickerStack}>
          <FolderPickerRow
            label={messages.batchReplacement.inputFolder}
            value={inputFolder}
            variant="input"
            onChange={setInputFolder}
            historyKey="general_tools:batch_replacement:input_folder"
          />
          <FolderPickerRow
            label={messages.batchReplacement.outputFolder}
            value={outputFolder}
            variant="output"
            onChange={setOutputFolder}
            historyKey="general_tools:batch_replacement:output_folder"
          />
        </div>
      </Panel>

      <Panel
        label={messages.batchReplacement.rulesLabel}
      >
        <div className={styles.ruleImportRow}>
          <TextField
            label={messages.batchReplacement.ruleFile}
            value={ruleFilePath}
            onChange={setRuleFilePath}
            placeholder={messages.batchReplacement.ruleFilePlaceholder}
            mono
          />
          <Pill variant="ghost" onClick={handleChooseRuleFile}>
            {messages.batchReplacement.chooseRuleFile}
          </Pill>
          <Pill
            onClick={() => void importRulesFromPath(ruleFilePath)}
            disabled={!normalizeRuleFilePath(ruleFilePath)}
          >
            {messages.batchReplacement.importRules}
          </Pill>
        </div>
        {rules.length === 0 ? (
          <div className={styles.empty}>
            {messages.batchReplacement.noRules}
          </div>
        ) : (
          <>
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
              rules={visibleRules}
              selection={ruleSelection}
              onSelectionChange={setRuleSelection}
              isEnabled={() => true}
              columns={replacementColumns(messages, updateRule)}
              emptyMessage={messages.batchReplacement.noRules}
              onBulkDelete={deleteRules}
              contextMenuLabels={{
                deleteSelected: (n) =>
                  format(messages.ruleTable.deleteSelected, { n }),
              }}
            />
          </>
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
              !inputFolder
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
              <Pill
                variant="ghost"
                onClick={() => setFailedModalOpen(true)}
              >
                {`${failedModalMessages.triggerPrefix}${snapshot.failures.length}${failedModalMessages.triggerSuffix}`}
              </Pill>
            </div>
          ) : null}
        </Panel>
      ) : null}

      {failedModalOpen ? (
        <FailedSubtasksModal
          failures={snapshot.failures}
          onClose={() => setFailedModalOpen(false)}
        />
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
          <div className={styles.artifactActions}>
            <Pill
              variant="ghost"
              onClick={() =>
                void openOutputFolder(artifacts.output_folder, setArtifactFeedback)
              }
            >
              {messages.batchReplacement.openOutputFolder}
            </Pill>
            <Pill
              variant="ghost"
              onClick={() =>
                void copyOutputPaths(
                  artifacts.output_files,
                  messages.batchReplacement.copyOutputPathsDone,
                  setArtifactFeedback,
                )
              }
              disabled={artifacts.output_files.length === 0}
            >
              {messages.batchReplacement.copyOutputPaths}
            </Pill>
            {artifactFeedback ? (
              <span className={styles.artifactFeedback}>{artifactFeedback}</span>
            ) : null}
          </div>
          {artifacts.output_files.length > 0 ? (
            <ul className={styles.artifactList}>
              {artifacts.output_files.map((path) => (
                <li key={path}>
                  <CompactPath
                    value={path}
                    asCode
                    copyLabel={messages.common.copyPath}
                    onCopy={copyArtifactPath}
                  />
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
              <span>{messages.batchReplacement.statisticsFile}:</span>
              <CompactPath
                value={artifacts.statistics_json_path}
                asCode
                copyLabel={messages.common.copyPath}
                onCopy={copyArtifactPath}
              />
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

    </>
  );
}

async function openOutputFolder(
  folder: string,
  setFeedback: (message: string | null) => void,
) {
  try {
    await dialogsBridge.openDirectory(folder);
    setFeedback(null);
  } catch (error) {
    setFeedback(
      BridgeError.isBridgeError(error)
        ? `${error.code}: ${error.message}`
        : String(error),
    );
  }
}

async function copyOutputPaths(
  paths: string[],
  successMessage: string,
  setFeedback: (message: string | null) => void,
) {
  try {
    await navigator.clipboard.writeText(paths.join("\n"));
    setFeedback(successMessage);
  } catch (error) {
    setFeedback(
      BridgeError.isBridgeError(error)
        ? `${error.code}: ${error.message}`
        : String(error),
    );
  }
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

function replacementColumns(
  messages: ReturnType<typeof useMessages>,
  updateRule: (index: number, patch: Partial<ReplacementRule>) => void,
): RuleTableColumn<ReplacementRule>[] {
  const labels = messages.batchReplacementHeaders;
  return [
    {
      key: "src",
      label: labels.src,
      width: "1.5fr",
      render: (rule) => <span>{rule.src}</span>,
      edit: {
        getValue: (rule) => rule.src,
        onCommit: (idx, value) => updateRule(idx, { src: value }),
      },
    },
    {
      key: "dst",
      label: labels.dst,
      width: "1.5fr",
      render: (rule) => <span>{rule.dst}</span>,
      edit: {
        getValue: (rule) => rule.dst,
        onCommit: (idx, value) => updateRule(idx, { dst: value }),
      },
    },
    {
      key: "regex",
      label: labels.regex,
      width: "0.6fr",
      align: "center",
      render: (rule, index) => (
        <input
          type="checkbox"
          checked={rule.regex}
          onChange={() => updateRule(index, { regex: !rule.regex })}
          onClick={(e) => e.stopPropagation()}
        />
      ),
    },
    {
      key: "case_sensitive",
      label: labels.caseSensitive,
      width: "0.7fr",
      align: "center",
      render: (rule, index) => (
        <input
          type="checkbox"
          checked={rule.case_sensitive}
          onChange={() =>
            updateRule(index, { case_sensitive: !rule.case_sensitive })
          }
          onClick={(e) => e.stopPropagation()}
        />
      ),
    },
  ];
}
