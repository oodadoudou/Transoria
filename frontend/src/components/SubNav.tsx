import { useI18n, useMessages, type Locale } from "@/locales";
import type { Route } from "@/store/useTaskStore";
import { Pill } from "./Pill";
import { PlayIcon, StopIcon } from "./Icon";
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
  const altLocale: Locale = locale === "en" ? "zh" : "en";
  const altLabel =
    altLocale === "en"
      ? messages.appSettings.languageEnglish
      : messages.appSettings.languageChinese;

  return (
    <header className={styles.bar}>
      <div className={styles.brand}>
        <span className={styles.logo} aria-hidden>
          T
        </span>
        <span className={styles.wordmark}>{messages.brand.name}</span>
      </div>

      <nav className={styles.crumb} aria-label="breadcrumb">
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
          <button
            type="button"
            className={styles.localeLink}
            onClick={() => setLocale(altLocale)}
            aria-label={messages.appSettings.interfaceLanguage}
          >
            {altLabel}
          </button>
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
  return undefined;
}
