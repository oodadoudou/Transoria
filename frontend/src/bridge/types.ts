// Types mirror docs/bridge-contract.md.
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

export type SettingsModule =
  | "app"
  | "translation"
  | "glossary"
  | "glossary_review"
  | "replacement";

export type PromptKind = "translation" | "glossary" | "glossary_review";

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
  ui_scale: number;
  proxy_url: string;
  active_translation_model_id: string | null;
  active_glossary_model_id: string | null;
  active_glossary_review_model_id: string | null;
  active_translation_prompt_id: string | null;
  active_glossary_prompt_id: string | null;
  active_glossary_review_prompt_id: string | null;
  /** Tag of the latest release the user has chosen to ignore. The
   * startup update prompt only appears when ``latest_version`` differs
   * from this value, so a confirmed-or-dismissed release never re-nags. */
  skipped_update_version: string;
}

export interface PersistedGlossaryEntry {
  src: string;
  dst: string;
  info: string;
  regex: boolean;
  case_sensitive: boolean;
  enabled: boolean;
  /** Carried over from import (e.g. glossary-extraction xlsx).
   * Optional so existing settings.json from older versions still
   * load — missing field treated as 0. */
  frequency?: number;
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
  auto_retry_max_rounds: number;
  auto_open_output_folder: boolean;
  timeout_seconds: number;
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
  timeout_seconds: number;
}

export interface GlossaryReviewSettings {
  input_folder: string;
  output_filename: string;
  novel_background: string;
  review_rounds: number;
  batch_size: number;
  auto_open_output_folder: boolean;
  timeout_seconds: number;
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
  glossary_review: GlossaryReviewSettings;
  replacement: ReplacementSettings;
}

export type ModuleSettings =
  | AppSettings
  | TranslationSettings
  | GlossarySettings
  | GlossaryReviewSettings
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
  /** Opt-in: when true and ``thinking_level === "off"``, the runner
   * injects built-in thinking guidance without sending a wire-level
   * thinking field. */
  force_thinking_enable: boolean;
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
  /** Seeded read-only preset shipped with the app. UI must show
   *  view-only and forbid edit / delete. */
  is_system: boolean;
  /** Full system prompt; the list row renders a truncated preview
   *  so users see what they will actually send to the model. */
  system_prompt: string;
}

export interface PromptPresetBody extends PromptPresetSummary {
  system_prompt: string;
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
  /** Whether the rendered prompt actually included the reasoning addendum.
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

export type TaskKind =
  | "translation"
  | "glossary"
  | "glossary_review"
  | "replacement";

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
  elapsed_seconds: number;
  rate_per_second: number;
}

export interface TaskUsage {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export type SubtaskStatusValue =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped";

export interface SubtaskMini {
  id: string;
  status: SubtaskStatusValue;
  /** Populated only for ``failed`` subtasks; the chunk-grid tooltip
   * surfaces this so users can hover a red square and see the LLM /
   * decoder error instead of guessing why it failed. */
  last_error?: string;
}

export interface TaskLowConfidenceSummary {
  total: number;
  source_residue: number;
}

export interface GlossaryReviewRoundProgress {
  total_rounds: number;
  current_round: number;
  completed_rounds: number;
  current_total_batches: number;
  current_completed_batches: number;
}

export interface TaskSnapshot {
  header: TaskHeader;
  progress: TaskProgress;
  usage: TaskUsage;
  low_confidence?: TaskLowConfidenceSummary;
  round_progress?: GlossaryReviewRoundProgress | null;
  subtasks: SubtaskMini[];
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

export interface GlossaryReviewArtifacts {
  kind: "glossary_review";
  output_folder: string;
  output_path: string | null;
  report_path: string | null;
  changed_count: number;
}

export interface GlossaryReviewReportRow {
  round: number;
  action: "modify" | "delete" | "category" | "modify_category";
  row_index: number;
  src: string;
  original_dst: string;
  suggested_dst: string;
  original_info: string;
  suggested_info: string;
  reason: string;
  context_excerpt: string;
}

export interface GlossaryReviewReport {
  task_id: string;
  generated_at: string;
  input_xlsx: string;
  output_path: string;
  changed_count: number;
  rows: GlossaryReviewReportRow[];
}

export interface GlossaryReviewFinalRow {
  row_index: number;
  src: string;
  dst: string;
  info: string;
  frequency: number;
}

export interface GlossaryReviewFinalSheet {
  task_id: string;
  path: string;
  rows: GlossaryReviewFinalRow[];
}

export interface ReplacementArtifacts {
  kind: "replacement";
  output_folder: string;
  output_files: string[];
  statistics_json_path: string | null;
  /** Path to the per-rule occurrence report JSON, when the task
   * generated one. Read via ``replacementBridge.readReplacementReport``. */
  replacement_report_path?: string | null;
  total_replacements: number;
}

export interface ReplacementReportOccurrence {
  file_path: string;
  char_offset: number;
  before_context: string;
  match_text: string;
  after_context: string;
  replacement_text: string;
}

export interface ReplacementReportRule {
  rule_index: number;
  src: string;
  dst: string;
  regex: boolean;
  case_sensitive: boolean;
  enabled: boolean;
  total_count: number;
  occurrences: ReplacementReportOccurrence[];
  occurrences_truncated: boolean;
}

export interface ReplacementReportFile {
  source_path: string;
  output_path: string;
  replacement_count: number;
}

export interface ReplacementReport {
  task_id: string;
  generated_at: string;
  totals: {
    rules_active: number;
    rules_with_matches: number;
    total_replacements: number;
    files_processed: number;
  };
  files: ReplacementReportFile[];
  rules: ReplacementReportRule[];
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
