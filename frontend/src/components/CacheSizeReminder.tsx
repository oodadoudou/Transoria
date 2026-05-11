import { useEffect, useState } from "react";

import { tasksBridge } from "@/bridge";
import { format, useMessages } from "@/locales";
import { useSettingsStore } from "@/store/useSettingsStore";
import { useTaskStore } from "@/store/useTaskStore";

import modalStyles from "./GlossaryExportModal.module.css";

const CACHE_REMINDER_THRESHOLD_BYTES = 500 * 1024 * 1024;
const DISMISSED_KEY = "transoria.cache-size-reminder.dismissed";

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function CacheSizeReminder() {
  const labels = useMessages().appSettingsExtra;
  const hydrated = useSettingsStore((state) => state.hydrated);
  const navigate = useTaskStore((state) => state.navigate);
  const [cacheBytes, setCacheBytes] = useState<number | null>(null);

  useEffect(() => {
    if (!hydrated) return;
    if (window.sessionStorage.getItem(DISMISSED_KEY) === "1") return;

    let cancelled = false;
    void tasksBridge
      .summarizeCaches()
      .then((summary) => {
        if (cancelled) return;
        if (summary.total_bytes >= CACHE_REMINDER_THRESHOLD_BYTES) {
          setCacheBytes(summary.total_bytes);
        }
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [hydrated]);

  const dismiss = () => {
    window.sessionStorage.setItem(DISMISSED_KEY, "1");
    setCacheBytes(null);
  };

  if (cacheBytes === null) return null;

  return (
    <div className={modalStyles.overlay} role="presentation">
      <section
        aria-labelledby="cache-size-reminder-title"
        aria-modal="true"
        className={modalStyles.modal}
        role="alertdialog"
      >
        <header className={modalStyles.header}>
          <h2 className={modalStyles.title} id="cache-size-reminder-title">
            {labels.cacheLargeTitle}
          </h2>
          <button
            aria-label={labels.cacheLargeLater}
            className={modalStyles.close}
            onClick={dismiss}
            type="button"
          >
            ×
          </button>
        </header>
        <div className={modalStyles.body}>
          <p className={modalStyles.hint}>
            {format(labels.cacheLargeBody, { size: formatBytes(cacheBytes) })}
          </p>
          <div className={modalStyles.choices}>
            <button
              className={modalStyles.choice}
              onClick={() => {
                dismiss();
                navigate({ module: "app-settings", page: "general" });
              }}
              type="button"
            >
              <span className={modalStyles.choiceText}>
                <span className={modalStyles.choiceLabel}>
                  {labels.cacheLargeOpenSettings}
                </span>
              </span>
            </button>
          </div>
        </div>
        <footer className={modalStyles.footer}>
          <button className={modalStyles.cancel} onClick={dismiss} type="button">
            {labels.cacheLargeLater}
          </button>
        </footer>
      </section>
    </div>
  );
}
