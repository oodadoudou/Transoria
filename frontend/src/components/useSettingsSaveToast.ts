import { useEffect, useRef } from "react";
import { useMessages } from "@/locales";
import { useSettingsStore } from "@/store/useSettingsStore";
import { useToastStore } from "@/store/useToastStore";
import type { BridgeError, SettingsModule } from "@/bridge";

/**
 * Mount once at app root. Settings persistence runs on a debounced
 * auto-save the user does not initiate, so a "saved" toast on every
 * keystroke would be noise. The success toast fires only when the
 * caller flagged the save as ``explicit: true`` (the manual 保存
 * button). Errors still surface for any save so the user knows their
 * change didn't land.
 */
export function useSettingsSaveToast(): void {
  useModuleSaveToast("app");
  useModuleSaveToast("translation");
  useModuleSaveToast("glossary");
  useModuleSaveToast("glossary_review");
  useModuleSaveToast("replacement");
}

function useModuleSaveToast(module: SettingsModule): void {
  const messages = useMessages();
  const push = useToastStore((state) => state.push);
  const lastSavedAt = useSettingsStore((state) => state[module].lastSavedAt);
  const lastExplicitSavedAt = useSettingsStore(
    (state) => state[module].lastExplicitSavedAt,
  );
  const lastError = useSettingsStore((state) => state[module].lastError);
  const saveState = useSettingsStore((state) => state[module].saveState);
  const seenExplicitAt = useRef<string | null>(lastExplicitSavedAt);
  const seenError = useRef<BridgeError | null>(lastError);

  useEffect(() => {
    if (
      saveState === "saved" &&
      lastExplicitSavedAt &&
      lastExplicitSavedAt === lastSavedAt &&
      lastExplicitSavedAt !== seenExplicitAt.current
    ) {
      seenExplicitAt.current = lastExplicitSavedAt;
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
  }, [saveState, lastSavedAt, lastExplicitSavedAt, messages, push, module]);

  useEffect(() => {
    if (saveState === "error" && lastError && lastError !== seenError.current) {
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
