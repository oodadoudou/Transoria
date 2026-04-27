import { useMessages } from "./locales";
import { isRunPage, useTaskStore, type Route } from "./store/useTaskStore";
import { SubNav, crumbFor } from "./components/SubNav";
import { Rail } from "./components/Rail";
import { StatusBar } from "./components/StatusBar";
import { TranslationModule } from "./pages/translation";
import { GlossaryModule } from "./pages/glossary";
import { GeneralToolsModule } from "./pages/general-tools";
import { AppSettingsModule } from "./pages/app-settings";
import styles from "./App.module.css";

export function App() {
  const messages = useMessages();
  const route = useTaskStore((state) => state.route);

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
    </div>
  );
}

function PageBody({ route }: { route: Route }) {
  switch (route.module) {
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
