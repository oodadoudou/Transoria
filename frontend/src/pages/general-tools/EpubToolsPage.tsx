import { lazy, Suspense, useEffect, useMemo, useState } from "react";

import { HelpTip } from "@/components/HelpTip";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/Button";
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
import { nativeEditor } from "@/bridge/native";
import { ChevronLeft } from "lucide-react";
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
  const desktop = new URLSearchParams(window.location.search).has("desktop");
  const [contentOpen, setContentOpen] = useState(() => !desktop && (initialTool === "epubContent" || (!initialTool && editorWasOpen())));
  const [openError, setOpenError] = useState("");
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
  const contentSpec = tools.find((tool) => tool.id === "epubContent");

  useEffect(() => {
    if (initialTool === "epubContent" && desktop) {
      void nativeEditor.open().catch((cause) => setOpenError(String(cause)));
    } else if (initialTool === "epubContent" || (!initialTool && !desktop && editorWasOpen())) {
      setContentOpen(true);
    }
  }, [initialTool, desktop]);
  useEffect(() => {
    if (!contentOpen) return;
    document.documentElement.classList.add("transoria-epub-editor-open");
    return () => document.documentElement.classList.remove("transoria-epub-editor-open");
  }, [contentOpen]);

  const openContent = (path: string) => {
    setOpenError("");
    void nativeEditor.open(path).then((opened) => {
      if (!opened) {
        markEditorOpen(true);
        setContentPath(path);
        setContentOpen(true);
      }
    }).catch((cause) => setOpenError(String(cause)));
  };

  const closeContent = () => {
    markEditorOpen(false);
    setContentOpen(false);
    if (initialTool === "epubContent") navigate({ module: "general-tools", page: "epubTools" });
  };

  const tool = initialTool && initialTool !== "epubContent" ? initialTool : null;
  const backToTools = () => navigate({ module: "general-tools", page: "epubTools" });
  useEscapeKey(() => {
    // Tool dialogs that don't register their own Escape handler must keep the page.
    if (document.querySelector('[aria-modal="true"]')) return;
    backToTools();
  }, tool !== null && !contentOpen);

  return (
    <>
      {tool ? (
        <div className={styles.toolPage}>
          <Button
            size="sm"
            variant="quiet"
            className={styles.backButton}
            icon={<ChevronLeft size={14} />}
            onClick={backToTools}
          >
            {messages.pages.generalTools.epubTools}
          </Button>
          {renderTool(tool, openContent)}
        </div>
      ) : (
        <Panel title={text.title} subtitle={text.sub}>
          {openError ? <div role="alert">{openError}</div> : null}
          <div className={styles.toolGrid}>
            {tools.map((item) => (
              <button
                key={item.id}
                type="button"
                className={styles.toolButton}
                onClick={() => {
                  if (item.id === "epubContent") openContent("");
                  else navigate({ module: "general-tools", page: item.id });
                }}
              >
                <span>{item.title}</span>
                <small>{item.sub}</small>
                <strong>{text.open}</strong>
              </button>
            ))}
          </div>
        </Panel>
      )}

      {contentOpen && contentSpec ? (
        <div className={styles.overlay} role="presentation">
          <section
            className={`${styles.dialog} ${styles.contentDialog}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="epub-tool-dialog-title"
          >
            <div className={styles.dialogHeader}>
              <div className={styles.dialogTitleRow}>
                <h2 id="epub-tool-dialog-title">{contentSpec.title}</h2>
                <HelpTip>{contentSpec.sub}</HelpTip>
              </div>
            </div>
            <div className={styles.contentBody}>
              <Suspense fallback={null}>
                <EpubContentPage onClose={closeContent} initialPath={contentPath} />
              </Suspense>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

function renderTool(tool: Exclude<EpubToolPage, "epubContent">, openContent: (path: string) => void) {
  switch (tool) {
    case "epubCompress":
      return <EpubCompressPage />;
    case "epubMerge":
      return <EpubMergePage />;
    case "epubConvert":
      return <EpubConvertPage />;
    case "txtToEpub":
      return <TxtToEpubPage />;
    case "epubMetadata":
      return <EpubMetadataPage onEditContent={openContent} />;
    case "epubRepair":
      return <EpubRepairPage />;
    case "batchReplacement":
      return <BatchReplacementPage />;
  }
}
