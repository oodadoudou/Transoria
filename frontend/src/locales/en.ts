import type { Messages } from "./types";

export const en: Messages = {
  brand: { name: "Transoria" },
  rail: {
    modules: "Modules",
    workspace: "Workspace",
    translation: "Translation",
    glossary: "Glossary Extraction",
    generalTools: "General Tools",
    appSettings: "App Settings",
  },
  topbar: {
    settings: "Settings",
    stop: "Stop",
    start: {
      translation: "Start translation",
      extraction: "Start extraction",
    },
  },
  status: {
    running: "Running",
    stopping: "Stopping",
    stopped: "Stopped",
    failed: "Failed",
    completed: "Completed",
    inFlight: "batch #{n} in flight",
    activeRequests: "{n} active requests",
    perMinute: "{n} segments / min",
    tokens: "{n} tokens",
  },
  errors: {
    runFailureTitle: "Last action failed",
    dismiss: "Dismiss",
  },
  runControls: {
    start: "Start",
    pause: "Pause",
    stop: "Stop",
    resume: "Resume",
  },
  bilingual: {
    label: "Bilingual output",
    hint: "Emit a side-by-side bilingual file alongside the translated one.",
    dedupeLabel: "Skip bilingual when source equals target",
    dedupeHint:
      "If source and target language tags match, only emit the translated file.",
    subfolderLabel: "Bilingual subfolder name",
  },
  modelExtra: {
    deleteProfile: "Delete profile",
    timeoutSeconds: "Timeout (s)",
    setActive: "Set as active",
    activeBadge: "Active",
  },
  batchReplacementHeaders: {
    src: "src",
    dst: "dst",
    regex: "regex",
    caseSensitive: "case",
  },
  appSettingsExtra: {
    theme: "Theme",
    themeHint: "Light, dark, or follow system.",
    themeSystem: "System",
    themeLight: "Light",
    themeDark: "Dark",
    uiScale: "UI scale",
    uiScaleHint: "Visual density (0.85–1.5).",
    proxyUrl: "Proxy URL",
    proxyUrlHint: "HTTP(S) proxy used by LLM and update calls. Empty disables.",
    aboutLabel: "About",
    updatesLabel: "Updates",
    pythonRuntime: "Python runtime",
    pythonRuntimeHint: "Reported by the desktop shell.",
    cacheRoot: "Cache root",
    cacheRootHint: "Read-only; configured by the runtime.",
    checkForUpdates: "Check for updates",
    checking: "Checking…",
    currentLabel: "Current",
    latestLabel: "Latest",
    upToDate: "You're up to date.",
    openReleasePage: "Open release page",
    download: "Download",
    savedTo: "Saved to",
  },
  settingsToolbar: {
    save: "Save",
    reset: "Reset to defaults",
    saving: "Saving…",
    saved: "Saved",
    error: "Save failed",
    idle: "All changes saved",
  },
  folderPicker: {
    choose: "Choose folder",
    open: "Open",
  },
  language: {
    sourceLabel: "Source language",
    targetLabel: "Target language",
    options: {
      kr: "Korean",
      zh: "Simplified Chinese",
      "zh-Hant": "Traditional Chinese",
      en: "English",
      ja: "Japanese",
    },
    chineseFormSimplified: "Simplified",
    chineseFormTraditional: "Traditional",
  },
  batchReplacement: {
    title: "Batch Replacement",
    sub: "Apply imported arrow rules (`source->target`) to TXT and EPUB files.",
    inputFolder: "Input folder",
    outputFolder: "Output folder",
    rulesLabel: "Rules",
    importRules: "Import TXT rules",
    noRules:
      "No rules imported. Import a TXT file with `source->target` lines.",
    execute: "Execute",
  },
  pages: {
    translation: {
      run: "Run",
      settings: "Settings",
      model: "Model",
      glossary: "Glossary",
      textPreserve: "Text Preserve",
      replacement: "Replacement",
      prompt: "Prompt",
    },
    glossary: {
      run: "Run",
      settings: "Settings",
      model: "Model",
      prompt: "Prompt",
    },
    generalTools: { batchReplacement: "Batch Replacement" },
    appSettings: { general: "General" },
  },
  translation: {
    crumb: "Translation",
    settings: {
      title: "Translation Settings",
      sub: "Folder choice, language pair, and the runtime knobs that aren't model-specific. Per-model pacing lives on the Model page; per-run progress lives on the Run page.",
      inputFolder: "Input folder",
      outputFolder: "Output folder",
      sourceLanguage: "Source language",
      targetLanguage: "Target language",
      openOutputOnComplete: "Open output folder on task completion",
      openOutputOnCompleteHint:
        "When the run finishes successfully, automatically open the output folder in the system file browser.",
      requestTimeout: "Request timeout (s)",
      requestTimeoutHelp:
        "Per-request HTTP timeout in seconds. Slow networks or large reasoning chunks may need higher values.",
      precedingLines: "Preceding lines threshold",
      precedingLinesHelp:
        "Maximum number of preceding source lines included as context for each translation task. More context = better consistency, slightly higher token cost.",
      on: "On",
      off: "Off",
    },
    glossaryPage: {
      title: "Glossary",
      sub: "Pin recurring proper nouns and translation choices. The active glossary is injected into every prompt as `src -> dst (description)` rows so the model stays consistent across chapters.",
      enableHint: "When off, the glossary is not added to translation prompts.",
      enabled: "Enabled",
      disabled: "Disabled",
      columns: {
        source: "Source",
        translation: "Translation",
        description: "Description",
        rule: "Rule",
        status: "Status",
      },
      editor: {
        empty:
          "Select an entry from the table, or click + Add to create a new one.",
        source: "Source",
        translation: "Translation",
        description: "Description",
        rule: "Rule",
        sourcePlaceholder: "Source term",
        translationPlaceholder: "Translated term",
        descriptionPlaceholder: "Description",
        caseSensitive: "Case-sensitive match",
        caseSensitiveHelp:
          "When off (default), `Aa` matches regardless of case. Turn on for languages where case matters or when the source string already mixes cases on purpose.",
        active: "Active",
      },
      actions: {
        add: "Add",
        save: "Save",
        more: "More",
        delete: "Delete",
        import: "Import",
        export: "Export",
        search: "Search",
        statistics: "Statistics",
        preset: "Preset",
      },
      empty: "No glossary entries yet. Click + Add to create one.",
      stats: {
        total: "{n} entries",
        enabled: "{n} enabled",
      },
    },
    run: {
      title: "Run Translation",
      sub: "Start, monitor, and stop the active translation job. Folder and language choices are configured in Settings.",
      progress: "Progress",
      runtimeTuning: "Runtime tuning",
      activeConfig: "Active configuration",
      activeModel: "Model",
      activePrompt: "Prompt",
      switch: "Switch →",
      stats: {
        completed: "Completed",
        failed: "Failed",
        remaining: "Remaining",
        elapsed: "Elapsed",
        eta: "ETA",
        avgSpeed: "Avg speed",
      },
      tuning: {
        concurrency: "Concurrency",
        rpm: "Requests / minute",
        contextLines: "Context lines",
        timeout: "Timeout (s)",
      },
      tuningHelp: {
        concurrency:
          "How many requests run in parallel for this module. Higher = faster but may hit per-key rate limits.",
        rpm: "Soft cap on requests per minute. The backend pre-throttles before each call to stay under this.",
        contextLines:
          "Number of preceding source lines fed alongside each chunk. More context = better consistency, slightly higher token cost.",
        timeout:
          "Per-request timeout in seconds. Slower networks or large reasoning chunks may need higher values.",
      },
    },
  },
  glossary: {
    crumb: "Glossary Extraction",
    settings: {
      title: "Glossary Settings",
      sub: "Choose where files come from and where to write the three artifacts. Pacing and chunk shape live on the Run page.",
      inputFolder: "Input folder",
      outputFolder: "Output folder",
      sourceLanguage: "Source language",
      targetLanguage: "Target language",
      combineFolderGlossary: "Combined folder glossary",
      combineFolderGlossaryHint:
        "Emit a single `<folder>-Glossary.{xlsx,json,txt}` set covering every file under the input folder, in addition to the per-file artifacts.",
      allowSrcEqDst: "Allow src == dst entries",
      allowSrcEqDstHint:
        "Keep candidates whose source and target spelling are identical. Useful when names are spelled the same in both languages; off by default.",
      on: "On",
      off: "Off",
    },
    run: {
      title: "Run Glossary Extraction",
      sub: "Start, monitor, and stop the extraction job. Outputs are emitted as XLSX, JSON, and a references TXT alongside the source folder.",
      progress: "Progress",
      runtimeTuning: "Runtime tuning",
      stats: {
        completed: "Completed",
        failed: "Failed",
        remaining: "Remaining",
        elapsed: "Elapsed",
        eta: "ETA",
        avgSpeed: "Avg speed",
      },
      tuning: {
        chunkCharLimit: "Chunk size (chars)",
        minFrequency: "Min frequency",
        referenceExamples: "Reference examples",
        rpm: "Requests / minute",
      },
      tuningHelp: {
        chunkCharLimit:
          "Max characters per chunk sent to the model. Smaller chunks = more requests but easier on context limits.",
        minFrequency:
          "Discard candidates whose frequency in the source is below this threshold. 1 keeps everything; raise for noisy outputs.",
        referenceExamples:
          "Number of source lines attached as references for each glossary entry, sampled across the source files.",
        rpm: "Soft cap on requests per minute. The backend pre-throttles before each call to stay under this.",
      },
    },
  },
  generalTools: {
    crumb: "General Tools",
    title: "Batch Replacement",
    sub: "Apply a TXT rule file across an input folder of TXT and EPUB files. Output uses `<Name>-Replaced.<ext>`.",
  },
  appSettings: {
    crumb: "App Settings",
    title: "App Settings",
    sub: "Interface language, UI scale, theme, proxy, and packaging-time preferences.",
    interfaceLanguage: "Interface language",
    interfaceLanguageHint:
      "Switches every label, button, and message in the UI. The translation source/target languages are separate and live under each module.",
    languageEnglish: "English",
    languageChinese: "中文",
  },
  inspector: {
    activeModel: "Active model",
    noActiveModel: "No model selected",
    noModelId: "No model ID",
    tokensThisRun: "Tokens",
    tokensSubtitle: "this run",
    tokensInput: "Input",
    tokensOutput: "Output",
    tokensTotal: "Total",
    activePrompt: "Active prompt",
    activePromptSubtitle: "preset",
    noActivePrompt: "No prompt selected",
  },
  prompt: {
    pageTitle: "Prompt",
    pageSub:
      "Translation and Glossary each have their own prompt presets. The active preset drives every request this module sends.",
    active: "Active preset",
    activeHint: "Used by every request this module sends.",
    available: "Available presets",
    availableHint:
      "Default presets are seeded from LinguaGacha and KeywordGacha and cannot be deleted, only duplicated.",
    preview: "Preview",
    previewSystem: "System prompt",
    previewSuffix: "Suffix",
    previewThinking: "Reasoning addendum",
    sourceLabel: {
      linguagacha: "LinguaGacha",
      keywordgacha: "KeywordGacha",
      custom: "Custom",
    },
    badgeDefault: "Default",
    badgeCustom: "Custom",
    actions: {
      add: "New preset",
      edit: "Edit",
      duplicate: "Duplicate",
      delete: "Delete",
    },
    noThinkingPrompt:
      "This preset has no reasoning addendum. The model receives the system prompt and suffix only.",
  },
  model: {
    pageTitle: "Model",
    pageSub:
      "Translation and Glossary each carry their own model library. Pick a preset or add a custom model; clicking a chip opens its edit panel, then use Set as active to apply it.",
    sections: {
      preset: {
        title: "Preset Models",
        sub: "Built-in entries shipped with the application. Settings are editable; entries cannot be deleted.",
      },
      customOpenai: {
        title: "Custom OpenAI Models",
        sub: "Custom models compatible with the OpenAI API format.",
      },
      customGoogle: {
        title: "Custom Google Models",
        sub: "Custom models compatible with the Google Gemini API format.",
      },
      customAnthropic: {
        title: "Custom Anthropic Models",
        sub: "Custom models compatible with the Anthropic Claude API format.",
      },
    },
    add: "Add",
    empty: "No custom entries yet.",
    editTitle: "Edit",
    editSub:
      "Changes save immediately to this module. Other modules keep their own copy.",
    basic: "Basic",
    displayName: "Display name",
    displayNameHelp:
      "How this entry appears in the model picker. Display only — does not affect requests.",
    baseUrl: "API URL",
    baseUrlHelp:
      "API base URL. Pay attention to whether the path ends in `/v1` — most OpenAI-compatible endpoints want it; native OpenAI itself wants `/v1` too.",
    apiKeys: "API keys",
    apiKeysHelp:
      "One key per line. The client rotates between keys on transient auth/rate failures.",
    apiKeysPlaceholder: "sk-…\nsk-…",
    modelId: "Model identifier",
    modelIdHelp:
      "The exact model ID the API expects (e.g. `gpt-4o-mini`, `deepseek-v3-2-251201`). Check the provider's documentation for valid values.",
    limits: "Limits",
    limitsHint:
      "Soft caps applied per request. 0 means automatic / unlimited per the provider's defaults.",
    inputTokenLimit: "Input token limit",
    inputTokenLimitHelp:
      "Maximum tokens accepted per task input. 0 = unbounded. Set higher than your largest chunk to avoid truncation.",
    outputTokenLimit: "Output token limit",
    outputTokenLimitHelp:
      "Maximum tokens the model is allowed to produce per task. 0 = automatic (the provider's default for this model).",
    concurrency: "Concurrent task limit",
    concurrencyHelp:
      "How many tasks may run in parallel. Refer to the provider's documentation for safe limits. 0 = automatic.",
    rpm: "Requests / minute (RPM)",
    rpmHelp:
      "Soft cap on requests per minute. The backend pre-throttles before each call to stay under this. 0 = unlimited.",
    tpm: "Tokens / minute (TPM)",
    tpmHelp:
      "Soft cap on input + output tokens consumed per minute. Useful when the provider throttles by token bucket. 0 = unlimited.",
    retryAttempts: "Retry attempts",
    retryAttemptsHelp:
      "How many times to retry on transient errors (5xx, 429, network). Each retry waits longer (exponential backoff).",
    reasoning: "Reasoning",
    reasoningHint:
      "Engages the model's hidden chain-of-thought when supported. Increases latency and token cost; leave Off for plain models.",
    thinkingLevel: "Reasoning level",
    thinking: { off: "Off", low: "Low", medium: "Medium", high: "High" },
    advanced: "Advanced",
    advancedHint:
      "Most users should leave these off. Set with caution — incorrect values can cause abnormal results or request errors.",
    topP: "top_p",
    topPHelp:
      "Nucleus sampling cutoff. Lower values keep responses more focused on the most likely tokens.",
    temperature: "temperature",
    temperatureHelp:
      "Sampling temperature. Lower = more deterministic; higher = more creative. Default for translation work is 0.4-0.7.",
    presencePenalty: "presence_penalty",
    presencePenaltyHelp:
      "Discourages the model from re-using tokens that have already appeared. Range -2.0 to 2.0.",
    frequencyPenalty: "frequency_penalty",
    frequencyPenaltyHelp:
      "Discourages frequent token repetition proportional to count. Range -2.0 to 2.0.",
    customHeaders: "Custom request headers",
    customHeadersHelp:
      "JSON object merged into outgoing HTTP headers. Set with caution — incorrect values can break requests.",
    customHeadersPlaceholder: '{"Authorization": "Bearer xxx"}',
    apiFormatLabel: "API format",
    apiFormat: { openai: "OpenAI", anthropic: "Anthropic", google: "Google" },
  },
  common: {
    placeholder:
      "This page is part of the Phase 2 frontend scaffold and is wired to backend endpoints in a later phase.",
  },
};

export type { Messages };
