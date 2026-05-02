import { useEffect, useState } from "react";

import { BridgeError, updatesBridge, type UpdateCheckResult } from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";
import { useRuntimeStore } from "@/store/useRuntimeStore";

const STARTUP_DELAY_MS = 2500;

interface UpdatePromptApi {
  result: UpdateCheckResult | null;
  dismiss: () => void;
  goToReleasePage: () => void;
}

/** Startup-only update probe: 2.5s after first hydration, asks the
 * backend whether GitHub has a newer tag than the running build. The
 * modal renders only when (newer && not previously skipped && no
 * active task), and "Update now" / "Later" both persist
 * ``skipped_update_version`` so we don't re-nag for the same release. */
export function useUpdatePrompt(): UpdatePromptApi {
  const hydrated = useSettingsStore((state) => state.hydrated);
  const skipped = useSettingsStore(
    (state) => state.app.draft?.skipped_update_version ?? "",
  );
  const updateField = useSettingsStore((state) => state.updateField);
  const translationActive = useRuntimeStore(
    (state) => state.translation.activeTaskId,
  );
  const glossaryActive = useRuntimeStore(
    (state) => state.glossary.activeTaskId,
  );
  const replacementActive = useRuntimeStore(
    (state) => state.replacement.activeTaskId,
  );

  const [result, setResult] = useState<UpdateCheckResult | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (!hydrated || checked) return;
    const anyTaskRunning =
      translationActive !== null ||
      glossaryActive !== null ||
      replacementActive !== null;
    if (anyTaskRunning) {
      // A user resuming the app mid-task should not be interrupted.
      // The next cold start without a live task will re-attempt.
      return;
    }
    let cancelled = false;
    const handle = setTimeout(() => {
      void (async () => {
        try {
          const requestId = `update-prompt-${Date.now()}`;
          const data = await updatesBridge.checkLatest(requestId, "stable");
          if (cancelled) return;
          setChecked(true);
          if (
            data.is_newer_available &&
            data.latest_version &&
            data.latest_version !== skipped
          ) {
            setResult(data);
          }
        } catch (error) {
          // Network failures are silent — the user has not asked for
          // this check, so a toast would be noise. Next launch retries.
          if (!BridgeError.isBridgeError(error)) {
            // eslint-disable-next-line no-console
            console.warn("update check failed", error);
          }
          if (!cancelled) setChecked(true);
        }
      })();
    }, STARTUP_DELAY_MS);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [
    hydrated,
    checked,
    skipped,
    translationActive,
    glossaryActive,
    replacementActive,
  ]);

  const persistSkip = (latest: string): void => {
    if (!latest) return;
    updateField("app", "skipped_update_version", latest);
  };

  return {
    result,
    dismiss: () => {
      if (result) persistSkip(result.latest_version);
      setResult(null);
    },
    goToReleasePage: () => {
      if (!result) return;
      const url = result.release_url;
      persistSkip(result.latest_version);
      setResult(null);
      if (url) {
        void updatesBridge.openReleasePage(url).catch(() => {
          // Already closed; if the open fails the user can still hit
          // GitHub manually. Silent by design.
        });
      }
    },
  };
}
