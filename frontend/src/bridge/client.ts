import { getTransport } from "./transport";
import type {
  AllSettings,
  AppMetadata,
  AppSettings,
  DialogPathResult,
  GlossaryArtifacts,
  GlossaryFileResult,
  ModelListEntry,
  ModelProfile,
  ModelProfileDraft,
  ModelTestResult,
  ModuleSettings,
  PromptKind,
  PromptPresetBody,
  PromptPresetSummary,
  PromptPreviewContext,
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
    return call("dialogs.choose_input_directory", {
      initial_path: initialPath,
    });
  },
  chooseOutputDirectory(initialPath?: string): Promise<DialogPathResult> {
    return call("dialogs.choose_output_directory", {
      initial_path: initialPath,
    });
  },
  chooseGlossaryFile(
    opts: {
      initialPath?: string;
      allowXlsx?: boolean;
      allowJson?: boolean;
    } = {},
  ): Promise<GlossaryFileResult> {
    return call("dialogs.choose_glossary_file", {
      initial_path: opts.initialPath,
      allow_xlsx: opts.allowXlsx,
      allow_json: opts.allowJson,
    });
  },
  chooseReplacementRulesFile(initialPath?: string): Promise<DialogPathResult> {
    return call("dialogs.choose_replacement_rules_file", {
      initial_path: initialPath,
    });
  },
  openDirectory(path: string): Promise<Record<string, never>> {
    return call("dialogs.open_directory", { path });
  },
  revealFile(path: string): Promise<Record<string, never>> {
    return call("dialogs.reveal_file", { path });
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
  setApiKey(id: string, apiKeys: string[]): Promise<{ profile: ModelProfile }> {
    return call("model_profiles.set_api_key", { id, api_keys: apiKeys });
  },
  testConnection(id: string, requestId: string): Promise<ModelTestResult> {
    return call("model_profiles.test_connection", {
      id,
      request_id: requestId,
    });
  },
  fetchModelList(
    id: string,
    requestId: string,
  ): Promise<{ models: ModelListEntry[] }> {
    return call("model_profiles.fetch_model_list", {
      id,
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
  ): Promise<{ prompt: string }> {
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
  resumeTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("translation.resume_task", { task_id: taskId });
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
  resumeTask(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("glossary.resume_task", { task_id: taskId });
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
  readSnapshot(taskId: string): Promise<{ snapshot: TaskSnapshot }> {
    return call("replacement.read_snapshot", { task_id: taskId });
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

// --- Cancellation -----------------------------------------------------------

export const bridgeControl = {
  cancel(requestId: string): Promise<Record<string, never>> {
    return call("bridge.cancel", { request_id: requestId });
  },
};
