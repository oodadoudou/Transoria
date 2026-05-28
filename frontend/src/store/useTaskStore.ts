import { create } from "zustand";

export type ModuleId =
  | "model"
  | "translation"
  | "glossary"
  | "glossary-review"
  | "general-tools"
  | "app-settings";

export type TranslationPage =
  | "run"
  | "settings"
  | "glossary"
  | "proofreading"
  | "textPreserve"
  | "preReplacement"
  | "postReplacement"
  | "prompt";

export type GlossaryPage = "run" | "settings" | "prompt";

export type GlossaryReviewPage = "run" | "review" | "settings" | "prompt";

export type GeneralToolsPage =
  | "batchReplacement"
  | "epubCompress"
  | "epubMerge"
  | "epubConvert"
  | "epubMetadata";

const GENERAL_TOOLS_PAGES = [
  "batchReplacement",
  "epubCompress",
  "epubMerge",
  "epubConvert",
  "epubMetadata",
] as const;

export type AppSettingsPage = "general";

export type ModelPage = "general";

export type Route =
  | { module: "model"; page: ModelPage }
  | { module: "translation"; page: TranslationPage }
  | { module: "glossary"; page: GlossaryPage }
  | { module: "glossary-review"; page: GlossaryReviewPage }
  | { module: "general-tools"; page: GeneralToolsPage }
  | { module: "app-settings"; page: AppSettingsPage };

export function isRunPage(route: Route): boolean {
  return (
    (route.module === "translation" && route.page === "run") ||
    (route.module === "glossary" && route.page === "run") ||
    (route.module === "glossary-review" && route.page === "run")
  );
}

export function defaultPageFor(module: ModuleId): Route {
  switch (module) {
    case "model":
      return { module: "model", page: "general" };
    case "translation":
      return { module: "translation", page: "run" };
    case "glossary":
      return { module: "glossary", page: "run" };
    case "glossary-review":
      return { module: "glossary-review", page: "run" };
    case "general-tools":
      return { module: "general-tools", page: "batchReplacement" };
    case "app-settings":
      return { module: "app-settings", page: "general" };
  }
}

const ROUTE_STORAGE_KEY = "transoria.route";

function loadInitialRoute(): Route {
  if (typeof window === "undefined") return { module: "model", page: "general" };
  try {
    const raw = window.localStorage.getItem(ROUTE_STORAGE_KEY);
    if (!raw) return { module: "model", page: "general" };
    return coerceRoute(JSON.parse(raw));
  } catch {
    return { module: "model", page: "general" };
  }
}

function persistRoute(route: Route): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ROUTE_STORAGE_KEY, JSON.stringify(route));
  } catch {
    // Navigation still works if storage is blocked.
  }
}

function coerceRoute(value: unknown): Route {
  if (!value || typeof value !== "object") return { module: "model", page: "general" };
  const candidate = value as { module?: unknown; page?: unknown };
  switch (candidate.module) {
    case "model":
      return { module: "model", page: "general" };
    case "translation":
      if (
        [
          "run",
          "settings",
          "glossary",
          "proofreading",
          "textPreserve",
          "preReplacement",
          "postReplacement",
          "prompt",
        ].includes(String(candidate.page))
      ) {
        return {
          module: "translation",
          page: candidate.page as TranslationPage,
        };
      }
      return { module: "translation", page: "run" };
    case "glossary":
      if (["run", "settings", "prompt"].includes(String(candidate.page))) {
        return { module: "glossary", page: candidate.page as GlossaryPage };
      }
      return { module: "glossary", page: "run" };
    case "glossary-review":
      if (["run", "review", "settings", "prompt"].includes(String(candidate.page))) {
        return {
          module: "glossary-review",
          page: candidate.page as GlossaryReviewPage,
        };
      }
      return { module: "glossary-review", page: "run" };
    case "general-tools":
      if (GENERAL_TOOLS_PAGES.includes(candidate.page as GeneralToolsPage)) {
        return {
          module: "general-tools",
          page: candidate.page as GeneralToolsPage,
        };
      }
      return { module: "general-tools", page: "batchReplacement" };
    case "app-settings":
      return { module: "app-settings", page: "general" };
    default:
      return { module: "model", page: "general" };
  }
}

export interface GlossaryEntry {
  id: string;
  source: string;
  translation: string;
  description: string;
  caseSensitive: boolean;
  enabled: boolean;
  /** 0 for hand-authored/imported rows that do not track frequency. */
  frequency: number;
}

export interface ModuleGlossaryRules {
  enabled: boolean;
  selectedId: string | null;
  entries: GlossaryEntry[];
}

export type ProofreadingFilterKey =
  | "low_conf"
  | "source_residue"
  | "possible_duplicate"
  | "untranslated"
  | "format_rescue";

export interface ProofreadingLaunchState {
  taskId: string | null;
  filters: ProofreadingFilterKey[];
}

interface TaskState {
  route: Route;
  navigate: (route: Route) => void;
  proofreadingLaunch: ProofreadingLaunchState;
  openProofreadingTask: (
    taskId: string,
    filters?: ProofreadingFilterKey[],
  ) => void;
  consumeProofreadingLaunch: () => ProofreadingLaunchState;
  translationGlossary: ModuleGlossaryRules;
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

const initialTranslationGlossary: ModuleGlossaryRules = {
  enabled: false,
  selectedId: null,
  entries: [],
};

export const useTaskStore = create<TaskState>((set, get) => ({
  route: loadInitialRoute(),
  navigate: (route) => {
    persistRoute(route);
    set({ route });
  },
  proofreadingLaunch: { taskId: null, filters: [] },
  openProofreadingTask: (taskId, filters = []) =>
    set(() => {
      const route: Route = { module: "translation", page: "proofreading" };
      persistRoute(route);
      return {
        route,
        proofreadingLaunch: { taskId, filters },
      };
    }),
  consumeProofreadingLaunch: () => {
    const launch = get().proofreadingLaunch;
    set({ proofreadingLaunch: { taskId: null, filters: [] } });
    return launch;
  },
  translationGlossary: initialTranslationGlossary,
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
        frequency: 0,
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
  // Replaces the full entries list. Callers that mean "append" must
  // build the merged list themselves and pass it in as one snapshot
  // (e.g. ``importEntries([...state.entries, ...incoming])``). Doing
  // the append inside this reducer once also produced doubled rows
  // every time the page rehydrated from saved settings.
  importTranslationGlossaryEntries: (entries) =>
    set((state) => ({
      translationGlossary: {
        ...state.translationGlossary,
        entries: [...entries],
      },
    })),
}));
