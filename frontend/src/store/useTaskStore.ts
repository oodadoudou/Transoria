import { create } from "zustand";

/* ------------------------------------------------------------------ *
 * Routing
 *
 * Route state lives client-side because it does not affect the backend
 * task graph. Runtime data (progress, tokens, status) lives in
 * `useRuntimeStore`. Settings, model profiles, prompt presets, and the
 * configuration libraries each live behind the bridge in their own
 * dedicated stores. Translation glossary entries are still in-memory
 * here pending the Step F.P0.1 redesign that threads them into
 * `TranslationConfig`.
 * ------------------------------------------------------------------ */

export type ModuleId =
  | "model"
  | "translation"
  | "glossary"
  | "general-tools"
  | "app-settings";

export type TranslationPage =
  | "run"
  | "settings"
  | "glossary"
  | "textPreserve"
  | "replacement"
  | "prompt";

export type GlossaryPage = "run" | "settings" | "prompt";

export type GeneralToolsPage = "batchReplacement";

export type AppSettingsPage = "general";

export type ModelPage = "general";

export type Route =
  | { module: "model"; page: ModelPage }
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
    case "model":
      return { module: "model", page: "general" };
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
 * Translation glossary edit buffer. Mirrors the backend `Glossary`
 * shape from `transoria/workflows/translation/rules.py`. Persistence
 * + threading into `TranslationConfig` happens through
 * `useModuleSettings('translation').translation_glossary`; this
 * store is the per-page edit buffer that syncs to settings on
 * change (see GlossaryPage.tsx).
 * ------------------------------------------------------------------ */

export interface GlossaryEntry {
  id: string;
  source: string;
  translation: string;
  description: string;
  caseSensitive: boolean;
  enabled: boolean;
  /** Occurrence count carried over from glossary-extraction artifacts.
   * 0 when the row was hand-authored / imported from a source that
   * doesn't track frequency. Drives the optional sort-by-frequency
   * column in the rule table. */
  frequency: number;
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

export const useTaskStore = create<TaskState>((set) => ({
  route: { module: "translation", page: "run" },
  navigate: (route) => set({ route }),
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
