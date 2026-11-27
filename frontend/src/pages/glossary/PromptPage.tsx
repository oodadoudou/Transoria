import { useState } from "react";
import { useMessages } from "@/locales";
import { Segmented } from "@/components/Segmented";
import { PromptConfigPage } from "../shared/PromptConfigPage";
import styles from "./RunPage.module.css";

type Stage = "glossary" | "glossary_review";

export function PromptPage() {
  const { run } = useMessages().glossary;
  const [stage, setStage] = useState<Stage>("glossary");
  return (
    <>
      <div className={styles.stageSwitch}>
        <Segmented<Stage>
          ariaLabel={run.stageSwitch}
          options={[
            { id: "glossary", label: run.extractStage },
            { id: "glossary_review", label: run.reviewStage },
          ]}
          value={stage}
          onChange={setStage}
        />
      </div>
      <PromptConfigPage key={stage} owner={stage} />
    </>
  );
}
