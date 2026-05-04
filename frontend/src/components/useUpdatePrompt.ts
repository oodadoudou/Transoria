import { useEffect, useState } from "react";

import {
  BridgeError,
  appBridge,
  updatesBridge,
  type AppMetadata,
  type UpdateCheckResult,
} from "@/bridge";
import { useSettingsStore } from "@/store/useSettingsStore";
import { useRuntimeStore } from "@/store/useRuntimeStore";

const STARTUP_DELAY_MS = 2500;

export type AutoUpdateState = "idle" | "preparing" | "ready" | "error";

interface UpdatePromptApi {
  result: UpdateCheckResult | null;
  canAutoUpdate: boolean;
  autoUpdateState: AutoUpdateState;
  autoUpdateError: string | null;
  shutdownInSeconds: number | null;
  dismiss: () => void;
  goToReleasePage: () => void;
  applyAutoUpdate: () => Promise<void>;
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
  const saveNow = useSettingsStore((state) => state.saveNow);
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
  const [metadata, setMetadata] = useState<AppMetadata | null>(null);
  const [autoUpdateState, setAutoUpdateState] =
    useState<AutoUpdateState>("idle");
  const [autoUpdateError, setAutoUpdateError] = useState<string | null>(null);
  const [shutdownInSeconds, setShutdownInSeconds] = useState<number | null>(
    null,
  );

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
          // Fetch metadata in parallel so the modal can render the
          // right button (auto-update vs open-release-page) the moment
          // it appears, without a second round-trip.
          const [data, meta] = await Promise.all([
            updatesBridge.checkLatest(requestId, "stable"),
            appBridge.getMetadata().catch(() => null),
          ]);
          if (cancelled) return;
          setChecked(true);
          if (meta) setMetadata(meta);
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

  // Tick the shutdown countdown so the modal can show "App will
  // close in N seconds" while the backend's daemon thread is sleeping
  // before tearing down the webview.
  useEffect(() => {
    if (shutdownInSeconds === null || shutdownInSeconds <= 0) return;
    const handle = window.setTimeout(() => {
      setShutdownInSeconds((current) =>
        current === null ? null : Math.max(0, current - 1),
      );
    }, 1000);
    return () => window.clearTimeout(handle);
  }, [shutdownInSeconds]);

  const persistSkip = async (latest: string): Promise<void> => {
    if (!latest) return;
    updateField("app", "skipped_update_version", latest);
    await saveNow("app");
  };

  const canAutoUpdate =
    metadata?.platform === "win32" &&
    metadata?.build_mode === "packaged" &&
    !!result?.asset &&
    result.asset.platform === "win32" &&
    !!result.asset.download_url;

  return {
    result,
    canAutoUpdate,
    autoUpdateState,
    autoUpdateError,
    shutdownInSeconds,
    dismiss: () => {
      if (autoUpdateState === "preparing" || autoUpdateState === "ready") {
        // Mid-flight or already-staged: the App is about to close;
        // the user dismissing the modal must not abort the shutdown.
        return;
      }
      if (result) void persistSkip(result.latest_version);
      setResult(null);
    },
    goToReleasePage: () => {
      if (!result) return;
      const url = result.release_url;
      void persistSkip(result.latest_version);
      setResult(null);
      if (url) {
        void updatesBridge.openReleasePage(url).catch(() => {
          // Already closed; if the open fails the user can still hit
          // GitHub manually. Silent by design.
        });
      }
    },
    applyAutoUpdate: async () => {
      if (!canAutoUpdate || !result?.asset) return;
      if (autoUpdateState === "preparing" || autoUpdateState === "ready") {
        return;
      }
      setAutoUpdateState("preparing");
      setAutoUpdateError(null);
      try {
        const response = await updatesBridge.applyUpdateWindows(
          result.asset.download_url,
          result.asset.name,
          result.latest_version,
        );
        // Persist skip BEFORE the App tears down so the next launch
        // (after manual relaunch) doesn't re-prompt for the same
        // version we just installed.
        await persistSkip(result.latest_version);
        setShutdownInSeconds(
          Math.max(1, Math.floor(response.shutdown_in_seconds || 2)),
        );
        setAutoUpdateState("ready");
      } catch (error) {
        const message = BridgeError.isBridgeError(error)
          ? error.message
          : error instanceof Error
            ? error.message
            : String(error);
        setAutoUpdateError(message);
        setAutoUpdateState("error");
      }
    },
  };
}
