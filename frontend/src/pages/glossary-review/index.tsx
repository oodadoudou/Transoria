import { useMessages } from "@/locales";
import type { GlossaryReviewPage } from "@/store/useTaskStore";
import { PlaceholderPage } from "../PlaceholderPage";
import { PromptConfigPage } from "../shared/PromptConfigPage";
import { WorkflowPresetsPage } from "../shared/WorkflowPresetsPage";
import { ReviewPage } from "./ReviewPage";
import { RunPage } from "./RunPage";
import { SettingsPage } from "./SettingsPage";

interface GlossaryReviewModuleProps {
  page: GlossaryReviewPage;
}

export function GlossaryReviewModule({ page }: GlossaryReviewModuleProps) {
  const messages = useMessages();
  if (page === "run") return <RunPage />;
  if (page === "review") return <ReviewPage />;
  if (page === "settings") return <SettingsPage />;
  if (page === "prompt") return <PromptConfigPage owner="glossary_review" />;
  if (page === "presets")
    return <WorkflowPresetsPage owner="glossary_review" />;
  return <PlaceholderPage title={messages.pages.glossaryReview[page]} />;
}
