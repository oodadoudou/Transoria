import { useEffect, useState } from "react";
import { useI18n, useMessages, type Locale } from "@/locales";
import type { AppSettingsPage } from "@/store/useTaskStore";
import { useModuleSettings, useSettingsStore } from "@/store/useSettingsStore";
import {
  appBridge,
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
import styles from "./index.module.css";

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
  const [metaError, setMetaError] = useState<BridgeError | null>(null);

  useEffect(() => {
    let cancelled = false;
    appBridge
      .getMetadata()
      .then((result) => {
        if (!cancelled) setMeta(result);
      })
      .catch((error) => {
        if (cancelled) return;
        if (BridgeError.isBridgeError(error)) setMetaError(error);
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
              label={appSettingsExtra.theme}
              hint={appSettingsExtra.themeHint}
            >
              <Segmented<"light" | "dark" | "system">
                ariaLabel={appSettingsExtra.theme}
                options={[
                  { id: "system", label: appSettingsExtra.themeSystem },
                  { id: "light", label: appSettingsExtra.themeLight },
                  { id: "dark", label: appSettingsExtra.themeDark },
                ]}
                value={draft.theme}
                onChange={(v) => moduleSettings.update("theme", v)}
              />
            </SettingRow>
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
        >
          <SettingRow
            label={appSettingsExtra.pythonRuntime}
            hint={appSettingsExtra.pythonRuntimeHint}
          >
            <span className={styles.metaValue}>{meta.python_version}</span>
          </SettingRow>
          <SettingRow
            label={appSettingsExtra.cacheRoot}
            hint={appSettingsExtra.cacheRootHint}
          >
            <span className={styles.metaPath}>{meta.cache_root}</span>
          </SettingRow>
        </Panel>
      ) : metaError ? (
        <Panel label={appSettingsExtra.aboutLabel}>
          <pre className={styles.error}>{metaError.message}</pre>
        </Panel>
      ) : null}

      <UpdatesPanel />

      <SettingsToolbar
        saveState={moduleSettings.saveState}
        lastError={moduleSettings.lastError}
        onSave={() => {
          void moduleSettings.saveNow();
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
          {checking ? appSettingsExtra.checking : appSettingsExtra.checkForUpdates}
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
