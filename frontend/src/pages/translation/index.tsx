import { useMessages } from "@/locales";
import type { TranslationPage } from "@/store/useTaskStore";
import { PlaceholderPage } from "../PlaceholderPage";
import { ModelConfigPage } from "../shared/ModelConfigPage";
import { PromptConfigPage } from "../shared/PromptConfigPage";
import { GlossaryPage } from "./GlossaryPage";
import { RunPage } from "./RunPage";
import { SettingsPage } from "./SettingsPage";
import { TextPreservePage } from "./TextPreservePage";
import { TranslationReplacementPage } from "./TranslationReplacementPage";

interface TranslationModuleProps {
  page: TranslationPage;
}

export function TranslationModule({ page }: TranslationModuleProps) {
  const messages = useMessages();
  if (page === "run") return <RunPage />;
  if (page === "settings") return <SettingsPage />;
  if (page === "model") return <ModelConfigPage owner="translation" />;
  if (page === "prompt") return <PromptConfigPage owner="translation" />;
  if (page === "glossary") return <GlossaryPage />;
  if (page === "textPreserve") return <TextPreservePage />;
  if (page === "replacement") return <TranslationReplacementPage />;
  return <PlaceholderPage title={messages.pages.translation[page]} />;
}
