import { useI18n, useMessages, type Locale } from "@/locales";
import { useSettingsStore } from "@/store/useSettingsStore";
import type { Route } from "@/store/useTaskStore";
import { Pill } from "./Pill";
import { MoonIcon, PlayIcon, StopIcon, SunIcon } from "./Icon";
import { useTypewriter } from "./useTypewriter";
import styles from "./SubNav.module.css";

interface SubNavProps {
  route: Route;
  category: string;
  pageLabel: string;
  showRunActions: boolean;
  primaryLabel?: string;
  primaryDisabled?: boolean;
  onStop?: () => void;
  onStart?: () => void;
}

/**
 * Single-row top bar. Flat — no border, no card. Brand wordmark on the left,
 * breadcrumb in the middle, run controls (only on Run pages) on the right.
 * Sits on the cream canvas without a visual container of its own.
 */
export function SubNav({
  category,
  pageLabel,
  showRunActions,
  primaryLabel,
  primaryDisabled,
  onStop,
  onStart,
}: SubNavProps) {
  const messages = useMessages();
  const locale = useI18n((state) => state.locale);
  const setLocale = useI18n((state) => state.setLocale);
  const appDraft = useSettingsStore((state) => state.app.draft);
  const altLocale: Locale = locale === "en" ? "zh" : "en";
  const altLabel =
    altLocale === "en"
      ? messages.appSettings.languageEnglish
      : messages.appSettings.languageChinese;
  const colorTheme = appDraft?.color_theme === "dark" ? "dark" : "light";
  const nextColorTheme = colorTheme === "dark" ? "light" : "dark";
  const nextThemeLabel =
    nextColorTheme === "dark"
      ? messages.appSettingsExtra.colorThemeDark
      : messages.appSettingsExtra.colorThemeLight;

  const handleLocaleChange = async () => {
    const previous = locale;
    setLocale(altLocale);
    const store = useSettingsStore.getState();
    if (!store.hydrated && !store.hydrating) {
      await store.hydrate();
    }
    if (!useSettingsStore.getState().app.draft) {
      setLocale(previous);
      return;
    }
    useSettingsStore
      .getState()
      .updateField("app", "interface_language", altLocale);
    await useSettingsStore.getState().saveNow("app");
    if (useSettingsStore.getState().app.lastError) {
      setLocale(previous);
      useSettingsStore
        .getState()
        .updateField("app", "interface_language", previous);
    }
  };
  const handleThemeChange = async () => {
    const store = useSettingsStore.getState();
    if (!store.hydrated && !store.hydrating) {
      await store.hydrate();
    }
    const currentDraft = useSettingsStore.getState().app.draft;
    if (!currentDraft) return;
    const previous =
      currentDraft.color_theme === "dark" ? "dark" : "light";
    const next = previous === "dark" ? "light" : "dark";
    useSettingsStore.getState().updateField("app", "color_theme", next);
    await useSettingsStore.getState().saveNow("app");
    if (useSettingsStore.getState().app.lastError) {
      useSettingsStore
        .getState()
        .updateField("app", "color_theme", previous);
    }
  };

  const wordmark = useTypewriter(messages.brand.name, {
    loops: 1,
    typeMs: 160,
    eraseMs: 90,
    holdFullMs: 1800,
    holdEmptyMs: 380,
  });

  return (
    <header className={styles.bar}>
      <div className={styles.brand}>
        <span className={styles.logo} aria-hidden>
          T
        </span>
        <span className={styles.wordmark} aria-label={messages.brand.name}>
          <span className={styles.wordmarkText}>{wordmark.text}</span>
          {wordmark.done ? null : (
            <span className={styles.wordmarkCursor} aria-hidden>
              |
            </span>
          )}
        </span>
      </div>

      <nav className={styles.crumb} aria-label={messages.topbar.breadcrumb}>
        <span className={styles.module}>{category}</span>
        <span className={styles.sep} aria-hidden>
          ›
        </span>
        <span className={styles.page}>{pageLabel}</span>
      </nav>

      <div className={styles.actions}>
        {showRunActions ? (
          <>
            <Pill
              variant="ghost"
              icon={<StopIcon size={12} />}
              onClick={onStop}
            >
              {messages.topbar.stop}
            </Pill>
            <Pill
              icon={<PlayIcon size={12} />}
              disabled={primaryDisabled}
              onClick={onStart}
            >
              {primaryLabel ?? messages.topbar.start.translation}
            </Pill>
          </>
        ) : (
          <>
            <button
              type="button"
              className={styles.localeLink}
              onClick={() => {
                void handleLocaleChange();
              }}
              aria-label={messages.appSettings.interfaceLanguage}
            >
              {altLabel}
            </button>
            <button
              type="button"
              className={styles.themeToggle}
              onClick={() => {
                void handleThemeChange();
              }}
              aria-label={`${messages.appSettingsExtra.colorTheme}: ${nextThemeLabel}`}
              title={`${messages.appSettingsExtra.colorTheme}: ${nextThemeLabel}`}
            >
              {colorTheme === "dark" ? (
                <SunIcon size={15} aria-hidden />
              ) : (
                <MoonIcon size={15} aria-hidden />
              )}
            </button>
          </>
        )}
      </div>
    </header>
  );
}

export function crumbFor(
  route: Route,
  messages: ReturnType<typeof useMessages>,
): { module: string; page: string } {
  switch (route.module) {
    case "model":
      return {
        module: messages.model.crumb,
        page: messages.pages.model[route.page],
      };
    case "translation":
      return {
        module: messages.translation.crumb,
        page: messages.pages.translation[route.page],
      };
    case "glossary":
      return {
        module: messages.glossary.crumb,
        page: messages.pages.glossary[route.page],
      };
    case "glossary-review":
      return {
        module: messages.glossaryReview.crumb,
        page: messages.pages.glossaryReview[route.page],
      };
    case "general-tools":
      return {
        module: messages.generalTools.crumb,
        page: messages.pages.generalTools[route.page],
      };
    case "app-settings":
      return {
        module: messages.appSettings.crumb,
        page: messages.pages.appSettings[route.page],
      };
  }
}

export function primaryLabelFor(
  route: Route,
  messages: ReturnType<typeof useMessages>,
): string | undefined {
  if (route.module === "translation") return messages.topbar.start.translation;
  if (route.module === "glossary") return messages.topbar.start.extraction;
  if (route.module === "glossary-review") return messages.topbar.start.extraction;
  return undefined;
}
