// Types mirror docs/active/frontend-backend-bridge-contract.md.
// Keep wire format snake_case here; component code uses these directly.

export type Language =
  | "kr"
  | "zh"
  | "zh-Hant"
  | "en"
  | "ja"
  | "ru"
  | "ar"
  | "de"
  | "fr"
  | "pl"
  | "es"
  | "it"
  | "pt"
  | "hu"
  | "tr"
  | "th"
  | "id"
  | "vi";

export type TaskStatus =
  | "pending"
  | "running"
  | "stopping"
  | "stopped"
  | "pausing"
  | "paused"
  | "completed"
  | "failed";

export type SubtaskStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped";

export type ProviderFormat =
  | "openai"
  | "google"
  | "anthropic"
  | "sakura"
  | "custom";

export type ThinkingLevel = "off" | "low" | "medium" | "high";

export type Platform = "darwin" | "win32" | "linux";

export type ChineseOutputForm = "simplified" | "traditional";

export type Theme = "light" | "dark" | "system";

export type SettingsModule = "app" | "translation" | "glossary" | "replacement";

export type PromptKind = "translation" | "glossary";

// --- App ---------------------------------------------------------------------

export interface AppMetadata {
  app_version: string;
  platform: Platform;
  build_mode: "dev" | "packaged";
  python_version: string;
  cache_root: string;
}

// --- Settings ----------------------------------------------------------------

export interface AppSettings {
  interface_language: "en" | "zh";
  theme: Theme;
  ui_scale: number;
  proxy_url: string;
  /** Ordered list of profile ids the runtime rotates across for
   *  translation tasks. Empty = no model selected. */
  translation_model_ids: string[];
  glossary_model_ids: string[];
  active_translation_prompt_id: string | null;
  active_glossary_prompt_id: string | null;
}

export interface PersistedGlossaryEntry {
  src: string;
  dst: string;
  info: string;
  regex: boolean;
  case_sensitive: boolean;
  enabled: boolean;
}

export interface PersistedTextPreserveRule {
  pattern: string;
  note: string;
  enabled: boolean;
}

export interface PersistedTranslationReplacementRule {
  src: string;
  dst: string;
  regex: boolean;
  case_sensitive: boolean;
  note: string;
  enabled: boolean;
}

export interface TranslationSettings {
  input_folder: string;
  output_folder: string;
  source_language: Language;
  target_language: Language;
  chinese_output_form: ChineseOutputForm;
  bilingual_enabled: boolean;
  bilingual_dedupe_identical: boolean;
  bilingual_subfolder_name: string;
  context_lines: number;
  low_confidence_max_retries: number;
  auto_open_output_folder: boolean;
  translation_glossary: PersistedGlossaryEntry[];
  text_preserve_rules: PersistedTextPreserveRule[];
  pre_replacements: PersistedTranslationReplacementRule[];
  post_replacements: PersistedTranslationReplacementRule[];
}

export interface GlossarySettings {
  input_folder: string;
  output_folder: string;
  source_language: Language;
  target_language: Language;
  chinese_output_form: ChineseOutputForm;
  reference_examples_per_term: number;
  max_term_display_length: number;
  minimum_frequency: number;
  chunk_token_limit: number;
  merge_folder_glossary: boolean;
  keep_identical_src_dst: boolean;
  normalize_widths: boolean;
  auto_open_output_folder: boolean;
}

export interface ReplacementSettings {
  input_folder: string;
  output_folder: string;
  allow_same_folder: boolean;
  output_naming_suffix: string;
  overwrite_existing: boolean;
  apply_to_epub_titles: boolean;
  stop_on_first_error: boolean;
}

export interface AllSettings {
  app: AppSettings;
  translation: TranslationSettings;
  glossary: GlossarySettings;
  replacement: ReplacementSettings;
}

export type ModuleSettings =
  | AppSettings
  | TranslationSettings
  | GlossarySettings
  | ReplacementSettings;

// --- Dialogs -----------------------------------------------------------------

export interface DialogPathResult {
  path: string | null;
}

export interface GlossaryFileResult extends DialogPathResult {
  format: "xlsx" | "json" | null;
}

// --- Model profiles ---------------------------------------------------------

export type ApiKeyStatus = "missing" | "present" | "from_env";

export interface ModelProfile {
  id: string;
  display_name: string;
  provider_format: ProviderFormat;
  base_url: string;
  model_id: string;
  api_key_status: ApiKeyStatus;
  api_key_masked: string;
  thinking_level: ThinkingLevel;
  timeout_seconds: number;
  concurrency_limit: number;
  rpm_limit: number;
  tpm_limit: number;
  rotate_keys: boolean;
  retry_attempts: number;
  retry_initial_backoff_seconds: number;
  retry_max_backoff_seconds: number;
  max_output_tokens: number;
  thinking_budget_tokens: number;
  input_token_limit: number;
  top_p: number | null;
  temperature: number | null;
  presence_penalty: number | null;
  frequency_penalty: number | null;
  custom_headers: Array<[string, string]>;
}

export type ModelProfileDraft = Omit<
  ModelProfile,
  "id" | "api_key_status" | "api_key_masked"
> & { api_keys?: string[] };

export interface ModelTestResult {
  request_id: string;
  ok: boolean;
  latency_ms: number;
  provider_response: {
    model: string | null;
    status_code: number | null;
    detail: string;
  };
}

export interface ModelListEntry {
  id: string;
  display_name?: string;
}

export interface ModelListResult {
  request_id: string;
  models: ModelListEntry[];
}

// --- Prompt presets ---------------------------------------------------------

export interface PromptPresetSummary {
  id: string;
  name: string;
  kind: PromptKind;
  description: string;
  enabled: boolean;
  is_default: boolean;
}

export interface PromptPresetBody extends PromptPresetSummary {
  system_prompt: string;
  suffix_prompt: string;
  thinking_prompt: string;
}

export interface PromptPreviewContext {
  source_language: string;
  target_language: string;
  glossary?: string;
  context?: string;
  input?: string;
}

export interface PromptPreviewResult {
  prompt: string;
  /** Whether the rendered prompt actually included the thinking suffix.
   * Differs from the requested ``thinking`` flag when the active model
   * profile has ``thinking_level=off`` and the bridge clamped it. */
  thinking: boolean;
  /** True when the requested ``thinking=true`` was overridden because
   * the active model has ``thinking_level=off``. UI should warn. */
  clamped: boolean;
  /** The active model profile's thinking_level for the preset's kind,
   * or null when no model is selected / the saved profile is gone. */
  active_thinking_level: "off" | "low" | "medium" | "high" | null;
}

// --- Tasks (translation, glossary, replacement) -----------------------------

export type TaskKind = "translation" | "glossary" | "replacement";

/** Inline-credential payload used by the Add API Profile modal to
 *  test_connection / fetch_model_list before persisting the profile.
 *  Architecture § 3.4 G.2 — keys travel localhost only and are not
 *  written to disk. */
export interface InlineProbeCredentials {
  provider_format: ProviderFormat;
  base_url: string;
  api_key: string;
  /** Required for testConnectionInline (LLM call needs a model id);
   *  optional for fetchModelListInline (only base_url + key matter). */
  model_id?: string;
  custom_headers?: Array<[string, string]>;
}

export interface ProviderTemplateFieldHint {
  description_key: string;
  recommended_value: string;
  source_url: string | null;
}

export interface ProviderTemplateRecommendedDefaults {
  timeout_seconds: number;
  concurrency_limit: number;
  rpm_limit: number;
  tpm_limit: number;
  retry_attempts: number;
  max_output_tokens: number;
  temperature: number;
  top_p: number;
  thinking_level: "off" | "low" | "medium" | "high";
}

export interface ProviderTemplate {
  id: string;
  display_name: string;
  provider_format: ProviderFormat;
  default_base_url: string;
  hint_models: string[];
  supports_fetch_model_list: boolean;
  recommended_defaults: ProviderTemplateRecommendedDefaults;
  field_hints: Record<string, ProviderTemplateFieldHint>;
}

export interface ProbeContinuable {
  continuable: boolean;
  task_id: string | null;
  status: TaskStatus | null;
  pending: number;
  failed: number;
}

export interface TaskHeader {
  id: string;
  kind: TaskKind;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
}

export interface TaskProgress {
  total: number;
  pending: number;
  running: number;
  completed: number;
  failed: number;
  skipped: number;
  rate_per_second: number;
  eta_seconds: number;
}

export interface TaskUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface TaskSnapshot {
  header: TaskHeader;
  progress: TaskProgress;
  usage: TaskUsage;
  active_model_id: string | null;
  active_prompt_id: string | null;
  metadata: Record<string, unknown>;
}

export interface TaskFailure {
  subtask_id: string;
  source_file: string;
  message: string;
  message_key?: string;
  attempts: number;
  last_error_code: string;
  last_error_at: string;
}

export interface TaskLogLine {
  timestamp: string;
  level: "info" | "warn" | "error";
  message: string;
  context?: Record<string, unknown>;
}

export interface TaskOutcome {
  status: "completed" | "stopped" | "failed";
  artifacts_path: string;
  statistics_path: string | null;
}

export interface TranslationArtifacts {
  kind: "translation";
  output_folder: string;
  bilingual_folder: string | null;
  translated_files: string[];
  bilingual_files: string[];
  statistics_json_path: string | null;
  statistics_text_path: string | null;
  processed_files?: string[];
  completed_segments?: number;
  total_segments?: number;
}

export interface GlossaryArtifacts {
  kind: "glossary";
  output_folder: string;
  per_novel_artifacts: Array<{
    novel_name: string;
    xlsx_path: string;
    json_path: string;
    references_path: string;
  }>;
  combined_artifact: {
    novel_name: string;
    xlsx_path: string;
    json_path: string;
    references_path: string;
  } | null;
  statistics_json_path: string | null;
  decode_issue_path: string | null;
}

export interface ReplacementArtifacts {
  kind: "replacement";
  output_folder: string;
  output_files: string[];
  statistics_json_path: string | null;
  total_replacements: number;
}

// --- Replacement rules ------------------------------------------------------

export interface ReplacementRule {
  src: string;
  dst: string;
  regex: boolean;
  case_sensitive: boolean;
  enabled: boolean;
}

export interface ReplacementRuleParseResult {
  rules: ReplacementRule[];
  parse_warnings: Array<{ line_number: number; message: string }>;
}

export interface ReplacementValidationIssue {
  rule_index: number;
  code: "regex_error" | "duplicate_src" | "empty_src" | "empty_dst";
  message: string;
}

// --- Updates ----------------------------------------------------------------

export interface UpdateCheckResult {
  current_version: string;
  latest_version: string;
  is_newer_available: boolean;
  release_notes_markdown: string;
  release_url: string;
  published_at: string;
  asset: {
    name: string;
    download_url: string;
    size_bytes: number;
    platform: Platform;
  } | null;
}

// --- Task events (push channel) ---------------------------------------------

export type TaskEvent =
  | { kind: "snapshot"; task_id: string; snapshot: TaskSnapshot }
  | { kind: "log"; task_id: string; line: TaskLogLine }
  | { kind: "completed"; task_id: string; outcome: TaskOutcome }
  | { kind: "failed"; task_id: string; error: BridgeErrorPayload };

// --- Errors -----------------------------------------------------------------

export interface BridgeErrorPayload {
  code: string;
  message: string;
  message_key?: string;
  details?: Record<string, unknown>;
  retryable: boolean;
}
