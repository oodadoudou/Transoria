import type { GlossaryPage } from "@/store/useTaskStore";
import { WorkflowPresetsPage } from "../shared/WorkflowPresetsPage";
import { ReviewPage } from "../glossary-review/ReviewPage";
import { PromptPage } from "./PromptPage";
import { RunPage } from "./RunPage";
import { SettingsPage } from "./SettingsPage";

interface GlossaryModuleProps {
  page: GlossaryPage;
}

export function GlossaryModule({ page }: GlossaryModuleProps) {
  if (page === "review") return <ReviewPage />;
  if (page === "settings") return <SettingsPage />;
  if (page === "prompt") return <PromptPage />;
  if (page === "presets") return <WorkflowPresetsPage owner="glossary" />;
  return <RunPage />;
}
