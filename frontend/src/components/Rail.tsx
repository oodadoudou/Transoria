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

const RAIL_EXPANDED_STORAGE_KEY = "transoria.rail.expanded";

function buildTree(messages: ReturnType<typeof useMessages>): {
  modules: ReadonlyArray<ModuleSpec>;
  workspace: ReadonlyArray<ModuleSpec>;
} {
  const t = messages.pages.translation;
  const g = messages.pages.glossary;
  const gr = messages.pages.glossaryReview;
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
          { page: "proofreading", label: t.proofreading },
          { page: "prompt", label: t.prompt },
          { page: "settings", label: t.settings },
          { page: "textPreserve", label: t.textPreserve },
          { page: "preReplacement", label: t.preReplacement },
          { page: "postReplacement", label: t.postReplacement },
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
        id: "glossary-review",
        label: messages.rail.glossaryReview,
        children: [
          { page: "run", label: gr.run },
          { page: "review", label: gr.review },
          { page: "prompt", label: gr.prompt },
          { page: "settings", label: gr.settings },
        ],
      },
      {
        id: "general-tools",
        label: messages.rail.generalTools,
        children: [
          { page: "batchReplacement", label: gt.batchReplacement },
          { page: "epubCompress", label: gt.epubCompress },
          { page: "epubMerge", label: gt.epubMerge },
          { page: "epubConvert", label: gt.epubConvert },
          { page: "txtToEpub", label: gt.txtToEpub },
          { page: "epubMetadata", label: gt.epubMetadata },
          { page: "epubRepair", label: gt.epubRepair },
        ],
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

function loadExpandedModules(): ReadonlySet<ModuleId> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(RAIL_EXPANDED_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    const modules = parsed.filter(isModuleId);
    return new Set(modules);
  } catch {
    return new Set();
  }
}

function saveExpandedModules(expanded: ReadonlySet<ModuleId>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      RAIL_EXPANDED_STORAGE_KEY,
      JSON.stringify([...expanded]),
    );
  } catch {
    // Manual sidebar state is optional.
  }
}

function isModuleId(value: unknown): value is ModuleId {
  return (
    value === "model" ||
    value === "translation" ||
    value === "glossary" ||
    value === "glossary-review" ||
    value === "general-tools" ||
    value === "app-settings"
  );
}

export function Rail() {
  const messages = useMessages();
  const route = useTaskStore((state) => state.route);
  const navigate = useTaskStore((state) => state.navigate);
  const tree = buildTree(messages);

  const [expanded, setExpanded] = useState<ReadonlySet<ModuleId>>(
    loadExpandedModules,
  );

  const toggle = (id: ModuleId) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      saveExpandedModules(next);
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
