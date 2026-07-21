import { getTransport } from "./transport";
import { nativeDialogs } from "./native";
import type {
  AllSettings,
  AppMetadata,
  AppSettings,
  DialogPathResult,
  EpubCompressAction,
  EpubCompressArtifacts,
  EpubCompressOptions,
  EpubCompressPlan,
  EpubCompressReport,
  EpubConvertAction,
  EpubConvertArtifacts,
  EpubConvertOptions,
  EpubConvertPlan,
  EpubConvertReport,
  EpubMetadataApplyResult,
  EpubMetadataInfo,
  EpubMergeAction,
  EpubMergeArtifacts,
  EpubMergeOptions,
  EpubMergePlan,
  EpubMergeReport,
  EpubRepairResult,
  EpubRepairPreview,
  GlossaryArtifacts,
  GlossaryFileResult,
  GlossaryReviewArtifacts,
  GlossaryReviewFinalSheet,
  GlossaryReviewInputCandidates,
  GlossaryReviewReport,
  ModelListResult,
  ModelProfile,
  ModelProfileDraft,
  ModelTestResult,
  InlineProbeCredentials,
  ModuleSettings,
  ProviderTemplate,
  ProbeContinuable,
  PromptKind,
  PersistedGlossaryEntry,
  PromptPresetBody,
  PromptPresetSummary,
  PromptPreviewContext,
  PromptPreviewResult,
  WorkflowPreset,
  WorkflowPresetDraft,
  WorkflowPresetListResult,
  ReplacementArtifacts,
  ReplacementReport,
  ReplacementRule,
  ReplacementRuleParseResult,
  ReplacementValidationIssue,
  RequestLogQuery,
  RequestLogResult,
  SettingsModule,
  TaskFailure,
  TaskHeader,
  TaskSnapshot,
  TxtToEpubArtifacts,
  TxtToEpubOptions,
  TxtToEpubPlan,
  TxtToEpubPreset,
  TxtToEpubReport,
  TxtToEpubRule,
  TxtToEpubScanResult,
  TxtToEpubStyle,
  TxtToEpubTocEntry,
  TranslationArtifacts,
  TranslationStartResult,
  UpdateCheckResult,
} from "./types";

function call<TResponse>(
  method: string,
  payload: unknown = {},
): Promise<TResponse> {
  return getTransport().call<TResponse>(method, payload);
}

type RetranslateStatusResponse = {
  request_id: string;
  task_id: string;
  segment_id: string;
  status:
    | "pending"
    | "running"
    | "completed"
    | "failed"
    | "stale"
    | "skipped"
    | "unresolved";
  result_dst: string;
  error: string;
  attempts: number;
  last_error: string;
  last_translation: string;
  model_id: string;
  segment_count: number;
  created_at: string;
  updated_at: string;
  elapsed_seconds: number;
  results: Array<{
    segment_id: string;
    status: "completed" | "failed" | "stale" | "skipped" | "unresolved";
    result_dst?: string;
    error?: string;
  }>;
};

export const appBridge = {
  getMetadata(): Promise<AppMetadata> {
    return call("app.get_metadata");
  },
};

export const settingsBridge = {
  loadAll(): Promise<AllSettings> {
    return call("settings.load_all");
  },
  savePartial<TModule extends SettingsModule>(
    module: TModule,
    patch: Partial<ModuleSettings>,
  ): Promise<{
    saved_at: string;
    rejected_fields: Array<{ field: string; reason: string }>;
  }> {
    return call("settings.save_partial", { module, patch });
  },
  resetModule(module: SettingsModule): Promise<ModuleSettings> {
    return call("settings.reset_module", { module });
  },
};

export const dialogsBridge = {
  chooseInputDirectory(initialPath?: string): Promise<DialogPathResult> {
    return nativeDialogs.chooseDirectory(initialPath);
  },
  chooseOutputDirectory(initialPath?: string): Promise<DialogPathResult> {
    return nativeDialogs.chooseDirectory(initialPath);
  },
  chooseGlossaryFile(
    opts: {
      initialPath?: string;
      allowXlsx?: boolean;
      allowJson?: boolean;
    } = {},
  ): Promise<GlossaryFileResult> {
    const extensions = [
      opts.allowXlsx === false ? null : "xlsx",
      opts.allowJson === false ? null : "json",
    ].filter((value): value is string => value !== null);
    return nativeDialogs.chooseGlossaryFile(opts.initialPath, extensions);
  },
  chooseReplacementRulesFile(initialPath?: string): Promise<DialogPathResult> {
    return nativeDialogs.chooseFile(initialPath, []);
  },
  chooseEpubFile(initialPath?: string): Promise<DialogPathResult> {
    return nativeDialogs.chooseFile(initialPath, ["epub"]);
  },
  chooseTxtFile(initialPath?: string): Promise<DialogPathResult> {
    return nativeDialogs.chooseFile(initialPath, ["txt"]);
  },
  chooseCssFile(initialPath?: string): Promise<DialogPathResult> {
    return nativeDialogs.chooseFile(initialPath, ["css"]);
  },
  chooseImageFile(initialPath?: string): Promise<DialogPathResult> {
    return nativeDialogs.chooseFile(initialPath, ["jpg", "jpeg", "png", "webp"]);
  },
  chooseSavePath(
    defaultFilename: string,
    extensions: string[],
  ): Promise<DialogPathResult> {
    return nativeDialogs.chooseSavePath(defaultFilename, extensions);
  },
  openDirectory(path: string): Promise<Record<string, never>> {
    return nativeDialogs.openDirectory(path);
  },
  revealFile(path: string): Promise<Record<string, never>> {
    return nativeDialogs.revealFile(path);
  },
};

export const modelProfilesBridge = {
  list(): Promise<{ profiles: ModelProfile[] }> {
    return call("model_profiles.list");
  },
  create(profile: ModelProfileDraft): Promise<{ profile: ModelProfile }> {
    return call("model_profiles.create", { profile });
  },
  update(
    id: string,
    patch: Partial<ModelProfile>,
  ): Promise<{ profile: ModelProfile }> {
    return call("model_profiles.update", { id, patch });
  },
  delete(id: string): Promise<Record<string, never>> {
    return call("model_profiles.delete", { id });
  },
  duplicate(id: string): Promise<{ profile: ModelProfile }> {
    return call("model_profiles.duplicate", { id });
  },
  readFull(id: string): Promise<{ profile: ModelProfile; api_keys: string[] }> {
    return call("model_profiles.read_full", { id });
  },
  setApiKey(id: string, apiKeys: string[]): Promise<{ profile: ModelProfile }> {
    return call("model_profiles.set_api_key", { id, api_keys: apiKeys });
  },
  testConnection(id: string, requestId: string): Promise<ModelTestResult> {
    return call("model_profiles.test_connection", {
      id,
      request_id: requestId,
    });
  },
  /** Inline-credential variant: validate a draft profile before save.
   *  Architecture § 3.4 G.2 — used by the Add API Profile modal. */
  testConnectionInline(
    creds: InlineProbeCredentials,
    requestId: string,
  ): Promise<ModelTestResult> {
    return call("model_profiles.test_connection", {
      ...creds,
      request_id: requestId,
    });
  },
  fetchModelList(id: string, requestId: string): Promise<ModelListResult> {
    return call("model_profiles.fetch_model_list", {
      id,
      request_id: requestId,
    });
  },
  fetchModelListInline(
    creds: InlineProbeCredentials,
    requestId: string,
  ): Promise<ModelListResult> {
    return call("model_profiles.fetch_model_list", {
      ...creds,
      request_id: requestId,
    });
  },
  selectActive(
    module: "translation" | "glossary" | "glossary_review",
    profileId: string | null,
  ): Promise<{ app: AppSettings }> {
    return call("model_profiles.select_active", {
      module,
      profile_id: profileId,
    });
  },
};

export const modelTemplatesBridge = {
  /** Read-only catalog of provider templates (architecture § 3.4). */
  list(): Promise<{ templates: ProviderTemplate[] }> {
    return call("model_templates.list");
  },
};

export const promptsBridge = {
  list(
    kind: PromptKind,
  ): Promise<{ presets: PromptPresetSummary[]; active_id: string }> {
    return call("prompts.list", { kind });
  },
  read(id: string): Promise<{ preset: PromptPresetBody }> {
    return call("prompts.read", { id });
  },
  create(
    kind: PromptKind,
    preset: Omit<PromptPresetBody, "id" | "is_default">,
  ): Promise<{ preset: PromptPresetBody }> {
    return call("prompts.create", { kind, preset });
  },
  update(
    id: string,
    patch: Partial<PromptPresetBody>,
  ): Promise<{ preset: PromptPresetBody }> {
    return call("prompts.update", { id, patch });
  },
  duplicate(
    id: string,
    newName?: string,
  ): Promise<{ preset: PromptPresetBody }> {
    return call("prompts.duplicate", { id, new_name: newName });
  },
  delete(id: string): Promise<Record<string, never>> {
    return call("prompts.delete", { id });
  },
  selectActive(
    kind: PromptKind,
    presetId: string | null,
  ): Promise<{ app: AppSettings }> {
    return call("prompts.select_active", { kind, preset_id: presetId });
  },
  preview(
    presetId: string,
    context: PromptPreviewContext,
    thinking = false,
  ): Promise<PromptPreviewResult> {
    return call("prompts.preview", { preset_id: presetId, context, thinking });
  },
  resetToDefault(id: string): Promise<{ preset: PromptPresetBody }> {
    return call("prompts.reset_to_default", { id });
  },
};

export const workflowPresetsBridge = {
  list(kind: PromptKind): Promise<WorkflowPresetListResult> {
    return call("workflow_presets.list", { kind });
  },
  create(
    kind: PromptKind,
    preset: WorkflowPresetDraft,
  ): Promise<{ preset: WorkflowPreset }> {
    return call("workflow_presets.create", { kind, preset });
  },
  update(
    id: string,
    patch: Partial<WorkflowPresetDraft>,
  ): Promise<{ preset: WorkflowPreset }> {
    return call("workflow_presets.update", { id, patch });
  },
  duplicate(id: string, newName?: string): Promise<{ preset: WorkflowPreset }> {
    return call("workflow_presets.duplicate", { id, new_name: newName });
  },
  delete(id: string): Promise<Record<string, never>> {
    return call("workflow_presets.delete", { id });
  },
  apply(
    kind: PromptKind,
    id: string,
  ): Promise<{ app: AppSettings; settings: ModuleSettings }> {
    return call("workflow_presets.apply", { kind, id });
  },
};

export const translationBridge = {
  startTask(requestId: string): Promise<TranslationStartResult> {
    return call("translation.start_task", { request_id: requestId });
  },
  pauseTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("translation.pause_task", { task_id: taskId });
  },
  stopTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("translation.stop_task", { task_id: taskId });
  },
  continueTask(
    taskId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("translation.continue_task", { task_id: taskId });
  },
  probeContinuable(): Promise<ProbeContinuable> {
    return call("translation.probe_continuable", {});
  },
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("translation.read_snapshot", { task_id: taskId });
  },
  listRecentTasks(limit?: number): Promise<{ tasks: TaskHeader[] }> {
    return call("translation.list_recent_tasks", { limit });
  },
  readArtifacts(taskId: string): Promise<TranslationArtifacts> {
    return call("translation.read_artifacts", { task_id: taskId });
  },
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("translation.list_failed_subtasks", { task_id: taskId });
  },
  readRequestEvents(
    taskId: string,
    query: RequestLogQuery = {},
  ): Promise<RequestLogResult> {
    return call("translation.read_request_events", { task_id: taskId, ...query });
  },
};

export interface ProofreadingItem {
  segment_id: string;
  src: string;
  dst: string;
  low_confidence: boolean;
  reasons?: string[];
  subtask_ids?: string[];
  glossary_terms?: Array<{
    src: string;
    dst: string;
    info: string;
    applied: boolean;
    inconsistent: boolean;
  }>;
  /** Optional per-segment classification tags such as source residue or
   * possible adjacent duplicate translation. */
  tags?: string[];
}

export interface ProofreadingSnapshot {
  task_id: string;
  task_status: string;
  input_dir: string;
  output_dir: string;
  items: ProofreadingItem[];
}

export const proofreadingBridge = {
  listTasks(): Promise<{ tasks: TaskHeader[] }> {
    return call("proofreading.list_tasks", {});
  },
  loadSnapshot(taskId: string): Promise<ProofreadingSnapshot> {
    return call("proofreading.load_snapshot", { task_id: taskId });
  },
  updateSegment(
    taskId: string,
    segmentId: string,
    dst: string,
  ): Promise<{
    updated: boolean;
    segment_id: string;
    dst: string;
    low_confidence: boolean;
    tags: string[];
    reasons: string[];
  }> {
    return call("proofreading.update_segment", {
      task_id: taskId,
      segment_id: segmentId,
      dst,
    });
  },
  regenerateOutputs(taskId: string, bilingual = false): Promise<{
    task_id: string;
    translated_files: string[];
    bilingual_files: string[];
    failed_files: Array<{
      path: string;
      reason: string;
      code?: string;
      details?: Record<string, unknown>;
    }>;
  }> {
    return call("proofreading.regenerate_outputs", {
      task_id: taskId,
      bilingual,
    });
  },
  retranslateSegment(
    taskId: string,
    segmentId: string,
    options?: {
      modelId?: string | null;
      promptPresetId?: string | null;
      segmentIds?: string[];
    },
  ): Promise<{ request_id: string; status: string }> {
    return call("proofreading.retranslate_segment", {
      task_id: taskId,
      segment_id: segmentId,
      segment_ids: options?.segmentIds ?? null,
      model_id: options?.modelId ?? null,
      prompt_preset_id: options?.promptPresetId ?? null,
    });
  },
  retranslateStatus(requestId: string): Promise<RetranslateStatusResponse> {
    return call("proofreading.retranslate_status", { request_id: requestId });
  },
  resumeRetranslate(requestId: string): Promise<RetranslateStatusResponse> {
    return call("proofreading.resume_retranslate", { request_id: requestId });
  },
};

export const glossaryBridge = {
  startTask(
    requestId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("glossary.start_task", { request_id: requestId });
  },
  pauseTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("glossary.pause_task", { task_id: taskId });
  },
  stopTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("glossary.stop_task", { task_id: taskId });
  },
  continueTask(
    taskId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("glossary.continue_task", { task_id: taskId });
  },
  probeContinuable(): Promise<ProbeContinuable> {
    return call("glossary.probe_continuable", {});
  },
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("glossary.read_snapshot", { task_id: taskId });
  },
  listRecentTasks(limit?: number): Promise<{ tasks: TaskHeader[] }> {
    return call("glossary.list_recent_tasks", { limit });
  },
  readArtifacts(taskId: string): Promise<GlossaryArtifacts> {
    return call("glossary.read_artifacts", { task_id: taskId });
  },
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("glossary.list_failed_subtasks", { task_id: taskId });
  },
  readRequestEvents(
    taskId: string,
    query: RequestLogQuery = {},
  ): Promise<RequestLogResult> {
    return call("glossary.read_request_events", { task_id: taskId, ...query });
  },
  importRules(path: string): Promise<{
    entries: Array<{
      src: string;
      dst: string;
      info: string;
      regex: boolean;
      case_sensitive: boolean;
      enabled: boolean;
      frequency: number;
    }>;
  }> {
    return call("glossary.import_rules", { path });
  },
  exportRules(
    path: string,
    entries: Array<{
      src: string;
      dst: string;
      info: string;
      regex: boolean;
      case_sensitive: boolean;
      enabled: boolean;
      frequency?: number;
    }>,
  ): Promise<{ path: string; count: number }> {
    return call("glossary.export_rules", { path, entries });
  },
  listPresets(): Promise<{
    directory: string;
    presets: Array<{
      id: string;
      name: string;
      entry_count: number;
      entries: Array<{
        src: string;
        dst: string;
        info: string;
        regex: boolean;
        case_sensitive: boolean;
        enabled: boolean;
      }>;
    }>;
  }> {
    return call("glossary.list_presets", {});
  },
};

export const glossaryReviewBridge = {
  startTask(
    requestId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("glossary_review.start_task", { request_id: requestId });
  },
  pauseTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("glossary_review.pause_task", { task_id: taskId });
  },
  stopTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("glossary_review.stop_task", { task_id: taskId });
  },
  continueTask(
    taskId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("glossary_review.continue_task", { task_id: taskId });
  },
  probeContinuable(): Promise<ProbeContinuable> {
    return call("glossary_review.probe_continuable", {});
  },
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("glossary_review.read_snapshot", { task_id: taskId });
  },
  listRecentTasks(limit?: number): Promise<{ tasks: TaskHeader[] }> {
    return call("glossary_review.list_recent_tasks", { limit });
  },
  discoverInputs(
    inputFolder: string,
    outputFilename: string,
  ): Promise<GlossaryReviewInputCandidates> {
    return call("glossary_review.discover_inputs", {
      input_folder: inputFolder,
      output_filename: outputFilename,
    });
  },
  readArtifacts(taskId: string): Promise<GlossaryReviewArtifacts> {
    return call("glossary_review.read_artifacts", { task_id: taskId });
  },
  readReport(taskId: string): Promise<GlossaryReviewReport> {
    return call("glossary_review.read_report", { task_id: taskId });
  },
  readFinal(taskId: string): Promise<GlossaryReviewFinalSheet> {
    return call("glossary_review.read_final", { task_id: taskId });
  },
  updateFinalRow(
    taskId: string,
    row: {
      row_index: number;
      src: string;
      dst: string;
      info: string;
      delete?: boolean;
    },
  ): Promise<GlossaryReviewFinalSheet> {
    return call("glossary_review.update_final_row", {
      task_id: taskId,
      ...row,
    });
  },
  deleteFinalRows(
    taskId: string,
    rowIndices: number[],
  ): Promise<GlossaryReviewFinalSheet> {
    return call("glossary_review.delete_final_rows", {
      task_id: taskId,
      row_indices: rowIndices,
    });
  },
  restoreDeletedReportRow(
    taskId: string,
    row: {
      src: string;
      dst: string;
      info: string;
      frequency?: number;
    },
  ): Promise<GlossaryReviewFinalSheet> {
    return call("glossary_review.restore_deleted_report_row", {
      task_id: taskId,
      ...row,
    });
  },
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("glossary_review.list_failed_subtasks", { task_id: taskId });
  },
  readRequestEvents(
    taskId: string,
    query: RequestLogQuery = {},
  ): Promise<RequestLogResult> {
    return call("glossary_review.read_request_events", {
      task_id: taskId,
      ...query,
    });
  },
};

export function importedGlossaryToPersisted(
  entries: Awaited<ReturnType<typeof glossaryBridge.importRules>>["entries"],
): PersistedGlossaryEntry[] {
  return entries.map((entry) => ({
    src: entry.src,
    dst: entry.dst,
    info: entry.info,
    regex: entry.regex,
    case_sensitive: entry.case_sensitive,
    enabled: entry.enabled,
    frequency: entry.frequency ?? 0,
  }));
}

export const tasksBridge = {
  summarizeCaches(): Promise<{
    task_count: number;
    total_bytes: number;
    cache_root: string;
  }> {
    return call("tasks.summarize_caches", {});
  },
  purgeCaches(
    scope: "all" | "older_than_days" | "completed",
    days?: number,
  ): Promise<{
    scope: string;
    days: number | null;
    removed_count: number;
    removed_ids: string[];
    skipped_active_count: number;
  }> {
    return call("tasks.purge_caches", { scope, days: days ?? null });
  },
};

export type TranslationRuleKind =
  | "text_preserve"
  | "pre_replacement"
  | "post_replacement";

export interface TextPreserveRulePayload {
  pattern: string;
  note: string;
  enabled: boolean;
}

export interface ReplacementRulePayload {
  src: string;
  dst: string;
  regex: boolean;
  case_sensitive: boolean;
  note: string;
  enabled: boolean;
}

export type TranslationRulePayload =
  | TextPreserveRulePayload
  | ReplacementRulePayload;

export const rulesBridge = {
  importRules(
    kind: TranslationRuleKind,
    path: string,
  ): Promise<{ kind: TranslationRuleKind; rules: TranslationRulePayload[] }> {
    return call("rules.import_rules", { kind, path });
  },
  exportRules(
    kind: TranslationRuleKind,
    path: string,
    rules: TranslationRulePayload[],
  ): Promise<{ kind: TranslationRuleKind; path: string; count: number }> {
    return call("rules.export_rules", { kind, path, rules });
  },
};

export const replacementBridge = {
  importRules(path: string): Promise<ReplacementRuleParseResult> {
    return call("replacement.import_rules", { path });
  },
  validateRules(rules: ReplacementRule[]): Promise<{
    ok: boolean;
    issues: ReplacementValidationIssue[];
  }> {
    return call("replacement.validate_rules", { rules });
  },
  startTask(
    requestId: string,
    rules: ReplacementRule[],
    inputFolder?: string,
    outputFolder?: string,
  ): Promise<{
    task_id: string;
    started_at: string;
  }> {
    return call("replacement.start_task", {
      request_id: requestId,
      rules,
      input_folder: inputFolder,
      output_folder: outputFolder,
    });
  },
  stopTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("replacement.stop_task", { task_id: taskId });
  },
  pauseTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("replacement.pause_task", { task_id: taskId });
  },
  continueTask(
    taskId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("replacement.continue_task", { task_id: taskId });
  },
  probeContinuable(): Promise<ProbeContinuable> {
    return call("replacement.probe_continuable", {});
  },
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("replacement.read_snapshot", { task_id: taskId });
  },
  listRecentTasks(limit?: number): Promise<{ tasks: TaskHeader[] }> {
    return call("replacement.list_recent_tasks", { limit });
  },
  readArtifacts(taskId: string): Promise<ReplacementArtifacts> {
    return call("replacement.read_artifacts", { task_id: taskId });
  },
  readReplacementReport(taskId: string): Promise<ReplacementReport> {
    return call("replacement.read_replacement_report", {
      task_id: taskId,
    });
  },
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("replacement.list_failed_subtasks", { task_id: taskId });
  },
};

export const epubCompressBridge = {
  preview(
    inputPath: string,
    mode: "file" | "folder",
    options: EpubCompressOptions,
  ): Promise<EpubCompressPlan> {
    return call("epub_compress.preview", {
      input_path: inputPath,
      mode,
      options,
    });
  },
  startTask(
    requestId: string,
    inputPath: string,
    mode: "file" | "folder",
    options: EpubCompressOptions,
    actions: EpubCompressAction[],
  ): Promise<{ task_id: string; started_at: string }> {
    return call("epub_compress.start_task", {
      request_id: requestId,
      input_path: inputPath,
      mode,
      options,
      actions,
    });
  },
  stopTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_compress.stop_task", { task_id: taskId });
  },
  pauseTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_compress.pause_task", { task_id: taskId });
  },
  continueTask(
    taskId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("epub_compress.continue_task", { task_id: taskId });
  },
  probeContinuable(): Promise<ProbeContinuable> {
    return call("epub_compress.probe_continuable", {});
  },
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_compress.read_snapshot", { task_id: taskId });
  },
  listRecentTasks(limit?: number): Promise<{ tasks: TaskHeader[] }> {
    return call("epub_compress.list_recent_tasks", { limit });
  },
  readArtifacts(taskId: string): Promise<EpubCompressArtifacts> {
    return call("epub_compress.read_artifacts", { task_id: taskId });
  },
  readReport(taskId: string): Promise<EpubCompressReport> {
    return call("epub_compress.read_report", { task_id: taskId });
  },
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("epub_compress.list_failed_subtasks", { task_id: taskId });
  },
};

export const epubMergeBridge = {
  preview(
    inputDir: string,
    options: EpubMergeOptions,
  ): Promise<EpubMergePlan> {
    return call("epub_merge.preview", {
      input_dir: inputDir,
      options,
    });
  },
  startTask(
    requestId: string,
    inputDir: string,
    outputPath: string,
    options: EpubMergeOptions,
    actions: EpubMergeAction[],
  ): Promise<{ task_id: string; started_at: string }> {
    return call("epub_merge.start_task", {
      request_id: requestId,
      input_dir: inputDir,
      output_path: outputPath,
      options,
      actions,
    });
  },
  stopTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_merge.stop_task", { task_id: taskId });
  },
  pauseTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_merge.pause_task", { task_id: taskId });
  },
  continueTask(
    taskId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("epub_merge.continue_task", { task_id: taskId });
  },
  probeContinuable(): Promise<ProbeContinuable> {
    return call("epub_merge.probe_continuable", {});
  },
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_merge.read_snapshot", { task_id: taskId });
  },
  listRecentTasks(limit?: number): Promise<{ tasks: TaskHeader[] }> {
    return call("epub_merge.list_recent_tasks", { limit });
  },
  readArtifacts(taskId: string): Promise<EpubMergeArtifacts> {
    return call("epub_merge.read_artifacts", { task_id: taskId });
  },
  readReport(taskId: string): Promise<EpubMergeReport> {
    return call("epub_merge.read_report", { task_id: taskId });
  },
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("epub_merge.list_failed_subtasks", { task_id: taskId });
  },
};

export const epubConvertBridge = {
  preview(
    inputPath: string,
    mode: "file" | "folder",
    options: EpubConvertOptions,
  ): Promise<EpubConvertPlan> {
    return call("epub_convert.preview", {
      input_path: inputPath,
      mode,
      options,
    });
  },
  startTask(
    requestId: string,
    inputPath: string,
    mode: "file" | "folder",
    options: EpubConvertOptions,
    actions: EpubConvertAction[],
  ): Promise<{ task_id: string; started_at: string }> {
    return call("epub_convert.start_task", {
      request_id: requestId,
      input_path: inputPath,
      mode,
      options,
      actions,
    });
  },
  stopTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_convert.stop_task", { task_id: taskId });
  },
  pauseTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_convert.pause_task", { task_id: taskId });
  },
  continueTask(
    taskId: string,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("epub_convert.continue_task", { task_id: taskId });
  },
  probeContinuable(): Promise<ProbeContinuable> {
    return call("epub_convert.probe_continuable", {});
  },
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("epub_convert.read_snapshot", { task_id: taskId });
  },
  listRecentTasks(limit?: number): Promise<{ tasks: TaskHeader[] }> {
    return call("epub_convert.list_recent_tasks", { limit });
  },
  readArtifacts(taskId: string): Promise<EpubConvertArtifacts> {
    return call("epub_convert.read_artifacts", { task_id: taskId });
  },
  readReport(taskId: string): Promise<EpubConvertReport> {
    return call("epub_convert.read_report", { task_id: taskId });
  },
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("epub_convert.list_failed_subtasks", { task_id: taskId });
  },
};

export const txtToEpubBridge = {
  listStyles(): Promise<{ styles: TxtToEpubStyle[]; template: string }> {
    return call("txt_to_epub.list_styles");
  },
  listPresets(): Promise<{ presets: TxtToEpubPreset[] }> {
    return call("txt_to_epub.list_presets");
  },
  scanToc(
    sourcePath: string,
    presetId: string,
    customRules: TxtToEpubRule[],
    advancedPattern: string,
  ): Promise<TxtToEpubScanResult> {
    return call("txt_to_epub.scan_toc", {
      source_path: sourcePath,
      preset_id: presetId,
      custom_rules: customRules,
      advanced_pattern: advancedPattern,
    });
  },
  locateTocEntry(
    sourcePath: string,
    query: string,
    level: number,
    usedStartLines: number[],
  ): Promise<TxtToEpubTocEntry> {
    return call("txt_to_epub.locate_toc_entry", {
      source_path: sourcePath,
      query,
      level,
      used_start_lines: usedStartLines,
    });
  },
  preview(options: TxtToEpubOptions): Promise<TxtToEpubPlan> {
    return call("txt_to_epub.preview", { options });
  },
  startTask(
    requestId: string,
    options: TxtToEpubOptions,
  ): Promise<{ task_id: string; started_at: string }> {
    return call("txt_to_epub.start_task", { request_id: requestId, options });
  },
  stopTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("txt_to_epub.stop_task", { task_id: taskId });
  },
  probeContinuable(): Promise<ProbeContinuable> {
    return call("txt_to_epub.probe_continuable", {});
  },
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("txt_to_epub.read_snapshot", { task_id: taskId });
  },
  listRecentTasks(limit?: number): Promise<{ tasks: TaskHeader[] }> {
    return call("txt_to_epub.list_recent_tasks", { limit });
  },
  readArtifacts(taskId: string): Promise<TxtToEpubArtifacts> {
    return call("txt_to_epub.read_artifacts", { task_id: taskId });
  },
  readReport(taskId: string): Promise<TxtToEpubReport> {
    return call("txt_to_epub.read_report", { task_id: taskId });
  },
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("txt_to_epub.list_failed_subtasks", { task_id: taskId });
  },
};

export const epubMetadataBridge = {
  read(inputPath: string): Promise<EpubMetadataInfo> {
    return call("epub_metadata.read", { input_path: inputPath });
  },
  coverPreview(coverPath: string): Promise<{ data_url: string }> {
    return call("epub_metadata.cover_preview", { cover_path: coverPath });
  },
  apply(
    inputPath: string,
    outputPath: string,
    title: string,
    author: string,
    coverPath: string,
    overwrite = false,
    compress = false,
  ): Promise<EpubMetadataApplyResult> {
    return call("epub_metadata.apply", {
      input_path: inputPath,
      output_path: outputPath,
      title,
      author,
      cover_path: coverPath,
      overwrite,
      compress,
    });
  },
};

export const epubRepairBridge = {
  preview(inputPath: string, outputPath: string): Promise<EpubRepairPreview> {
    return call("epub_repair.preview", {
      input_path: inputPath,
      output_path: outputPath,
    });
  },
  apply(
    inputPath: string,
    outputPath: string,
    overwrite = false,
  ): Promise<EpubRepairResult> {
    return call("epub_repair.apply", {
      input_path: inputPath,
      output_path: outputPath,
      overwrite,
    });
  },
};

export const updatesBridge = {
  checkLatest(
    requestId: string,
    channel: "stable" | "prerelease" = "stable",
  ): Promise<UpdateCheckResult> {
    return call("updates.check_latest", { request_id: requestId, channel });
  },
  openReleasePage(url: string): Promise<Record<string, never>> {
    return call("updates.open_release_page", { url });
  },
  downloadAsset(
    requestId: string,
    assetUrl: string,
    suggestedFilename: string,
  ): Promise<{ saved_path: string }> {
    return call("updates.download_asset", {
      request_id: requestId,
      asset_url: assetUrl,
      suggested_filename: suggestedFilename,
    });
  },
  applyUpdateWindows(
    assetUrl: string,
    suggestedFilename: string,
    targetVersion: string,
  ): Promise<{
    staging_root: string;
    install_root: string;
    shutdown_in_seconds: number;
  }> {
    return call("updates.apply_update_windows", {
      asset_url: assetUrl,
      suggested_filename: suggestedFilename,
      target_version: targetVersion,
    });
  },
};
