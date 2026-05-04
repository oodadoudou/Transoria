/** Shape of every locale catalogue. Both en.ts and zh.ts implement this. */
export interface Messages {
  brand: { name: string };
  rail: {
    modulesAria: string;
    modules: string;
    workspace: string;
    model: string;
    translation: string;
    glossary: string;
    generalTools: string;
    appSettings: string;
    githubLink: string;
    githubFace: string;
    githubAria: string;
  };
  topbar: {
    breadcrumb: string;
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
    tokenDetail: {
      title: string;
      input: string;
      output: string;
      total: string;
      perMinute: string;
      perSegment: string;
    };
  };
  errors: {
    notifications: string;
    runFailureTitle: string;
    loadFailureTitle: string;
    dismiss: string;
    retry: string;
    /** Localized texts indexed by ``BridgeError.message_key``. Lookup
     * with `bridgeMessages[messageKey]`; fall back to `error.message`
     * (the backend's English text) when the key is absent. */
    bridgeMessages: Record<string, string>;
  };
  rowMenu: {
    triggerLabel: string;
    view: string;
    edit: string;
    duplicate: string;
    delete: string;
    systemBadge: string;
  };
  ruleTable: {
    selectAll: string;
    deleteSelected: string;
    duplicateSelected: string;
  };
  allKeysFailed: {
    title: string;
    body: string;
    dismiss: string;
    openModelConfig: string;
  };
  runControls: {
    start: string;
    starting: string;
    running: string;
    pause: string;
    stop: string;
    continue: string;
    pausing: string;
    stopping: string;
    confirmStartTitle: string;
    confirmStartBody: string;
    confirmStartConfirm: string;
    confirmStartCancel: string;
    restartHint: string;
    taskControls: string;
  };
  bilingual: {
    label: string;
    hint: string;
    dedupeLabel: string;
    dedupeHint: string;
    subfolderDefault: string;
  };
  fieldHint: {
    toggleLabel: string;
    recommendedFor: string;
    fallbackProvider: string;
    source: string;
  };
  modelHints: {
    timeout: string;
    concurrency: string;
    rpm: string;
    tpm: string;
    retry: string;
    maxOutputTokens: string;
    temperature: string;
  };
  modelModal: {
    titleAdd: string;
    titleEdit: string;
    step1Title: string;
    step1Sub: string;
    step2Title: string;
    customTemplateName: string;
    pickerBack: string;
    saveAction: string;
    cancelAction: string;
    rotateKeysLabel: string;
    rotateKeysHelp: string;
    forceThinkingLabel: string;
    forceThinkingHelp: string;
    runtimeTuningLabel: string;
    samplingLabel: string;
    unsavedChangesConfirm: string;
  };
  quickSwitch: {
    titleModel: string;
    titlePrompt: string;
    closeAction: string;
    activeBadge: string;
    emptyModel: string;
    emptyPrompt: string;
    manageLink: string;
  };
  promptModal: {
    titleAdd: string;
    titleEdit: string;
    titleView: string;
    closeAction: string;
    systemReadOnlyNotice: string;
    nameLabel: string;
    namePlaceholder: string;
    descriptionLabel: string;
    descriptionPlaceholder: string;
    enabledLabel: string;
    systemTab: string;
    saveAction: string;
    cancelAction: string;
    resetAction: string;
    deleteAction: string;
    duplicateAction: string;
    previewAction: string;
    previewRunning: string;
    previewLabel: string;
    previewClampedNotice: string;
    previewSampleContext: string;
    sampleSourceLanguage: string;
    sampleTargetLanguage: string;
    sampleInput: string;
    unsavedChangesConfirm: string;
  };
  modelExtra: {
    deleteProfile: string;
    timeoutSeconds: string;
    setActive: string;
    activeBadge: string;
    testConnection: string;
    testConnectionHint: string;
    testRunning: string;
    testOk: string;
    testFailed: string;
    testLatency: string;
    fetchModels: string;
    fetchModelsHint: string;
    fetchRunning: string;
    fetchSuccess: string;
    fetchFailed: string;
    fetchUnsupported: string;
    pickModel: string;
    addCustom: string;
    addCustomHint: string;
  };
  batchReplacementHeaders: {
    src: string;
    dst: string;
    regex: string;
    caseSensitive: string;
  };
  appSettingsExtra: {
    uiScale: string;
    uiScaleHint: string;
    proxyUrl: string;
    proxyUrlHint: string;
    aboutLabel: string;
    updatesLabel: string;
    checkForUpdates: string;
    checking: string;
    currentLabel: string;
    latestLabel: string;
    upToDate: string;
    openReleasePage: string;
    download: string;
    savedTo: string;
    cacheLabel: string;
    cacheHint: string;
    cacheOpenAction: string;
    cacheManageAction: string;
    cacheSummary: string;
    cacheSummaryEmpty: string;
    cacheModalTitle: string;
    cacheModalHint: string;
    cachePurgeAll: string;
    cachePurgeAllHint: string;
    cachePurgeMonth: string;
    cachePurgeMonthHint: string;
    cachePurgeWeek: string;
    cachePurgeWeekHint: string;
    cachePurgeAllConfirm: string;
    cachePurgeAllConfirmYes: string;
    cachePurgeAllConfirmNo: string;
    cachePurgeResult: string;
    cachePurgeSkipped: string;
    cacheModalClose: string;
  };
  settingsToolbar: {
    save: string;
    reset: string;
    saving: string;
    saved: string;
    error: string;
    idle: string;
  };
  toast: {
    settingsSaved: string;
    settingsSaveFailed: string;
    settingsRejectedFields: string;
    presetSaved: string;
    presetSaveFailed: string;
    profileSaved: string;
    profileSaveFailed: string;
  };
  folderPicker: {
    choose: string;
    open: string;
    placeholder: string;
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
      ru: string;
      ar: string;
      de: string;
      fr: string;
      pl: string;
      es: string;
      it: string;
      pt: string;
      hu: string;
      tr: string;
      th: string;
      id: string;
      vi: string;
    };
    chineseFormSimplified: string;
    chineseFormTraditional: string;
  };
  pages: {
    /** Visible names for every leaf page, used by the rail and breadcrumbs. */
    model: {
      general: string;
    };
    translation: {
      run: string;
      settings: string;
      glossary: string;
      proofreading: string;
      textPreserve: string;
      preReplacement: string;
      postReplacement: string;
      prompt: string;
    };
    glossary: {
      run: string;
      settings: string;
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
      lowConfidenceMaxRetries: string;
      lowConfidenceMaxRetriesHelp: string;
      autoRetryMaxRounds: string;
      autoRetryMaxRoundsHelp: string;
      timeoutSeconds: string;
      timeoutSecondsHelp: string;
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
        frequency: string;
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
      importEmpty: string;
      searchPlaceholder: string;
      stats: {
        total: string;
        enabled: string;
      };
      presets: {
        title: string;
        empty: string;
        directoryHint: string;
        importAction: string;
        close: string;
      };
    };
    run: {
      title: string;
      sub: string;
      progress: string;
      failedSubtasks: string;
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
        avgSpeed: string;
      };
      liveCounter: {
        progressLabel: string;
        inflightLabel: string;
        chunksLabel: string;
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
    textPreservePage: {
      title: string;
      sub: string;
      addRule: string;
      empty: string;
      patternLabel: string;
      patternPlaceholder: string;
      noteLabel: string;
      notePlaceholder: string;
      enabledLabel: string;
      deleteAction: string;
      columns: { pattern: string; note: string; status: string };
      editorEmpty: string;
      actions: {
        add: string;
        import: string;
        export: string;
        search: string;
        statistics: string;
      };
      stats: { total: string; enabled: string };
      importEmpty: string;
    };
    replacementPage: {
      title: string;
      sub: string;
      preLabel: string;
      preHint: string;
      postLabel: string;
      postHint: string;
      addRule: string;
      empty: string;
      srcLabel: string;
      srcPlaceholder: string;
      dstLabel: string;
      dstPlaceholder: string;
      regexLabel: string;
      caseSensitiveLabel: string;
      noteLabel: string;
      enabledLabel: string;
      deleteAction: string;
      columns: {
        src: string;
        dst: string;
        rule: string;
        status: string;
      };
      editorEmpty: string;
      actions: {
        add: string;
        import: string;
        export: string;
        search: string;
        statistics: string;
      };
      stats: { total: string; enabled: string };
      importEmpty: string;
    };
    proofreadingPage: {
      title: string;
      sub: string;
      noTasks: string;
      taskPicker: string;
      loading: string;
      regenerateAction: string;
      regenerating: string;
      regenerateSuccess: string;
      regenerateFailed: string;
      columns: {
        index: string;
        src: string;
        dst: string;
        status: string;
      };
      statusLowConfidence: string;
      statusOk: string;
      statusEmpty: string;
      statusSourceResidue: string;
      statusSourceResidueHint: string;
      editorEmpty: string;
      editorSrcLabel: string;
      editorDstLabel: string;
      editorSaveAction: string;
      editorSavedHint: string;
      editorDirty: string;
      empty: string;
      stats: { total: string; lowConfidence: string };
      filterPlaceholder: string;
      filterOnlyLowConfidence: string;
      filterAll: string;
      taskFolderHint: string;
      retranslateAction: string;
      retranslating: string;
      retranslateSuccess: string;
      retranslateFailed: string;
      retranslateStale: string;
      retranslateTimeout: string;
      retranslateRejectedRunning: string;
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
      normalizeWidths: string;
      normalizeWidthsHint: string;
      referenceExamplesPerTerm: string;
      referenceExamplesPerTermHelp: string;
      minimumFrequency: string;
      minimumFrequencyHelp: string;
      maxTermDisplayLength: string;
      maxTermDisplayLengthHelp: string;
      timeoutSeconds: string;
      timeoutSecondsHelp: string;
      on: string;
      off: string;
    };
    run: {
      title: string;
      sub: string;
      progress: string;
      failedSubtasks: string;
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
        avgSpeed: string;
      };
      liveCounter: {
        progressLabel: string;
        inflightLabel: string;
        chunksLabel: string;
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
    stop: string;
    progressLabel: string;
    statusLabel: string;
    processedFiles: string;
    failedFiles: string;
    totalReplacements: string;
    artifactsLabel: string;
    noArtifacts: string;
    outputFiles: string;
    statisticsFile: string;
    viewReport: string;
  };
  batchReplacementReport: {
    title: string;
    close: string;
    expandAll: string;
    collapseAll: string;
    searchPlaceholder: string;
    noRules: string;
    noResults: string;
    noMatchesForRule: string;
    noOccurrenceMatchesQuery: string;
    matchCount: string;
    truncated: string;
    disabledBadge: string;
    fileLabel: string;
    summary: {
      totalReplacements: string;
      rulesWithMatches: string;
      filesProcessed: string;
      generatedAt: string;
    };
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
    previewThinking: string;
    badgeDefault: string;
    badgeCustom: string;
    actions: { add: string; edit: string; duplicate: string; delete: string };
    noThinkingPrompt: string;
  };
  model: {
    crumb: string;
    pageTitle: string;
    pageSub: string;
    sections: {
      preset: { title: string; sub: string };
      configured: {
        title: string;
        sub: string;
        empty: string;
        applyAction: string;
        appliedBadge: string;
        editAction: string;
        deleteAction: string;
      };
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
  glossaryStats: {
    title: string;
    close: string;
    total: string;
    enabled: string;
    disabled: string;
    uniqueSrc: string;
    caseSensitive: string;
    avgLen: string;
    topInfo: string;
    uncategorized: string;
    empty: string;
  };
  glossaryExport: {
    title: string;
    hint: string;
    formatJson: string;
    formatJsonHint: string;
    formatXlsx: string;
    formatXlsxHint: string;
    cancel: string;
  };
  ruleExport: {
    title: string;
    hint: string;
    formatJson: string;
    formatJsonHint: string;
    formatXlsx: string;
    formatXlsxHint: string;
    cancel: string;
  };
  ruleStats: {
    title: string;
    close: string;
    total: string;
    enabled: string;
    disabled: string;
    regexCount: string;
    caseSensitive: string;
    avgPatternLen: string;
    avgSrcDstLen: string;
    topNote: string;
    uncategorized: string;
    empty: string;
  };
  glossaryScrollNav: {
    top: string;
    bottom: string;
  };
  updatePrompt: {
    title: string;
    bodyPrefix: string;
    bodySuffix: string;
    publishedAtLabel: string;
    notesLabel: string;
    notesEmpty: string;
    updateAction: string;
    laterAction: string;
    autoUpdateAction: string;
    autoPreparingAction: string;
    autoReadyAction: string;
    autoPreparing: string;
    autoReadyPrefix: string;
    autoReadySuffix: string;
    autoFailed: string;
  };
  failedSubtasksModal: {
    triggerPrefix: string;
    triggerSuffix: string;
    autoFixingPrefix: string;
    autoFixingSuffix: string;
    autoFixingHint: string;
    continueHint: string;
    title: string;
    fileLabel: string;
    affectedLabel: string;
    noMessage: string;
    empty: string;
    close: string;
  };
  completionWithFailures: {
    title: string;
    bodyPrefix: string;
    bodySuffix: string;
    rerunAction: string;
    rerunPending: string;
    acceptAction: string;
  };
  runCompleted: {
    title: string;
  };
  runLowConfReminder: {
    title: string;
    totalLine: string;
    residueLine: string;
  };
  common: { placeholder: string };
}
