import { useEffect } from "react";
import { useI18n, useMessages } from "./locales";
import { isRunPage, useTaskStore, type Route } from "./store/useTaskStore";
import { useSettingsStore } from "./store/useSettingsStore";
import { SubNav, crumbFor } from "./components/SubNav";
import { Rail } from "./components/Rail";
import { StatusBar } from "./components/StatusBar";
import { ModelModule } from "./pages/model";
import { TranslationModule } from "./pages/translation";
import { GlossaryModule } from "./pages/glossary";
import { GeneralToolsModule } from "./pages/general-tools";
import { AppSettingsModule } from "./pages/app-settings";
import { AllKeysFailedDialog } from "./components/AllKeysFailedDialog";
import { ToastHost } from "./components/ToastHost";
import { useSettingsSaveToast } from "./components/useSettingsSaveToast";
import styles from "./App.module.css";

export function App() {
  const messages = useMessages();
  const locale = useI18n((state) => state.locale);
  const setLocale = useI18n((state) => state.setLocale);
  const route = useTaskStore((state) => state.route);
  const hydrateSettings = useSettingsStore((state) => state.hydrate);
  const interfaceLanguage = useSettingsStore(
    (state) => state.app.draft?.interface_language,
  );
  useSettingsSaveToast();

  useEffect(() => {
    void hydrateSettings();
  }, [hydrateSettings]);

  useEffect(() => {
    if (interfaceLanguage && interfaceLanguage !== locale) {
      setLocale(interfaceLanguage);
    }
  }, [interfaceLanguage, locale, setLocale]);

  const onRunPage = isRunPage(route);
  const crumb = crumbFor(route, messages);

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
    case "general-tools":
      return <GeneralToolsModule page={route.page} />;
    case "app-settings":
      return <AppSettingsModule page={route.page} />;
  }
}
