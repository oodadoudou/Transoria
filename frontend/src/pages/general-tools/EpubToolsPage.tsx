import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import { HelpTip } from "@/components/HelpTip";
import { Panel } from "@/components/Panel";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useMessages } from "@/locales";
import type { GeneralToolsPage } from "@/store/useTaskStore";
import { useTaskStore } from "@/store/useTaskStore";
import { BatchReplacementPage } from "./BatchReplacementPage";
import { EpubCompressPage } from "./EpubCompressPage";
import { EpubConvertPage } from "./EpubConvertPage";
import { EpubMergePage } from "./EpubMergePage";
import { EpubMetadataPage } from "./EpubMetadataPage";
import { EpubRepairPage } from "./EpubRepairPage";
import { TxtToEpubPage } from "./TxtToEpubPage";
import { editorWasOpen, markEditorOpen } from "./epubEditorDraft";
import styles from "./EpubToolsPage.module.css";

type EpubToolPage = Exclude<GeneralToolsPage, "epubTools">;
const EpubContentPage = lazy(() => import("./EpubContentPage").then((module) => ({ default: module.EpubContentPage })));

interface EpubToolsPageProps {
  initialTool?: EpubToolPage | null;
}

export function EpubToolsPage({ initialTool = null }: EpubToolsPageProps) {
  const messages = useMessages();
  const text = messages.generalTools.epubTools;
  const navigate = useTaskStore((state) => state.navigate);
  const [activeTool, setActiveTool] = useState<EpubToolPage | null>(() => initialTool ?? (editorWasOpen() ? "epubContent" : null));
  const [contentPath, setContentPath] = useState("");
  const tools = useMemo(
    () =>
      [
        {
          id: "epubCompress",
          title: messages.generalTools.epubCompress.title,
          sub: messages.generalTools.epubCompress.sub,
        },
        {
          id: "epubMerge",
          title: messages.generalTools.epubMerge.title,
          sub: messages.generalTools.epubMerge.sub,
        },
        {
          id: "epubConvert",
          title: messages.generalTools.epubConvert.title,
          sub: messages.generalTools.epubConvert.sub,
        },
        {
          id: "txtToEpub",
          title: messages.generalTools.txtToEpub.title,
          sub: messages.generalTools.txtToEpub.sub,
        },
        {
          id: "epubMetadata",
          title: messages.generalTools.epubMetadata.title,
          sub: messages.generalTools.epubMetadata.sub,
        },
        {
          id: "epubContent",
          title: messages.generalTools.epubContent.title,
          sub: messages.generalTools.epubContent.sub,
        },
        {
          id: "epubRepair",
          title: messages.generalTools.epubRepair.title,
          sub: messages.generalTools.epubRepair.sub,
        },
        {
          id: "batchReplacement",
          title: messages.batchReplacement.title,
          sub: messages.batchReplacement.sub,
        },
      ] satisfies Array<{ id: EpubToolPage; title: string; sub: string }>,
    [messages],
  );
  const activeSpec = tools.find((tool) => tool.id === activeTool) ?? null;

  useEffect(() => {
    if (initialTool) setActiveTool(initialTool);
    else if (editorWasOpen()) setActiveTool("epubContent");
  }, [initialTool]);
  useEffect(() => {
    if (activeTool !== "epubContent") return;
    document.documentElement.classList.add("transoria-epub-editor-open");
    return () => document.documentElement.classList.remove("transoria-epub-editor-open");
  }, [activeTool]);
  useEscapeKey(() => setActiveTool(null), activeTool !== null && activeTool !== "epubContent");

  const openContent = (path: string) => {
    markEditorOpen(true);
    setContentPath(path);
    setActiveTool("epubContent");
  };

  const closeContent = () => {
    markEditorOpen(false);
    setActiveTool(null);
    if (initialTool === "epubContent") navigate({ module: "general-tools", page: "epubTools" });
  };

  return (
    <>
      <Panel title={text.title} subtitle={text.sub}>
        <div className={styles.toolGrid}>
          {tools.map((tool) => (
            <button
              key={tool.id}
              type="button"
              className={styles.toolButton}
              onClick={() => { if (tool.id === "epubContent") openContent(""); else setActiveTool(tool.id); }}
            >
              <span>{tool.title}</span>
              <small>{tool.sub}</small>
              <strong>{text.open}</strong>
            </button>
          ))}
        </div>
      </Panel>

      {activeTool && activeSpec ? (
        <div className={styles.overlay} role="presentation">
          <section
            className={`${styles.dialog} ${activeTool === "epubContent" ? styles.contentDialog : ""}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="epub-tool-dialog-title"
          >
            <div className={styles.dialogHeader}>
              <div>
                <div className={styles.dialogTitleRow}>
                  <h2 id="epub-tool-dialog-title">{activeSpec.title}</h2>
                  <HelpTip>{activeSpec.sub}</HelpTip>
                </div>
              </div>
              {activeTool !== "epubContent" ? <button
                type="button"
                className={styles.closeButton}
                onClick={() => setActiveTool(null)}
                aria-label={text.close}
              >
                ×
              </button> : null}
            </div>
            <div className={`${styles.dialogBody} ${activeTool === "epubContent" ? styles.contentBody : ""}`}>{activeTool === "epubContent" ? <Suspense fallback={null}><EpubContentPage onClose={closeContent} initialPath={contentPath} /></Suspense> : renderTool(activeTool, openContent)}</div>
          </section>
        </div>
      ) : null}
    </>
  );
}

function renderTool(tool: EpubToolPage, openContent: (path: string) => void) {
  switch (tool) {
    case "epubCompress":
      return <EpubCompressPage embedded />;
    case "epubMerge":
      return <EpubMergePage embedded />;
    case "epubConvert":
      return <EpubConvertPage embedded />;
    case "txtToEpub":
      return <TxtToEpubPage embedded />;
    case "epubMetadata":
      return <EpubMetadataPage embedded onEditContent={openContent} />;
    case "epubContent":
      return null;
    case "epubRepair":
      return <EpubRepairPage embedded />;
    case "batchReplacement":
      return <BatchReplacementPage embedded />;
  }
}
