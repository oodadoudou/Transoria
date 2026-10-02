import { lazy, Suspense, useEffect, useState } from "react";
import { Maximize } from "lucide-react";
import { nativeEditor } from "@/bridge/native";
import { useI18n, useMessages } from "@/locales";
import { useSettingsStore } from "@/store/useSettingsStore";
import styles from "./EpubToolsPage.module.css";

const Content = lazy(() => import("./EpubContentPage").then((module) => ({ default: module.EpubContentPage })));

export function EpubEditorWindow() {
  const messages = useMessages();
  const hydrate = useSettingsStore((state) => state.hydrate);
  const hydrated = useSettingsStore((state) => state.hydrated);
  const settings = useSettingsStore((state) => state.app.draft);
  const setLocale = useI18n((state) => state.setLocale);
  const [error, setError] = useState("");
  const initialPath = new URLSearchParams(window.location.search).get("path") ?? "";
  useEffect(() => { void hydrate(); }, [hydrate]);
  useEffect(() => {
    if (settings?.interface_language) setLocale(settings.interface_language);
    document.documentElement.dataset.theme = settings?.color_theme ?? "light";
    document.documentElement.classList.add("transoria-epub-editor-open");
    return () => document.documentElement.classList.remove("transoria-epub-editor-open");
  }, [settings?.interface_language, settings?.color_theme, setLocale]);
  const operate = (action: () => Promise<void>) => { void action().catch((cause) => setError(String(cause))); };
  return <section className={`${styles.dialog} ${styles.contentDialog} ${styles.standalone}`}>
    <header className={styles.dialogHeader}>
      <h2>{messages.generalTools.epubContent.title}</h2>
      <button className={styles.windowAction} type="button" title={messages.epubContentTool.toggleFullscreen} aria-label={messages.epubContentTool.toggleFullscreen} onClick={() => operate(nativeEditor.toggleFullscreen)}><Maximize size={16} /></button>
    </header>
    {error ? <div role="alert">{error}</div> : null}
    <div className={styles.contentBody}>{hydrated ? <Suspense fallback={null}><Content initialPath={initialPath} onClose={() => operate(nativeEditor.close)} /></Suspense> : null}</div>
  </section>;
}
