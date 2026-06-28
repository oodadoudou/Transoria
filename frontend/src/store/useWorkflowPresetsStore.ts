import { useEffect } from "react";
import { create } from "zustand";

import {
  BridgeError,
  workflowPresetsBridge,
  type AppSettings,
  type GlossaryReviewSettings,
  type GlossarySettings,
  type PromptKind,
  type TranslationSettings,
  type WorkflowPreset,
  type WorkflowPresetDraft,
} from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";
import { usePromptPresetsStore } from "@/store/usePromptPresetsStore";

interface KindSlice {
  presets: WorkflowPreset[];
  matchedId: string | null;
  hydrated: boolean;
  loading: boolean;
  loadError: BridgeError | null;
}

type ModuleSettingsByKind = {
  translation: TranslationSettings;
  glossary: GlossarySettings;
  glossary_review: GlossaryReviewSettings;
};

type ApplyResult<K extends PromptKind> = {
  app: AppSettings;
  settings: ModuleSettingsByKind[K];
};

interface WorkflowPresetsState {
  translation: KindSlice;
  glossary: KindSlice;
  glossary_review: KindSlice;
  mutationError: BridgeError | null;

  hydrate: (kind: PromptKind) => Promise<void>;
  refresh: (kind: PromptKind) => Promise<void>;
  createPreset: (
    kind: PromptKind,
    draft: WorkflowPresetDraft,
  ) => Promise<WorkflowPreset | null>;
  updatePreset: (
    id: string,
    patch: Partial<WorkflowPresetDraft>,
  ) => Promise<WorkflowPreset | null>;
  duplicatePreset: (
    id: string,
    newName?: string,
  ) => Promise<WorkflowPreset | null>;
  deletePreset: (id: string) => Promise<boolean>;
  applyPreset: (
    kind: PromptKind,
    id: string,
  ) => Promise<ApplyResult<PromptKind> | null>;
  clearMutationError: () => void;
}

const emptySlice: KindSlice = {
  presets: [],
  matchedId: null,
  hydrated: false,
  loading: false,
  loadError: null,
};

function asBridgeError(error: unknown): BridgeError {
  if (BridgeError.isBridgeError(error)) return error;
  return new BridgeError({
    code: "bridge.io_error",
    message: error instanceof Error ? error.message : String(error),
    retryable: true,
  });
}

export const useWorkflowPresetsStore = create<WorkflowPresetsState>(
  (set, get) => {
    const refresh = async (kind: PromptKind): Promise<void> => {
      set((state) => ({
        [kind]: { ...state[kind], loading: true, loadError: null },
      }));
      try {
        const { presets, matched_id } = await workflowPresetsBridge.list(kind);
        set((state) => ({
          [kind]: {
            ...state[kind],
            presets,
            matchedId: matched_id,
            loading: false,
            hydrated: true,
          },
        }));
      } catch (error) {
        set((state) => ({
          [kind]: {
            ...state[kind],
            loading: false,
            loadError: asBridgeError(error),
          },
        }));
      }
    };

    const refreshAll = async (): Promise<void> => {
      await Promise.all([
        refresh("translation"),
        refresh("glossary"),
        refresh("glossary_review"),
      ]);
    };

    return {
      translation: { ...emptySlice },
      glossary: { ...emptySlice },
      glossary_review: { ...emptySlice },
      mutationError: null,

      hydrate: async (kind) => {
        const slice = get()[kind];
        if (slice.hydrated || slice.loading) return;
        await refresh(kind);
      },

      refresh,

      createPreset: async (kind, draft) => {
        set({ mutationError: null });
        try {
          const { preset } = await workflowPresetsBridge.create(kind, draft);
          await refresh(kind);
          return preset;
        } catch (error) {
          set({ mutationError: asBridgeError(error) });
          return null;
        }
      },

      updatePreset: async (id, patch) => {
        set({ mutationError: null });
        try {
          const { preset } = await workflowPresetsBridge.update(id, patch);
          await refresh(preset.kind);
          return preset;
        } catch (error) {
          set({ mutationError: asBridgeError(error) });
          return null;
        }
      },

      duplicatePreset: async (id, newName) => {
        set({ mutationError: null });
        try {
          const { preset } = await workflowPresetsBridge.duplicate(id, newName);
          await refresh(preset.kind);
          return preset;
        } catch (error) {
          set({ mutationError: asBridgeError(error) });
          return null;
        }
      },

      deletePreset: async (id) => {
        set({ mutationError: null });
        try {
          await workflowPresetsBridge.delete(id);
          await refreshAll();
          return true;
        } catch (error) {
          set({ mutationError: asBridgeError(error) });
          return false;
        }
      },

      applyPreset: async (kind, id) => {
        set({ mutationError: null });
        try {
          const result = await workflowPresetsBridge.apply(kind, id);
          const settingsStore = useSettingsStore.getState();
          settingsStore.applyAppFromBridge(result.app);
          settingsStore.applyModuleFromBridge(
            kind,
            result.settings as ModuleSettingsByKind[typeof kind],
          );
          await Promise.all([
            refresh(kind),
            usePromptPresetsStore.getState().refresh(kind),
          ]);
          return result as ApplyResult<typeof kind>;
        } catch (error) {
          set({ mutationError: asBridgeError(error) });
          return null;
        }
      },

      clearMutationError: () => set({ mutationError: null }),
    };
  },
);

export function useWorkflowPresets(kind: PromptKind): WorkflowPresetsState {
  const state = useWorkflowPresetsStore();
  useEffect(() => {
    void state.hydrate(kind);
  }, [kind, state.hydrate]);
  return state;
}
