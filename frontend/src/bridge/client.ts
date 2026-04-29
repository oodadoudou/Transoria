import { getTransport } from "./transport";
import { nativeDialogs } from "./native";
import type {
  AllSettings,
  AppMetadata,
  AppSettings,
  DialogPathResult,
  GlossaryArtifacts,
  GlossaryFileResult,
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
  ): Promise<{ saved_at: string }> {
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
    module: "translation" | "glossary",
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
    }>;
  }> {
    return call("glossary.import_rules", { path });
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
};
