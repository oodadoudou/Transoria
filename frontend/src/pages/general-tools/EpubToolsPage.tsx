import { useEffect, useMemo, useState } from "react";

import { HelpTip } from "@/components/HelpTip";
import { Panel } from "@/components/Panel";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useMessages } from "@/locales";
import type { GeneralToolsPage } from "@/store/useTaskStore";
import { EpubCompressPage } from "./EpubCompressPage";
import { EpubConvertPage } from "./EpubConvertPage";
import { EpubMergePage } from "./EpubMergePage";
import { EpubMetadataPage } from "./EpubMetadataPage";
import { EpubRepairPage } from "./EpubRepairPage";
import { TxtToEpubPage } from "./TxtToEpubPage";
import styles from "./EpubToolsPage.module.css";

type EpubToolPage = Exclude<GeneralToolsPage, "batchReplacement" | "epubTools">;

interface EpubToolsPageProps {
  initialTool?: EpubToolPage | null;
}

export function EpubToolsPage({ initialTool = null }: EpubToolsPageProps) {
  const messages = useMessages();
  const text = messages.generalTools.epubTools;
  const [activeTool, setActiveTool] = useState<EpubToolPage | null>(initialTool);
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
          id: "epubRepair",
          title: messages.generalTools.epubRepair.title,
          sub: messages.generalTools.epubRepair.sub,
        },
      ] satisfies Array<{ id: EpubToolPage; title: string; sub: string }>,
    [messages],
  );
  const activeSpec = tools.find((tool) => tool.id === activeTool) ?? null;

  useEffect(() => {
    setActiveTool(initialTool);
  }, [initialTool]);
  useEscapeKey(() => setActiveTool(null), activeTool !== null);

  return (
    <>
      <Panel title={text.title} subtitle={text.sub}>
        <div className={styles.toolGrid}>
          {tools.map((tool) => (
            <button
              key={tool.id}
              type="button"
              className={styles.toolButton}
              onClick={() => setActiveTool(tool.id)}
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
            className={styles.dialog}
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
              <button
                type="button"
                className={styles.closeButton}
                onClick={() => setActiveTool(null)}
                aria-label={text.close}
              >
                ×
              </button>
            </div>
            <div className={styles.dialogBody}>{renderTool(activeTool)}</div>
          </section>
        </div>
      ) : null}
    </>
  );
}

function renderTool(tool: EpubToolPage) {
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
      return <EpubMetadataPage embedded />;
    case "epubRepair":
      return <EpubRepairPage embedded />;
  }
}
