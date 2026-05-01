import { useEffect, useRef } from "react";
import { useMessages } from "@/locales";
import { useSettingsStore } from "@/store/useSettingsStore";
import { useToastStore } from "@/store/useToastStore";
import type { BridgeError, SettingsModule } from "@/bridge";

/**
 * Mount once at app root. Fires a toast on each completed settings
 * save (success or error), centralized so individual settings pages
 * don't each have to wire it up.
 *
 * Toasts only fire on transitions (``lastSavedAt`` advancing or a
 * fresh ``lastError`` instance), so debounced auto-saves trigger at
 * most once per actual disk write.
 */
export function useSettingsSaveToast(): void {
  useModuleSaveToast("app");
  useModuleSaveToast("translation");
  useModuleSaveToast("glossary");
  useModuleSaveToast("replacement");
}

function useModuleSaveToast(module: SettingsModule): void {
  const messages = useMessages();
  const push = useToastStore((state) => state.push);
  const lastSavedAt = useSettingsStore((state) => state[module].lastSavedAt);
  const lastError = useSettingsStore((state) => state[module].lastError);
  const saveState = useSettingsStore((state) => state[module].saveState);
  const seenSavedAt = useRef<string | null>(lastSavedAt);
  const seenError = useRef<BridgeError | null>(lastError);

  useEffect(() => {
    if (
      saveState === "saved" &&
      lastSavedAt &&
      lastSavedAt !== seenSavedAt.current
    ) {
      seenSavedAt.current = lastSavedAt;
      const rejected = useSettingsStore.getState()[module].lastRejectedFields;
      const detail =
        rejected.length > 0
          ? messages.toast.settingsRejectedFields
              .replace("{count}", String(rejected.length))
              .replace("{fields}", rejected.map((r) => r.field).join(", "))
          : undefined;
      push({
        variant: rejected.length > 0 ? "warning" : "success",
        title: messages.toast.settingsSaved,
        detail,
      });
    }
  }, [saveState, lastSavedAt, messages, push, module]);

  useEffect(() => {
    if (
      saveState === "error" &&
      lastError &&
      lastError !== seenError.current
    ) {
      seenError.current = lastError;
      push({
        variant: "error",
        title: messages.toast.settingsSaveFailed,
        detail: lastError.message,
        durationMs: 5000,
      });
    }
  }, [saveState, lastError, messages, push, module]);
}
