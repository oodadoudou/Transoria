import { useEffect, useState } from "react";
import { useI18n, useMessages } from "./locales";
import { isRunPage, useTaskStore, type Route } from "./store/useTaskStore";
import { useSettingsStore } from "./store/useSettingsStore";
import { useModelProfilesStore } from "./store/useModelProfilesStore";
import { SubNav, crumbFor } from "./components/SubNav";
import { Rail } from "./components/Rail";
import { StatusBar } from "./components/StatusBar";
import { CacheSizeReminder } from "./components/CacheSizeReminder";
import { ModelModule } from "./pages/model";
import { TranslationModule } from "./pages/translation";
import { GlossaryModule } from "./pages/glossary";
import { GlossaryReviewModule } from "./pages/glossary-review";
import { GeneralToolsModule } from "./pages/general-tools";
import { AppSettingsModule } from "./pages/app-settings";
import { AllKeysFailedDialog } from "./components/AllKeysFailedDialog";
import { ToastHost } from "./components/ToastHost";
import { UpdateAvailableModal } from "./components/UpdateAvailableModal";
import { useSettingsSaveToast } from "./components/useSettingsSaveToast";
import { useUpdatePrompt } from "./components/useUpdatePrompt";
import {
  FirstRunOnboardingModal,
  needsFirstRunOnboarding,
} from "./components/FirstRunOnboardingModal";
import styles from "./App.module.css";

let firstRunOnboardingDismissed = false;

export function App() {
  const messages = useMessages();
  const locale = useI18n((state) => state.locale);
  const setLocale = useI18n((state) => state.setLocale);
  const route = useTaskStore((state) => state.route);
  const hydrateSettings = useSettingsStore((state) => state.hydrate);
  const settingsHydrated = useSettingsStore((state) => state.hydrated);
  const appSettings = useSettingsStore((state) => state.app.draft);
  const hydrateProfiles = useModelProfilesStore((state) => state.hydrate);
  const profileHydrated = useModelProfilesStore((state) => state.hydrated);
  const profileLoading = useModelProfilesStore((state) => state.loading);
  const profiles = useModelProfilesStore((state) => state.profiles);
  const interfaceLanguage = useSettingsStore(
    (state) => state.app.draft?.interface_language,
  );
  const colorTheme = useSettingsStore(
    (state) => state.app.draft?.color_theme ?? "light",
  );
  useSettingsSaveToast();
  const updatePrompt = useUpdatePrompt();
  const [onboardingDismissed, setOnboardingDismissed] = useState(
    firstRunOnboardingDismissed,
  );

  useEffect(() => {
    void hydrateSettings();
  }, [hydrateSettings]);

  useEffect(() => {
    void hydrateProfiles();
  }, [hydrateProfiles]);

  useEffect(() => {
    if (interfaceLanguage && interfaceLanguage !== locale) {
      setLocale(interfaceLanguage);
    }
  }, [interfaceLanguage, locale, setLocale]);

  useEffect(() => {
    document.documentElement.dataset.theme =
      colorTheme === "dark" ? "dark" : "light";
  }, [colorTheme]);

  const onRunPage = isRunPage(route);
  const crumb = crumbFor(route, messages);
  const showFirstRunOnboarding =
    !onboardingDismissed &&
    settingsHydrated &&
    profileHydrated &&
    !profileLoading &&
    needsFirstRunOnboarding(profiles, appSettings);

  const shellClass = `${styles.app} ${onRunPage ? styles.withInspector : styles.minimal}`;

  return (
    <div className={styles.shell}>
      <main className={shellClass}>
        <SubNav
          route={route}
          category={crumb.module}
          pageLabel={crumb.page}
          showRunActions={false}
        />
        <Rail />
        <section className={styles.main}>
          <PageBody route={route} />
        </section>
        {onRunPage ? <StatusBar /> : null}
      </main>
      <AllKeysFailedDialog />
      {updatePrompt.result ? (
        <UpdateAvailableModal
          result={updatePrompt.result}
          canAutoUpdate={updatePrompt.canAutoUpdate}
          autoUpdateState={updatePrompt.autoUpdateState}
          autoUpdateError={updatePrompt.autoUpdateError}
          shutdownInSeconds={updatePrompt.shutdownInSeconds}
          onDismiss={updatePrompt.dismiss}
          onUpdateNow={updatePrompt.goToReleasePage}
          onAutoUpdate={() => {
            void updatePrompt.applyAutoUpdate();
          }}
        />
      ) : (
        <CacheSizeReminder />
      )}
      {showFirstRunOnboarding ? (
        <FirstRunOnboardingModal
          onDone={() => {
            firstRunOnboardingDismissed = true;
            setOnboardingDismissed(true);
          }}
          onSkip={() => {
            firstRunOnboardingDismissed = true;
            setOnboardingDismissed(true);
          }}
        />
      ) : null}
      <ToastHost />
    </div>
  );
}

function PageBody({ route }: { route: Route }) {
  switch (route.module) {
    case "model":
      return <ModelModule />;
    case "translation":
      return <TranslationModule page={route.page} />;
    case "glossary":
      return <GlossaryModule page={route.page} />;
    case "glossary-review":
      return <GlossaryReviewModule page={route.page} />;
    case "general-tools":
      return <GeneralToolsModule page={route.page} />;
    case "app-settings":
      return <AppSettingsModule page={route.page} />;
  }
}
