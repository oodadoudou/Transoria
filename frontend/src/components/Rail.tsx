import { useState } from "react";
import { useMessages } from "@/locales";
import {
  defaultPageFor,
  useTaskStore,
  type ModuleId,
  type Route,
} from "@/store/useTaskStore";
import styles from "./Rail.module.css";

interface ChildSpec {
  page: Route["page"];
  label: string;
}

interface ModuleSpec {
  id: ModuleId;
  label: string;
  /** Null = leaf (no children, navigates to its default page on click). */
  children: ReadonlyArray<ChildSpec> | null;
}

function buildTree(messages: ReturnType<typeof useMessages>): {
  modules: ReadonlyArray<ModuleSpec>;
  workspace: ReadonlyArray<ModuleSpec>;
} {
  const t = messages.pages.translation;
  const g = messages.pages.glossary;
  const gt = messages.pages.generalTools;

  return {
    modules: [
      {
        id: "model",
        label: messages.rail.model,
        children: null,
      },
      {
        id: "translation",
        label: messages.rail.translation,
        children: [
          { page: "run", label: t.run },
          { page: "glossary", label: t.glossary },
          { page: "prompt", label: t.prompt },
          { page: "settings", label: t.settings },
          { page: "textPreserve", label: t.textPreserve },
          { page: "replacement", label: t.replacement },
        ],
      },
      {
        id: "glossary",
        label: messages.rail.glossary,
        children: [
          { page: "run", label: g.run },
          { page: "prompt", label: g.prompt },
          { page: "settings", label: g.settings },
        ],
      },
      {
        id: "general-tools",
        label: messages.rail.generalTools,
        children: [{ page: "batchReplacement", label: gt.batchReplacement }],
      },
    ],
    workspace: [
      {
        id: "app-settings",
        label: messages.rail.appSettings,
        children: null,
      },
    ],
  };
}

export function Rail() {
  const messages = useMessages();
  const route = useTaskStore((state) => state.route);
  const navigate = useTaskStore((state) => state.navigate);
  const tree = buildTree(messages);

  // Modules whose subtree the user has manually toggled open. The active
  // module's subtree is always treated as open even if it isn't in this set.
  const [expanded, setExpanded] = useState<ReadonlySet<ModuleId>>(
    () => new Set([route.module]),
  );

  const toggle = (id: ModuleId) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const isOpen = (id: ModuleId) => expanded.has(id);

  return (
    <nav className={styles.rail} aria-label={messages.rail.modulesAria}>
      <div className={styles.label}>{messages.rail.modules}</div>
      {tree.modules.map((mod) => (
        <ModuleNode
          key={mod.id}
          spec={mod}
          route={route}
          open={isOpen(mod.id)}
          onToggle={() => toggle(mod.id)}
          onNavigate={navigate}
        />
      ))}

      <div className={styles.sep} />
      <div className={styles.label}>{messages.rail.workspace}</div>
      {tree.workspace.map((mod) => (
        <ModuleNode
          key={mod.id}
          spec={mod}
          route={route}
          open={isOpen(mod.id)}
          onToggle={() => toggle(mod.id)}
          onNavigate={navigate}
        />
      ))}

      <div className={styles.footer}>
        <a
          className={styles.githubLink}
          href="https://github.com/oodadoudou/Transoria"
          target="_blank"
          rel="noreferrer"
          aria-label={messages.rail.githubAria}
        >
          <span>{messages.rail.githubLink}</span>
          <span className={styles.githubStar}>★</span>
          <span className={styles.githubFace}>{messages.rail.githubFace}</span>
        </a>
        <div className={styles.watermark} aria-hidden>
          <span className={styles.watermarkApp}>Transoria</span>
          <span className={styles.watermarkSep}>·</span>
          <span className={styles.watermarkAuthor}>Dadoudouoo</span>
        </div>
      </div>
    </nav>
  );
}

interface ModuleNodeProps {
  spec: ModuleSpec;
  route: Route;
  open: boolean;
  onToggle: () => void;
  onNavigate: (route: Route) => void;
}

function ModuleNode({
  spec,
  route,
  open,
  onToggle,
  onNavigate,
}: ModuleNodeProps) {
  if (spec.children === null) {
    // Leaf — navigate directly. No expand affordance.
    const active = route.module === spec.id;
    return (
      <button
        type="button"
        className={`${styles.row} ${styles.leaf} ${active ? styles.active : ""}`.trim()}
        aria-current={active ? "page" : undefined}
        onClick={() => onNavigate(defaultPageFor(spec.id))}
      >
        <span className={styles.ind} />
        <span className={styles.label2}>{spec.label}</span>
      </button>
    );
  }

  return (
    <div className={styles.group}>
      <button
        type="button"
        className={`${styles.row} ${styles.parent}`.trim()}
        onClick={onToggle}
        aria-expanded={open}
      >
        <Chevron open={open} />
        <span className={styles.label2}>{spec.label}</span>
      </button>
      {open ? (
        <div className={styles.children}>
          {spec.children.map((child) => {
            const active =
              route.module === spec.id && route.page === child.page;
            return (
              <button
                key={child.page}
                type="button"
                className={`${styles.row} ${styles.child} ${active ? styles.active : ""}`.trim()}
                aria-current={active ? "page" : undefined}
                onClick={() =>
                  onNavigate({
                    module: spec.id,
                    page: child.page,
                  } as Route)
                }
              >
                <span className={styles.ind} />
                <span className={styles.label2}>{child.label}</span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="10"
      height="10"
      viewBox="0 0 10 10"
      className={`${styles.chevron} ${open ? styles.chevronOpen : ""}`.trim()}
      aria-hidden
    >
      <path
        d="M3 2l4 3-4 3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
