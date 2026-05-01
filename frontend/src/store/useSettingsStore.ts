import { useEffect } from "react";
import { create } from "zustand";

import {
  BridgeError,
  settingsBridge,
  type AppSettings,
  type GlossarySettings,
  type ReplacementSettings,
  type SettingsModule,
  type TranslationSettings,
} from "@/bridge";

const SAVE_DEBOUNCE_MS = 400;

type SaveState = "idle" | "saving" | "saved" | "error";

type ModuleSettingsMap = {
  app: AppSettings;
  translation: TranslationSettings;
  glossary: GlossarySettings;
  replacement: ReplacementSettings;
};

interface RejectedField {
  field: string;
  reason: string;
}

interface ModuleSlice<TModule extends SettingsModule> {
  draft: ModuleSettingsMap[TModule] | null;
  baseline: ModuleSettingsMap[TModule] | null;
  pendingPatch: Partial<ModuleSettingsMap[TModule]>;
  saveState: SaveState;
  lastSavedAt: string | null;
  /** Auto-saves and programmatic saves are silent; the success toast
   * fires only when the caller passes ``explicit: true`` (the manual
   * "保存" button does this). The ``saved_at`` from such a save is
   * mirrored here so the toast hook knows which write to surface. */
  lastExplicitSavedAt: string | null;
  lastError: BridgeError | null;
  /** Fields the backend's lenient ``save_partial`` couldn't apply
   * (unknown fields, type mismatches). Reset on every successful save
   * so a previous rejection from an old patch doesn't haunt the
   * current toast. */
  lastRejectedFields: RejectedField[];
  debounceHandle: ReturnType<typeof setTimeout> | null;
}

type Slices = {
  [K in SettingsModule]: ModuleSlice<K>;
};

interface SettingsState extends Slices {
  hydrated: boolean;
  hydrating: boolean;
  loadError: BridgeError | null;
  hydrate: () => Promise<void>;
  applyAppFromBridge: (app: AppSettings) => void;
  updateField: <
    TModule extends SettingsModule,
    TKey extends keyof ModuleSettingsMap[TModule],
  >(
    module: TModule,
    key: TKey,
    value: ModuleSettingsMap[TModule][TKey],
  ) => void;
  saveNow: (
    module: SettingsModule,
    opts?: { explicit?: boolean },
  ) => Promise<void>;
  reset: (module: SettingsModule) => Promise<void>;
  clearSaveError: (module: SettingsModule) => void;
}

const emptySlice: ModuleSlice<SettingsModule> = {
  draft: null,
  baseline: null,
  pendingPatch: {},
  saveState: "idle",
  lastSavedAt: null,
  lastExplicitSavedAt: null,
  lastError: null,
  lastRejectedFields: [],
  debounceHandle: null,
};

function asBridgeError(error: unknown): BridgeError {
  if (BridgeError.isBridgeError(error)) return error;
  return new BridgeError({
    code: "bridge.io_error",
    message: error instanceof Error ? error.message : String(error),
    retryable: true,
  });
}

function patchSlice<TModule extends SettingsModule>(
  state: SettingsState,
  module: TModule,
  patch: Partial<ModuleSlice<TModule>>,
): Pick<SettingsState, TModule> {
  const current = state[module] as ModuleSlice<TModule>;
  return { [module]: { ...current, ...patch } } as unknown as Pick<
    SettingsState,
    TModule
  >;
}

export const useSettingsStore = create<SettingsState>((set, get) => {
  const flush = async (
    module: SettingsModule,
    explicit = false,
  ): Promise<void> => {
    const slice = get()[module];
    const patch = slice.pendingPatch;
    if (!Object.keys(patch).length) return;
    set((state) =>
      patchSlice(state, module, {
        saveState: "saving",
        lastError: null,
      }),
    );
    try {
      const { saved_at, rejected_fields } = await settingsBridge.savePartial(
        module,
        patch,
      );
      set((state) => {
        const updated = state[module];
        const consumed = updated.pendingPatch;
        const remaining: Record<string, unknown> = {};
        let hasRemaining = false;
        for (const [key, value] of Object.entries(consumed)) {
          if (key in patch && patch[key as never] === value) {
            continue;
          }
          remaining[key] = value;
          hasRemaining = true;
        }
        return patchSlice(state, module, {
          pendingPatch: hasRemaining
            ? (remaining as Partial<ModuleSettingsMap[typeof module]>)
            : {},
          baseline: updated.draft,
          saveState: hasRemaining ? "saving" : "saved",
          lastSavedAt: saved_at,
          lastExplicitSavedAt: explicit
            ? saved_at
            : updated.lastExplicitSavedAt,
          lastRejectedFields: rejected_fields ?? [],
        });
      });
    } catch (error) {
      const bridgeError = asBridgeError(error);
      set((state) =>
        patchSlice(state, module, {
          saveState: "error",
          lastError: bridgeError,
        }),
      );
    }
  };

  const scheduleSave = (module: SettingsModule): void => {
    const slice = get()[module];
    if (slice.debounceHandle) {
      clearTimeout(slice.debounceHandle);
    }
    const handle = setTimeout(() => {
      set((state) => patchSlice(state, module, { debounceHandle: null }));
      void flush(module);
    }, SAVE_DEBOUNCE_MS);
    set((state) => patchSlice(state, module, { debounceHandle: handle }));
  };

  return {
    app: { ...emptySlice } as ModuleSlice<"app">,
    translation: { ...emptySlice } as ModuleSlice<"translation">,
    glossary: { ...emptySlice } as ModuleSlice<"glossary">,
    replacement: { ...emptySlice } as ModuleSlice<"replacement">,
    hydrated: false,
    hydrating: false,
    loadError: null,

    hydrate: async () => {
      if (get().hydrated || get().hydrating) return;
      set({ hydrating: true, loadError: null });
      try {
        const all = await settingsBridge.loadAll();
        set((state) => ({
          hydrated: true,
          hydrating: false,
          app: {
            ...state.app,
            draft: all.app,
            baseline: all.app,
          } as ModuleSlice<"app">,
          translation: {
            ...state.translation,
            draft: all.translation,
            baseline: all.translation,
          } as ModuleSlice<"translation">,
          glossary: {
            ...state.glossary,
            draft: all.glossary,
            baseline: all.glossary,
          } as ModuleSlice<"glossary">,
          replacement: {
            ...state.replacement,
            draft: all.replacement,
            baseline: all.replacement,
          } as ModuleSlice<"replacement">,
        }));
      } catch (error) {
        set({
          hydrating: false,
          loadError: asBridgeError(error),
        });
      }
    },

    applyAppFromBridge: (app) => {
      set((state) => ({
        app: {
          ...state.app,
          draft: app,
          baseline: app,
        } as ModuleSlice<"app">,
      }));
    },

    updateField: (module, key, value) => {
      set((state) => {
        const slice = state[module] as ModuleSlice<typeof module>;
        if (!slice.draft) return {} as Partial<SettingsState>;
        const draft = { ...slice.draft, [key]: value };
        const pendingPatch = {
          ...slice.pendingPatch,
          [key]: value,
        } as Partial<ModuleSettingsMap[typeof module]>;
        return patchSlice(state, module, {
          draft,
          pendingPatch,
          saveState: "saving",
          lastError: null,
        });
      });
      scheduleSave(module);
    },

    saveNow: async (module, opts) => {
      const slice = get()[module];
      if (slice.debounceHandle) {
        clearTimeout(slice.debounceHandle);
        set((state) => patchSlice(state, module, { debounceHandle: null }));
      }
      const explicit = opts?.explicit ?? false;
      const hadPending = Object.keys(slice.pendingPatch).length > 0;
      await flush(module, explicit);
      // An explicit save with nothing pending (auto-save already
      // committed every change) should still confirm to the user.
      // Bump the explicit timestamp so the toast hook fires.
      if (explicit && !hadPending && get()[module].saveState !== "error") {
        const now = new Date().toISOString();
        set((state) =>
          patchSlice(state, module, {
            saveState: "saved",
            lastSavedAt: now,
            lastExplicitSavedAt: now,
          }),
        );
      }
    },

    reset: async (module) => {
      const slice = get()[module];
      if (slice.debounceHandle) {
        clearTimeout(slice.debounceHandle);
      }
      set((state) =>
        patchSlice(state, module, {
          debounceHandle: null,
          pendingPatch: {},
          saveState: "saving",
          lastError: null,
        }),
      );
      try {
        const defaults = (await settingsBridge.resetModule(
          module,
        )) as ModuleSettingsMap[typeof module];
        set((state) =>
          patchSlice(state, module, {
            draft: defaults,
            baseline: defaults,
            saveState: "saved",
            lastSavedAt: new Date().toISOString(),
          }),
        );
      } catch (error) {
        set((state) =>
          patchSlice(state, module, {
            saveState: "error",
            lastError: asBridgeError(error),
          }),
        );
      }
    },

    clearSaveError: (module) =>
      set((state) =>
        patchSlice(state, module, {
          saveState: "idle",
          lastError: null,
        }),
      ),
  };
});

/** React hook that hydrates settings on first mount and returns the slice for one module. */
export function useModuleSettings<TModule extends SettingsModule>(
  module: TModule,
): {
  draft: ModuleSettingsMap[TModule] | null;
  saveState: SaveState;
  lastSavedAt: string | null;
  lastExplicitSavedAt: string | null;
  lastError: BridgeError | null;
  lastRejectedFields: RejectedField[];
  isHydrated: boolean;
  loadError: BridgeError | null;
  update: <TKey extends keyof ModuleSettingsMap[TModule]>(
    key: TKey,
    value: ModuleSettingsMap[TModule][TKey],
  ) => void;
  saveNow: (opts?: { explicit?: boolean }) => Promise<void>;
  reset: () => Promise<void>;
  clearError: () => void;
} {
  const hydrate = useSettingsStore((state) => state.hydrate);
  const slice = useSettingsStore(
    (state) => state[module],
  ) as ModuleSlice<TModule>;
  const hydrated = useSettingsStore((state) => state.hydrated);
  const loadError = useSettingsStore((state) => state.loadError);
  const updateField = useSettingsStore((state) => state.updateField);
  const saveNow = useSettingsStore((state) => state.saveNow);
  const reset = useSettingsStore((state) => state.reset);
  const clearError = useSettingsStore((state) => state.clearSaveError);

  useEffect(() => {
    void hydrate();
  }, [hydrate]);

  return {
    draft: slice.draft,
    saveState: slice.saveState,
    lastSavedAt: slice.lastSavedAt,
    lastExplicitSavedAt: slice.lastExplicitSavedAt,
    lastError: slice.lastError,
    lastRejectedFields: slice.lastRejectedFields,
    isHydrated: hydrated,
    loadError,
    update: (key, value) => updateField(module, key, value),
    saveNow: (opts) => saveNow(module, opts),
    reset: () => reset(module),
    clearError: () => clearError(module),
  };
}

export type { SaveState };
