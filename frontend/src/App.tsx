import { useEffect } from "react";
import { useMessages } from "./locales";
import { isRunPage, useTaskStore, type Route } from "./store/useTaskStore";
import { useModuleSettings } from "./store/useSettingsStore";
import { SubNav, crumbFor } from "./components/SubNav";
import { Rail } from "./components/Rail";
import { StatusBar } from "./components/StatusBar";
import { ModelModule } from "./pages/model";
import { TranslationModule } from "./pages/translation";
import { GlossaryModule } from "./pages/glossary";
import { GeneralToolsModule } from "./pages/general-tools";
import { AppSettingsModule } from "./pages/app-settings";
import { AllKeysFailedDialog } from "./components/AllKeysFailedDialog";
import styles from "./App.module.css";

export function App() {
  const messages = useMessages();
  const route = useTaskStore((state) => state.route);
  useThemeAttribute();

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
    </div>
  );
}

function useThemeAttribute() {
  const draft = useModuleSettings("app").draft;
  const theme = draft?.theme ?? "system";
  useEffect(() => {
    const root = document.documentElement;
    const apply = () => {
      const resolved =
        theme === "system"
          ? window.matchMedia("(prefers-color-scheme: dark)").matches
            ? "dark"
            : "light"
          : theme;
      root.dataset.theme = resolved;
    };
    apply();
    if (theme !== "system") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    mql.addEventListener("change", apply);
    return () => mql.removeEventListener("change", apply);
  }, [theme]);
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
