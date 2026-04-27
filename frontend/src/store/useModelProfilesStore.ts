import { useEffect } from "react";
import { create } from "zustand";

import {
  BridgeError,
  modelProfilesBridge,
  settingsBridge,
  type AppSettings,
  type ModelProfile,
  type ModelProfileDraft,
} from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";

interface ModelProfilesState {
  profiles: ModelProfile[];
  hydrated: boolean;
  loading: boolean;
  loadError: BridgeError | null;
  mutationError: BridgeError | null;

  hydrate: () => Promise<void>;
  refresh: () => Promise<void>;
  createProfile: (draft: ModelProfileDraft) => Promise<ModelProfile | null>;
  updateProfile: (
    id: string,
    patch: Partial<ModelProfile>,
  ) => Promise<ModelProfile | null>;
  deleteProfile: (id: string) => Promise<boolean>;
  setApiKey: (id: string, apiKeys: string[]) => Promise<ModelProfile | null>;
  selectActive: (
    module: "translation" | "glossary",
    profileId: string | null,
  ) => Promise<AppSettings | null>;
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

export const useModelProfilesStore = create<ModelProfilesState>((set, get) => {
  const fetchAll = async (): Promise<void> => {
    set({ loading: true, loadError: null });
    try {
      const { profiles } = await modelProfilesBridge.list();
      set({
        profiles,
        loading: false,
        hydrated: true,
      });
    } catch (error) {
      set({
        loading: false,
        loadError: asBridgeError(error),
      });
    }
  };

  return {
    profiles: [],
    hydrated: false,
    loading: false,
    loadError: null,
    mutationError: null,

    hydrate: async () => {
      if (get().hydrated || get().loading) return;
      await fetchAll();
    },

    refresh: fetchAll,

    createProfile: async (draft) => {
      set({ mutationError: null });
      try {
        const { profile } = await modelProfilesBridge.create(draft);
        set((state) => ({ profiles: [...state.profiles, profile] }));
        return profile;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    updateProfile: async (id, patch) => {
      set({ mutationError: null });
      try {
        const { profile } = await modelProfilesBridge.update(id, patch);
        set((state) => ({
          profiles: state.profiles.map((p) => (p.id === id ? profile : p)),
        }));
        return profile;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    deleteProfile: async (id) => {
      set({ mutationError: null });
      try {
        await modelProfilesBridge.delete(id);
        set((state) => ({
          profiles: state.profiles.filter((p) => p.id !== id),
        }));
        // Backend clears app.active_*_model_id when the deleted profile was
        // active. Refresh the settings draft so subsequent reads agree.
        try {
          const all = await settingsBridge.loadAll();
          useSettingsStore.setState((settings) => ({
            app: {
              ...settings.app,
              draft: all.app,
              baseline: all.app,
            },
          }));
        } catch {
          // Non-fatal: settings hydrate failure surfaces through useSettingsStore.
        }
        return true;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return false;
      }
    },

    setApiKey: async (id, apiKeys) => {
      set({ mutationError: null });
      try {
        const { profile } = await modelProfilesBridge.setApiKey(id, apiKeys);
        set((state) => ({
          profiles: state.profiles.map((p) => (p.id === id ? profile : p)),
        }));
        return profile;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    selectActive: async (module, profileId) => {
      set({ mutationError: null });
      try {
        const { app } = await modelProfilesBridge.selectActive(
          module,
          profileId,
        );
        return app;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    clearMutationError: () => set({ mutationError: null }),
  };
});

export function useModelProfiles(): ModelProfilesState {
  const state = useModelProfilesStore();
  useEffect(() => {
    void state.hydrate();
  }, [state.hydrate]);
  return state;
}
