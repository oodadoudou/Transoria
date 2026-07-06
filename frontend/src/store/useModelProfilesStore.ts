import { useEffect } from "react";
import { create } from "zustand";

import {
  BridgeError,
  modelProfilesBridge,
  settingsBridge,
  type AppSettings,
  type ModelListEntry,
  type ModelProfile,
  type ModelProfileDraft,
  type ModelTestResult,
} from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";

interface TestConnectionState {
  running: boolean;
  result: ModelTestResult | null;
  error: BridgeError | null;
}

interface FetchModelListState {
  running: boolean;
  models: ModelListEntry[];
  error: BridgeError | null;
}

interface ModelProfilesState {
  profiles: ModelProfile[];
  hydrated: boolean;
  loading: boolean;
  loadError: BridgeError | null;
  mutationError: BridgeError | null;

  /** Per-profile transient state for the test/fetch buttons. */
  testStates: Record<string, TestConnectionState>;
  fetchStates: Record<string, FetchModelListState>;

  hydrate: () => Promise<void>;
  refresh: () => Promise<void>;
  createProfile: (draft: ModelProfileDraft) => Promise<ModelProfile | null>;
  updateProfile: (
    id: string,
    patch: Partial<ModelProfile>,
  ) => Promise<ModelProfile | null>;
  deleteProfile: (id: string) => Promise<boolean>;
  duplicateProfile: (id: string) => Promise<ModelProfile | null>;
  setApiKey: (id: string, apiKeys: string[]) => Promise<ModelProfile | null>;
  selectActive: (
    module: "translation" | "glossary" | "glossary_review",
    profileId: string | null,
  ) => Promise<AppSettings | null>;
  testConnection: (id: string) => Promise<void>;
  fetchModelList: (id: string) => Promise<void>;
  clearTestState: (id: string) => void;
  clearFetchState: (id: string) => void;
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
      const bridgeError = asBridgeError(error);
      // Surface the failure on the Console so DevTools shows it without
      // the user having to dig into Zustand state. This is the line you
      // want to read when "the model page is empty" surprises you.
      // eslint-disable-next-line no-console
      console.warn(
        "[modelProfilesStore] model_profiles.list failed:",
        bridgeError.code,
        bridgeError.message,
        bridgeError.details,
      );
      set({
        loading: false,
        loadError: bridgeError,
      });
    }
  };

  return {
    profiles: [],
    hydrated: false,
    loading: false,
    loadError: null,
    mutationError: null,
    testStates: {},
    fetchStates: {},

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

    duplicateProfile: async (id) => {
      set({ mutationError: null });
      try {
        const { profile } = await modelProfilesBridge.duplicate(id);
        set((state) => ({ profiles: [...state.profiles, profile] }));
        return profile;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
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
        useSettingsStore.getState().applyAppFromBridge(app);
        return app;
      } catch (error) {
        set({ mutationError: asBridgeError(error) });
        return null;
      }
    },

    testConnection: async (id) => {
      const requestId = `test-${id}-${Date.now()}`;
      set((state) => ({
        testStates: {
          ...state.testStates,
          [id]: { running: true, result: null, error: null },
        },
      }));
      try {
        const result = await modelProfilesBridge.testConnection(id, requestId);
        set((state) => ({
          testStates: {
            ...state.testStates,
            [id]: { running: false, result, error: null },
          },
        }));
      } catch (error) {
        set((state) => ({
          testStates: {
            ...state.testStates,
            [id]: { running: false, result: null, error: asBridgeError(error) },
          },
        }));
      }
    },

    fetchModelList: async (id) => {
      const requestId = `fetch-${id}-${Date.now()}`;
      set((state) => ({
        fetchStates: {
          ...state.fetchStates,
          [id]: {
            running: true,
            models: state.fetchStates[id]?.models ?? [],
            error: null,
          },
        },
      }));
      try {
        const { models } = await modelProfilesBridge.fetchModelList(
          id,
          requestId,
        );
        set((state) => ({
          fetchStates: {
            ...state.fetchStates,
            [id]: { running: false, models, error: null },
          },
        }));
      } catch (error) {
        set((state) => ({
          fetchStates: {
            ...state.fetchStates,
            [id]: {
              running: false,
              models: state.fetchStates[id]?.models ?? [],
              error: asBridgeError(error),
            },
          },
        }));
      }
    },

    clearTestState: (id) =>
      set((state) => {
        const next = { ...state.testStates };
        delete next[id];
        return { testStates: next };
      }),

    clearFetchState: (id) =>
      set((state) => {
        const next = { ...state.fetchStates };
        delete next[id];
        return { fetchStates: next };
      }),

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
