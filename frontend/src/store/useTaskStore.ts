import { create } from "zustand";

/* ------------------------------------------------------------------ *
 * Routing
 *
 * Route state lives client-side because it does not affect the backend
 * task graph. Runtime data (progress, tokens, status) lives in
 * `useRuntimeStore`, which is bridge-backed.
 * ------------------------------------------------------------------ */

export type ModuleId =
  | "translation"
  | "glossary"
  | "general-tools"
  | "app-settings";

export type TranslationPage =
  | "run"
  | "settings"
  | "model"
  | "glossary"
  | "textPreserve"
  | "replacement"
  | "prompt";

export type GlossaryPage = "run" | "settings" | "model" | "prompt";

export type GeneralToolsPage = "batchReplacement";

export type AppSettingsPage = "general";

export type Route =
  | { module: "translation"; page: TranslationPage }
  | { module: "glossary"; page: GlossaryPage }
  | { module: "general-tools"; page: GeneralToolsPage }
  | { module: "app-settings"; page: AppSettingsPage };

export function isRunPage(route: Route): boolean {
  return (
    (route.module === "translation" && route.page === "run") ||
    (route.module === "glossary" && route.page === "run")
  );
}

export function defaultPageFor(module: ModuleId): Route {
  switch (module) {
    case "translation":
      return { module: "translation", page: "run" };
    case "glossary":
      return { module: "glossary", page: "run" };
    case "general-tools":
      return { module: "general-tools", page: "batchReplacement" };
    case "app-settings":
      return { module: "app-settings", page: "general" };
  }
}

/* ------------------------------------------------------------------ *
 * Per-module model library — mirrors the backend `ModelConfig` shape,
 * extended to match what the LinguaGacha-style edit panels expose.
 * Step 5 will replace this with bridge-backed model profiles.
 * ------------------------------------------------------------------ */

export type ProviderFormat =
  | "openai"
  | "anthropic"
  | "google"
  | "sakura"
  | "custom";
export type ThinkingLevel = "off" | "low" | "medium" | "high";

export type ModelCategory =
  | "preset"
  | "custom-openai"
  | "custom-google"
  | "custom-anthropic";

export interface SamplingOverride {
  enabled: boolean;
  value: number;
}

export interface ModelEntry {
  id: string;
  category: ModelCategory;
  vendor: string;
  apiFormat: ProviderFormat;
  displayName: string;
  baseUrl: string;
  apiKeys: string;
  modelId: string;
  inputTokenLimit: number;
  outputTokenLimit: number;
  concurrency: number;
  rpm: number;
  tpm: number;
  retryAttempts: number;
  thinkingLevel: ThinkingLevel;
  topP: SamplingOverride;
  temperature: SamplingOverride;
  presencePenalty: SamplingOverride;
  frequencyPenalty: SamplingOverride;
  customHeaders: { enabled: boolean; value: string };
}

export type ModelOwner = "translation" | "glossary";

export interface ModuleModelLibrary {
  selectedId: string;
  entries: ModelEntry[];
}

/* ------------------------------------------------------------------ *
 * Prompt presets — Step 5 will replace this with bridge-backed presets.
 * ------------------------------------------------------------------ */

export type PromptSource = "linguagacha" | "keywordgacha" | "custom";

export interface PromptPresetInfo {
  id: string;
  name: string;
  source: PromptSource;
  isDefault: boolean;
  systemPrompt: string;
  suffixPrompt: string;
  thinkingPrompt: string | null;
}

export interface ModulePrompts {
  activeId: string;
  presets: PromptPresetInfo[];
}

/* ------------------------------------------------------------------ *
 * Translation glossary entries (used while translating, not during
 * extraction). Mirrors the backend `Glossary` shape from
 * `transoria/workflows/translation/rules.py`.
 * ------------------------------------------------------------------ */

export interface GlossaryEntry {
  id: string;
  source: string;
  translation: string;
  description: string;
  caseSensitive: boolean;
  enabled: boolean;
}

export interface ModuleGlossaryRules {
  enabled: boolean;
  selectedId: string | null;
  entries: GlossaryEntry[];
}

interface TaskState {
  route: Route;
  navigate: (route: Route) => void;
  translationGlossary: ModuleGlossaryRules;
  modelLibraries: Record<ModelOwner, ModuleModelLibrary>;
  prompts: Record<ModelOwner, ModulePrompts>;
  setSelectedModelId: (owner: ModelOwner, id: string) => void;
  updateModelEntry: (
    owner: ModelOwner,
    id: string,
    updates: Partial<ModelEntry>,
  ) => void;
  setActivePromptId: (owner: ModelOwner, id: string) => void;
  setTranslationGlossaryEnabled: (enabled: boolean) => void;
  setTranslationGlossarySelectedId: (id: string | null) => void;
  addTranslationGlossaryEntry: () => void;
  updateTranslationGlossaryEntry: (
    id: string,
    updates: Partial<GlossaryEntry>,
  ) => void;
  deleteTranslationGlossaryEntry: (id: string) => void;
  importTranslationGlossaryEntries: (entries: GlossaryEntry[]) => void;
}

/* ------------------------------------------------------------------ *
 * Initial state
 * ------------------------------------------------------------------ */

const initialTranslationGlossary: ModuleGlossaryRules = {
  enabled: false,
  selectedId: null,
  entries: [],
};

function defaultEntry(
  id: string,
  category: ModelCategory,
  vendor: string,
  apiFormat: ProviderFormat,
  displayName: string,
  baseUrl: string,
  modelId: string,
  overrides: Partial<ModelEntry> = {},
): ModelEntry {
  return {
    id,
    category,
    vendor,
    apiFormat,
    displayName,
    baseUrl,
    apiKeys: "",
    modelId,
    inputTokenLimit: 0,
    outputTokenLimit: 0,
    concurrency: 0,
    rpm: 0,
    tpm: 0,
    retryAttempts: 2,
    thinkingLevel: "off",
    topP: { enabled: false, value: 1 },
    temperature: { enabled: false, value: 0.6 },
    presencePenalty: { enabled: false, value: 0 },
    frequencyPenalty: { enabled: false, value: 0 },
    customHeaders: { enabled: false, value: "" },
    ...overrides,
  };
}

function makeLibrary(selectedId: string): ModuleModelLibrary {
  return {
    selectedId,
    entries: [
      defaultEntry(
        "preset-deepseek",
        "preset",
        "DeepSeek",
        "openai",
        "DeepSeek",
        "https://api.deepseek.com/v1",
        "deepseek-chat",
      ),
      defaultEntry(
        "preset-anthropic",
        "preset",
        "Anthropic",
        "anthropic",
        "Anthropic",
        "https://api.anthropic.com",
        "claude-3-5-sonnet-20241022",
      ),
      defaultEntry(
        "preset-google",
        "preset",
        "Google",
        "google",
        "Google",
        "https://generativelanguage.googleapis.com/v1beta",
        "gemini-2.0-flash",
      ),
      defaultEntry(
        "preset-openai",
        "preset",
        "OpenAI",
        "openai",
        "OpenAI",
        "https://api.openai.com/v1",
        "gpt-4o-mini",
      ),
    ],
  };
}

const initialModelLibraries: Record<ModelOwner, ModuleModelLibrary> = {
  translation: makeLibrary("preset-openai"),
  glossary: makeLibrary("preset-openai"),
};

const LG_SYSTEM_PROMPT = `You are a professional Korean→Simplified Chinese novel translator.

The text follows JSONL format. Each line is one segment to translate.
- Preserve emphasis, dialog markers, and proper nouns from the supplied glossary.
- Match the source register; do not paraphrase.
- Never translate filenames, sentinel tokens, or numeric indices.
- Output one JSONL line per input line, indices preserved.`;

const KG_SYSTEM_PROMPT = `You are a glossary extraction assistant for novel translation.

Read the source segments and emit a JSONL list of \`{"src","dst","type"}\` entries:
- src: original term (proper noun, place, item, recurring phrase)
- dst: target-language rendering
- type: short tag (Male Name, Female Name, Place, Item, etc.)

Skip common words and grammatical particles. Aim for terms that recur or
require a fixed translation across chapters.`;

const LG_THINKING = `Before answering, briefly state which terms in the source require glossary lookup, and any honorifics or speech-level cues that should be preserved. Then produce the JSONL.`;

const initialPrompts: Record<ModelOwner, ModulePrompts> = {
  translation: {
    activeId: "tr-default",
    presets: [
      {
        id: "tr-default",
        name: "Default Translation",
        source: "linguagacha",
        isDefault: true,
        systemPrompt: LG_SYSTEM_PROMPT,
        suffixPrompt: "Output JSONL only. No commentary.",
        thinkingPrompt: LG_THINKING,
      },
    ],
  },
  glossary: {
    activeId: "gl-default",
    presets: [
      {
        id: "gl-default",
        name: "Default Glossary",
        source: "keywordgacha",
        isDefault: true,
        systemPrompt: KG_SYSTEM_PROMPT,
        suffixPrompt: "Output JSONL only. No commentary.",
        thinkingPrompt: null,
      },
    ],
  },
};

export const useTaskStore = create<TaskState>((set) => ({
  route: { module: "translation", page: "run" },
  navigate: (route) => set({ route }),
  translationGlossary: initialTranslationGlossary,
  modelLibraries: initialModelLibraries,
  prompts: initialPrompts,
  setSelectedModelId: (owner, id) =>
    set((state) => ({
      modelLibraries: {
        ...state.modelLibraries,
        [owner]: { ...state.modelLibraries[owner], selectedId: id },
      },
    })),
  updateModelEntry: (owner, id, updates) =>
    set((state) => ({
      modelLibraries: {
        ...state.modelLibraries,
        [owner]: {
          ...state.modelLibraries[owner],
          entries: state.modelLibraries[owner].entries.map((entry) =>
            entry.id === id ? { ...entry, ...updates } : entry,
          ),
        },
      },
    })),
  setActivePromptId: (owner, id) =>
    set((state) => ({
      prompts: {
        ...state.prompts,
        [owner]: { ...state.prompts[owner], activeId: id },
      },
    })),
  setTranslationGlossaryEnabled: (enabled) =>
    set((state) => ({
      translationGlossary: { ...state.translationGlossary, enabled },
    })),
  setTranslationGlossarySelectedId: (id) =>
    set((state) => ({
      translationGlossary: { ...state.translationGlossary, selectedId: id },
    })),
  addTranslationGlossaryEntry: () =>
    set((state) => {
      const id = `g-${Date.now().toString(36)}`;
      const entry: GlossaryEntry = {
        id,
        source: "",
        translation: "",
        description: "",
        caseSensitive: false,
        enabled: true,
      };
      return {
        translationGlossary: {
          ...state.translationGlossary,
          selectedId: id,
          entries: [...state.translationGlossary.entries, entry],
        },
      };
    }),
  updateTranslationGlossaryEntry: (id, updates) =>
    set((state) => ({
      translationGlossary: {
        ...state.translationGlossary,
        entries: state.translationGlossary.entries.map((entry) =>
          entry.id === id ? { ...entry, ...updates } : entry,
        ),
      },
    })),
  deleteTranslationGlossaryEntry: (id) =>
    set((state) => {
      const entries = state.translationGlossary.entries.filter(
        (e) => e.id !== id,
      );
      const selectedId =
        state.translationGlossary.selectedId === id
          ? (entries[0]?.id ?? null)
          : state.translationGlossary.selectedId;
      return {
        translationGlossary: {
          ...state.translationGlossary,
          entries,
          selectedId,
        },
      };
    }),
  importTranslationGlossaryEntries: (entries) =>
    set((state) => ({
      translationGlossary: {
        ...state.translationGlossary,
        entries: [...state.translationGlossary.entries, ...entries],
      },
    })),
}));
