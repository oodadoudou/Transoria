import { useEffect, useState } from "react";
import { format, useI18n, useMessages, type Locale } from "@/locales";
import type { AppSettingsPage } from "@/store/useTaskStore";
import { useRuntimeStore } from "@/store/useRuntimeStore";
import { useModuleSettings, useSettingsStore } from "@/store/useSettingsStore";
import {
  appBridge,
  dialogsBridge,
  tasksBridge,
  updatesBridge,
  BridgeError,
  type AppMetadata,
  type UpdateCheckResult,
} from "@/bridge";
import { Panel } from "@/components/Panel";
import { Pill } from "@/components/Pill";
import { NumberField } from "@/components/NumberField";
import { TextField } from "@/components/TextField";
import { Segmented } from "@/components/Segmented";
import { SettingsToolbar } from "@/components/SettingsToolbar";
import modalStyles from "@/components/GlossaryExportModal.module.css";
import styles from "./index.module.css";

const CACHE_BLOCK_STATUSES = new Set(["pending", "running", "stopping", "pausing"]);

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const kb = bytes / 1024;
  if (kb < 1024) return `${kb.toFixed(1)} KB`;
  const mb = kb / 1024;
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

interface AppSettingsModuleProps {
  page: AppSettingsPage;
}

export function AppSettingsModule({ page: _page }: AppSettingsModuleProps) {
  const messages = useMessages();
  const { appSettingsExtra } = messages;
  const locale = useI18n((state) => state.locale);
  const setLocale = useI18n((state) => state.setLocale);
  const moduleSettings = useModuleSettings("app");
  const draft = moduleSettings.draft;

  const [meta, setMeta] = useState<AppMetadata | null>(null);

  useEffect(() => {
    let cancelled = false;
    appBridge
      .getMetadata()
      .then((result) => {
        if (!cancelled) setMeta(result);
      })
      .catch(() => {
        // Silently ignore — About panel only renders when connected
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const interfaceLanguage = draft?.interface_language ?? locale;

  const handleLanguageChange = async (next: Locale) => {
    const previous = locale;
    setLocale(next);
    moduleSettings.update("interface_language", next);
    try {
      await moduleSettings.saveNow();
    } catch {
      // saveNow itself doesn't throw; failure is captured in
      // moduleSettings.lastError. Watch for it and revert.
    }
    const post = useSettingsStore.getState().app;
    if (post.lastError) {
      // Backend rejected the change — restore the UI and draft to the persisted value.
      setLocale(previous);
      moduleSettings.update("interface_language", previous);
    }
  };

  return (
    <>
      <Panel
        title={messages.appSettings.title}
        subtitle={messages.appSettings.sub}
      >
        <SettingRow
          label={messages.appSettings.interfaceLanguage}
          hint={messages.appSettings.interfaceLanguageHint}
        >
          <Segmented<Locale>
            ariaLabel={messages.appSettings.interfaceLanguage}
            options={[
              { id: "en", label: messages.appSettings.languageEnglish },
              { id: "zh", label: messages.appSettings.languageChinese },
            ]}
            value={interfaceLanguage as Locale}
            onChange={(v) => {
              void handleLanguageChange(v);
            }}
          />
        </SettingRow>
        {draft ? (
          <>
            <SettingRow
              label={appSettingsExtra.uiScale}
              hint={appSettingsExtra.uiScaleHint}
            >
              <NumberField
                label=""
                value={draft.ui_scale}
                onChange={(v) => moduleSettings.update("ui_scale", v)}
                min={0.85}
                max={1.5}
              />
            </SettingRow>
            <SettingRow
              label={appSettingsExtra.proxyUrl}
              hint={appSettingsExtra.proxyUrlHint}
            >
              <TextField
                label=""
                value={draft.proxy_url}
                onChange={(v) => moduleSettings.update("proxy_url", v)}
                placeholder="http://127.0.0.1:7890"
                mono
              />
            </SettingRow>
            <SettingRow
              label={appSettingsExtra.taskSoundNotifications}
              hint={appSettingsExtra.taskSoundNotificationsHint}
            >
              <Segmented<"on" | "off">
                ariaLabel={appSettingsExtra.taskSoundNotifications}
                options={[
                  { id: "on", label: appSettingsExtra.taskSoundOn },
                  { id: "off", label: appSettingsExtra.taskSoundOff },
                ]}
                value={draft.task_sound_notifications ? "on" : "off"}
                onChange={(v) =>
                  moduleSettings.update(
                    "task_sound_notifications",
                    v === "on",
                  )
                }
              />
            </SettingRow>
          </>
        ) : null}
      </Panel>

      {meta ? (
        <Panel
          label={appSettingsExtra.aboutLabel}
          labelExtra={
            <span>
              {meta.app_version} · {meta.platform} · {meta.build_mode}
            </span>
          }
        />
      ) : null}

      <CachePanel />

      <UpdatesPanel />

      <SettingsToolbar
        saveState={moduleSettings.saveState}
        lastError={moduleSettings.lastError}
        onSave={() => {
          void moduleSettings.saveNow({ explicit: true });
        }}
        onReset={() => {
          void moduleSettings.reset();
        }}
      />
    </>
  );
}

function UpdatesPanel() {
  const messages = useMessages();
  const { appSettingsExtra } = messages;
  const [result, setResult] = useState<UpdateCheckResult | null>(null);
  const [error, setError] = useState<BridgeError | null>(null);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const handleCheck = async () => {
    setChecking(true);
    setError(null);
    setSavedPath(null);
    try {
      const next = await updatesBridge.checkLatest(
        `update-${Date.now().toString(36)}`,
      );
      setResult(next);
    } catch (err) {
      if (BridgeError.isBridgeError(err)) setError(err);
      else throw err;
    } finally {
      setChecking(false);
    }
  };

  const handleOpen = async () => {
    if (!result?.release_url) return;
    setError(null);
    try {
      await updatesBridge.openReleasePage(result.release_url);
    } catch (err) {
      if (BridgeError.isBridgeError(err)) setError(err);
      else throw err;
    }
  };

  const handleDownload = async () => {
    if (!result?.asset) return;
    setError(null);
    setSavedPath(null);
    try {
      const { saved_path } = await updatesBridge.downloadAsset(
        `download-${Date.now().toString(36)}`,
        result.asset.download_url,
        result.asset.name,
      );
      setSavedPath(saved_path);
    } catch (err) {
      if (BridgeError.isBridgeError(err)) setError(err);
      else throw err;
    }
  };

  return (
    <Panel
      label={appSettingsExtra.updatesLabel}
      labelExtra={
        <Pill variant="ghost" onClick={handleCheck} disabled={checking}>
          {checking
            ? appSettingsExtra.checking
            : appSettingsExtra.checkForUpdates}
        </Pill>
      }
    >
      {result ? (
        <div className={styles.updates}>
          <div>
            <b>{appSettingsExtra.currentLabel}:</b> {result.current_version}
          </div>
          <div>
            <b>{appSettingsExtra.latestLabel}:</b> {result.latest_version}
          </div>
          {result.is_newer_available ? (
            <>
              <pre className={styles.notes}>
                {result.release_notes_markdown}
              </pre>
              <div className={styles.updateActions}>
                <Pill variant="ghost" onClick={handleOpen}>
                  {appSettingsExtra.openReleasePage}
                </Pill>
                {result.asset ? (
                  <Pill onClick={handleDownload}>
                    {appSettingsExtra.download} {result.asset.name}
                  </Pill>
                ) : null}
              </div>
            </>
          ) : (
            <div className={styles.upToDate}>{appSettingsExtra.upToDate}</div>
          )}
        </div>
      ) : null}
      {savedPath ? (
        <div className={styles.savedPath}>
          {appSettingsExtra.savedTo} <code>{savedPath}</code>
        </div>
      ) : null}
      {error ? (
        <pre className={styles.error}>
          <code>{error.code}</code> {error.message}
        </pre>
      ) : null}
    </Panel>
  );
}

function CachePanel() {
  const messages = useMessages();
  const labels = messages.appSettingsExtra;
  const [summary, setSummary] = useState<{
    task_count: number;
    total_bytes: number;
    cache_root: string;
  } | null>(null);
  const [open, setOpen] = useState(false);
  const [resultText, setResultText] = useState<string | null>(null);
  const [error, setError] = useState<BridgeError | null>(null);
  const cleanupBlocked = useRuntimeStore((state) =>
    (["translation", "glossary", "glossary_review", "replacement"] as const).some(
      (kind) => {
        const status = state[kind].snapshot?.header.status ?? state[kind].header?.status;
        return status ? CACHE_BLOCK_STATUSES.has(status) : false;
      },
    ),
  );

  const refresh = async () => {
    try {
      const next = await tasksBridge.summarizeCaches();
      setSummary({
        task_count: next.task_count,
        total_bytes: next.total_bytes,
        cache_root: next.cache_root,
      });
    } catch (err) {
      if (BridgeError.isBridgeError(err)) setError(err);
    }
  };

  const handleOpenCacheRoot = async () => {
    if (!summary?.cache_root) return;
    try {
      await dialogsBridge.openDirectory(summary.cache_root);
    } catch (err) {
      if (BridgeError.isBridgeError(err)) setError(err);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const summaryText =
    summary === null
      ? ""
      : summary.task_count === 0
        ? labels.cacheSummaryEmpty
        : format(labels.cacheSummary, {
            count: summary.task_count,
            size: formatBytes(summary.total_bytes),
          });

  return (
    <Panel
      label={labels.cacheLabel}
      labelExtra={
        <Pill
          variant="ghost"
          onClick={() => setOpen(true)}
          disabled={cleanupBlocked}
          title={cleanupBlocked ? labels.cacheRunningBlock : undefined}
        >
          {labels.cacheManageAction}
        </Pill>
      }
    >
      <div className={styles.row}>
        <div className={styles.rowText}>
          <div className={styles.rowHint}>{labels.cacheHint}</div>
          {summary?.cache_root ? (
            <div className={styles.rowHint} style={{ marginTop: 8 }}>
              <code style={{ fontSize: "11.5px", wordBreak: "break-all" }}>
                {summary.cache_root}
              </code>
            </div>
          ) : null}
          {summaryText ? (
            <div className={styles.rowHint} style={{ marginTop: 8 }}>
              {summaryText}
            </div>
          ) : null}
          {cleanupBlocked ? (
            <div className={styles.rowHint} style={{ marginTop: 8 }}>
              {labels.cacheRunningBlock}
            </div>
          ) : null}
          {resultText ? (
            <div className={styles.rowHint} style={{ marginTop: 8 }}>
              {resultText}
            </div>
          ) : null}
        </div>
        {summary?.cache_root ? (
          <div className={styles.rowControl}>
            <Pill variant="ghost" onClick={() => void handleOpenCacheRoot()}>
              {labels.cacheOpenAction}
            </Pill>
          </div>
        ) : null}
      </div>
      {error ? (
        <pre className={styles.error}>
          <code>{error.code}</code> {error.message}
        </pre>
      ) : null}
      {open ? (
        <CacheCleanupModal
          cleanupBlocked={cleanupBlocked}
          onClose={() => setOpen(false)}
          onPurged={(text) => {
            setResultText(text);
            void refresh();
          }}
        />
      ) : null}
    </Panel>
  );
}

interface CacheCleanupModalProps {
  cleanupBlocked: boolean;
  onClose: () => void;
  onPurged: (text: string) => void;
}

function CacheCleanupModal({
  cleanupBlocked,
  onClose,
  onPurged,
}: CacheCleanupModalProps) {
  const labels = useMessages().appSettingsExtra;
  const [confirmingAll, setConfirmingAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<BridgeError | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const purge = async (scope: "all" | "older_than_days", days?: number) => {
    if (cleanupBlocked) return;
    setBusy(true);
    setError(null);
    try {
      const result = await tasksBridge.purgeCaches(scope, days);
      const main = format(labels.cachePurgeResult, {
        count: result.removed_count,
      });
      const skipped =
        result.skipped_active_count > 0
          ? ` ${format(labels.cachePurgeSkipped, {
              count: result.skipped_active_count,
            })}`
          : "";
      onPurged(`${main}${skipped}`);
      onClose();
    } catch (err) {
      if (BridgeError.isBridgeError(err)) setError(err);
      else throw err;
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={modalStyles.overlay}
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div className={modalStyles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={modalStyles.header}>
          <h2 className={modalStyles.title}>{labels.cacheModalTitle}</h2>
          <button
            type="button"
            className={modalStyles.close}
            onClick={onClose}
            aria-label={labels.cacheModalClose}
          >
            ×
          </button>
        </div>
        <div className={modalStyles.body}>
          <p className={modalStyles.hint}>
            {cleanupBlocked
              ? labels.cacheRunningBlock
              : confirmingAll
                ? labels.cachePurgeAllConfirm
                : labels.cacheModalHint}
          </p>
          {confirmingAll ? (
            <div className={modalStyles.choices}>
              <button
                type="button"
                className={modalStyles.choice}
                disabled={busy || cleanupBlocked}
                onClick={() => void purge("all")}
              >
                <span className={modalStyles.choiceText}>
                  <span className={modalStyles.choiceLabel}>
                    {labels.cachePurgeAllConfirmYes}
                  </span>
                </span>
              </button>
            </div>
          ) : (
            <div className={modalStyles.choices}>
              <button
                type="button"
                className={modalStyles.choice}
                disabled={busy || cleanupBlocked}
                onClick={() => setConfirmingAll(true)}
              >
                <span className={modalStyles.choiceText}>
                  <span className={modalStyles.choiceLabel}>
                    {labels.cachePurgeAll}
                  </span>
                  <span className={modalStyles.choiceHint}>
                    {labels.cachePurgeAllHint}
                  </span>
                </span>
              </button>
              <button
                type="button"
                className={modalStyles.choice}
                disabled={busy || cleanupBlocked}
                onClick={() => void purge("older_than_days", 30)}
              >
                <span className={modalStyles.choiceText}>
                  <span className={modalStyles.choiceLabel}>
                    {labels.cachePurgeMonth}
                  </span>
                  <span className={modalStyles.choiceHint}>
                    {labels.cachePurgeMonthHint}
                  </span>
                </span>
              </button>
              <button
                type="button"
                className={modalStyles.choice}
                disabled={busy || cleanupBlocked}
                onClick={() => void purge("older_than_days", 7)}
              >
                <span className={modalStyles.choiceText}>
                  <span className={modalStyles.choiceLabel}>
                    {labels.cachePurgeWeek}
                  </span>
                  <span className={modalStyles.choiceHint}>
                    {labels.cachePurgeWeekHint}
                  </span>
                </span>
              </button>
            </div>
          )}
          {error ? (
            <pre className={styles.error}>
              <code>{error.code}</code> {error.message}
            </pre>
          ) : null}
        </div>
        <div className={modalStyles.footer}>
          <button
            type="button"
            className={modalStyles.cancel}
            onClick={() => {
              if (confirmingAll) setConfirmingAll(false);
              else onClose();
            }}
          >
            {confirmingAll
              ? labels.cachePurgeAllConfirmNo
              : labels.cacheModalClose}
          </button>
        </div>
      </div>
    </div>
  );
}

interface SettingRowProps {
  label: string;
  hint?: string;
  children: React.ReactNode;
}

function SettingRow({ label, hint, children }: SettingRowProps) {
  return (
    <div className={styles.row}>
      <div className={styles.rowText}>
        <div className={styles.rowLabel}>{label}</div>
        {hint ? <div className={styles.rowHint}>{hint}</div> : null}
      </div>
      <div className={styles.rowControl}>{children}</div>
    </div>
  );
}
