/** Shape of every locale catalogue. Both en.ts and zh.ts implement this. */
export interface Messages {
  brand: { name: string };
  rail: {
    modules: string;
    workspace: string;
    translation: string;
    glossary: string;
    generalTools: string;
    appSettings: string;
  };
  topbar: {
    settings: string;
    stop: string;
    start: { translation: string; extraction: string };
  };
  status: {
    running: string;
    stopping: string;
    stopped: string;
    failed: string;
    completed: string;
    inFlight: string;
    activeRequests: string;
    perMinute: string;
    tokens: string;
  };
  errors: {
    runFailureTitle: string;
    dismiss: string;
  };
  runControls: {
    start: string;
    pause: string;
    stop: string;
    resume: string;
  };
  bilingual: {
    label: string;
    hint: string;
    dedupeLabel: string;
    dedupeHint: string;
    subfolderLabel: string;
  };
  modelExtra: {
    deleteProfile: string;
    timeoutSeconds: string;
    setActive: string;
    activeBadge: string;
  };
  batchReplacementHeaders: {
    src: string;
    dst: string;
    regex: string;
    caseSensitive: string;
  };
  appSettingsExtra: {
    theme: string;
    themeHint: string;
    themeSystem: string;
    themeLight: string;
    themeDark: string;
    uiScale: string;
    uiScaleHint: string;
    proxyUrl: string;
    proxyUrlHint: string;
    aboutLabel: string;
    updatesLabel: string;
    pythonRuntime: string;
    pythonRuntimeHint: string;
    cacheRoot: string;
    cacheRootHint: string;
    checkForUpdates: string;
    checking: string;
    currentLabel: string;
    latestLabel: string;
    upToDate: string;
    openReleasePage: string;
    download: string;
    savedTo: string;
  };
  settingsToolbar: {
    save: string;
    reset: string;
    saving: string;
    saved: string;
    error: string;
    idle: string;
  };
  folderPicker: {
    choose: string;
    open: string;
  };
  language: {
    sourceLabel: string;
    targetLabel: string;
    options: {
      kr: string;
      zh: string;
      "zh-Hant": string;
      en: string;
      ja: string;
    };
    chineseFormSimplified: string;
    chineseFormTraditional: string;
  };
  pages: {
    /** Visible names for every leaf page, used by the rail and breadcrumbs. */
    translation: {
      run: string;
      settings: string;
      model: string;
      glossary: string;
      textPreserve: string;
      replacement: string;
      prompt: string;
    };
    glossary: {
      run: string;
      settings: string;
      model: string;
      prompt: string;
    };
    generalTools: {
      batchReplacement: string;
    };
    appSettings: {
      general: string;
    };
  };
  translation: {
    crumb: string;
    settings: {
      title: string;
      sub: string;
      inputFolder: string;
      outputFolder: string;
      sourceLanguage: string;
      targetLanguage: string;
      openOutputOnComplete: string;
      openOutputOnCompleteHint: string;
      requestTimeout: string;
      requestTimeoutHelp: string;
      precedingLines: string;
      precedingLinesHelp: string;
      on: string;
      off: string;
    };
    glossaryPage: {
      title: string;
      sub: string;
      enableHint: string;
      enabled: string;
      disabled: string;
      columns: {
        source: string;
        translation: string;
        description: string;
        rule: string;
        status: string;
      };
      editor: {
        empty: string;
        source: string;
        translation: string;
        description: string;
        rule: string;
        sourcePlaceholder: string;
        translationPlaceholder: string;
        descriptionPlaceholder: string;
        caseSensitive: string;
        caseSensitiveHelp: string;
        active: string;
      };
      actions: {
        add: string;
        save: string;
        more: string;
        delete: string;
        import: string;
        export: string;
        search: string;
        statistics: string;
        preset: string;
      };
      empty: string;
      stats: {
        total: string;
        enabled: string;
      };
    };
    run: {
      title: string;
      sub: string;
      progress: string;
      runtimeTuning: string;
      activeConfig: string;
      activeModel: string;
      activePrompt: string;
      switch: string;
      stats: {
        completed: string;
        failed: string;
        remaining: string;
        elapsed: string;
        eta: string;
        avgSpeed: string;
      };
      tuning: {
        concurrency: string;
        rpm: string;
        contextLines: string;
        timeout: string;
      };
      tuningHelp: {
        concurrency: string;
        rpm: string;
        contextLines: string;
        timeout: string;
      };
    };
  };
  glossary: {
    crumb: string;
    settings: {
      title: string;
      sub: string;
      inputFolder: string;
      outputFolder: string;
      sourceLanguage: string;
      targetLanguage: string;
      combineFolderGlossary: string;
      combineFolderGlossaryHint: string;
      allowSrcEqDst: string;
      allowSrcEqDstHint: string;
      on: string;
      off: string;
    };
    run: {
      title: string;
      sub: string;
      progress: string;
      runtimeTuning: string;
      stats: {
        completed: string;
        failed: string;
        remaining: string;
        elapsed: string;
        eta: string;
        avgSpeed: string;
      };
      tuning: {
        chunkCharLimit: string;
        minFrequency: string;
        referenceExamples: string;
        rpm: string;
      };
      tuningHelp: {
        chunkCharLimit: string;
        minFrequency: string;
        referenceExamples: string;
        rpm: string;
      };
    };
  };
  generalTools: { crumb: string; title: string; sub: string };
  batchReplacement: {
    title: string;
    sub: string;
    inputFolder: string;
    outputFolder: string;
    rulesLabel: string;
    importRules: string;
    noRules: string;
    execute: string;
  };
  prompt: {
    pageTitle: string;
    pageSub: string;
    active: string;
    activeHint: string;
    available: string;
    availableHint: string;
    preview: string;
    previewSystem: string;
    previewSuffix: string;
    previewThinking: string;
    sourceLabel: {
      linguagacha: string;
      keywordgacha: string;
      custom: string;
    };
    badgeDefault: string;
    badgeCustom: string;
    actions: { add: string; edit: string; duplicate: string; delete: string };
    noThinkingPrompt: string;
  };
  model: {
    pageTitle: string;
    pageSub: string;
    sections: {
      preset: { title: string; sub: string };
      customOpenai: { title: string; sub: string };
      customGoogle: { title: string; sub: string };
      customAnthropic: { title: string; sub: string };
    };
    add: string;
    empty: string;
    /** Edit form */
    editTitle: string;
    editSub: string;
    basic: string;
    displayName: string;
    displayNameHelp: string;
    baseUrl: string;
    baseUrlHelp: string;
    apiKeys: string;
    apiKeysHelp: string;
    apiKeysPlaceholder: string;
    modelId: string;
    modelIdHelp: string;
    limits: string;
    limitsHint: string;
    inputTokenLimit: string;
    inputTokenLimitHelp: string;
    outputTokenLimit: string;
    outputTokenLimitHelp: string;
    concurrency: string;
    concurrencyHelp: string;
    rpm: string;
    rpmHelp: string;
    tpm: string;
    tpmHelp: string;
    retryAttempts: string;
    retryAttemptsHelp: string;
    reasoning: string;
    reasoningHint: string;
    thinkingLevel: string;
    thinking: { off: string; low: string; medium: string; high: string };
    advanced: string;
    advancedHint: string;
    topP: string;
    topPHelp: string;
    temperature: string;
    temperatureHelp: string;
    presencePenalty: string;
    presencePenaltyHelp: string;
    frequencyPenalty: string;
    frequencyPenaltyHelp: string;
    customHeaders: string;
    customHeadersHelp: string;
    customHeadersPlaceholder: string;
    apiFormatLabel: string;
    apiFormat: { openai: string; anthropic: string; google: string };
  };
  appSettings: {
    crumb: string;
    title: string;
    sub: string;
    interfaceLanguage: string;
    interfaceLanguageHint: string;
    languageEnglish: string;
    languageChinese: string;
  };
  inspector: {
    activeModel: string;
    noActiveModel: string;
    noModelId: string;
    tokensThisRun: string;
    tokensSubtitle: string;
    tokensInput: string;
    tokensOutput: string;
    tokensTotal: string;
    activePrompt: string;
    activePromptSubtitle: string;
    noActivePrompt: string;
  };
  common: { placeholder: string };
}
