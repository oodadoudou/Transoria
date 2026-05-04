import { getTransport } from "./transport";
import { nativeDialogs } from "./native";
import type {
  AllSettings,
  AppMetadata,
  AppSettings,
  DialogPathResult,
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
  PromptPresetBody,
  PromptPresetSummary,
  PromptPreviewContext,
  PromptPreviewResult,
  ReplacementArtifacts,
  ReplacementReport,
  ReplacementRule,
  ReplacementRuleParseResult,
  ReplacementValidationIssue,
  SettingsModule,
  TaskFailure,
  TaskHeader,
  TaskSnapshot,
  TranslationArtifacts,
  UpdateCheckResult,
} from "./types";

function call<TResponse>(
  method: string,
  payload: unknown = {},
): Promise<TResponse> {
  return getTransport().call<TResponse>(method, payload);
}

// --- App ---------------------------------------------------------------------

export const appBridge = {
  getMetadata(): Promise<AppMetadata> {
    return call("app.get_metadata");
  },
};

// --- Settings ----------------------------------------------------------------

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

// --- Dialogs -----------------------------------------------------------------

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
    return nativeDialogs.chooseFile(initialPath, ["txt"]);
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

// --- Model profiles ---------------------------------------------------------

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

// --- Model templates --------------------------------------------------------

export const modelTemplatesBridge = {
  /** Read-only catalog of provider templates (architecture § 3.4). */
  list(): Promise<{ templates: ProviderTemplate[] }> {
    return call("model_templates.list");
  },
};

// --- Prompts ----------------------------------------------------------------

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

// --- Translation ------------------------------------------------------------

export const translationBridge = {
  startTask(
    requestId: string,
  ): Promise<{ task_id: string; started_at: string }> {
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
};

// --- Proofreading -----------------------------------------------------------

export interface ProofreadingItem {
  segment_id: string;
  src: string;
  dst: string;
  low_confidence: boolean;
  /** Optional per-segment classification tags. Currently only
   * "source_residue" is emitted (translation kept source-language
   * characters), but the field is open for future categories. */
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
  }> {
    return call("proofreading.update_segment", {
      task_id: taskId,
      segment_id: segmentId,
      dst,
    });
  },
  regenerateOutputs(taskId: string): Promise<{
    task_id: string;
    translated_files: string[];
    bilingual_files: string[];
    failed_files: Array<{ path: string; reason: string }>;
  }> {
    return call("proofreading.regenerate_outputs", { task_id: taskId });
  },
  retranslateSegment(
    taskId: string,
    segmentId: string,
  ): Promise<{ request_id: string; status: string }> {
    return call("proofreading.retranslate_segment", {
      task_id: taskId,
      segment_id: segmentId,
    });
  },
  retranslateStatus(requestId: string): Promise<{
    request_id: string;
    task_id: string;
    segment_id: string;
    status: "pending" | "running" | "completed" | "failed" | "stale";
    result_dst: string;
    error: string;
  }> {
    return call("proofreading.retranslate_status", { request_id: requestId });
  },
};

// --- Glossary ---------------------------------------------------------------

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

// --- Glossary Review --------------------------------------------------------

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
  listFailedSubtasks(taskId: string): Promise<{ failures: TaskFailure[] }> {
    return call("glossary_review.list_failed_subtasks", { task_id: taskId });
  },
};

// --- Task-cache management (Settings page) --------------------------------

export const tasksBridge = {
  summarizeCaches(): Promise<{
    task_count: number;
    total_bytes: number;
    cache_root: string;
  }> {
    return call("tasks.summarize_caches", {});
  },
  purgeCaches(
    scope: "all" | "older_than_days",
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

// --- Translation rules (text-preserve + pre/post replacement) --------------

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

// --- Replacement ------------------------------------------------------------

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
  ): Promise<{
    task_id: string;
    started_at: string;
  }> {
    return call("replacement.start_task", { request_id: requestId, rules });
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

// --- Updates ----------------------------------------------------------------

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
