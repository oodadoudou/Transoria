import { useEffect } from "react";
import { create } from "zustand";

import {
  BridgeError,
  promptsBridge,
  type AppSettings,
  type PromptKind,
  type PromptPresetBody,
  type PromptPresetSummary,
  type PromptPreviewContext,
  type PromptPreviewResult,
} from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";

interface KindSlice {
  presets: PromptPresetSummary[];
  activeId: string | null;
  hydrated: boolean;
  loading: boolean;
  loadError: BridgeError | null;
}

interface PromptPresetsState {
  translation: KindSlice;
  glossary: KindSlice;
  mutationError: BridgeError | null;

  hydrate: (kind: PromptKind) => Promise<void>;
  refresh: (kind: PromptKind) => Promise<void>;
  read: (id: string) => Promise<PromptPresetBody | null>;
  createPreset: (
    kind: PromptKind,
    preset: Omit<PromptPresetBody, "id" | "is_default">,
  ) => Promise<PromptPresetBody | null>;
  updatePreset: (
    id: string,
    patch: Partial<PromptPresetBody>,
  ) => Promise<PromptPresetBody | null>;
  duplicatePreset: (
    id: string,
    newName?: string,
  ) => Promise<PromptPresetBody | null>;
  deletePreset: (id: string) => Promise<boolean>;
  selectActive: (
    kind: PromptKind,
    presetId: string | null,
  ) => Promise<AppSettings | null>;
  preview: (
    presetId: string,
    context: PromptPreviewContext,
    thinking?: boolean,
  ) => Promise<PromptPreviewResult | null>;
  resetToDefault: (id: string) => Promise<PromptPresetBody | null>;
  clearMutationError: () => void;
}

function asBridgeError(error: unknown): BridgeError {
  if (BridgeError.isBridgeError(error)) return error;
  return new BridgeError({
    code: "bridge.io_error",
    message: error instanceof Error ? error.message : String(error),
    retryable: true,
  });
}

const emptySlice: KindSlice = {
  presets: [],
  activeId: null,
  hydrated: false,
  loading: false,
  loadError: null,
};

export const usePromptPresetsStore = create<PromptPresetsState>((set, get) => {
  const refresh = async (kind: PromptKind): Promise<void> => {
    set((state) => ({
      [kind]: { ...state[kind], loading: true, loadError: null },
    }));
    try {
      const { presets, active_id } = await promptsBridge.list(kind);
      set((state) => ({
        [kind]: {
          ...state[kind],
          presets,
          activeId: active_id,
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

  return {
    translation: { ...emptySlice },
    glossary: { ...emptySlice },
    mutationError: null,

    hydrate: async (kind) => {
      const slice = get()[kind];
      if (slice.hydrated || slice.loading) return;
      await refresh(kind);
    },

    refresh,

    read: async (id) => {
      set({ mutationError: null });
      try {
        const { preset } = await promptsBridge.read(id);
        return preset;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    createPreset: async (kind, body) => {
      set({ mutationError: null });
      try {
        const { preset } = await promptsBridge.create(kind, body);
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
        const { preset } = await promptsBridge.update(id, patch);
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
        const { preset } = await promptsBridge.duplicate(id, newName);
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
        await promptsBridge.delete(id);
        await Promise.all([refresh("translation"), refresh("glossary")]);
        return true;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return false;
      }
    },

    selectActive: async (kind, presetId) => {
      set({ mutationError: null });
      try {
        const { app } = await promptsBridge.selectActive(kind, presetId);
        useSettingsStore.getState().applyAppFromBridge(app);
        set((state) => ({
          [kind]: {
            ...state[kind],
            activeId:
              app[
                kind === "translation"
                  ? "active_translation_prompt_id"
                  : "active_glossary_prompt_id"
              ] ?? null,
          },
        }));
        return app;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    preview: async (presetId, context, thinking = false) => {
      try {
        return await promptsBridge.preview(presetId, context, thinking);
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    resetToDefault: async (id) => {
      set({ mutationError: null });
      try {
        const { preset } = await promptsBridge.resetToDefault(id);
        await refresh(preset.kind);
        return preset;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    clearMutationError: () => set({ mutationError: null }),
  };
});

export function usePromptPresets(kind: PromptKind): PromptPresetsState {
  const state = usePromptPresetsStore();
  useEffect(() => {
    void state.hydrate(kind);
  }, [kind, state.hydrate]);
  return state;
}
